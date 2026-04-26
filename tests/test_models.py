"""Tests for aidlc.models module."""

import pytest
from aidlc.models import Issue, IssueStatus, RunPhase, RunState, RunStatus


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


def test_legacy_auditing_phase_is_absent():
    """SSOT: pre-planning intent flows through DISCOVERY → RESEARCH → PLANNING.
    The deprecated AUDITING phase and its forward-migration shim were removed;
    state.json files referencing 'auditing' will fail to deserialize."""
    assert not hasattr(RunPhase, "AUDITING")
    with pytest.raises(ValueError):
        RunState.from_dict(
            {
                "run_id": "x",
                "config_name": "default",
                "phase": "auditing",
            }
        )


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
            {
                "id": "ISSUE-001",
                "title": "A",
                "status": "pending",
                "dependencies": [],
                "attempt_count": 0,
                "max_attempts": 3,
            },
            {
                "id": "ISSUE-002",
                "title": "B",
                "status": "implemented",
                "dependencies": [],
                "attempt_count": 1,
                "max_attempts": 3,
            },
            {
                "id": "ISSUE-003",
                "title": "C",
                "status": "pending",
                "dependencies": ["ISSUE-002"],
                "attempt_count": 0,
                "max_attempts": 3,
            },
            {
                "id": "ISSUE-004",
                "title": "D",
                "status": "pending",
                "dependencies": ["ISSUE-999"],
                "attempt_count": 0,
                "max_attempts": 3,
            },
        ]
        pending = state.get_pending_issues()
        ids = [i.id for i in pending]
        assert "ISSUE-001" in ids  # No deps, pending
        assert "ISSUE-003" in ids  # Deps met (002 is implemented)
        assert "ISSUE-004" not in ids  # Dep 999 not met

    def test_get_pending_issues_prefers_in_progress(self):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "A",
                "status": "pending",
                "dependencies": [],
                "attempt_count": 0,
                "max_attempts": 3,
            },
            {
                "id": "ISSUE-002",
                "title": "B",
                "status": "in_progress",
                "dependencies": [],
                "attempt_count": 0,
                "max_attempts": 3,
            },
        ]
        ids = [i.id for i in state.get_pending_issues()]
        assert ids == ["ISSUE-002", "ISSUE-001"]

    def test_get_pending_excludes_exhausted(self):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "A",
                "status": "failed",
                "dependencies": [],
                "attempt_count": 3,
                "max_attempts": 3,
            },
        ]
        assert state.get_pending_issues() == []

    def test_all_issues_resolved(self):
        state = RunState(run_id="t", config_name="c")
        assert not state.all_issues_resolved()  # No issues = not resolved (need > 0)

        state.issues = [
            {
                "id": "ISSUE-001",
                "status": "verified",
                "attempt_count": 1,
                "max_attempts": 3,
            },
            {
                "id": "ISSUE-002",
                "status": "implemented",
                "attempt_count": 1,
                "max_attempts": 3,
            },
        ]
        assert not state.all_issues_resolved()

        state.issues = [
            {
                "id": "ISSUE-001",
                "status": "verified",
                "attempt_count": 1,
                "max_attempts": 3,
            },
        ]
        assert state.all_issues_resolved()

    def test_all_issues_resolved_with_failed_exhausted(self):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "A",
                "status": "verified",
                "attempt_count": 1,
                "max_attempts": 3,
            },
            {
                "id": "ISSUE-002",
                "title": "B",
                "status": "failed",
                "attempt_count": 3,
                "max_attempts": 3,
            },
        ]
        assert state.all_issues_resolved()  # Failed but exhausted

    def test_all_issues_not_resolved_with_retryable_failed(self):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "A",
                "status": "failed",
                "attempt_count": 1,
                "max_attempts": 3,
            },
        ]
        assert not state.all_issues_resolved()  # Can still retry

    def test_to_dict_and_from_dict_roundtrip(self):
        state = RunState(run_id="test_rt", config_name="default")
        state.status = RunStatus.RUNNING
        state.phase = RunPhase.IMPLEMENTING
        state.elapsed_seconds = 123.4
        state.planning_cycles = 5
        state.issues_created = 3
        state.claude_calls_total = 4
        state.claude_cost_usd_exact = 1.25
        state.claude_model_usage = {"sonnet": {"calls": 4}}
        state.issues = [
            {"id": "ISSUE-001", "title": "T", "status": "pending"},
        ]
        state.project_wide_tests_unstable = True
        d = state.to_dict()
        restored = RunState.from_dict(d)
        assert restored.run_id == "test_rt"
        assert restored.status == RunStatus.RUNNING
        assert restored.phase == RunPhase.IMPLEMENTING
        assert restored.elapsed_seconds == 123.4
        assert restored.claude_calls_total == 4
        assert restored.claude_cost_usd_exact == 1.25
        assert restored.claude_model_usage["sonnet"]["calls"] == 4
        assert len(restored.issues) == 1
        assert restored.project_wide_tests_unstable is True

    def test_record_provider_result_uses_exact_cost_when_available(self):
        state = RunState(run_id="t", config_name="c")
        result = {
            "success": True,
            "retries": 1,
            "provider_id": "openai",
            "account_id": "acct-1",
            "model_used": "sonnet",
            "total_cost_usd": 0.42,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 30,
                "web_search_requests": 2,
                "web_fetch_requests": 1,
            },
        }
        state.record_provider_result(
            result,
            {"telemetry_cost_mode": "auto"},
            phase="planning",
        )
        assert state.claude_calls_total == 1
        assert state.claude_calls_succeeded == 1
        assert state.claude_retries_total == 1
        assert state.claude_total_input_tokens == 150
        assert state.claude_total_tokens == 200
        assert state.claude_web_search_requests == 2
        assert state.claude_web_fetch_requests == 1
        assert state.claude_cost_usd_exact == pytest.approx(0.42)
        assert state.claude_cost_usd_estimated == pytest.approx(0.0)
        assert state.provider_account_usage["openai"]["acct-1"]["calls"] == 1
        assert (
            state.provider_account_usage["openai"]["acct-1"]["total_tokens"] == 200
        )  # 100+50+20+30
        assert state.phase_usage["planning"]["calls"] == 1
        assert state.phase_usage["planning"]["provider_id"] == "openai"

    def test_record_provider_result_estimates_cost_without_exact(self):
        state = RunState(run_id="t", config_name="c")
        result = {
            "success": False,
            "retries": 0,
            "provider_id": "copilot",
            "account_id": "primary",
            "model_used": "sonnet",
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
        state.record_provider_result(
            result,
            {
                "telemetry_cost_mode": "auto",
                "telemetry_estimate_usd": True,
                "telemetry_model_pricing_usd_per_million_tokens": {
                    "sonnet": {"input": 2.0, "output": 8.0}
                },
            },
            phase="research",
        )
        assert state.claude_calls_total == 1
        assert state.claude_calls_failed == 1
        assert state.claude_cost_usd_exact == pytest.approx(0.0)
        assert state.claude_cost_usd_estimated == pytest.approx(10.0)
        assert state.provider_account_usage["copilot"]["primary"]["calls_failed"] == 1
        assert state.phase_usage["research"]["calls"] == 1

    def test_record_provider_result_auto_still_estimates_when_exact_is_zero(self):
        """total_cost_usd=0 often means unknown billing, not a free run — still estimate from tokens."""
        state = RunState(run_id="t", config_name="c")
        result = {
            "success": True,
            "provider_id": "openai",
            "account_id": "default",
            "model_used": "sonnet",
            "total_cost_usd": 0.0,
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
        state.record_provider_result(
            result,
            {
                "telemetry_cost_mode": "auto",
                "telemetry_estimate_usd": True,
                "telemetry_model_pricing_usd_per_million_tokens": {
                    "sonnet": {"input": 3.0, "output": 15.0}
                },
            },
        )
        assert state.claude_cost_usd_estimated > 0

    def test_record_provider_result_auto_skips_estimate_when_telemetry_estimate_usd_false(
        self,
    ):
        state = RunState(run_id="t", config_name="c")
        result = {
            "success": True,
            "provider_id": "openai",
            "account_id": "default",
            "model_used": "sonnet",
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
        state.record_provider_result(
            result,
            {
                "telemetry_cost_mode": "auto",
                "telemetry_estimate_usd": False,
                "telemetry_model_pricing_usd_per_million_tokens": {
                    "sonnet": {"input": 3.0, "output": 15.0}
                },
            },
        )
        assert state.claude_cost_usd_estimated == pytest.approx(0.0)
        assert state.claude_total_input_tokens == 1_000_000

    def test_record_provider_result_estimate_only_still_estimates_without_flag(self):
        state = RunState(run_id="t", config_name="c")
        result = {
            "success": True,
            "provider_id": "openai",
            "account_id": "default",
            "model_used": "sonnet",
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
        state.record_provider_result(
            result,
            {
                "telemetry_cost_mode": "estimate_only",
                "telemetry_estimate_usd": False,
                "telemetry_model_pricing_usd_per_million_tokens": {
                    "sonnet": {"input": 3.0, "output": 15.0}
                },
            },
        )
        assert state.claude_cost_usd_estimated == pytest.approx(3.0)
