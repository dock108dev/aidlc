"""Tests for aidlc.implementer_workspace git helpers and pruning."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aidlc.implementer_workspace import (
    get_changed_files,
    git_commit_cycle_snapshot,
    git_current_branch,
    git_has_changes,
    git_push_current_branch,
    prune_aidlc_data,
)
from aidlc.models import RunState


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _git_init(tmp_path)
    (tmp_path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_get_changed_files_after_edit(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    (git_repo / "README.md").write_text("changed\n")
    files = get_changed_files(git_repo, state, logger)
    assert "README.md" in files


def test_get_changed_files_untracked_only(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    (git_repo / "solo.py").write_text("u")
    files = get_changed_files(git_repo, state, logger)
    assert "solo.py" in files


def test_get_changed_files_no_git_binary(tmp_path: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")

    def boom(*_a, **_k):
        raise FileNotFoundError()

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", boom)
    names, ok = get_changed_files(tmp_path, state, MagicMock(), with_status=True)
    assert names == [] and ok is False


def test_get_changed_files_with_status(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    (git_repo / "README.md").write_text("x\n")
    out = get_changed_files(git_repo, state, logger, with_status=True)
    assert isinstance(out, tuple)
    names, ok = out
    assert ok is True
    assert "README.md" in names


def test_git_has_changes_true_when_dirty(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    (git_repo / "new.txt").write_text("u")
    assert git_has_changes(git_repo, state, MagicMock()) is True


def test_git_has_changes_false_when_clean(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    assert git_has_changes(git_repo, state, MagicMock()) is False


def test_git_current_branch(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    assert git_current_branch(git_repo, state, MagicMock()) == "main"


def test_git_current_branch_nonzero_exit(git_repo: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")

    def fake(*_a, **_k):
        class R:
            returncode = 1
            stdout = "main\n"

        return R()

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", fake)
    assert git_current_branch(git_repo, state, MagicMock()) is None


def test_git_commit_cycle_snapshot_no_changes(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    assert git_commit_cycle_snapshot(git_repo, 1, logger, state, "c{cycle}") is False


def test_git_commit_cycle_snapshot_with_changes(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    (git_repo / "x.txt").write_text("1")
    assert git_commit_cycle_snapshot(git_repo, 2, logger, state, "cycle {cycle}") is True


def test_git_commit_cycle_snapshot_nothing_to_commit_after_stage(git_repo: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    (git_repo / "z.txt").write_text("1")

    def fake(cmd, **_kw):
        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        class R2:
            returncode = 1
            stderr = "nothing to commit"
            stdout = ""

        if "add" in cmd:
            return R()
        return R2()

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", fake)
    assert git_commit_cycle_snapshot(git_repo, 3, logger, state, "m {cycle}") is False


def test_git_commit_cycle_snapshot_add_raises(git_repo: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    (git_repo / "z2.txt").write_text("1")

    def fake(cmd, **_kw):
        if "add" in cmd:
            raise subprocess.CalledProcessError(1, cmd)

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", fake)
    assert git_commit_cycle_snapshot(git_repo, 4, logger, state, "m {cycle}") is False


def test_prune_aidlc_data_removes_old_runs(git_repo: Path):
    state = RunState(run_id="keep-me", config_name="default")
    logger = MagicMock()
    aidlc = git_repo / ".aidlc" / "runs"
    aidlc.mkdir(parents=True)
    (aidlc / "keep-me").mkdir()
    (aidlc / "old-run").mkdir()
    (aidlc / "keep-me" / "f").write_text("a")
    (aidlc / "old-run" / "f").write_text("b")
    run_dir = aidlc / "keep-me"
    prune_aidlc_data(git_repo, run_dir, state, logger, runs_to_keep=1, keep_claude_outputs=5)
    assert (aidlc / "keep-me").exists()
    assert not (aidlc / "old-run").exists()


def test_prune_claude_outputs_trims_old_files(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    out = git_repo / "run" / "claude_outputs"
    out.mkdir(parents=True)
    for i in range(4):
        p = out / f"o{i}.txt"
        p.write_text(str(i))
    run_dir = git_repo / "run"
    prune_aidlc_data(git_repo, run_dir, state, logger, runs_to_keep=5, keep_claude_outputs=2)
    remaining = list(out.iterdir())
    assert len(remaining) <= 2


def test_prune_aidlc_data_orphan_report(git_repo: Path):
    state = RunState(run_id="r-current", config_name="default")
    logger = MagicMock()
    aidlc = git_repo / ".aidlc"
    (aidlc / "runs" / "r-current").mkdir(parents=True)
    (aidlc / "reports").mkdir(parents=True)
    orphan = aidlc / "reports" / "orphan-report"
    orphan.mkdir()
    (orphan / "x").write_text("1")
    prune_aidlc_data(
        git_repo, aidlc / "runs" / "r-current", state, logger, runs_to_keep=5, keep_claude_outputs=5
    )
    assert not orphan.exists()


def test_git_push_current_branch_success(git_repo: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()

    def fake(cmd, **_kw):
        if cmd and "--show-current" in cmd:
            return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", fake)
    assert git_push_current_branch(git_repo, logger, state) is True


def test_git_push_current_branch_sets_upstream(git_repo: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    stage = {"n": 0}

    def fake(cmd, **_kw):
        if cmd and "--show-current" in cmd:
            return SimpleNamespace(returncode=0, stdout="feat\n", stderr="")
        stage["n"] += 1
        if stage["n"] == 1:
            return SimpleNamespace(
                returncode=1,
                stderr="fatal: The current branch feat has no upstream branch",
                stdout="",
            )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", fake)
    assert git_push_current_branch(git_repo, logger, state) is True


def test_git_push_current_branch_upstream_fails(git_repo: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    stage = {"n": 0}

    def fake(cmd, **_kw):
        if cmd and "--show-current" in cmd:
            return SimpleNamespace(returncode=0, stdout="feat\n", stderr="")
        stage["n"] += 1
        if stage["n"] == 1:
            return SimpleNamespace(returncode=1, stderr="no upstream", stdout="")
        return SimpleNamespace(returncode=1, stderr="fail", stdout="")

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", fake)
    assert git_push_current_branch(git_repo, logger, state) is False


def test_git_push_current_branch_warns_without_upstream(git_repo: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stderr = "fatal: The current branch main has no upstream branch"
            stdout = ""

        return R()

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", fake_run)
    assert git_push_current_branch(git_repo, logger, state) is False
