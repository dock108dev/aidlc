"""Tests for resume-time issue reconciliation."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aidlc.models import Issue, IssueStatus, RunState
from aidlc.resume_reconcile import (
    _git_repo_root,
    _issue_id_in_non_test_source,
    _looks_like_test_path,
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
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def git_project_test_only(tmp_path):
    """Repo where the issue id only appears in test files — should NOT trigger
    a reconcile, because Claude scaffolds test filenames with issue ids before
    the implementation finishes."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "tests" / "gut").mkdir(parents=True)
    (tmp_path / "tests" / "gut" / "test_retro_scene_issue_006.gd").write_text(
        "# Generated test for ISSUE-006\n"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


# Heuristic is now opt-in. Most tests pass this explicitly.
ENABLED = {"resume_reconcile_enabled": True}


def test_reconcile_marks_pending_when_id_in_source(git_project):
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
    n = reconcile_issues_on_resume(state, git_project, logger, ENABLED)
    assert n == 1
    assert state.get_issue("ISSUE-001").status == IssueStatus.IMPLEMENTED


def test_reconcile_disabled_by_default(git_project):
    """Resume reconcile is off by default. The heuristic produces too many
    false positives (foundation docs / Claude comments mentioning planned
    issue IDs look identical to evidence-of-completion). Users must opt in."""
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
    n = reconcile_issues_on_resume(state, git_project, MagicMock(), {})
    assert n == 0
    assert state.get_issue("ISSUE-001").status == IssueStatus.PENDING


def test_reconcile_skips_active_in_flight_issue(git_project):
    """Defense-in-depth: even when enabled and even if attempt_count looks
    like 0 (e.g. hydration overwrote it before the run reached the
    implementer), the issue marked as ``current_issue_id`` in state.json was
    the one being worked on when the prior run stopped. Never flip it."""
    state = RunState(run_id="r", config_name="c")
    state.issues = [
        {
            "id": "ISSUE-001",
            "title": "t",
            "description": "",
            "status": "in_progress",
            "dependencies": [],
            "attempt_count": 0,
            "max_attempts": 3,
        }
    ]
    state.current_issue_id = "ISSUE-001"
    n = reconcile_issues_on_resume(state, git_project, MagicMock(), ENABLED)
    assert n == 0
    assert state.get_issue("ISSUE-001").status == IssueStatus.IN_PROGRESS


def test_reconcile_skips_issue_with_prior_attempts(git_project):
    """Regression: an issue with attempt_count > 0 was actively being worked on
    in this run. The implementer recorded its status (e.g. pending after a
    failed JSON parse, in_progress mid-attempt). The reconcile heuristic must
    not silently flip it to implemented just because the issue id appears in
    a test file scaffolded during the failed attempt."""
    state = RunState(run_id="r", config_name="c")
    state.issues = [
        {
            "id": "ISSUE-001",
            "title": "t",
            "description": "",
            "status": "pending",
            "dependencies": [],
            "implementation_notes": "Attempt 1 failed: JSON parse error",
            "attempt_count": 1,
            "max_attempts": 3,
        }
    ]
    logger = MagicMock()
    n = reconcile_issues_on_resume(state, git_project, logger, ENABLED)
    assert n == 0
    assert state.get_issue("ISSUE-001").status == IssueStatus.PENDING
    # Attempt count must stay so the implementer respects max_attempts.
    assert state.get_issue("ISSUE-001").attempt_count == 1


def test_reconcile_skips_when_id_only_in_test_paths(git_project_test_only):
    """Regression: Claude scaffolds test files like
    ``tests/gut/test_retro_scene_issue_006.gd`` during ISSUE-006 work. The
    file lands in git before the implementation finishes. On resume, finding
    the id only in tests/ is NOT evidence the issue was implemented."""
    state = RunState(run_id="r", config_name="c")
    state.issues = [
        {
            "id": "ISSUE-006",
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
    n = reconcile_issues_on_resume(state, git_project_test_only, logger, ENABLED)
    assert n == 0
    assert state.get_issue("ISSUE-006").status == IssueStatus.PENDING


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
    n = reconcile_issues_on_resume(state, tmp_path, MagicMock(), ENABLED)
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
    assert _issue_id_in_non_test_source(Path("/p"), "ISSUE-9") is False


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
    assert reconcile_issues_on_resume(state, git_project, MagicMock(), ENABLED) == 0


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
    n = reconcile_issues_on_resume(state, git_project, MagicMock(), ENABLED)
    assert n == 1
    notes = state.get_issue("ISSUE-001").implementation_notes or ""
    assert "prior note" in notes
    assert "aidlc resume" in notes


@pytest.mark.parametrize(
    "path,is_test",
    [
        ("tests/gut/test_x.gd", True),
        ("tests/gut/feature_a/test_y.gd", True),
        ("src/foo/test_helper.py", True),  # filename starts with test_
        ("specs/spec_widget.rb", True),
        ("__tests__/foo.test.ts", True),
        ("addons/gut/lib.gd", True),  # /gut/ fragment
        ("src/widget.py", False),
        ("app/api/router.go", False),
        ("game/scenes/store.tscn", False),
        ("", True),  # defensive: empty path is "treat as test"
    ],
)
def test_looks_like_test_path(path, is_test):
    assert _looks_like_test_path(path) is is_test
