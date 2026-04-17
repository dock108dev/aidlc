"""Coverage for aidlc.cli.config_cmd (mocked I/O)."""

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest
from aidlc.cli.config_cmd import (
    cmd_config_edit,
    cmd_config_show,
    print_config_summary,
    print_effective_preview,
    run_config_wizard,
)
from aidlc.routing.types import RouteDecision


@pytest.fixture
def version():
    return "0.0.0-test"


def _ns(**kw):
    base = dict(project=None, config=None, effective=False, config_cmd="show")
    base.update(kw)
    return Namespace(**base)


@patch("aidlc.cli.config_cmd.print_banner")
@patch("aidlc.cli.config_cmd.load_config")
def test_cmd_config_show_summary(mock_load, mock_banner, version, tmp_path, capsys):
    mock_load.return_value = {
        "runtime_profile": "standard",
        "routing_strategy": "balanced",
        "plan_budget_hours": 4,
        "dry_run": False,
        "providers": {
            "claude": {"enabled": True, "cli_command": "claude", "default_model": "sonnet"},
            "bad": "skip-me",
        },
    }
    cmd_config_show(_ns(project=str(tmp_path)), version)
    out = capsys.readouterr().out
    assert "claude" in out
    assert "Phase Models" in out


@patch("aidlc.cli.config_cmd.print_banner")
@patch("aidlc.cli.config_cmd.load_config")
def test_cmd_config_show_effective(mock_load, mock_banner, version, tmp_path, capsys):
    mock_load.return_value = {
        "routing_strategy": "balanced",
        "providers": {"claude": {"enabled": True, "cli_command": "claude"}},
    }
    adapter = MagicMock()
    health = MagicMock()
    health.is_usable = True
    health.status.value = "ok"
    health.message = "all good" * 10
    adapter.validate_health.return_value = health
    fake_adapter = MagicMock()
    dec = RouteDecision(
        provider_id="claude",
        account_id=None,
        adapter=fake_adapter,
        model="sonnet",
        reasoning="test",
        fallback=False,
    )
    with patch("aidlc.cli.config_cmd.ProviderRouter") as mock_router_cls:
        router = MagicMock()
        router._adapters = {"claude": adapter}
        router.resolve_preview.return_value = {"planning": dec}
        mock_router_cls.return_value = router
        mgr = MagicMock()
        acc = MagicMock()
        acc.health_status = "healthy"
        acc.is_premium = True
        acc.account_id = "a1"
        acc.provider_id = "claude"
        acc.membership_tier.value = "pro"
        mgr.list.return_value = [acc]
        with patch("aidlc.cli.config_cmd.AccountManager", return_value=mgr):
            cmd_config_show(_ns(project=str(tmp_path), effective=True), version)
    out = capsys.readouterr().out
    assert "Effective Runtime" in out
    assert "planning" in out.lower() or "Phase Routing" in out


@patch("aidlc.cli.config_cmd.print_banner")
@patch("aidlc.cli.config_cmd.cmd_config_edit")
def test_cmd_config_show_delegates_edit(mock_edit, mock_banner, version, tmp_path):
    cmd_config_show(_ns(project=str(tmp_path), config_cmd="edit"), version)
    mock_edit.assert_called_once()


@patch("aidlc.cli.config_cmd.print_banner")
@patch("aidlc.cli.config_cmd.run_config_wizard")
def test_cmd_config_show_delegates_wizard(mock_wiz, mock_banner, version, tmp_path):
    cmd_config_show(_ns(project=str(tmp_path), config_cmd="wizard"), version)
    mock_wiz.assert_called_once()


def test_cmd_config_edit_missing_exits(tmp_path):
    p = tmp_path / ".aidlc" / "config.json"
    with patch("aidlc.cli.config_cmd.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_config_edit(p)


def test_cmd_config_edit_opens_editor(tmp_path, monkeypatch):
    p = tmp_path / ".aidlc" / "config.json"
    p.parent.mkdir(parents=True)
    p.write_text("{}")
    monkeypatch.setenv("EDITOR", "true")
    with patch("aidlc.cli.config_cmd._sp.run") as mock_run:
        cmd_config_edit(p)
    mock_run.assert_called_once()


def test_print_config_summary_skips_non_dict_provider_entries(capsys):
    print_config_summary(
        {
            "providers": {
                "bad": "not-a-dict",
                "claude": {"enabled": False, "cli_command": "claude", "default_model": "sonnet"},
            }
        }
    )
    out = capsys.readouterr().out
    assert "Phase Models" in out


def test_print_config_summary_phase_models_defaults(capsys):
    print_config_summary(
        {
            "providers": {
                "openai": {
                    "enabled": True,
                    "cli_command": "x",
                    "default_model": "gpt-4",
                    "phase_models": {},
                }
            }
        }
    )
    out = capsys.readouterr().out
    assert "planning" in out


@patch("aidlc.cli.config_cmd.ProviderRouter")
def test_print_effective_preview_providers_not_a_dict(mock_router_cls, tmp_path, capsys):
    config = {"routing_strategy": "balanced", "providers": None}
    router = MagicMock()
    router._adapters = {}
    router.resolve_preview.return_value = {}
    mock_router_cls.return_value = router
    with patch("aidlc.cli.config_cmd.AccountManager", side_effect=RuntimeError("skip")):
        print_effective_preview(config, tmp_path)
    assert "Effective Runtime" in capsys.readouterr().out


@patch("aidlc.cli.config_cmd.ProviderRouter")
def test_print_effective_preview_no_account_manager(mock_router_cls, tmp_path, capsys):
    config = {"routing_strategy": "cheapest", "providers": {}}
    router = MagicMock()
    router._adapters = {}
    router.resolve_preview.return_value = {}
    mock_router_cls.return_value = router
    with patch("aidlc.cli.config_cmd.AccountManager", side_effect=RuntimeError("no")):
        print_effective_preview(config, tmp_path)
    assert "Effective Runtime" in capsys.readouterr().out


def test_run_config_wizard_missing_file(tmp_path):
    p = tmp_path / "nope.json"
    with patch("aidlc.cli.config_cmd.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            run_config_wizard(p)


def test_run_config_wizard_no_changes_all_eof(tmp_path):
    p = tmp_path / "config.json"
    base = {
        "routing_strategy": "balanced",
        "plan_budget_hours": 4,
        "providers": {
            "claude": {"enabled": True, "cli_command": "claude", "default_model": ""},
            "copilot": {"enabled": False, "cli_command": "copilot", "default_model": ""},
            "openai": {"enabled": False, "cli_command": "codex", "default_model": ""},
        },
    }
    p.write_text(json.dumps(base))
    with patch("builtins.input", side_effect=EOFError()):
        run_config_wizard(p)
    data = json.loads(p.read_text())
    assert data["plan_budget_hours"] == 4


def test_run_config_wizard_invalid_strategy_choice_and_abort_save(tmp_path, capsys):
    p = tmp_path / "config.json"
    base = {
        "routing_strategy": "balanced",
        "plan_budget_hours": 4,
        "providers": {
            "claude": {"enabled": True, "cli_command": "claude", "default_model": ""},
            "copilot": {"enabled": False, "cli_command": "copilot", "default_model": ""},
            "openai": {"enabled": False, "cli_command": "codex", "default_model": ""},
        },
    }
    p.write_text(json.dumps(base))
    inputs = ["bogus", "7"] + [""] * 9 + ["n"]
    with patch("builtins.input", side_effect=inputs):
        run_config_wizard(p)
    out = capsys.readouterr().out
    assert "Invalid choice" in out
    assert "Aborted" in out
    assert json.loads(p.read_text())["plan_budget_hours"] == 4


def test_run_config_wizard_saves_plan_budget(tmp_path, capsys):
    p = tmp_path / "config.json"
    base = {
        "routing_strategy": "balanced",
        "plan_budget_hours": 4,
        "providers": {
            "claude": {"enabled": True, "cli_command": "claude", "default_model": ""},
            "copilot": {"enabled": False, "cli_command": "copilot", "default_model": ""},
            "openai": {"enabled": False, "cli_command": "codex", "default_model": ""},
        },
    }
    p.write_text(json.dumps(base))
    seq = ["", "9"] + [""] * 9 + ["y"]
    with patch("builtins.input", side_effect=seq):
        run_config_wizard(p)
    data = json.loads(p.read_text())
    assert data["plan_budget_hours"] == 9.0
    assert "saved" in capsys.readouterr().out.lower()
