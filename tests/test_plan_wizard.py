"""Tests for aidlc.plan_wizard."""

from pathlib import Path
from unittest.mock import patch

import pytest

from aidlc.plan_wizard import (
    _auto_detect,
    _build_starter,
    _strip_starter_comments,
    run_wizard,
)


def test_strip_starter_comments_removes_html_comments():
    raw = "<!-- a -->\nReal line\n<!-- b -->"
    out = _strip_starter_comments(raw)
    assert "<!--" not in out
    assert "Real line" in out


def test_build_starter_minimal(tmp_path):
    s = _build_starter(tmp_path, {})
    assert tmp_path.name in s or "#" in s
    assert "What am I building" in s


def test_build_starter_with_tech_and_one_liner(tmp_path):
    s = _build_starter(
        tmp_path,
        {"project_name": "X", "tech_stack": "Python", "one_liner": "A" * 25},
    )
    assert "Python" in s
    assert ">" in s


def test_auto_detect_pyproject_and_readme(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname=x\n")
    (tmp_path / "README.md").write_text(
        "# Title\n\nThis is a long enough first content line for one_liner.\n"
    )
    d = _auto_detect(tmp_path)
    assert "Python" in d.get("tech_stack", "")
    assert "long enough" in d.get("one_liner", "")


def test_auto_detect_has_code_via_subdir(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("x = 1\n")
    d = _auto_detect(tmp_path)
    assert d.get("has_code") is True


def test_auto_detect_readme_oserror(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    readme.write_text("ok")
    orig_read = Path.read_text

    def guarded(self, *a, **k):
        if self.resolve() == readme.resolve():
            raise OSError("read fail")
        return orig_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", guarded)
    d = _auto_detect(tmp_path)
    assert "one_liner" not in d


def test_run_wizard_eof_on_input(tmp_path, monkeypatch, capsys):
    (tmp_path / "BRAINDUMP.md").write_text("# real\n\nSome real brain dump content here.\n")
    monkeypatch.setattr("builtins.input", lambda _p="": (_ for _ in ()).throw(EOFError()))
    out = run_wizard(tmp_path, auto_detect=False)
    assert isinstance(out, dict)
    captured = capsys.readouterr()
    assert "Brain Dump" in captured.out or "BRAINDUMP" in captured.out


def test_run_wizard_creates_starter_and_reads(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    answers = run_wizard(tmp_path, auto_detect=False)
    assert (tmp_path / "BRAINDUMP.md").exists()
    assert "brain_dump" in answers or answers.get("project_name")


def test_run_wizard_existing_nonempty(tmp_path, monkeypatch):
    (tmp_path / "BRAINDUMP.md").write_text(
        "# My app\n\nReal content here that is not only comments.\n"
    )
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    answers = run_wizard(tmp_path, auto_detect=False)
    assert "brain_dump" in answers


def test_run_wizard_empty_after_strip(tmp_path, monkeypatch, capsys):
    (tmp_path / "BRAINDUMP.md").write_text("<!-- only -->\n")
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    run_wizard(tmp_path, auto_detect=False)
    out = capsys.readouterr().out
    assert "empty" in out.lower() or "Claude" in out


@patch("aidlc.plan_wizard.sys.stdout.isatty", return_value=True)
def test_ansi_helpers_when_tty(_m):
    from aidlc import plan_wizard as pw

    assert "\033" in pw._bold("x")
    assert "\033" in pw._dim("x")
    assert "\033" in pw._cyan("x")
    assert "\033" in pw._green("x")
    assert "\033" in pw._yellow("x")


@patch("aidlc.plan_wizard.sys.stdout.isatty", return_value=False)
def test_ansi_helpers_when_not_tty(_m):
    from aidlc import plan_wizard as pw

    assert pw._bold("hi") == "hi"
