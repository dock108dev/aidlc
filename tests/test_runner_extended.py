"""Extended tests for aidlc.runner — targeting uncovered lines."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aidlc.models import RunPhase, RunState, RunStatus
from aidlc.runner import init_run, run_full, scan_project
from aidlc.state_manager import save_state


def _issue_stub(iid: str, status: str = "pending") -> dict:
    return {
        "id": iid,
        "title": "t",
        "description": "d",
        "status": status,
        "dependencies": [],
        "attempt_count": 0,
        "max_attempts": 3,
    }


@pytest.fixture
def config(tmp_path):
    aidlc_dir = tmp_path / ".aidlc"
    aidlc_dir.mkdir()
    (aidlc_dir / "issues").mkdir()
    (aidlc_dir / "runs").mkdir()
    (aidlc_dir / "reports").mkdir()

    return {
        "_project_root": str(tmp_path),
        "_aidlc_dir": str(aidlc_dir),
        "_runs_dir": str(aidlc_dir / "runs"),
        "_reports_dir": str(aidlc_dir / "reports"),
        "_issues_dir": str(aidlc_dir / "issues"),
        "providers": {
            "claude": {"enabled": True, "cli_command": "claude", "default_model": "sonnet"}
        },
        "plan_budget_hours": 0.01,
        "checkpoint_interval_minutes": 999,
        "dry_run": True,
        "claude_hard_timeout_seconds": 10,
        "retry_max_attempts": 0,
        "retry_base_delay_seconds": 0.01,
        "retry_max_delay_seconds": 0.05,
        "retry_backoff_factor": 2.0,
        "max_consecutive_failures": 3,
        "finalization_budget_percent": 10,
        "max_implementation_attempts": 3,
        "max_planning_cycles": 1,
        "max_implementation_cycles": 1,
        "test_timeout_seconds": 30,
        "max_doc_chars": 10000,
        "max_context_chars": 80000,
        "max_implementation_context_chars": 30000,
        "doc_scan_patterns": ["**/*.md"],
        "doc_scan_exclude": [".aidlc/**", ".git/**"],
        "run_tests_command": None,
    }


class TestInitRunResume:
    def test_resume_completed_run_starts_new(self, config, capsys):
        # Create a completed run
        runs_dir = Path(config["_runs_dir"])
        run_dir = runs_dir / "old_run"
        run_dir.mkdir()
        state = RunState(run_id="old_run", config_name="default")
        state.status = RunStatus.COMPLETE
        save_state(state, run_dir)

        new_state, new_dir = init_run(config, resume=True, dry_run=True)
        captured = capsys.readouterr()
        assert "Starting new run" in captured.out
        assert new_state.run_id != "old_run"

    def test_resume_paused_run(self, config, capsys):
        runs_dir = Path(config["_runs_dir"])
        run_dir = runs_dir / "paused_run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        state = RunState(run_id="paused_run", config_name="default")
        state.status = RunStatus.PAUSED
        state.phase = RunPhase.IMPLEMENTING
        save_state(state, run_dir)

        resumed_state, resumed_dir = init_run(config, resume=True, dry_run=True)
        captured = capsys.readouterr()
        assert "Resuming" in captured.out
        assert resumed_state.run_id == "paused_run"

    def test_config_snapshot_saved(self, config):
        state, run_dir = init_run(config, resume=False, dry_run=True)
        snapshot = run_dir / "config_snapshot.json"
        assert snapshot.exists()
        data = json.loads(snapshot.read_text())
        # Internal keys should be excluded
        assert "_project_root" not in data
        assert "plan_budget_hours" in data


class TestRunFullEdgeCases:
    @patch("aidlc.runner.RunLock")
    def test_implement_only(self, MockLock, config, tmp_path):
        (tmp_path / "README.md").write_text("# Test")
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock

        # Pre-create issues
        issues_dir = Path(config["_issues_dir"])
        (issues_dir / "ISSUE-001.md").write_text("# ISSUE-001\nTest")

        run_full(config=config, dry_run=True, implement_only=True, verbose=False)
        mock_lock.release.assert_called()

    @patch("aidlc.runner.RunLock")
    def test_no_issues_warning(self, MockLock, config, tmp_path):
        """Run with implement_only but no issues should warn."""
        (tmp_path / "README.md").write_text("# Test")
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock

        run_full(config=config, dry_run=True, implement_only=True, verbose=False)

    @patch("aidlc.runner.RunLock")
    def test_lock_failure_exits(self, MockLock, config, tmp_path):
        mock_lock = MagicMock()
        mock_lock.acquire.side_effect = RuntimeError("locked")
        MockLock.return_value = mock_lock

        with pytest.raises(SystemExit):
            run_full(config=config, dry_run=True, verbose=False)

    @patch("aidlc.runner.RunLock")
    def test_verbose_mode(self, MockLock, config, tmp_path):
        (tmp_path / "README.md").write_text("# Test")
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock

        run_full(config=config, dry_run=True, verbose=True, plan_only=True)


class TestResumeSkipsPlanning:
    @patch("aidlc.doc_gap_detector.detect_doc_gaps")
    @patch("aidlc.runner.Implementer")
    @patch("aidlc.runner.scan_project")
    @patch("aidlc.runner.ProviderRouter")
    @patch("aidlc.runner.RunLock")
    def test_resume_implementing_skips_planner_and_doc_gaps(
        self, MockLock, MockRouter, mock_scan, MockImplementer, mock_doc_gaps, config, tmp_path
    ):
        (tmp_path / "README.md").write_text("# Test")
        runs_dir = Path(config["_runs_dir"])
        run_dir = runs_dir / "paused_run"
        run_dir.mkdir(parents=True)
        (run_dir / "claude_outputs").mkdir()
        state = RunState(run_id="paused_run", config_name="default")
        state.status = RunStatus.PAUSED
        state.phase = RunPhase.IMPLEMENTING
        state.issues = [_issue_stub("ISSUE-001")]
        state.total_issues = 1
        save_state(state, run_dir)

        MockLock.return_value = MagicMock()
        mock_cli = MagicMock()
        mock_cli.check_available.return_value = True
        MockRouter.return_value = mock_cli

        def _scan(state, cfg, logger, cli=None):
            return (
                "ctx",
                {
                    "doc_files": [],
                    "existing_issues": [],
                    "total_docs": 0,
                    "project_type": "py",
                },
            )

        mock_scan.side_effect = _scan
        MockImplementer.return_value.run.return_value = True

        with patch("aidlc.runner.Planner") as MockPlanner:
            run_full(config=config, resume=True, dry_run=True, verbose=False)
            MockPlanner.assert_not_called()
        mock_doc_gaps.assert_not_called()
        MockImplementer.return_value.run.assert_called_once()


class TestScanProject:
    def test_existing_issues_found(self, config, tmp_path):
        (tmp_path / "README.md").write_text("# Test")
        issues_dir = Path(config["_issues_dir"])
        (issues_dir / "ISSUE-001.md").write_text("# Issue 1")

        logger = logging.getLogger("test_scan")
        state = RunState(run_id="t", config_name="c")
        scan_project(state, config, logger)
        assert state.docs_scanned >= 1
