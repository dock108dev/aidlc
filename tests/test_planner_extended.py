"""Extended tests for aidlc.planner — targeting uncovered lines."""

import json
import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aidlc.planner import Planner
from aidlc.models import RunState, RunPhase, Issue


@pytest.fixture
def logger():
    return logging.getLogger("test_plan_ext")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "checkpoint_interval_minutes": 999,
        "max_consecutive_failures": 3,
        "finalization_budget_percent": 10,
        "dry_run": False,
        "max_planning_cycles": 0,
    }


def make_planning_response(actions=None, frontier="Assessed", notes="Notes"):
    """Build a valid planning JSON response."""
    data = {
        "frontier_assessment": frontier,
        "actions": actions or [],
        "cycle_notes": notes,
    }
    return f"```json\n{json.dumps(data)}\n```"


class TestPlanningCycleWithRealOutput:
    def test_creates_issues_from_claude_output(self, config, logger, tmp_path):
        response = make_planning_response(actions=[
            {
                "action_type": "create_issue",
                "rationale": "Need auth",
                "issue_id": "ISSUE-001",
                "title": "Add authentication",
                "description": "Implement auth module",
                "priority": "high",
                "labels": ["feature"],
                "dependencies": [],
                "acceptance_criteria": ["Login works", "Logout works"],
            },
            {
                "action_type": "create_issue",
                "rationale": "Need tests",
                "issue_id": "ISSUE-002",
                "title": "Add auth tests",
                "description": "Test auth module",
                "priority": "medium",
                "labels": ["test"],
                "dependencies": ["ISSUE-001"],
                "acceptance_criteria": ["All tests pass"],
            },
        ])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 5.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["max_planning_cycles"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert state.issues_created == 2
        assert len(state.issues) == 2
        assert state.issues[0]["id"] == "ISSUE-001"

    def test_empty_actions_stops_planning(self, config, logger, tmp_path):
        response = make_planning_response(actions=[])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["max_planning_cycles"] = 10
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert "clear" in (state.stop_reason or "").lower()

    def test_invalid_json_counts_as_failure(self, config, logger, tmp_path):
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": "Just some text with no JSON",
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["max_consecutive_failures"] = 1
        config["max_planning_cycles"] = 10
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert "failures" in (state.stop_reason or "").lower()

    def test_strict_mode_fails_on_validation_errors(self, config, logger, tmp_path):
        response = make_planning_response(actions=[
            {
                "action_type": "create_issue",
                "rationale": "Need auth",
                "issue_id": "ISSUE-001",
                "title": "Add authentication",
                # Missing required description + acceptance_criteria
            },
        ])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["strict_mode"] = True
        config["max_consecutive_failures"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        assert "failures" in (state.stop_reason or "").lower()

    def test_cycle_fails_when_all_actions_error(self, config, logger, tmp_path):
        response = make_planning_response(actions=[
            {
                "action_type": "create_issue",
                "rationale": "Need auth",
                "issue_id": "ISSUE-001",
                "title": "Add authentication",
                "description": "desc",
                "acceptance_criteria": ["AC1"],
            },
        ])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600
        config["max_consecutive_failures"] = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner._apply_action = MagicMock(side_effect=RuntimeError("disk full"))
        planner.run()
        assert "failures" in (state.stop_reason or "").lower()


class TestBuildPrompt:
    def test_includes_existing_issues(self, config, logger, tmp_path):
        state = RunState(run_id="test", config_name="default")
        state.issues = [
            {"id": "ISSUE-001", "title": "Existing", "description": "D",
             "priority": "high", "labels": [], "dependencies": [],
             "acceptance_criteria": ["AC1"], "status": "pending",
             "implementation_notes": "", "verification_result": "",
             "files_changed": [], "attempt_count": 0, "max_attempts": 3},
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "project context", logger)
        prompt = planner._build_prompt(is_finalization=False)
        assert "ISSUE-001" in prompt
        assert "Existing" in prompt
        assert "project context" in prompt

    def test_finalization_prompt(self, config, logger, tmp_path):
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 100
        state.plan_elapsed_seconds = 95
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        prompt = planner._build_prompt(is_finalization=True)
        assert "FINALIZATION" in prompt
        assert "MUST NOT" in prompt

    def test_normal_prompt_includes_instructions(self, config, logger, tmp_path):
        state = RunState(run_id="test", config_name="default")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        prompt = planner._build_prompt(is_finalization=False)
        assert "Planning Mode" in prompt
        assert "acceptance criteria" in prompt.lower()


class TestApplyActionEdgeCases:
    def test_update_unknown_issue_warns(self, config, logger, tmp_path):
        from aidlc.schemas import PlanningAction
        state = RunState(run_id="test", config_name="default")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        action = PlanningAction(
            action_type="update_issue",
            rationale="Refine",
            issue_id="ISSUE-999",
        )
        planner._apply_action(action)
        assert len(state.issues) == 0

    def test_update_doc(self, config, logger, tmp_path):
        from aidlc.schemas import PlanningAction
        state = RunState(run_id="test", config_name="default")
        # Create initial doc
        doc_path = tmp_path / "docs" / "design.md"
        doc_path.parent.mkdir(parents=True)
        doc_path.write_text("# V1")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        action = PlanningAction(
            action_type="update_doc",
            rationale="Update design",
            file_path="docs/design.md",
            content="# V2\nUpdated",
        )
        planner._apply_action(action)
        assert doc_path.read_text() == "# V2\nUpdated"
        assert state.files_created == 1
        assert state.created_artifacts[0]["action"] == "update"


class TestCheckpointDuringPlanning:
    def test_checkpoint_fires(self, config, logger, tmp_path):
        config["checkpoint_interval_minutes"] = 0  # Checkpoint every cycle
        config["max_planning_cycles"] = 1
        response = make_planning_response(actions=[
            {
                "action_type": "create_issue",
                "rationale": "Need it",
                "issue_id": "ISSUE-001",
                "title": "T",
                "description": "D",
                "priority": "high",
                "acceptance_criteria": ["AC"],
            },
        ])
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True, "output": response,
            "error": None, "failure_type": None,
            "duration_seconds": 1.0, "retries": 0,
        }
        state = RunState(run_id="test", config_name="default")
        state.plan_budget_seconds = 3600

        reports_dir = tmp_path / ".aidlc" / "reports" / "test"
        reports_dir.mkdir(parents=True)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        planner = Planner(state, run_dir, config, cli, "context", logger)
        planner.run()
        # Should have checkpointed
        assert state.checkpoint_count >= 1


class TestRenderIssueMd:
    def test_renders_complete_issue(self, config, logger, tmp_path):
        state = RunState(run_id="test", config_name="default")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        planner = Planner(state, run_dir, config, MagicMock(), "context", logger)
        issue = Issue(
            id="ISSUE-001", title="Test", description="Desc",
            priority="high", labels=["feature"],
            dependencies=["ISSUE-000"],
            acceptance_criteria=["AC1", "AC2"],
        )
        issue.implementation_notes = "Some notes"
        md = planner._render_issue_md(issue)
        assert "# ISSUE-001: Test" in md
        assert "high" in md
        assert "feature" in md
        assert "ISSUE-000" in md
        assert "- [ ] AC1" in md
        assert "Some notes" in md
