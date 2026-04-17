"""Tests for resume-time issue reconciliation."""

import subprocess
from unittest.mock import MagicMock

import pytest
from aidlc.models import Issue, IssueStatus, RunState
from aidlc.resume_reconcile import reconcile_issues_on_resume


@pytest.fixture
def git_project(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("# ISSUE-001 helper\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
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
        Issue(id="ISSUE-001", title="t", description="", status=IssueStatus.PENDING).to_dict()
    ]
    n = reconcile_issues_on_resume(state, tmp_path, MagicMock(), {})
    assert n == 0
