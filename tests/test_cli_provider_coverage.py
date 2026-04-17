"""Coverage for aidlc.cli.provider (mocked subprocess and router)."""

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aidlc.cli.provider import (
    cmd_provider,
    cmd_provider_auth,
    cmd_provider_list,
    cmd_provider_toggle,
)


@pytest.fixture
def version():
    return "0.0.0-test"


def _ns(**kw):
    base = dict(project=None, provider_cmd="list", name=None)
    base.update(kw)
    return Namespace(**base)


@patch("aidlc.cli.display.print_banner")
def test_cmd_provider_list_no_config(mock_banner, version, tmp_path, capsys):
    cmd_provider(_ns(project=str(tmp_path)), version)
    assert "init" in capsys.readouterr().out.lower()


@patch("aidlc.cli.display.print_banner")
def test_cmd_provider_list_empty_providers(mock_banner, version, tmp_path, capsys):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    cfg = aidlc / "config.json"
    cfg.write_text(json.dumps({"providers": {}}))
    cmd_provider(_ns(project=str(tmp_path)), version)
    assert "No provider config" in capsys.readouterr().out


@patch("aidlc.cli.display.print_banner")
def test_cmd_provider_list_shows_rows(mock_banner, version, tmp_path, capsys):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    cfg = aidlc / "config.json"
    cfg.write_text(
        json.dumps({"providers": {"claude": {"enabled": True, "default_model": "sonnet"}}})
    )
    cmd_provider(_ns(project=str(tmp_path)), version)
    assert "claude" in capsys.readouterr().out


@patch("aidlc.cli.display.print_banner")
def test_cmd_provider_toggle_errors(mock_banner, version, tmp_path):
    with patch("aidlc.cli.provider.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_provider(_ns(provider_cmd="enable", name=None, project=str(tmp_path)), version)
    with patch("aidlc.cli.provider.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_provider(_ns(provider_cmd="enable", name="bad", project=str(tmp_path)), version)


@patch("aidlc.cli.display.print_banner")
def test_cmd_provider_toggle_success(mock_banner, version, tmp_path, capsys):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    cfg = aidlc / "config.json"
    cfg.write_text(json.dumps({"providers": {}}))
    cmd_provider(_ns(provider_cmd="enable", name="claude", project=str(tmp_path)), version)
    data = json.loads(cfg.read_text())
    assert data["providers"]["claude"]["enabled"] is True
    cmd_provider(_ns(provider_cmd="disable", name="claude", project=str(tmp_path)), version)
    assert json.loads(cfg.read_text())["providers"]["claude"]["enabled"] is False


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.provider.load_config")
def test_cmd_provider_auth_subcommand_requires_name(mock_load, mock_banner, version, tmp_path):
    with patch("aidlc.cli.provider.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_provider(_ns(provider_cmd="auth", name=None, project=str(tmp_path)), version)


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.provider.load_config")
def test_cmd_provider_auth_subcommand_runs(mock_load, mock_banner, version, tmp_path, capsys):
    mock_load.return_value = {"providers": {"claude": {"enabled": True}}}
    adapter = MagicMock()
    h = MagicMock()
    h.is_usable = False
    h.status.value = "x"
    adapter.validate_health.return_value = h
    with patch("aidlc.cli.provider.ProviderRouter") as cls:
        cls.return_value._adapters = {"claude": adapter}
        with patch("aidlc.cli.provider._sp.run") as run:
            run.return_value = MagicMock(returncode=0)
            cmd_provider(_ns(provider_cmd="auth", name="claude", project=str(tmp_path)), version)
    assert "Launching" in capsys.readouterr().out or "claude" in capsys.readouterr().out.lower()


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.provider.load_config")
@patch("aidlc.cli.provider.ProviderRouter")
def test_cmd_provider_reconnect_paths(mock_router, mock_load, mock_banner, version, tmp_path, capsys):
    with patch("aidlc.cli.provider.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_provider(_ns(provider_cmd="reconnect", project=str(tmp_path)), version)

    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    cfg = aidlc / "config.json"
    cfg.write_text(json.dumps({"providers": {"claude": {"enabled": False}}}))
    mock_load.return_value = {"providers": {"claude": {"enabled": False}}}
    cmd_provider(_ns(provider_cmd="reconnect", project=str(tmp_path)), version)
    assert "No providers enabled" in capsys.readouterr().out

    cfg.write_text(json.dumps({"providers": {"claude": {"enabled": True}}}))
    mock_load.return_value = {"providers": {"claude": {"enabled": True}}}
    adapter = MagicMock()
    good = MagicMock(is_usable=True, status=MagicMock(value="ok"))
    adapter.validate_health.return_value = good
    mock_router.return_value._adapters = {"claude": adapter}
    cmd_provider(_ns(provider_cmd="reconnect", project=str(tmp_path)), version)
    assert "healthy" in capsys.readouterr().out.lower()

    bad = MagicMock(is_usable=False, status=MagicMock(value="bad"))
    adapter.validate_health.return_value = bad
    with patch("aidlc.cli.provider.cmd_provider_auth"):
        cmd_provider(_ns(provider_cmd="reconnect", project=str(tmp_path)), version)
    assert "Reconnecting" in capsys.readouterr().out


@patch("aidlc.cli.display.print_banner")
def test_cmd_provider_unknown_subcommand(mock_banner, version, tmp_path):
    with patch("aidlc.cli.provider.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_provider(_ns(provider_cmd="nope", project=str(tmp_path)), version)


def test_cmd_provider_auth_disabled_adapter(capsys):
    cfg = {"providers": {}}
    with patch("aidlc.cli.provider.ProviderRouter") as cls:
        cls.return_value._adapters = {}
        cmd_provider_auth("claude", cfg, show_health=False)
    assert "disabled" in capsys.readouterr().out.lower()


def test_cmd_provider_auth_unknown_name():
    with patch("aidlc.cli.provider.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_provider_auth("bad", {})


def test_cmd_provider_auth_file_not_found(capsys):
    cfg = {"providers": {"claude": {"enabled": True}}}
    adapter = MagicMock()
    before = MagicMock(is_usable=True, status=MagicMock(value="ok"))
    adapter.validate_health.return_value = before
    with patch("aidlc.cli.provider.ProviderRouter") as cls:
        cls.return_value._adapters = {"claude": adapter}
        with patch("aidlc.cli.provider._sp.run", side_effect=FileNotFoundError):
            cmd_provider_auth("claude", cfg, show_health=True)
    out = capsys.readouterr().out
    assert "not found" in out.lower() or "PATH" in out


def test_cmd_provider_auth_exit_zero_not_usable_after(capsys):
    cfg = {"providers": {"openai": {"enabled": True}}}
    adapter = MagicMock()
    adapter.validate_health.side_effect = [
        MagicMock(is_usable=False, status=MagicMock(value="x")),
        MagicMock(is_usable=False, status=MagicMock(value="x"), message="still bad"),
    ]
    with patch("aidlc.cli.provider.ProviderRouter") as cls:
        cls.return_value._adapters = {"openai": adapter}
        with patch("aidlc.cli.provider._sp.run", return_value=MagicMock(returncode=0)):
            cmd_provider_auth("openai", cfg, show_health=True)
    assert "failing" in capsys.readouterr().out.lower() or "still bad" in capsys.readouterr().out


def test_cmd_provider_auth_nonzero_exit(capsys):
    cfg = {"providers": {"claude": {"enabled": True}}}
    adapter = MagicMock()
    adapter.validate_health.return_value = MagicMock(is_usable=False, status=MagicMock(value="x"))
    with patch("aidlc.cli.provider.ProviderRouter") as cls:
        cls.return_value._adapters = {"claude": adapter}
        with patch("aidlc.cli.provider._sp.run", return_value=MagicMock(returncode=2)):
            cmd_provider_auth("claude", cfg, show_health=False)
    assert "code 2" in capsys.readouterr().out


def test_cmd_provider_auth_copilot_gh_command(capsys):
    cfg = {"providers": {"copilot": {"enabled": True, "cli_command": "gh"}}}
    adapter = MagicMock()
    adapter.validate_health.return_value = MagicMock(is_usable=True, status=MagicMock(value="ok"))
    with patch("aidlc.cli.provider.ProviderRouter") as cls:
        cls.return_value._adapters = {"copilot": adapter}
        with patch("aidlc.cli.provider._sp.run", return_value=MagicMock(returncode=0)) as run:
            cmd_provider_auth("copilot", cfg, show_health=False)
    invoked = run.call_args[0][0]
    assert invoked[0] == "gh"


def test_cmd_provider_list_direct_no_providers_key(tmp_path, capsys):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({}))
    cmd_provider_list(p)
    assert "No provider config" in capsys.readouterr().out


def test_cmd_provider_toggle_direct_unknown():
    with patch("aidlc.cli.provider.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_provider_toggle(Path("x"), "nope", True)


def test_cmd_provider_toggle_missing_config_exits(tmp_path):
    p = tmp_path / "missing.json"
    with patch("aidlc.cli.provider.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_provider_toggle(p, "claude", True)


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.provider.load_config")
@patch("aidlc.cli.provider.ProviderRouter")
@patch("aidlc.cli.provider.cmd_provider_auth")
def test_cmd_provider_reconnect_adapter_not_loaded(
    mock_auth, mock_router, mock_load, mock_banner, version, tmp_path, capsys
):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    cfg = aidlc / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "providers": {
                    "claude": {"enabled": True},
                    "openai": {"enabled": True},
                }
            }
        )
    )
    mock_load.return_value = {"providers": {}}
    mock_router.return_value._adapters = {}
    cmd_provider(_ns(provider_cmd="reconnect", project=str(tmp_path)), version)
    out = capsys.readouterr().out
    assert "not loaded" in out
    assert "healthy" in out.lower()
