"""Tests for aidlc.runner module."""

import json
import logging
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from aidlc.runner import init_run, scan_project, run_full
from aidlc.models import RunState, RunStatus, RunPhase


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
        "plan_budget_hours": 0.01,
        "checkpoint_interval_minutes": 999,
        "dry_run": True,
        "claude_cli_command": "claude",
        "claude_model": "opus",
        "claude_timeout_seconds": 10,
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


class TestInitRun:
    def test_new_run(self, config):
        state, run_dir = init_run(config, resume=False, dry_run=True)
        assert state.run_id.startswith("aidlc_")
        assert run_dir.exists()
        assert (run_dir / "config_snapshot.json").exists()
        assert (run_dir / "claude_outputs").is_dir()

    def test_resume_no_previous(self, config, capsys):
        state, run_dir = init_run(config, resume=True, dry_run=True)
        captured = capsys.readouterr()
        assert "No previous run" in captured.out or "Starting new run" in captured.out

    def test_dry_run_flag_set(self, config):
        state, run_dir = init_run(config, resume=False, dry_run=True)
        assert config["dry_run"] is True


class TestScanProject:
    def test_scans_docs(self, config, tmp_path):
        (tmp_path / "README.md").write_text("# Test Project")
        logger = logging.getLogger("test_scan")
        state = RunState(run_id="t", config_name="c")
        context = scan_project(state, config, logger)
        assert "Test Project" in context
        assert state.docs_scanned >= 1


class TestRunFull:
    @patch("aidlc.runner.RunLock")
    def test_dry_run_completes(self, MockLock, config, tmp_path):
        (tmp_path / "README.md").write_text("# Test")
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock

        run_full(config=config, dry_run=True, verbose=False)
        mock_lock.acquire.assert_called_once()
        mock_lock.release.assert_called()

    @patch("aidlc.runner.RunLock")
    def test_plan_only(self, MockLock, config, tmp_path):
        (tmp_path / "README.md").write_text("# Test")
        mock_lock = MagicMock()
        MockLock.return_value = mock_lock

        run_full(config=config, dry_run=True, plan_only=True, verbose=False)
        mock_lock.release.assert_called()
