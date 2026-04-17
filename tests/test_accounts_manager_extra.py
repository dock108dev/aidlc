"""Extra tests for CredentialStore and AccountManager (tmp dirs, file fallback)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aidlc.accounts.credentials import CredentialStore
from aidlc.accounts.manager import AccountManager
from aidlc.accounts.models import Account, AuthState
from aidlc.providers.base import HealthResult, HealthStatus


def test_credential_store_file_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    d = tmp_path / "home" / ".aidlc"
    store = CredentialStore(d)
    store.store("acc1", "api_key", "secret-value")
    assert store.get("acc1", "api_key") == "secret-value"
    store.delete("acc1", "api_key")
    assert store.get("acc1", "api_key") is None


def test_credential_store_delete_all_keys_for_account(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    store = CredentialStore(tmp_path / ".aidlc")
    store.store("a", "k1", "v1")
    store.store("a", "k2", "v2")
    store.delete("a", None)
    assert store.get("a", "k1") is None


def test_credential_store_list_account_keys(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    store = CredentialStore(tmp_path / ".aidlc")
    store.store("x", "token", "t")
    assert "token" in store.list_account_keys("x")


def test_credential_store_load_corrupt_json(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    d = tmp_path / ".aidlc"
    d.mkdir(parents=True)
    (d / "credentials.json").write_text("not json {")
    store = CredentialStore(d)
    assert store.get("any", "k") is None


def test_account_manager_add_list_remove(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    mgr = AccountManager(tmp_path / ".aidlc")
    acc = Account(
        account_id="p-1",
        provider_id="openai",
        display_name="P",
        auth_state=AuthState.UNKNOWN,
    )
    mgr.add(acc)
    assert mgr.get("p-1") is not None
    assert mgr.by_provider("openai")[0].account_id == "p-1"
    assert mgr.remove("p-1") is True
    assert mgr.get("p-1") is None


def test_account_manager_add_duplicate_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    mgr = AccountManager(tmp_path / ".aidlc")
    acc = Account(account_id="dup", provider_id="claude", display_name="D")
    mgr.add(acc)
    with pytest.raises(ValueError, match="already exists"):
        mgr.add(acc)


def test_account_manager_load_invalid_json(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    d = tmp_path / ".aidlc"
    d.mkdir(parents=True)
    (d / "accounts.json").write_text("{")
    mgr = AccountManager(d)
    assert mgr.list() == []


def test_account_manager_validate_with_adapter(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    mgr = AccountManager(tmp_path / ".aidlc")
    mgr.add(Account(account_id="v1", provider_id="openai", display_name="V"))
    adapter = MagicMock()
    adapter.validate_health.return_value = HealthResult(status=HealthStatus.HEALTHY)
    out = mgr.validate("v1", adapter=adapter)
    assert out.auth_state == AuthState.CONNECTED


def test_account_manager_validate_missing_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    mgr = AccountManager(tmp_path / ".aidlc")
    with pytest.raises(ValueError, match="not found"):
        mgr.validate("nope")


def test_ensure_default_accounts_from_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("aidlc.accounts.credentials._try_keyring_import", lambda: None)
    mgr = AccountManager(tmp_path / ".aidlc")
    cfg = {
        "providers": {
            "openai": {
                "accounts": [{"id": "from-cfg", "display_name": "DC"}],
            }
        }
    }
    mgr.ensure_default_accounts(cfg)
    got = mgr.get("from-cfg")
    assert got is not None
    assert got.display_name == "DC"
