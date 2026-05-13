"""Tests for sub-repo discovery used by nested-repo project layouts."""

from __future__ import annotations

from pathlib import Path

from aidlc._git_repos import discover_repos, is_multi_repo


def _fake_git_dir(path: Path) -> None:
    """Create a ``.git`` dir cheaply — actual init not needed for discovery."""
    (path / ".git").mkdir(parents=True)


def test_single_repo_at_project_root(tmp_path: Path) -> None:
    _fake_git_dir(tmp_path)
    repos = discover_repos(tmp_path)
    assert repos == [tmp_path]
    assert is_multi_repo(tmp_path) is False


def test_nested_sub_repos(tmp_path: Path) -> None:
    sda = tmp_path / "sda"
    web = tmp_path / "scroll-down-web"
    sda.mkdir()
    web.mkdir()
    _fake_git_dir(sda)
    _fake_git_dir(web)
    # A non-repo directory should be ignored.
    (tmp_path / "notes").mkdir()

    repos = discover_repos(tmp_path)
    assert repos == [web, sda] or repos == [sda, web]
    # Sorted by name → scroll-down-web sorts after sda.
    assert repos == sorted([sda, web])
    assert is_multi_repo(tmp_path) is True


def test_root_takes_precedence_over_nested(tmp_path: Path) -> None:
    """If the parent itself is a repo, don't descend into children."""
    _fake_git_dir(tmp_path)
    child = tmp_path / "vendor"
    child.mkdir()
    _fake_git_dir(child)

    repos = discover_repos(tmp_path)
    assert repos == [tmp_path]


def test_no_repos_falls_back_to_root(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    repos = discover_repos(tmp_path)
    assert repos == [tmp_path]


def test_git_as_file_worktree(tmp_path: Path) -> None:
    """``.git`` files (git worktrees, submodules) count as a repo marker."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: ../.git/worktrees/foo\n")
    repos = discover_repos(tmp_path)
    assert repos == [sub]
