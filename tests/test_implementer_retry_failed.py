"""ISSUE-012: failed-issue retry policy.

Failed issues now record a ``failure_cause``. Transient causes
(``failed_token_exhausted``, ``failed_unknown``) auto-reopen on the next
implementation cycle. Real-blocker causes (``failed_dependency``,
``failed_test_regression``) stay failed for manual review unless the user
forces with ``aidlc run --retry-failed``.
"""

from __future__ import annotations

import logging

import pytest
from aidlc.implementer import Implementer
from aidlc.issue_model import (
    FAILURE_CAUSE_DEPENDENCY,
    FAILURE_CAUSE_TEST_REGRESSION,
    FAILURE_CAUSE_TOKEN_EXHAUSTED,
    FAILURE_CAUSE_UNKNOWN,
    TRANSIENT_FAILURE_CAUSES,
)
from aidlc.models import IssueStatus, RunState


@pytest.fixture
def logger():
    return logging.getLogger("test_impl_retry")


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
        "dry_run": True,
        "run_tests_command": None,
    }
    base.update(overrides)
    return base


def _failed_issue(issue_id: str, cause: str | None):
    return {
        "id": issue_id,
        "title": f"{issue_id} failed",
        "description": "x",
        "priority": "medium",
        "labels": [],
        "dependencies": [],
        "acceptance_criteria": ["AC"],
        "status": "failed",
        "implementation_notes": f"failed due to {cause}",
        "verification_result": "",
        "files_changed": [],
        "attempt_count": 1,
        "max_attempts": 3,
        "failure_cause": cause,
    }


def _state_with_failed(*issues):
    s = RunState(run_id="t", config_name="default")
    s.issues = list(issues)
    s.total_issues = len(issues)
    return s


def test_transient_failure_causes_set_correctly():
    assert FAILURE_CAUSE_TOKEN_EXHAUSTED in TRANSIENT_FAILURE_CAUSES
    assert FAILURE_CAUSE_UNKNOWN in TRANSIENT_FAILURE_CAUSES
    assert FAILURE_CAUSE_DEPENDENCY not in TRANSIENT_FAILURE_CAUSES
    assert FAILURE_CAUSE_TEST_REGRESSION not in TRANSIENT_FAILURE_CAUSES


def test_reopen_only_transient_by_default(logger, tmp_path):
    config = _config(tmp_path)
    state = _state_with_failed(
        _failed_issue("ISSUE-001", FAILURE_CAUSE_TOKEN_EXHAUSTED),
        _failed_issue("ISSUE-002", FAILURE_CAUSE_DEPENDENCY),
        _failed_issue("ISSUE-003", FAILURE_CAUSE_UNKNOWN),
        _failed_issue("ISSUE-004", FAILURE_CAUSE_TEST_REGRESSION),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "claude_outputs").mkdir()

    impl = Implementer(
        state, run_dir, config, cli=None, project_context="ctx", logger=logger
    )
    reopened = impl._maybe_reopen_transient_failures(force_all=False)
    assert reopened == 2

    by_id = {d["id"]: d for d in state.issues}
    assert by_id["ISSUE-001"]["status"] == IssueStatus.PENDING.value
    assert by_id["ISSUE-001"]["failure_cause"] is None
    assert by_id["ISSUE-002"]["status"] == IssueStatus.FAILED.value
    assert by_id["ISSUE-003"]["status"] == IssueStatus.PENDING.value
    assert by_id["ISSUE-004"]["status"] == IssueStatus.FAILED.value


def test_reopen_all_with_force(logger, tmp_path):
    config = _config(tmp_path)
    state = _state_with_failed(
        _failed_issue("ISSUE-001", FAILURE_CAUSE_TOKEN_EXHAUSTED),
        _failed_issue("ISSUE-002", FAILURE_CAUSE_DEPENDENCY),
        _failed_issue("ISSUE-003", FAILURE_CAUSE_TEST_REGRESSION),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "claude_outputs").mkdir()

    impl = Implementer(
        state, run_dir, config, cli=None, project_context="ctx", logger=logger
    )
    reopened = impl._maybe_reopen_transient_failures(force_all=True)
    assert reopened == 3
    for d in state.issues:
        assert d["status"] == IssueStatus.PENDING.value
        assert d["failure_cause"] is None


def test_reopen_preserves_attempt_count(logger, tmp_path):
    """attempt_count is preserved so max_attempts continues to bound retries."""
    config = _config(tmp_path)
    issue = _failed_issue("ISSUE-001", FAILURE_CAUSE_TOKEN_EXHAUSTED)
    issue["attempt_count"] = 2
    state = _state_with_failed(issue)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "claude_outputs").mkdir()

    impl = Implementer(
        state, run_dir, config, cli=None, project_context="ctx", logger=logger
    )
    impl._maybe_reopen_transient_failures(force_all=False)
    assert state.issues[0]["attempt_count"] == 2


def test_reopen_no_failed_issues_returns_zero(logger, tmp_path):
    config = _config(tmp_path)
    state = RunState(run_id="t", config_name="default")
    state.issues = []
    state.total_issues = 0
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "claude_outputs").mkdir()

    impl = Implementer(
        state, run_dir, config, cli=None, project_context="ctx", logger=logger
    )
    assert impl._maybe_reopen_transient_failures(force_all=False) == 0
    assert impl._maybe_reopen_transient_failures(force_all=True) == 0


def test_issue_serialization_round_trips_failure_cause():
    """failure_cause survives to_dict / from_dict for state persistence."""
    from aidlc.issue_model import Issue

    issue = Issue(id="ISSUE-001", title="t", description="d")
    issue.failure_cause = FAILURE_CAUSE_TOKEN_EXHAUSTED
    data = issue.to_dict()
    assert data["failure_cause"] == FAILURE_CAUSE_TOKEN_EXHAUSTED
    restored = Issue.from_dict(data)
    assert restored.failure_cause == FAILURE_CAUSE_TOKEN_EXHAUSTED


def test_issue_back_compat_without_failure_cause():
    """Old state files without failure_cause load with None."""
    from aidlc.issue_model import Issue

    data = {
        "id": "ISSUE-001",
        "title": "t",
        "description": "d",
        "priority": "medium",
        "labels": [],
        "dependencies": [],
        "acceptance_criteria": [],
        "status": "failed",
        "implementation_notes": "",
        "verification_result": "",
        "files_changed": [],
        "attempt_count": 1,
        "max_attempts": 3,
        # no failure_cause field
    }
    restored = Issue.from_dict(data)
    assert restored.failure_cause is None
