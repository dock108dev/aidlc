"""Coverage for aidlc.cli.accounts (mocked AccountManager)."""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest
from aidlc.accounts.models import Account, AuthState, MembershipTier
from aidlc.cli.accounts import cmd_accounts


@pytest.fixture
def version():
    return "0.0.0-test"


def _ns(**kw):
    base = dict(
        accounts_cmd="list",
        project=".",
        id=None,
        provider=None,
        tier=None,
        tags="",
        name="",
    )
    base.update(kw)
    return Namespace(**base)


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
def test_accounts_list_empty(mock_mgr_cls, mock_banner, version, capsys):
    mock_mgr_cls.return_value.list.return_value = []
    cmd_accounts(_ns(accounts_cmd="list"), version)
    assert "No accounts" in capsys.readouterr().out


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
def test_accounts_list_with_entries(mock_mgr_cls, mock_banner, version, capsys):
    a1 = Account(
        account_id="u1",
        provider_id="claude",
        display_name="Me",
        auth_state=AuthState.CONNECTED,
        health_status="healthy",
        membership_tier=MembershipTier.PRO,
        role_tags=["primary"],
        last_validated="2024-01-02T12:34:56+00:00",
    )
    a2 = Account(
        account_id="u2",
        provider_id="openai",
        display_name="",
        auth_state=AuthState.UNKNOWN,
        health_status="rate_limited",
        membership_tier=MembershipTier.UNKNOWN,
        role_tags=[],
        enabled=False,
    )
    a3 = Account(
        account_id="u3",
        provider_id="copilot",
        display_name="X",
        auth_state=AuthState.EXPIRED,
        health_status="broken",
        membership_tier=MembershipTier.FREE,
        role_tags=["premium"],
    )
    mock_mgr_cls.return_value.list.return_value = [a1, a2, a3]
    cmd_accounts(_ns(accounts_cmd="list"), version)
    out = capsys.readouterr().out
    assert "u1" in out and "u2" in out


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
def test_accounts_add_requires_ids(mock_mgr_cls, mock_banner, version, capsys):
    with patch("aidlc.cli.accounts.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_accounts(_ns(accounts_cmd="add", id=None, provider="claude"), version)


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
def test_accounts_add_success(mock_mgr_cls, mock_banner, version, capsys):
    mgr = mock_mgr_cls.return_value
    cmd_accounts(
        _ns(accounts_cmd="add", id="a", provider="claude", tier="bogus", tags="x, y"),
        version,
    )
    mgr.add.assert_called_once()
    assert "added" in capsys.readouterr().out.lower()


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
def test_accounts_add_duplicate(mock_mgr_cls, mock_banner, version, capsys):
    mgr = mock_mgr_cls.return_value
    mgr.add.side_effect = ValueError("already exists")
    with patch("aidlc.cli.accounts.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_accounts(_ns(accounts_cmd="add", id="a", provider="claude"), version)


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
def test_accounts_remove_requires_id(mock_mgr_cls, mock_banner, version):
    with patch("aidlc.cli.accounts.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_accounts(_ns(accounts_cmd="remove", id=None), version)


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
def test_accounts_remove(mock_mgr_cls, mock_banner, version, capsys):
    mgr = mock_mgr_cls.return_value
    mgr.remove.return_value = True
    cmd_accounts(_ns(accounts_cmd="remove", id="a"), version)
    assert "removed" in capsys.readouterr().out.lower()
    mgr.remove.return_value = False
    cmd_accounts(_ns(accounts_cmd="remove", id="missing"), version)
    assert "not found" in capsys.readouterr().out.lower()


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
@patch("aidlc.cli.accounts.ProviderRouter")
@patch("aidlc.cli.accounts.load_config")
def test_accounts_validate_single_and_all(
    mock_load, mock_router_cls, mock_mgr_cls, mock_banner, version, tmp_path, capsys
):
    mock_load.return_value = {"providers": {}}
    adapter = MagicMock()
    router = MagicMock()
    router._adapters = {"claude": adapter}
    mock_router_cls.return_value = router

    mgr = mock_mgr_cls.return_value
    mgr.get.return_value = None
    with patch("aidlc.cli.accounts.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_accounts(
                _ns(accounts_cmd="validate", id="nope", project=str(tmp_path)),
                version,
            )

    acc = Account(account_id="a1", provider_id="claude", membership_tier=MembershipTier.STANDARD)
    mgr.get.return_value = acc
    updated = Account(
        account_id="a1",
        provider_id="claude",
        health_status="healthy",
        auth_state=AuthState.CONNECTED,
        membership_tier=MembershipTier.STANDARD,
    )
    mgr.validate.return_value = updated
    cmd_accounts(_ns(accounts_cmd="validate", id="a1", project=str(tmp_path)), version)

    mgr.list.return_value = [
        Account(account_id="b1", provider_id="openai", membership_tier=MembershipTier.API),
    ]
    mgr.validate.return_value = Account(
        account_id="b1",
        provider_id="openai",
        health_status="limited",
        auth_state=AuthState.UNKNOWN,
        membership_tier=MembershipTier.API,
    )
    cmd_accounts(_ns(accounts_cmd="validate", id=None, project=str(tmp_path)), version)
    out = capsys.readouterr().out
    assert "b1" in out


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
@patch("aidlc.cli.accounts.ProviderRouter")
@patch("aidlc.cli.accounts.load_config")
def test_accounts_validate_all_empty(mock_load, mock_router_cls, mock_mgr_cls, mock_banner, version, tmp_path, capsys):
    mock_load.return_value = {}
    mock_mgr_cls.return_value.list.return_value = []
    cmd_accounts(_ns(accounts_cmd="validate", project=str(tmp_path)), version)
    assert "No accounts" in capsys.readouterr().out


@patch("aidlc.cli.display.print_banner")
@patch("aidlc.cli.accounts.AccountManager")
def test_accounts_unknown_subcommand(mock_mgr_cls, mock_banner, version):
    with patch("aidlc.cli.accounts.sys.exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            cmd_accounts(_ns(accounts_cmd="nope"), version)
