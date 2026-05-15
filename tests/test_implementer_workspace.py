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
    # Disable commit signing in case the host's global git config requires
    # a signing key — tests don't sign and can't reach a signer.
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
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
    prune_aidlc_data(git_repo, run_dir, state, logger, runs_to_keep=1, keep_provider_outputs=5)
    assert (aidlc / "keep-me").exists()
    assert not (aidlc / "old-run").exists()


def test_prune_provider_outputs_trims_old_files(git_repo: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    out = git_repo / "run" / "provider_outputs"
    out.mkdir(parents=True)
    for i in range(4):
        p = out / f"o{i}.txt"
        p.write_text(str(i))
    run_dir = git_repo / "run"
    prune_aidlc_data(git_repo, run_dir, state, logger, runs_to_keep=5, keep_provider_outputs=2)
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
        git_repo,
        aidlc / "runs" / "r-current",
        state,
        logger,
        runs_to_keep=5,
        keep_provider_outputs=5,
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


# --- Nested sub-repo layouts (e.g. sports/sda/.git + sports/scroll-down-web/.git) ---


@pytest.fixture
def nested_repos(tmp_path: Path) -> Path:
    """Parent dir with two real git sub-repos one level down. The parent itself
    has no ``.git`` — exactly the ``sports/`` layout this support was added for.
    """
    for name in ("sda", "scroll-down-web"):
        sub = tmp_path / name
        sub.mkdir()
        _git_init(sub)
        (sub / "README.md").write_text(f"init {name}\n")
        subprocess.run(["git", "add", "-A"], cwd=sub, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=sub,
            check=True,
            capture_output=True,
        )
    return tmp_path


def test_get_changed_files_nested_repos_prefixes_paths(nested_repos: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    # Touch a file in one repo, add an untracked file in the other.
    (nested_repos / "sda" / "README.md").write_text("changed\n")
    (nested_repos / "scroll-down-web" / "new.py").write_text("u\n")

    files = get_changed_files(nested_repos, state, logger)
    # Sub-repo dir prefix lets the implementer warning point at the right repo.
    assert "sda/README.md" in files
    assert "scroll-down-web/new.py" in files


def test_get_changed_files_nested_repos_detection_status(nested_repos: Path):
    state = RunState(run_id="r1", config_name="default")
    (nested_repos / "sda" / "x.py").write_text("u")
    files, ok = get_changed_files(nested_repos, state, MagicMock(), with_status=True)
    assert ok is True
    assert any(f.startswith("sda/") for f in files)


def test_git_has_changes_nested_repos_any_dirty(nested_repos: Path):
    state = RunState(run_id="r1", config_name="default")
    assert git_has_changes(nested_repos, state, MagicMock()) is False
    (nested_repos / "scroll-down-web" / "n.txt").write_text("u")
    assert git_has_changes(nested_repos, state, MagicMock()) is True


def test_git_current_branch_nested_repos_unanimous(nested_repos: Path):
    state = RunState(run_id="r1", config_name="default")
    # Both sub-repos were created on `main`.
    assert git_current_branch(nested_repos, state, MagicMock()) == "main"


def test_git_current_branch_nested_repos_diverged(nested_repos: Path):
    state = RunState(run_id="r1", config_name="default")
    # Rename one sub-repo's branch so the two no longer agree.
    subprocess.run(
        ["git", "branch", "-m", "main", "feat"],
        cwd=nested_repos / "sda",
        check=True,
        capture_output=True,
    )
    assert git_current_branch(nested_repos, state, MagicMock()) is None


def test_git_commit_cycle_snapshot_nested_repos_commits_each(nested_repos: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    (nested_repos / "sda" / "a.txt").write_text("1")
    (nested_repos / "scroll-down-web" / "b.txt").write_text("2")

    assert git_commit_cycle_snapshot(nested_repos, 7, logger, state, "cycle {cycle}") is True

    for name in ("sda", "scroll-down-web"):
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=nested_repos / name,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "cycle 7" in log.stdout, f"missing cycle commit in {name}"


def test_git_commit_cycle_snapshot_nested_skips_clean_repos(nested_repos: Path):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    # Only one sub-repo is dirty; the other should not get an empty commit.
    (nested_repos / "sda" / "a.txt").write_text("1")

    assert git_commit_cycle_snapshot(nested_repos, 9, logger, state, "cycle {cycle}") is True

    sda_log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=nested_repos / "sda",
        capture_output=True,
        text=True,
        check=True,
    )
    web_log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=nested_repos / "scroll-down-web",
        capture_output=True,
        text=True,
        check=True,
    )
    assert "cycle 9" in sda_log.stdout
    assert "cycle 9" not in web_log.stdout


def test_git_push_current_branch_nested_repos_all_succeed(nested_repos: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    real_run = subprocess.run

    def fake(cmd, **kwargs):
        if cmd and cmd[:2] == ["git", "push"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", fake)
    assert git_push_current_branch(nested_repos, logger, state) is True


def test_git_push_current_branch_nested_repos_one_fails(nested_repos: Path, monkeypatch):
    state = RunState(run_id="r1", config_name="default")
    logger = MagicMock()
    real_run = subprocess.run
    seen = {"push_calls": 0}

    def fake(cmd, **kwargs):
        if cmd and cmd[:2] == ["git", "push"]:
            seen["push_calls"] += 1
            # First repo's push succeeds; second repo's push fails outright.
            if seen["push_calls"] == 1:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr("aidlc.implementer_workspace.subprocess.run", fake)
    assert git_push_current_branch(nested_repos, logger, state) is False
