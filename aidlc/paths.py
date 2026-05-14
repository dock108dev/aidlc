"""Path helpers for CLI entrypoints."""

from __future__ import annotations

import errno
import os
from pathlib import Path

_PERMISSION_ERRNOS = {errno.EACCES, errno.EPERM}


def resolve_project_root(project: str | Path | None = None) -> Path:
    """Return a usable absolute project root path.

    ``Path.resolve()`` canonicalizes symlinks via ``realpath``. On macOS that
    can raise ``PermissionError`` for privacy-restricted current directories
    even when normal file operations in that directory are allowed. In that
    specific case, fall back to a lexical absolute path so the CLI can proceed.
    Other resolution failures still surface to the caller.
    """
    path = Path(project or ".").expanduser()
    try:
        return path.resolve()
    except OSError as exc:
        if exc.errno not in _PERMISSION_ERRNOS:
            raise
        return _lexical_absolute_path(path)


def _lexical_absolute_path(path: Path) -> Path:
    if path.is_absolute():
        return Path(os.path.normpath(os.fspath(path)))

    pwd = os.environ.get("PWD")
    if pwd and os.path.isabs(pwd):
        base = Path(pwd)
    else:
        base = Path.cwd()

    return Path(os.path.normpath(os.fspath(base / path)))
