"""Tests for aidlc.finalizer module."""

import logging
from unittest.mock import MagicMock

import pytest
from aidlc.finalizer import Finalizer
from aidlc.models import RunPhase, RunState


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
        """Default pass set is intentionally narrow (docs + cleanup) since
        ssot/security/abend were removed in the core-focus audit. New passes
        will be reintroduced once their prompts are nailed down."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run()

        assert state.finalize_passes_completed == ["docs", "cleanup"]

    def test_runs_selected_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["cleanup", "docs"])

        # Passes run in user-supplied order, not PASS_ORDER.
        assert state.finalize_passes_completed == ["cleanup", "docs"]

    def test_skips_invalid_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["nonexistent", "docs"])

        assert state.finalize_passes_completed == ["docs"]

    def test_dropped_legacy_passes_are_invalid(self, state, config, cli, logger, tmp_path):
        """ssot, security, abend used to be valid pass names. Make sure the
        finalizer treats them as unknown so old configs don't silently run
        nothing useful."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["ssot", "security", "abend"])

        assert state.finalize_passes_completed == []

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
        finalizer.run(passes=["docs"])

        # Failed pass should NOT be in completed list
        assert "docs" not in state.finalize_passes_completed

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
        finalizer.run(passes=["cleanup"])

        output_file = run_dir / "claude_outputs" / "finalize_cleanup.md"
        assert output_file.exists()

    def test_tracks_requested_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs", "cleanup"])

        assert state.finalize_passes_requested == ["docs", "cleanup"]

    def test_sets_finalizing_phase(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs"])

        assert state.phase == RunPhase.FINALIZING

    def test_second_run_replaces_completed_passes(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "project context", logger)
        finalizer.run(passes=["docs"])
        assert state.finalize_passes_completed == ["docs"]
        finalizer.run(passes=["cleanup"])
        assert state.finalize_passes_completed == ["cleanup"]

    def test_empty_passes_logs_warning(self, state, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        finalizer = Finalizer(state, run_dir, config, cli, "context", logger)
        finalizer.run(passes=["invalid_only"])

        assert state.finalize_passes_completed == []
