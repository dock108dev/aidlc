"""Unit tests for FullAuditEngine and RuntimeAuditEngine (mocked CLI / subprocess)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aidlc.audit.full_engine import FullAuditEngine
from aidlc.audit.runtime_engine import RuntimeAuditEngine
from aidlc.audit_models import AuditResult, ModuleInfo


def _auditor(tmp_path: Path, **kwargs) -> MagicMock:
    aud = MagicMock()
    aud.project_root = tmp_path
    aud.source_extensions = kwargs.get("source_extensions", {".py"})
    aud.max_claude_calls = kwargs.get("max_claude_calls", 10)
    aud.max_source_chars = kwargs.get("max_source_chars", 50000)
    aud.logger = MagicMock()
    aud.cli = kwargs.get("cli")
    aud._mark_degraded = MagicMock()
    return aud


def _module_json(description: str = "mod", caps: list | None = None) -> str:
    caps = caps or ["x"]
    body = json.dumps(
        {
            "module_name": "m",
            "description": description,
            "capabilities": caps,
            "dependencies": [],
            "external_dependencies": [],
            "quality_signals": {
                "has_tests": True,
                "has_docstrings": True,
                "complexity": "low",
                "notes": "",
            },
        }
    )
    return f"```json\n{body}\n```"


def _features_json() -> str:
    body = json.dumps(
        {
            "features": [
                {"name": "Auth", "status": "complete", "description": "login"},
            ],
            "summary": "ok",
        }
    )
    return f"```json\n{body}\n```"


def test_full_audit_warns_and_returns_when_no_cli(tmp_path: Path):
    eng = FullAuditEngine(_auditor(tmp_path, cli=None))
    res = AuditResult(modules=[], project_type="python")
    out = eng.full_audit(res)
    assert out is res
    eng.logger.warning.assert_called()


def test_full_audit_skips_tests_role(tmp_path: Path):
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "x.py").write_text("x=1\n")

    cli = MagicMock()
    eng = FullAuditEngine(_auditor(tmp_path, cli=cli))
    res = AuditResult(
        project_type="python",
        modules=[ModuleInfo(name="t", path="t", role="tests")],
    )
    eng.full_audit(res)
    cli.execute_prompt.assert_not_called()


def test_full_audit_respects_max_claude_calls(tmp_path: Path):
    app = tmp_path / "a"
    app.mkdir()
    (app / "f.py").write_text("print(1)\n")

    calls: list[str] = []

    def execute_prompt(prompt: str, working_dir=None, allow_edits=False):
        calls.append(prompt)
        return {"success": True, "output": _module_json()}

    cli = MagicMock()
    cli.execute_prompt = execute_prompt

    eng = FullAuditEngine(_auditor(tmp_path, cli=cli, max_claude_calls=1))
    res = AuditResult(
        project_type="python",
        modules=[
            ModuleInfo(name="a1", path="a", role="api"),
            ModuleInfo(name="a2", path="a", role="api"),
        ],
    )
    out = eng.full_audit(res)
    assert len(calls) == 1
    assert out.features is None


def test_full_audit_module_analyze_parse_warning(tmp_path: Path):
    app = tmp_path / "a"
    app.mkdir()
    (app / "f.py").write_text("x=1\n")

    cli = MagicMock()
    cli.execute_prompt = MagicMock(
        return_value={"success": True, "output": "no json here {{{{"}
    )

    eng = FullAuditEngine(_auditor(tmp_path, cli=cli))
    mod = ModuleInfo(name="a", path="a", role="api")
    assert eng.analyze_module_with_claude(mod) is None
    eng.logger.warning.assert_called()


def test_read_module_source_truncates(tmp_path: Path):
    app = tmp_path / "a"
    app.mkdir()
    big = "x" * 4000
    (app / "big.py").write_text(big)

    eng = FullAuditEngine(_auditor(tmp_path, max_source_chars=1200))
    mod = ModuleInfo(name="a", path="a", role="api")
    src = eng.read_module_source(mod)
    assert "truncated" in src
    assert len(src) < len(big) + 200


def test_read_module_source_oserror_marks_degraded(tmp_path: Path):
    app = tmp_path / "a"
    app.mkdir()
    (app / "f.py").write_text("ok\n")

    eng = FullAuditEngine(_auditor(tmp_path))
    mod = ModuleInfo(name="a", path="a", role="api")
    with patch("builtins.open", side_effect=OSError("boom")):
        assert eng.read_module_source(mod) == ""
    eng.auditor._mark_degraded.assert_called_with("source_read_errors")


def test_inventory_features_parse_failure_logged(tmp_path: Path):
    cli = MagicMock()
    cli.execute_prompt = MagicMock(return_value={"success": True, "output": "bad {{{"})

    eng = FullAuditEngine(_auditor(tmp_path, cli=cli))
    res = AuditResult(project_type="python", frameworks=["flask"])
    analyses = {"m": {"description": "d", "capabilities": ["c"]}}
    assert eng.inventory_features_with_claude(res, analyses) is None
    eng.logger.warning.assert_called()


def test_full_audit_happy_path_includes_features(tmp_path: Path):
    app = tmp_path / "a"
    app.mkdir()
    (app / "f.py").write_text("def foo(): return 1\n")

    seq = [
        {"success": True, "output": _module_json()},
        {"success": True, "output": _features_json()},
    ]

    def execute_prompt(prompt, working_dir=None, allow_edits=False):
        return seq.pop(0)

    cli = MagicMock()
    cli.execute_prompt = execute_prompt

    eng = FullAuditEngine(_auditor(tmp_path, cli=cli, max_claude_calls=5))
    res = AuditResult(
        project_type="python",
        frameworks=["flask"],
        modules=[ModuleInfo(name="a", path="a", role="api")],
    )
    out = eng.full_audit(res)
    assert out.features
    assert "Auth" in out.features[0]


@patch("aidlc.audit.runtime_engine.detect_test_profile")
@patch("aidlc.audit.runtime_engine.subprocess.run")
def test_runtime_checks_runs_profile_tiers(mock_run, mock_profile, tmp_path: Path):
    mock_profile.return_value = {
        "unit": "true",
        "build": "true",
    }
    mock_run.return_value = MagicMock(
        returncode=0, stdout="Coverage 91% of statements\n", stderr=""
    )

    aud = MagicMock()
    aud.project_root = tmp_path
    aud.config = {"audit_runtime_timeout_seconds": 30}
    aud.logger = MagicMock()

    eng = RuntimeAuditEngine(aud)
    out = eng.run_runtime_checks("python")

    assert out["overall_passed"] is True
    assert out["coverage_percent"] == 91.0
    assert len(out["tier_results"]) == 2
    assert mock_run.call_count == 2


@patch("aidlc.audit.runtime_engine.detect_test_profile")
@patch("aidlc.audit.runtime_engine.subprocess.run")
def test_runtime_playwright_headless_injected(mock_run, mock_profile, tmp_path: Path):
    mock_profile.return_value = {
        "e2e": "npx playwright test --headed --reporter=list",
    }
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    aud = MagicMock()
    aud.project_root = tmp_path
    aud.config = {
        "audit_runtime_timeout_seconds": 30,
        "audit_playwright_headless": True,
    }
    aud.logger = MagicMock()

    eng = RuntimeAuditEngine(aud)
    out = eng.run_runtime_checks("javascript")

    assert out["playwright_present"] is True
    cmd = out["tier_results"][0]["command"]
    assert "playwright" in cmd.lower()
    assert "--headless" in cmd


@patch("aidlc.audit.runtime_engine.detect_test_profile")
@patch("aidlc.audit.runtime_engine.subprocess.run")
def test_runtime_command_timeout(mock_run, mock_profile, tmp_path: Path):
    mock_profile.return_value = {"unit": "slow-cmd"}
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="slow-cmd", timeout=1)

    aud = MagicMock()
    aud.project_root = tmp_path
    aud.config = {"audit_runtime_timeout_seconds": 1}
    aud.logger = MagicMock()

    eng = RuntimeAuditEngine(aud)
    out = eng.run_runtime_checks("python")
    assert out["tier_results"]
    assert out["tier_results"][0]["passed"] is False
    aud.logger.warning.assert_called()


@patch("aidlc.audit.runtime_engine.detect_test_profile")
@patch("aidlc.audit.runtime_engine.subprocess.run")
def test_runtime_file_not_found(mock_run, mock_profile, tmp_path: Path):
    mock_profile.return_value = {"unit": "missing-binary-xyz-123"}

    mock_run.side_effect = FileNotFoundError()

    aud = MagicMock()
    aud.project_root = tmp_path
    aud.config = {}
    aud.logger = MagicMock()

    eng = RuntimeAuditEngine(aud)
    out = eng.run_runtime_checks("python")
    assert out["tier_results"][0]["passed"] is False


def test_extract_coverage_percent_variants():
    assert RuntimeAuditEngine._extract_coverage_percent("") is None
    assert RuntimeAuditEngine._extract_coverage_percent("no percent in output") is None
    v = RuntimeAuditEngine._extract_coverage_percent("All files 92.5% covered")
    assert v == 92.5
    v2 = RuntimeAuditEngine._extract_coverage_percent("Statements   77.1%  (100/130)")
    assert v2 == 77.1


def test_excerpt_truncates_from_end():
    long = "a" * 1000
    ex = RuntimeAuditEngine._excerpt(long, max_chars=50)
    assert len(ex) == 50
    assert ex.endswith("a")


@pytest.mark.parametrize(
    "profile,cmd",
    [
        (
            {"e2e": "npx playwright test"},
            "custom playwright",
        ),
    ],
)
def test_normalize_playwright_override(profile, cmd, tmp_path: Path):
    aud = MagicMock()
    aud.project_root = tmp_path
    aud.config = {"audit_playwright_command_override": cmd}
    eng = RuntimeAuditEngine(aud)
    assert eng._normalize_command("e2e", profile["e2e"]) == cmd
