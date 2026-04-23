"""ISSUE-009: implementation no longer auto-runs finalization on early stop.

Default-off prevents burning more budget at exactly the moment we want to stop
cleanly (e.g., on token exhaustion). Set
``implementation_finalize_on_early_stop: true`` to opt back in.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from aidlc.implementer import Implementer
from aidlc.models import RunState


@pytest.fixture
def logger():
    return logging.getLogger("test_impl_early_stop")


def _config(tmp_path, **overrides):
    base = {
        "_project_root": str(tmp_path),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "checkpoint_interval_minutes": 999,
        "max_consecutive_failures": 3,
        "max_implementation_attempts": 3,
        "max_implementation_cycles": 0,
        "test_timeout_seconds": 5,
        "max_implementation_context_chars": 30000,
        "dry_run": False,
        "run_tests_command": None,
    }
    base.update(overrides)
    return base


def _state_with_unresolved_issue():
    s = RunState(run_id="t", config_name="default")
    s.issues = [
        {
            "id": "ISSUE-001",
            "title": "T",
            "description": "D",
            "priority": "high",
            "labels": [],
            "dependencies": [],
            "acceptance_criteria": ["AC1"],
            "status": "pending",
            "implementation_notes": "",
            "verification_result": "",
            "files_changed": [],
            "attempt_count": 0,
            "max_attempts": 3,
        }
    ]
    s.total_issues = 1
    return s


def _exhausted_cli():
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": False,
        "output": None,
        "error": "All available providers/models appear out of tokens or quota",
        "failure_type": "token_exhausted_all_models",
        "duration_seconds": 1.0,
        "retries": 0,
    }
    cli.set_complexity = MagicMock()
    return cli


@patch("aidlc.finalizer.Finalizer")
def test_default_does_not_auto_run_finalize_on_early_stop(mock_finalizer_cls, logger, tmp_path):
    """ISSUE-009: by default, hitting token exhaustion does NOT trigger
    ssot/abend/cleanup passes."""
    config = _config(tmp_path)  # default: implementation_finalize_on_early_stop is False
    state = _state_with_unresolved_issue()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "claude_outputs").mkdir()

    impl = Implementer(state, run_dir, config, _exhausted_cli(), "ctx", logger)
    ok = impl.run()

    assert ok is False
    mock_finalizer_cls.assert_not_called()
    # Stop reason still recorded so the user knows what happened.
    assert state.stop_reason is not None


@patch("aidlc.finalizer.Finalizer")
def test_opt_in_runs_finalize_on_early_stop(mock_finalizer_cls, logger, tmp_path):
    """When the user opts back in, ssot/abend/cleanup passes still run."""
    config = _config(tmp_path, implementation_finalize_on_early_stop=True)
    state = _state_with_unresolved_issue()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "claude_outputs").mkdir()
    mock_finalizer_cls.return_value.run = MagicMock()

    impl = Implementer(state, run_dir, config, _exhausted_cli(), "ctx", logger)
    impl.run()

    mock_finalizer_cls.assert_called_once()
    mock_finalizer_cls.return_value.run.assert_called_once()
    # Confirms the canonical pass list.
    call_kwargs = mock_finalizer_cls.return_value.run.call_args
    assert call_kwargs.kwargs.get("passes") == ["ssot", "abend", "cleanup"]


def test_stop_reason_logged_with_resume_hint(logger, tmp_path, caplog):
    """The user gets a single-line summary plus resume instructions."""
    config = _config(tmp_path)
    state = _state_with_unresolved_issue()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "claude_outputs").mkdir()

    impl = Implementer(state, run_dir, config, _exhausted_cli(), "ctx", logger)
    with caplog.at_level("ERROR"):
        impl.run()

    log_text = "\n".join(rec.message for rec in caplog.records)
    assert "STOP REASON" in log_text
    assert "RESUME WITH: aidlc run --resume" in log_text
    assert "Issues remaining" in log_text
