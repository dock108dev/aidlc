"""Drive coverage for aidlc.improve (ANSI helpers, ImprovementCycle paths)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aidlc.improve import (
    AUDIT_PROMPT,
    RESEARCH_PROMPT,
    ImprovementCycle,
    _bold,
    _dim,
)


@pytest.fixture
def tty_on(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)


@pytest.fixture
def tty_off(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)


def test_improve_ansi_tty(tty_on):
    assert "\033[1m" in _bold("a")


def test_improve_ansi_plain(tty_off):
    assert _dim("z") == "z"


def test_prompt_templates_format():
    s = AUDIT_PROMPT.format(user_concern="slow UI", project_context="ctx")
    assert "slow UI" in s and "ctx" in s
    r = RESEARCH_PROMPT.format(
        topic="t", question="q?", user_concern="c", project_type="python"
    )
    assert "t" in r and "python" in r


def _cycle(tmp_path: Path) -> ImprovementCycle:
    cfg = {"run_tests_command": None, "test_timeout_seconds": 5}
    return ImprovementCycle(
        tmp_path,
        cfg,
        MagicMock(),
        MagicMock(),
        "project type: python\nREADME exists",
    )


def test_audit_area_cli_fails(tmp_path):
    c = _cycle(tmp_path)
    c.cli.execute_prompt.return_value = {"success": False}
    assert c._audit_area("x") is None


def test_audit_area_bad_json_logs(tmp_path):
    c = _cycle(tmp_path)
    c.cli.execute_prompt.return_value = {"success": True, "output": "not json"}
    with patch("aidlc.improve.parse_json_output", side_effect=ValueError("bad")):
        assert c._audit_area("x") is None
    c.logger.warning.assert_called()


def test_run_returns_early_on_audit_failure(tmp_path, capsys):
    c = _cycle(tmp_path)
    c.cli.execute_prompt.return_value = {"success": False}
    out = c.run("concern", auto_implement=True)
    assert out.get("error") == "audit failed"


def test_run_no_improvements_branch(tmp_path, capsys):
    c = _cycle(tmp_path)
    audit = {
        "improvements": [],
        "files_involved": [],
        "weaknesses": [],
        "research_needed": [],
    }
    with patch.object(ImprovementCycle, "_audit_area", return_value=audit):
        r = c.run("x", auto_implement=True)
    assert r["status"] == "no_improvements"


def test_run_planned_without_auto_implement(tmp_path):
    c = _cycle(tmp_path)
    audit = {
        "improvements": [{"title": "T", "description": "D", "priority": "high", "files_to_change": ["a.py"]}],
        "files_involved": ["a.py"],
        "weaknesses": ["w1"],
        "research_needed": [],
    }
    with patch.object(ImprovementCycle, "_audit_area", return_value=audit):
        r = c.run("scope", auto_implement=False)
    assert r["status"] == "planned"
    assert "IMP-001" in r["issues"]


def test_run_research_skip_existing(tmp_path, capsys):
    c = _cycle(tmp_path)
    rd = tmp_path / "docs" / "research"
    rd.mkdir(parents=True)
    (rd / "improve-skip-me.md").write_text("old")
    c._run_research([{"topic": "skip me", "question": "q"}], "c")
    assert "skip" in capsys.readouterr().out.lower()


def test_run_research_writes(tmp_path):
    c = _cycle(tmp_path)
    c.cli.execute_prompt.return_value = {"success": True, "output": "# R\nbody"}
    c._run_research([{"topic": "alpha", "question": "why"}], "concern")
    assert (tmp_path / "docs" / "research" / "improve-alpha.md").exists()


def test_run_research_permission_retry(tmp_path):
    c = _cycle(tmp_path)
    chatter = "approve the write permission dialog to continue"
    c.cli.execute_prompt.side_effect = [
        {"success": True, "output": chatter},
        {"success": True, "output": "# ok\nyes"},
    ]
    c._run_research([{"topic": "perm2", "question": "q"}], "c")
    assert "yes" in (tmp_path / "docs" / "research" / "improve-perm2.md").read_text()


def test_run_research_retry_fails(tmp_path, capsys):
    c = _cycle(tmp_path)
    chatter = "needs your write permission to save"
    c.cli.execute_prompt.side_effect = [
        {"success": True, "output": chatter},
        {"success": False},
    ]
    c._run_research([{"topic": "badretry", "question": "q"}], "c")
    assert "failed" in capsys.readouterr().out.lower()


def test_verify_no_command_returns_true(tmp_path):
    c = _cycle(tmp_path)
    assert c._verify() is True


def test_verify_subprocess_success(tmp_path, monkeypatch):
    c = ImprovementCycle(
        tmp_path,
        {"run_tests_command": "true", "test_timeout_seconds": 5},
        MagicMock(),
        MagicMock(),
        "ctx",
    )

    class R:
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert c._verify() is True


def test_verify_subprocess_fail(tmp_path, monkeypatch):
    c = ImprovementCycle(
        tmp_path,
        {"run_tests_command": "false", "test_timeout_seconds": 5},
        MagicMock(),
        MagicMock(),
        "ctx",
    )

    class R:
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert c._verify() is False


def test_verify_subprocess_timeout(tmp_path, monkeypatch):
    c = ImprovementCycle(
        tmp_path,
        {"run_tests_command": "sleep 9", "test_timeout_seconds": 1},
        MagicMock(),
        MagicMock(),
        "ctx",
    )

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    assert c._verify() is False


def test_run_finalization_skips_when_zero(tmp_path, capsys):
    c = _cycle(tmp_path)
    c._run_finalization([], 0)
    assert "skip" in capsys.readouterr().out.lower()


def test_run_finalization_small_scope(tmp_path, monkeypatch):
    c = _cycle(tmp_path)
    fin = MagicMock()
    monkeypatch.setattr("aidlc.finalizer.Finalizer", lambda *a, **k: fin)
    imps = [{"files_to_change": ["a.py"]}, {"files_to_change": ["b.py"]}]
    c._run_finalization(imps, 2)
    fin.run.assert_called_once()
    assert fin.run.call_args.kwargs["passes"] == ["cleanup"]


def test_run_finalization_large_scope(tmp_path, monkeypatch):
    c = _cycle(tmp_path)
    fin = MagicMock()
    monkeypatch.setattr("aidlc.finalizer.Finalizer", lambda *a, **k: fin)
    imps = [{"files_to_change": [f"f{i}.py" for i in range(15)]} for _ in range(6)]
    c._run_finalization(imps, 6)
    assert "ssot" in fin.run.call_args.kwargs["passes"]


def test_run_finalization_warns_on_exception(tmp_path, monkeypatch, capsys):
    c = _cycle(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr("aidlc.finalizer.Finalizer", boom)
    c._run_finalization([{"files_to_change": ["a.py"]}], 1)
    assert "error" in capsys.readouterr().out.lower() or "!" in capsys.readouterr().out


@patch.object(ImprovementCycle, "_run_finalization")
@patch.object(ImprovementCycle, "_verify", return_value=True)
@patch.object(ImprovementCycle, "_implement_issues", return_value=1)
def test_run_full_complete(mock_impl, mock_verify, mock_fin, tmp_path, capsys):
    c = _cycle(tmp_path)
    audit = {
        "improvements": [{"title": "Fix", "description": "d", "priority": "low", "files_to_change": ["x.py"]}],
        "files_involved": ["x.py"],
        "weaknesses": ["w"],
        "research_needed": [{"topic": "rt", "question": "rq"}],
    }
    c.cli.execute_prompt.return_value = {"success": True, "output": "# r"}
    with patch.object(ImprovementCycle, "_audit_area", return_value=audit):
        r = c.run("lag", auto_implement=True)
    assert r["status"] == "complete"
    mock_fin.assert_called_once()
