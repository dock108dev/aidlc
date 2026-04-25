"""Tests for resume-time issue reconciliation."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aidlc.models import Issue, IssueStatus, RunState
from aidlc.resume_reconcile import (
    _git_repo_root,
    _issue_id_referenced_in_tree,
    reconcile_issues_on_resume,
)


@pytest.fixture
def git_project(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("# ISSUE-001 helper\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    return tmp_path


def test_reconcile_marks_pending_when_id_in_tree(git_project):
    state = RunState(run_id="r", config_name="c")
    state.issues = [
        {
            "id": "ISSUE-001",
            "title": "t",
            "description": "",
            "status": "pending",
            "dependencies": [],
            "implementation_notes": "",
            "attempt_count": 0,
            "max_attempts": 3,
        }
    ]
    logger = MagicMock()
    n = reconcile_issues_on_resume(state, git_project, logger, {})
    assert n == 1
    assert state.get_issue("ISSUE-001").status == IssueStatus.IMPLEMENTED


def test_reconcile_respects_disabled(git_project):
    state = RunState(run_id="r", config_name="c")
    state.issues = [
        {
            "id": "ISSUE-001",
            "title": "t",
            "description": "",
            "status": "pending",
            "dependencies": [],
            "attempt_count": 0,
            "max_attempts": 3,
        }
    ]
    n = reconcile_issues_on_resume(
        state, git_project, MagicMock(), {"resume_reconcile_enabled": False}
    )
    assert n == 0
    assert state.get_issue("ISSUE-001").status == IssueStatus.PENDING


def test_reconcile_no_git_is_noop(tmp_path):
    state = RunState(run_id="r", config_name="c")
    state.issues = [
        Issue(
            id="ISSUE-001", title="t", description="", status=IssueStatus.PENDING
        ).to_dict()
    ]
    n = reconcile_issues_on_resume(state, tmp_path, MagicMock(), {})
    assert n == 0


@patch("aidlc.resume_reconcile.subprocess.run", side_effect=OSError("no git"))
def test_git_repo_root_oserror(_mock_run):
    assert _git_repo_root(Path("/tmp/x")) is None


@patch("aidlc.resume_reconcile._git_repo_root", return_value=Path("/repo"))
@patch(
    "aidlc.resume_reconcile.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="g", timeout=1),
)
def test_issue_id_grep_timeout(_mock_run, _mock_root):
    assert _issue_id_referenced_in_tree(Path("/p"), "ISSUE-9") is False


def test_reconcile_skips_wrong_status_and_bad_id(git_project):
    state = RunState(run_id="r", config_name="c")
    state.issues = [
        {
            "id": "ISSUE-404",
            "title": "t",
            "description": "",
            "status": "implemented",
            "dependencies": [],
            "implementation_notes": "",
            "attempt_count": 0,
            "max_attempts": 3,
        },
        {
            "id": "",
            "title": "t",
            "description": "",
            "status": "pending",
            "dependencies": [],
            "implementation_notes": "",
            "attempt_count": 0,
            "max_attempts": 3,
        },
    ]
    assert reconcile_issues_on_resume(state, git_project, MagicMock(), {}) == 0


def test_reconcile_in_progress_with_existing_notes(git_project):
    state = RunState(run_id="r", config_name="c")
    state.issues = [
        {
            "id": "ISSUE-001",
            "title": "t",
            "description": "",
            "status": "in_progress",
            "dependencies": [],
            "implementation_notes": "prior note",
            "attempt_count": 0,
            "max_attempts": 3,
        }
    ]
    n = reconcile_issues_on_resume(state, git_project, MagicMock(), {})
    assert n == 1
    notes = state.get_issue("ISSUE-001").implementation_notes or ""
    assert "prior note" in notes
    assert "aidlc resume" in notes
