"""Tests for aidlc.reporting module."""

from aidlc.reporting import generate_run_report, generate_checkpoint_summary
from aidlc.models import RunState, RunStatus, RunPhase


class TestGenerateRunReport:
    def test_creates_report_file(self, tmp_path):
        state = RunState(run_id="test_report", config_name="default")
        state.status = RunStatus.COMPLETE
        state.phase = RunPhase.DONE
        state.plan_budget_seconds = 3600
        state.plan_elapsed_seconds = 1800
        state.elapsed_seconds = 2000
        state.console_seconds = 2500
        state.planning_cycles = 5
        state.issues_created = 3
        state.total_issues = 3
        state.issues_implemented = 2
        state.issues_verified = 2
        state.issues_failed = 1
        state.claude_calls_total = 6
        state.claude_cost_usd_exact = 0.55
        state.claude_model_usage = {"sonnet": {"calls": 6, "input_tokens": 100, "output_tokens": 50}}

        path = generate_run_report(state, tmp_path)
        assert path.exists()
        content = path.read_text()
        assert "test_report" in content
        assert "complete" in content
        assert "AI Provider Telemetry" in content
        assert "Model Breakdown" in content

    def test_includes_issue_table(self, tmp_path):
        state = RunState(run_id="test_report", config_name="default")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "Test Issue",
                "description": "D",
                "status": "verified",
                "attempt_count": 1,
                "max_attempts": 3,
                "priority": "high",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": [],
            }
        ]
        path = generate_run_report(state, tmp_path)
        content = path.read_text()
        assert "ISSUE-001" in content
        assert "Test Issue" in content
        assert "verified" in content

    def test_includes_artifacts_dict_format(self, tmp_path):
        state = RunState(run_id="test_report", config_name="default")
        state.created_artifacts = [
            {"path": "docs/design.md", "type": "doc", "action": "create"},
        ]
        path = generate_run_report(state, tmp_path)
        content = path.read_text()
        assert "docs/design.md" in content
        assert "create" in content

    def test_includes_artifacts_string_format(self, tmp_path):
        """Backwards compat with old string format."""
        state = RunState(run_id="test_report", config_name="default")
        state.created_artifacts = ["docs/old.md"]
        path = generate_run_report(state, tmp_path)
        content = path.read_text()
        assert "docs/old.md" in content


class TestGenerateCheckpointSummary:
    def test_creates_checkpoint_file(self, tmp_path):
        state = RunState(run_id="test_cp", config_name="default")
        state.checkpoint_count = 3
        state.phase = RunPhase.IMPLEMENTING
        state.elapsed_seconds = 3600
        state.planning_cycles = 5
        state.issues_created = 10
        state.implementation_cycles = 3
        state.issues_implemented = 2
        state.current_issue_id = "ISSUE-005"
        state.claude_calls_total = 9
        state.claude_calls_succeeded = 8
        state.claude_calls_failed = 1
        state.claude_retries_total = 2
        state.claude_total_tokens = 1234

        path = generate_checkpoint_summary(state, tmp_path)
        assert path.exists()
        content = path.read_text()
        assert "Checkpoint 3" in content
        assert "implementing" in content
        assert "ISSUE-005" in content
        assert "Provider calls" in content
        assert "All providers (totals) tokens" in content
        assert "no breakdown recorded" in content

    def test_checkpoint_includes_provider_table(self, tmp_path):
        state = RunState(run_id="test_cp2", config_name="default")
        state.checkpoint_count = 1
        state.provider_account_usage = {
            "copilot": {
                "primary": {
                    "calls": 2,
                    "calls_succeeded": 2,
                    "calls_failed": 0,
                    "input_tokens": 50,
                    "output_tokens": 25,
                    "total_tokens": 75,
                    "cost_usd_exact": 0.0,
                    "cost_usd_estimated": 0.01,
                }
            }
        }
        path = generate_checkpoint_summary(state, tmp_path)
        content = path.read_text()
        assert "| copilot | primary |" in content
        assert "| 2 | 2 | 0 |" in content
