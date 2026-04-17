"""Tests for aidlc.config._merge_user_config and write_default_config edges."""

import json
from unittest.mock import patch

from aidlc.config import _merge_user_config, load_config, write_default_config


def test_merge_providers_deep_merges_phase_models():
    config = {
        "providers": {
            "claude": {
                "enabled": True,
                "default_model": "sonnet",
                "phase_models": {"planning": "opus", "default": "sonnet"},
            }
        }
    }
    user = {
        "providers": {
            "claude": {
                "phase_models": {"implementation": "opus-2"},
            }
        }
    }
    _merge_user_config(config, user)
    phases = config["providers"]["claude"]["phase_models"]
    assert phases["planning"] == "opus"
    assert phases["default"] == "sonnet"
    assert phases["implementation"] == "opus-2"


def test_merge_providers_replaces_non_dict_provider_block():
    config = {"providers": {"claude": {"enabled": True}}}
    user = {"providers": {"claude": "replace-entirely"}}
    _merge_user_config(config, user)
    assert config["providers"]["claude"] == "replace-entirely"


def test_merge_plain_top_level_key():
    config = {"plan_budget_hours": 4}
    _merge_user_config(config, {"plan_budget_hours": 2, "x": 1})
    assert config["plan_budget_hours"] == 2
    assert config["x"] == 1


def test_write_default_config_chmod_oserror_ignored(tmp_path):
    aidlc = tmp_path / ".aidlc"
    with patch("aidlc.config.os.chmod", side_effect=OSError("no chmod")):
        p = write_default_config(aidlc, None)
    assert p.exists()


def test_load_config_relative_name_from_project_dot_aidlc(tmp_path):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    (aidlc / "config.json").write_text(json.dumps({"plan_budget_hours": 9}))
    cfg = load_config(config_path="config.json", project_root=str(tmp_path))
    assert cfg["plan_budget_hours"] == 9
    assert cfg["_project_root"]


def test_load_config_production_profile_applies_defaults(tmp_path):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    (aidlc / "config.json").write_text(json.dumps({"runtime_profile": "production"}))
    cfg = load_config(project_root=str(tmp_path))
    assert cfg["runtime_profile"] == "production"
    assert cfg.get("strict_validation") is True
    assert cfg.get("fail_on_final_test_failure") is True


def test_write_default_config_appends_gitignore_when_exists(tmp_path):
    aidlc = tmp_path / ".aidlc"
    (tmp_path / ".gitignore").write_text("# existing\n")
    write_default_config(aidlc, None)
    gi = (tmp_path / ".gitignore").read_text()
    assert ".aidlc/" in gi or "AIDLC" in gi
