"""Tests for aidlc.schemas module."""

import pytest
from aidlc.schemas import (
    PlanningAction, PlanningOutput, ImplementationResult,
    parse_json_output, parse_planning_output, parse_implementation_result,
)


class TestParseJsonOutput:
    def test_json_code_block(self):
        raw = '''Some text before
```json
{"key": "value", "num": 42}
```
Some text after'''
        result = parse_json_output(raw)
        assert result["key"] == "value"
        assert result["num"] == 42

    def test_raw_json(self):
        raw = 'Here is the result: {"success": true, "data": [1, 2]}'
        result = parse_json_output(raw)
        assert result["success"] is True

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON found"):
            parse_json_output("This has no JSON at all")

    def test_invalid_json_raises(self):
        raw = '```json\n{invalid json here}\n```'
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            parse_json_output(raw)

    def test_nested_json(self):
        raw = '''```json
{
  "actions": [{"type": "create", "data": {"nested": true}}]
}
```'''
        result = parse_json_output(raw)
        assert result["actions"][0]["data"]["nested"] is True


class TestPlanningAction:
    def test_valid_create_issue(self):
        action = PlanningAction(
            action_type="create_issue",
            rationale="Need this",
            issue_id="ISSUE-001",
            title="My Issue",
            description="Description",
            priority="high",
            acceptance_criteria=["AC1"],
        )
        errors = action.validate(known_issue_ids=set())
        assert errors == []

    def test_create_issue_missing_fields(self):
        action = PlanningAction(
            action_type="create_issue",
            rationale="Need this",
        )
        errors = action.validate()
        assert any("issue_id" in e for e in errors)
        assert any("title" in e for e in errors)
        assert any("description" in e for e in errors)
        assert any("acceptance_criteria" in e for e in errors)

    def test_create_issue_blocked_during_finalization(self):
        action = PlanningAction(
            action_type="create_issue",
            rationale="Need this",
            issue_id="ISSUE-001",
            title="T",
            description="D",
            acceptance_criteria=["AC1"],
        )
        errors = action.validate(is_finalization=True)
        assert any("prohibited during finalization" in e for e in errors)

    def test_create_issue_duplicate(self):
        action = PlanningAction(
            action_type="create_issue",
            rationale="Need this",
            issue_id="ISSUE-001",
            title="T",
            description="D",
            acceptance_criteria=["AC1"],
        )
        errors = action.validate(known_issue_ids={"ISSUE-001"})
        assert any("already exists" in e for e in errors)

    def test_create_issue_unknown_dependency(self):
        action = PlanningAction(
            action_type="create_issue",
            rationale="Need this",
            issue_id="ISSUE-002",
            title="T",
            description="D",
            acceptance_criteria=["AC1"],
            dependencies=["ISSUE-999"],
        )
        errors = action.validate(known_issue_ids={"ISSUE-001"})
        assert any("not a known issue" in e for e in errors)

    def test_valid_update_issue(self):
        action = PlanningAction(
            action_type="update_issue",
            rationale="Refine",
            issue_id="ISSUE-001",
            description="Updated desc",
        )
        errors = action.validate(known_issue_ids={"ISSUE-001"})
        assert errors == []

    def test_update_unknown_issue(self):
        action = PlanningAction(
            action_type="update_issue",
            rationale="Refine",
            issue_id="ISSUE-999",
        )
        errors = action.validate(known_issue_ids={"ISSUE-001"})
        assert any("unknown issue" in e for e in errors)

    def test_valid_create_doc(self):
        action = PlanningAction(
            action_type="create_doc",
            rationale="Design doc",
            file_path="docs/design.md",
            content="# Design\nContent here",
        )
        errors = action.validate()
        assert errors == []

    def test_create_doc_missing_fields(self):
        action = PlanningAction(
            action_type="create_doc",
            rationale="Design doc",
        )
        errors = action.validate()
        assert any("file_path" in e for e in errors)
        assert any("content" in e for e in errors)

    def test_unknown_action_type(self):
        action = PlanningAction(action_type="delete_issue", rationale="R")
        errors = action.validate()
        assert any("Unknown action_type" in e for e in errors)

    def test_empty_rationale(self):
        action = PlanningAction(action_type="create_issue", rationale="")
        errors = action.validate()
        assert any("rationale" in e for e in errors)

    def test_from_dict(self):
        data = {
            "action_type": "create_issue",
            "rationale": "Need it",
            "issue_id": "ISSUE-001",
            "title": "Title",
            "description": "Desc",
            "priority": "high",
            "labels": ["feature"],
            "dependencies": [],
            "acceptance_criteria": ["AC1"],
        }
        action = PlanningAction.from_dict(data)
        assert action.issue_id == "ISSUE-001"
        assert action.priority == "high"


class TestPlanningOutput:
    def test_valid(self):
        output = PlanningOutput(
            frontier_assessment="Assessed",
            actions=[
                PlanningAction(
                    action_type="create_issue",
                    rationale="Need",
                    issue_id="ISSUE-001",
                    title="T",
                    description="D",
                    acceptance_criteria=["AC"],
                )
            ],
        )
        errors = output.validate(known_issue_ids=set())
        assert errors == []

    def test_duplicate_ids_in_batch(self):
        output = PlanningOutput(
            frontier_assessment="Assessed",
            actions=[
                PlanningAction(action_type="create_issue", rationale="A", issue_id="ISSUE-001",
                               title="T1", description="D1", acceptance_criteria=["AC"]),
                PlanningAction(action_type="create_issue", rationale="B", issue_id="ISSUE-001",
                               title="T2", description="D2", acceptance_criteria=["AC"]),
            ],
        )
        errors = output.validate(known_issue_ids=set())
        assert any("Duplicate" in e for e in errors)

    def test_from_dict(self):
        data = {
            "frontier_assessment": "Test",
            "actions": [
                {"action_type": "create_issue", "rationale": "R", "issue_id": "ISSUE-001",
                 "title": "T", "description": "D", "acceptance_criteria": ["AC"]},
            ],
            "cycle_notes": "Notes",
        }
        output = PlanningOutput.from_dict(data)
        assert len(output.actions) == 1
        assert output.cycle_notes == "Notes"


class TestImplementationResult:
    def test_from_dict(self):
        data = {
            "issue_id": "ISSUE-001",
            "success": True,
            "summary": "Done",
            "files_changed": ["a.py"],
            "tests_passed": True,
            "notes": "",
        }
        result = ImplementationResult.from_dict(data)
        assert result.success is True
        assert result.files_changed == ["a.py"]

    def test_from_dict_defaults(self):
        result = ImplementationResult.from_dict({})
        assert result.issue_id == ""
        assert result.success is False
        assert result.files_changed == []


class TestParsePlanningOutput:
    def test_parse(self):
        raw = '''```json
{
  "frontier_assessment": "Initial scan",
  "actions": [
    {
      "action_type": "create_issue",
      "rationale": "Foundation",
      "issue_id": "ISSUE-001",
      "title": "Setup",
      "description": "Initial setup",
      "priority": "high",
      "acceptance_criteria": ["Project builds"]
    }
  ],
  "cycle_notes": "First cycle"
}
```'''
        output = parse_planning_output(raw)
        assert len(output.actions) == 1
        assert output.actions[0].issue_id == "ISSUE-001"


class TestParseImplementationResult:
    def test_parse(self):
        raw = '''I implemented the feature.
```json
{
  "issue_id": "ISSUE-001",
  "success": true,
  "summary": "Added auth module",
  "files_changed": ["src/auth.py"],
  "tests_passed": true,
  "notes": ""
}
```'''
        result = parse_implementation_result(raw)
        assert result.success is True
        assert result.issue_id == "ISSUE-001"
