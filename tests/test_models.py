"""Tests for aidlc.models module."""

import pytest
from aidlc.models import Issue, IssueStatus, RunState, RunStatus, RunPhase


class TestIssue:
    """Tests for the Issue dataclass."""

    def test_create_basic(self):
        issue = Issue(id="ISSUE-001", title="Test", description="Desc")
        assert issue.id == "ISSUE-001"
        assert issue.priority == "medium"
        assert issue.status == IssueStatus.PENDING
        assert issue.attempt_count == 0
        assert issue.max_attempts == 3

    def test_to_dict(self):
        issue = Issue(
            id="ISSUE-001",
            title="Test",
            description="Desc",
            priority="high",
            labels=["feature"],
            dependencies=["ISSUE-000"],
            acceptance_criteria=["AC1"],
        )
        d = issue.to_dict()
        assert d["id"] == "ISSUE-001"
        assert d["priority"] == "high"
        assert d["labels"] == ["feature"]
        assert d["dependencies"] == ["ISSUE-000"]
        assert d["status"] == "pending"

    def test_from_dict(self):
        data = {
            "id": "ISSUE-002",
            "title": "From Dict",
            "description": "Loaded",
            "priority": "low",
            "labels": ["bug"],
            "dependencies": [],
            "acceptance_criteria": ["AC1", "AC2"],
            "status": "implemented",
            "implementation_notes": "Done",
            "files_changed": ["src/main.py"],
            "attempt_count": 2,
            "max_attempts": 5,
        }
        issue = Issue.from_dict(data)
        assert issue.id == "ISSUE-002"
        assert issue.status == IssueStatus.IMPLEMENTED
        assert issue.attempt_count == 2
        assert issue.max_attempts == 5
        assert issue.files_changed == ["src/main.py"]

    def test_from_dict_defaults(self):
        data = {"id": "ISSUE-003", "title": "Minimal"}
        issue = Issue.from_dict(data)
        assert issue.description == ""
        assert issue.priority == "medium"
        assert issue.labels == []
        assert issue.status == IssueStatus.PENDING

    def test_roundtrip(self):
        issue = Issue(
            id="ISSUE-010",
            title="Roundtrip",
            description="Test roundtrip",
            priority="high",
            labels=["infra"],
            dependencies=["ISSUE-009"],
            acceptance_criteria=["Works"],
        )
        issue.status = IssueStatus.VERIFIED
        issue.attempt_count = 1
        restored = Issue.from_dict(issue.to_dict())
        assert restored.id == issue.id
        assert restored.status == issue.status
        assert restored.attempt_count == issue.attempt_count


class TestRunState:
    """Tests for the RunState dataclass."""

    def test_create_default(self):
        state = RunState(run_id="test_001", config_name="default")
        assert state.status == RunStatus.PENDING
        assert state.phase == RunPhase.INIT
        assert state.plan_budget_seconds == 14400.0
        assert state.issues == []

    def test_is_plan_budget_exhausted(self):
        state = RunState(run_id="t", config_name="c")
        state.plan_budget_seconds = 100.0
        state.plan_elapsed_seconds = 99.0
        assert not state.is_plan_budget_exhausted()
        state.plan_elapsed_seconds = 100.0
        assert state.is_plan_budget_exhausted()
        state.plan_elapsed_seconds = 101.0
        assert state.is_plan_budget_exhausted()

    def test_should_finalize_planning_default(self):
        state = RunState(run_id="t", config_name="c")
        state.plan_budget_seconds = 100.0
        state.plan_elapsed_seconds = 89.0
        assert not state.should_finalize_planning()
        state.plan_elapsed_seconds = 90.0
        assert state.should_finalize_planning()

    def test_should_finalize_planning_custom_percent(self):
        state = RunState(run_id="t", config_name="c")
        state.plan_budget_seconds = 100.0
        state.plan_elapsed_seconds = 79.0
        assert not state.should_finalize_planning(finalization_budget_percent=20)
        state.plan_elapsed_seconds = 80.0
        assert state.should_finalize_planning(finalization_budget_percent=20)

    def test_update_issue_new(self):
        state = RunState(run_id="t", config_name="c")
        issue = Issue(id="ISSUE-001", title="New", description="New issue")
        state.update_issue(issue)
        assert len(state.issues) == 1
        assert state.issues[0]["id"] == "ISSUE-001"

    def test_update_issue_existing(self):
        state = RunState(run_id="t", config_name="c")
        issue = Issue(id="ISSUE-001", title="V1", description="First")
        state.update_issue(issue)
        issue.title = "V2"
        issue.description = "Updated"
        state.update_issue(issue)
        assert len(state.issues) == 1
        assert state.issues[0]["title"] == "V2"

    def test_get_issue(self):
        state = RunState(run_id="t", config_name="c")
        issue = Issue(id="ISSUE-001", title="Test", description="D")
        state.update_issue(issue)
        found = state.get_issue("ISSUE-001")
        assert found is not None
        assert found.title == "Test"
        assert state.get_issue("ISSUE-999") is None

    def test_get_pending_issues(self):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {"id": "ISSUE-001", "title": "A", "status": "pending", "dependencies": [], "attempt_count": 0, "max_attempts": 3},
            {"id": "ISSUE-002", "title": "B", "status": "implemented", "dependencies": [], "attempt_count": 1, "max_attempts": 3},
            {"id": "ISSUE-003", "title": "C", "status": "pending", "dependencies": ["ISSUE-002"], "attempt_count": 0, "max_attempts": 3},
            {"id": "ISSUE-004", "title": "D", "status": "pending", "dependencies": ["ISSUE-999"], "attempt_count": 0, "max_attempts": 3},
        ]
        pending = state.get_pending_issues()
        ids = [i.id for i in pending]
        assert "ISSUE-001" in ids  # No deps, pending
        assert "ISSUE-003" in ids  # Deps met (002 is implemented)
        assert "ISSUE-004" not in ids  # Dep 999 not met

    def test_get_pending_excludes_exhausted(self):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {"id": "ISSUE-001", "title": "A", "status": "failed", "dependencies": [], "attempt_count": 3, "max_attempts": 3},
        ]
        assert state.get_pending_issues() == []

    def test_all_issues_resolved(self):
        state = RunState(run_id="t", config_name="c")
        assert not state.all_issues_resolved()  # No issues = not resolved (need > 0)

        state.issues = [
            {"id": "ISSUE-001", "status": "verified", "attempt_count": 1, "max_attempts": 3},
            {"id": "ISSUE-002", "status": "implemented", "attempt_count": 1, "max_attempts": 3},
        ]
        assert state.all_issues_resolved()

    def test_all_issues_resolved_with_failed_exhausted(self):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {"id": "ISSUE-001", "title": "A", "status": "verified", "attempt_count": 1, "max_attempts": 3},
            {"id": "ISSUE-002", "title": "B", "status": "failed", "attempt_count": 3, "max_attempts": 3},
        ]
        assert state.all_issues_resolved()  # Failed but exhausted

    def test_all_issues_not_resolved_with_retryable_failed(self):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {"id": "ISSUE-001", "title": "A", "status": "failed", "attempt_count": 1, "max_attempts": 3},
        ]
        assert not state.all_issues_resolved()  # Can still retry

    def test_to_dict_and_from_dict_roundtrip(self):
        state = RunState(run_id="test_rt", config_name="default")
        state.status = RunStatus.RUNNING
        state.phase = RunPhase.IMPLEMENTING
        state.elapsed_seconds = 123.4
        state.planning_cycles = 5
        state.issues_created = 3
        state.issues = [
            {"id": "ISSUE-001", "title": "T", "status": "pending"},
        ]
        d = state.to_dict()
        restored = RunState.from_dict(d)
        assert restored.run_id == "test_rt"
        assert restored.status == RunStatus.RUNNING
        assert restored.phase == RunPhase.IMPLEMENTING
        assert restored.elapsed_seconds == 123.4
        assert len(restored.issues) == 1
