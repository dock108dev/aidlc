"""Tests for aidlc.finalizer module."""

import json
import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aidlc.finalizer import Finalizer
from aidlc.models import RunState, RunPhase


@pytest.fixture
def logger():
    return logging.getLogger("test_finalizer")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_runs_dir": str(tmp_path / ".aidlc" / "runs"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "finalize_timeout_seconds": 60,
        "dry_run": True,
    }


@pytest.fixture
def state():
    s = RunState(run_id="test_finalize", config_name="default")
    s.total_issues = 10
    s.issues_implemented = 8
    s.issues_verified = 7
    s.issues_failed = 1
    return s


@pytest.fixture
def cli():
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "# Audit Report\n\nFindings here.",
        "error": None,
        "failure_type": None,
        "duration_seconds": 5.0,
        "retries": 0,
    }
    cli.timeout = 600
    return cli


class TestFinalizer:
    def test_runs_all_passes_by_default(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run()

        assert len(state.finalize_passes_completed) == 5
        assert "ssot" in state.finalize_passes_completed
        assert "security" in state.finalize_passes_completed
        assert "abend" in state.finalize_passes_completed
        assert "docs" in state.finalize_passes_completed
        assert "cleanup" in state.finalize_passes_completed

    def test_runs_selected_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs", "security"])

        assert state.finalize_passes_completed == ["docs", "security"]
        # Wrong order preserved from input, not PASS_ORDER
        # Actually they run in input order
        assert len(state.finalize_passes_completed) == 2

    def test_skips_invalid_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["nonexistent", "docs"])

        assert state.finalize_passes_completed == ["docs"]

    def test_handles_failed_pass(self, state, config, logger, tmp_path):
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": False,
            "output": "",
            "error": "timeout",
            "failure_type": "transient",
            "duration_seconds": 60.0,
            "retries": 2,
        }
        cli.timeout = 600
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "context", logger)
        finalizer.run(passes=["security"])

        # Failed pass should NOT be in completed list
        assert "security" not in state.finalize_passes_completed

    def test_writes_futures_note(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs"])

        futures_path = tmp_path / "AIDLC_FUTURES.md"
        assert futures_path.exists()
        content = futures_path.read_text()
        assert "AIDLC Futures" in content
        assert "test_finalize" in content

    def test_creates_audit_dir(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs"])

        audit_dir = tmp_path / "docs" / "audits"
        assert audit_dir.exists()

    def test_saves_raw_output(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["security"])

        output_file = run_dir / "claude_outputs" / "finalize_security.md"
        assert output_file.exists()

    def test_tracks_requested_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["ssot", "docs"])

        assert state.finalize_passes_requested == ["ssot", "docs"]

    def test_sets_finalizing_phase(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs"])

        assert state.phase == RunPhase.FINALIZING

    def test_empty_passes_logs_warning(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "context", logger)
        finalizer.run(passes=["invalid_only"])

        assert state.finalize_passes_completed == []
