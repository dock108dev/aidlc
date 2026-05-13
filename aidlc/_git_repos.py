"""Discover the git repo(s) that live inside an aidlc project root.

Most projects are a single git repo at the project root. Some users
work out of a parent directory that itself isn't a git repo but
contains multiple sub-repos one level down (e.g. ``sports/sda/.git``
and ``sports/scroll-down-web/.git``). All git-aware code in aidlc
should ask this module which directories to operate on instead of
assuming ``project_root`` is itself a repo.
"""

from __future__ import annotations

from pathlib import Path


def discover_repos(project_root: Path) -> list[Path]:
    """Return the git repos to operate on for ``project_root``.

    Order of preference:

    1. If ``project_root`` itself contains a ``.git`` entry, return
       just ``[project_root]`` — the classic single-repo layout.
    2. Otherwise, scan immediate child directories and return any
       that contain a ``.git`` entry, sorted by name for stability.
    3. If no nested repos are found, return ``[project_root]`` anyway
       so callers fail with the natural "not a git repo" error rather
       than silently no-oping.
    """
    root = Path(project_root)
    if (root / ".git").exists():
        return [root]

    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return [root]

    nested = [c for c in children if (c / ".git").exists()]
    return nested if nested else [root]


def is_multi_repo(project_root: Path) -> bool:
    """True when ``project_root`` itself is not a repo but has nested ones."""
    repos = discover_repos(project_root)
    return len(repos) > 1 or (repos and repos[0] != Path(project_root))
