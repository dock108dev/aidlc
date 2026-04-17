"""Coverage for aidlc.config_detect."""

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from aidlc.config_detect import (
    describe_detected,
    detect_config,
    update_config_file,
)


def test_detect_rust_adjusts_test_timeout(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
    out = detect_config(tmp_path)
    assert "rust" in out["_detected_project_type"]
    assert out["test_timeout_seconds"] == 600
    assert out.get("lint_command") == "cargo clippy"


def test_detect_godot_stack_timeout(tmp_path):
    (tmp_path / "project.godot").write_text("[application]\n")
    out = detect_config(tmp_path)
    assert "godot" in out["_detected_project_type"]
    assert out["claude_hard_timeout_seconds"] == 900


def test_detect_unity(tmp_path):
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    out = detect_config(tmp_path)
    assert "unity" in out["_detected_project_type"]
    assert out.get("claude_hard_timeout_seconds") == 900


def test_detect_pnpm_rewrites_npm_commands(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    (tmp_path / "pnpm-lock.yaml").write_text("lockfile")
    out = detect_config(tmp_path)
    assert out.get("run_tests_command", "").startswith("pnpm")


def test_detect_yarn_rewrites_npm_commands(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    (tmp_path / "yarn.lock").write_text("lockfile")
    out = detect_config(tmp_path)
    assert out.get("run_tests_command", "").startswith("yarn")


def test_update_config_file_merges_missing_keys_only(tmp_path, caplog):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    cfg = aidlc / "config.json"
    cfg.write_text(json.dumps({"run_tests_command": "keep-me"}))
    detected = detect_config(tmp_path)
    detected["extra_only"] = "new"
    log = logging.getLogger("test_cfg_merge")
    with caplog.at_level("INFO", logger=log.name):
        merged = update_config_file(tmp_path, detected, log)
    assert merged["run_tests_command"] == "keep-me"
    assert merged.get("extra_only") == "new"


def test_update_config_file_corrupt_json_raises_and_backs_up(tmp_path):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    cfg = aidlc / "config.json"
    cfg.write_text("{ not json")
    with patch("aidlc.config_detect.time.time", return_value=12345):
        with pytest.raises(ValueError, match="not valid JSON"):
            update_config_file(tmp_path, {"x": 1}, None)
    backups = list(aidlc.glob("*.corrupt-*.json.bak"))
    assert backups


def test_update_config_file_read_oserror_propagates(tmp_path):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    cfg = aidlc / "config.json"
    cfg.write_text("{}")

    real_read_text = Path.read_text

    def boom(self, *a, **kw):
        if self.resolve() == cfg.resolve():
            raise OSError("no read")
        return real_read_text(self, *a, **kw)

    with patch.object(Path, "read_text", boom):
        with pytest.raises(OSError):
            update_config_file(tmp_path, {"new_key": "v"}, None)


def test_describe_detected_all_lines():
    lines = describe_detected(
        {
            "_detected_project_type": "python",
            "run_tests_command": "pytest",
            "e2e_test_command": "playwright",
            "build_validation_command": "make",
            "lint_command": "ruff check .",
        }
    )
    joined = "\n".join(lines)
    assert "Test command" in joined
    assert "E2E" in joined
    assert "Build command" in joined
    assert "Lint command" in joined


def test_detect_lint_from_package_json_lint_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}))
    out = detect_config(tmp_path)
    assert out.get("lint_command") == "npm run lint"


def test_detect_lint_lint_fix_when_no_lint(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"lint:fix": "eslint --fix ."}}))
    out = detect_config(tmp_path)
    assert out.get("lint_command") == "npm run lint:fix"


def test_detect_lint_package_json_unparseable(tmp_path, caplog):
    (tmp_path / "package.json").write_text("{")
    caplog.set_level("WARNING")
    out = detect_config(tmp_path)
    assert "lint_command" not in out
    assert "Unable to parse package.json" in caplog.text


def test_detect_lint_python_ruff(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")
    out = detect_config(tmp_path)
    assert out.get("lint_command") == "ruff check ."


def test_detect_lint_python_flake8(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.flake8]\n")
    out = detect_config(tmp_path)
    assert out.get("lint_command") == "flake8 ."


def test_detect_lint_python_flake8_rc_only(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / ".flake8").write_text("[flake8]\n")
    out = detect_config(tmp_path)
    assert out.get("lint_command") == "flake8 ."


def test_detect_lint_pyproject_read_fails(tmp_path, caplog):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    p = tmp_path / "pyproject.toml"
    real = Path.read_text

    def boom(self, *a, **kw):
        if self.resolve() == p.resolve():
            raise OSError("e")
        return real(self, *a, **kw)

    caplog.set_level("WARNING")
    with patch.object(Path, "read_text", boom):
        out = detect_config(tmp_path)
    assert "Unable to read pyproject.toml" in caplog.text
    assert "lint_command" not in out or out.get("lint_command") is None


def test_detect_lint_go_golangci(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    out = detect_config(tmp_path)
    assert out.get("lint_command") == "golangci-lint run"


def test_detect_lint_godot_gdlint(tmp_path):
    (tmp_path / "project.godot").write_text("x")
    (tmp_path / ".gdlintrc").write_text("")
    out = detect_config(tmp_path)
    assert out.get("lint_command") == "gdlint ."
