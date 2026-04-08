"""Tests for aidlc.reporting module."""

import pytest
from pathlib import Path

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
        state.wall_clock_seconds = 2500
        state.planning_cycles = 5
        state.issues_created = 3
        state.total_issues = 3
        state.issues_implemented = 2
        state.issues_verified = 2
        state.issues_failed = 1

        path = generate_run_report(state, tmp_path)
        assert path.exists()
        content = path.read_text()
        assert "test_report" in content
        assert "complete" in content

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

        path = generate_checkpoint_summary(state, tmp_path)
        assert path.exists()
        content = path.read_text()
        assert "Checkpoint 3" in content
        assert "implementing" in content
        assert "ISSUE-005" in content
