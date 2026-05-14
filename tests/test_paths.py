import errno
from pathlib import Path

import pytest
from aidlc.paths import resolve_project_root


def test_resolve_project_root_falls_back_to_pwd_on_permission_error(monkeypatch, tmp_path):
    expected = tmp_path / "child"
    monkeypatch.setenv("PWD", str(tmp_path))

    def raise_permission_error(self):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(Path, "resolve", raise_permission_error)

    assert resolve_project_root("child") == expected


def test_resolve_project_root_falls_back_to_absolute_path_on_permission_error(
    monkeypatch, tmp_path
):
    expected = tmp_path / "project"

    def raise_permission_error(self):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(Path, "resolve", raise_permission_error)

    assert resolve_project_root(expected) == expected


def test_resolve_project_root_reraises_non_permission_errors(monkeypatch):
    def raise_missing_error(self):
        raise OSError(errno.ENOENT, "No such file or directory")

    monkeypatch.setattr(Path, "resolve", raise_missing_error)

    with pytest.raises(OSError) as exc:
        resolve_project_root(".")

    assert exc.value.errno == errno.ENOENT
