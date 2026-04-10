"""Tests for aidlc.config module."""

import json
import pytest
from pathlib import Path

from aidlc.config import load_config, get_run_dir, get_reports_dir, get_issues_dir, DEFAULTS
from aidlc.config_detect import update_config_file


class TestLoadConfig:
    """Tests for load_config()."""

    def test_defaults_returned_when_no_config(self, tmp_path):
        config = load_config(project_root=str(tmp_path))
        for key, value in DEFAULTS.items():
            assert config[key] == value

    def test_project_root_set(self, tmp_path):
        config = load_config(project_root=str(tmp_path))
        assert config["_project_root"] == str(tmp_path.resolve())

    def test_aidlc_dirs_set(self, tmp_path):
        config = load_config(project_root=str(tmp_path))
        assert config["_aidlc_dir"] == str(tmp_path / ".aidlc")
        assert config["_runs_dir"] == str(tmp_path / ".aidlc" / "runs")
        assert config["_reports_dir"] == str(tmp_path / ".aidlc" / "reports")
        assert config["_issues_dir"] == str(tmp_path / ".aidlc" / "issues")

    def test_user_config_overrides_defaults(self, tmp_path):
        aidlc_dir = tmp_path / ".aidlc"
        aidlc_dir.mkdir()
        config_file = aidlc_dir / "config.json"
        config_file.write_text(json.dumps({
            "plan_budget_hours": 8,
            "claude_model": "sonnet",
        }))
        config = load_config(project_root=str(tmp_path))
        assert config["plan_budget_hours"] == 8
        assert config["claude_model"] == "sonnet"
        # Defaults still present
        assert config["checkpoint_interval_minutes"] == 15

    def test_explicit_config_path(self, tmp_path):
        config_file = tmp_path / "custom.json"
        config_file.write_text(json.dumps({"plan_budget_hours": 2}))
        config = load_config(config_path=str(config_file), project_root=str(tmp_path))
        assert config["plan_budget_hours"] == 2

    def test_config_path_in_aidlc_dir(self, tmp_path):
        aidlc_dir = tmp_path / ".aidlc"
        aidlc_dir.mkdir()
        config_file = aidlc_dir / "fast.json"
        config_file.write_text(json.dumps({"plan_budget_hours": 1}))
        config = load_config(config_path="fast.json", project_root=str(tmp_path))
        assert config["plan_budget_hours"] == 1

    def test_missing_config_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Config not found"):
            load_config(config_path="/nonexistent/path.json", project_root=str(tmp_path))

    def test_defaults_include_new_keys(self):
        """Verify all expected config keys exist in DEFAULTS."""
        expected_keys = [
            "runtime_profile", "plan_budget_hours", "checkpoint_interval_minutes", "dry_run",
            "claude_cli_command", "claude_model", "claude_long_run_warn_seconds",
            "claude_hard_timeout_seconds",
            "retry_max_attempts", "retry_base_delay_seconds", "retry_max_delay_seconds",
            "retry_backoff_factor", "max_consecutive_failures",
            "finalization_budget_percent", "max_implementation_attempts",
            "max_planning_cycles", "max_implementation_cycles",
            "run_tests_command", "test_timeout_seconds",
            "max_doc_chars", "max_context_chars", "max_implementation_context_chars",
            "doc_scan_patterns", "doc_scan_exclude", "implementation_allowed_paths",
            "strict_validation", "validation_allow_no_tests", "fail_on_validation_incomplete",
            "fail_on_final_test_failure", "strict_change_detection",
            "planning_action_failure_ratio_threshold",
        ]
        for key in expected_keys:
            assert key in DEFAULTS, f"Missing key in DEFAULTS: {key}"

    def test_removed_legacy_keys_absent(self):
        removed = {
            "strict_mode",
            "strict_planning_validation",
            "allow_skip_precheck",
            "allow_dependency_bypass",
            "auto_break_dependency_cycles",
            "allow_unstructured_success",
        }
        for key in removed:
            assert key not in DEFAULTS, f"Legacy key should remain removed: {key}"

    def test_cwd_used_when_no_project_root(self):
        config = load_config()
        assert config["_project_root"] == str(Path.cwd().resolve())

    def test_production_profile_applies_strict_defaults(self, tmp_path):
        aidlc_dir = tmp_path / ".aidlc"
        aidlc_dir.mkdir()
        (aidlc_dir / "config.json").write_text(json.dumps({"runtime_profile": "production"}))
        config = load_config(project_root=str(tmp_path))
        assert config["strict_validation"] is True
        assert config["validation_allow_no_tests"] is False
        assert config["fail_on_validation_incomplete"] is True
        assert config["fail_on_final_test_failure"] is True
        assert config["strict_change_detection"] is True
        assert config["claude_hard_timeout_seconds"] == 3600

    def test_production_profile_respects_explicit_override(self, tmp_path):
        aidlc_dir = tmp_path / ".aidlc"
        aidlc_dir.mkdir()
        (aidlc_dir / "config.json").write_text(json.dumps({
            "runtime_profile": "production",
            "strict_validation": False,
            "claude_hard_timeout_seconds": 120,
        }))
        config = load_config(project_root=str(tmp_path))
        assert config["strict_validation"] is False
        assert config["claude_hard_timeout_seconds"] == 120


class TestHelperFunctions:
    """Tests for get_run_dir, get_reports_dir, get_issues_dir."""

    def test_get_run_dir_creates_directory(self, tmp_path):
        config = {"_runs_dir": str(tmp_path / "runs")}
        run_dir = get_run_dir(config, "test_run_001")
        assert run_dir.exists()
        assert run_dir.name == "test_run_001"

    def test_get_reports_dir_creates_directory(self, tmp_path):
        config = {"_reports_dir": str(tmp_path / "reports")}
        report_dir = get_reports_dir(config, "test_run_001")
        assert report_dir.exists()

    def test_get_issues_dir_creates_directory(self, tmp_path):
        config = {"_issues_dir": str(tmp_path / "issues")}
        issues_dir = get_issues_dir(config)
        assert issues_dir.exists()


class TestConfigDetectMerge:
    def test_update_config_file_rejects_corrupt_json(self, tmp_path):
        aidlc_dir = tmp_path / ".aidlc"
        aidlc_dir.mkdir()
        config_path = aidlc_dir / "config.json"
        config_path.write_text('{"plan_budget_hours": 4,,}')

        with pytest.raises(ValueError, match="not valid JSON"):
            update_config_file(tmp_path, {"run_tests_command": "pytest"})

        backups = list(aidlc_dir.glob("config.corrupt-*.json.bak"))
        assert backups, "Expected corrupt backup file to be created"
