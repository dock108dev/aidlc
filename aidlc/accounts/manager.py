"""Account manager for AIDLC.

Persists account metadata to ~/.aidlc/accounts.json.
Credentials are stored separately via CredentialStore.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Account, AuthState, MembershipTier
from .credentials import CredentialStore

_logger = logging.getLogger(__name__)


class AccountManager:
    """Manages the lifecycle of provider accounts.

    Persists to ~/.aidlc/accounts.json (metadata only, no secrets).
    Credentials are stored via CredentialStore (Keychain / secure file).

    Account operations:
      - add(account) — register a new account
      - remove(account_id) — delete account metadata and credentials
      - update(account) — update account metadata
      - validate(account_id) — run health check and update auth_state
      - list() — return all accounts
      - get(account_id) — return a single account
      - by_provider(provider_id) — return accounts for a provider
      - enabled() — return only enabled/usable accounts
    """

    def __init__(
        self,
        aidlc_dir: Path | None = None,
        credential_store: CredentialStore | None = None,
        logger: logging.Logger | None = None,
    ):
        self._dir = aidlc_dir or Path.home() / ".aidlc"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._accounts_path = self._dir / "accounts.json"
        self._creds = credential_store or CredentialStore(self._dir)
        self._log = logger or _logger

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_all(self) -> dict[str, dict]:
        """Load raw account data from disk."""
        if not self._accounts_path.exists():
            return {}
        try:
            with open(self._accounts_path) as f:
                data = json.load(f)
            if isinstance(data, list):
                # Migrate old list format -> dict
                return {a["account_id"]: a for a in data if isinstance(a, dict)}
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            self._log.warning(f"Could not load accounts.json: {e}")
            return {}

    def _save_all(self, accounts: dict[str, dict]) -> None:
        try:
            with open(self._accounts_path, "w") as f:
                json.dump(accounts, f, indent=2)
        except OSError as e:
            self._log.error(f"Failed to save accounts.json: {e}")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, account: Account) -> None:
        """Register a new account. Raises ValueError if ID already exists."""
        accounts = self._load_all()
        if account.account_id in accounts:
            raise ValueError(f"Account '{account.account_id}' already exists. Use update() to modify.")
        accounts[account.account_id] = account.to_dict()
        self._save_all(accounts)
        self._log.info(f"Account added: {account.account_id} ({account.provider_id})")

    def update(self, account: Account) -> None:
        """Update an existing account's metadata."""
        accounts = self._load_all()
        accounts[account.account_id] = account.to_dict()
        self._save_all(accounts)

    def remove(self, account_id: str, remove_credentials: bool = True) -> bool:
        """Remove an account. Returns True if found and removed."""
        accounts = self._load_all()
        if account_id not in accounts:
            return False
        del accounts[account_id]
        self._save_all(accounts)
        if remove_credentials:
            self._creds.delete(account_id)
        self._log.info(f"Account removed: {account_id}")
        return True

    def get(self, account_id: str) -> Optional[Account]:
        """Return a single account by ID, or None if not found."""
        accounts = self._load_all()
        data = accounts.get(account_id)
        return Account.from_dict(data) if data else None

    def list(self) -> list[Account]:
        """Return all registered accounts."""
        return [Account.from_dict(d) for d in self._load_all().values()]

    def by_provider(self, provider_id: str) -> list[Account]:
        """Return accounts for a specific provider."""
        return [a for a in self.list() if a.provider_id == provider_id]

    def enabled(self) -> list[Account]:
        """Return only enabled + usable accounts."""
        return [a for a in self.list() if a.is_usable]

    # ------------------------------------------------------------------
    # Validation / health check
    # ------------------------------------------------------------------

    def validate(
        self,
        account_id: str,
        adapter=None,
    ) -> Account:
        """Run a health check for the account and update its status.

        Args:
            account_id: Account to validate.
            adapter: Optional ProviderAdapter instance to use for health check.

        Returns:
            Updated Account with refreshed health_status and auth_state.
        """
        account = self.get(account_id)
        if account is None:
            raise ValueError(f"Account '{account_id}' not found.")

        if adapter is not None:
            try:
                health = adapter.validate_health(account_id=account_id)
                account.health_status = health.status.value
                account.auth_state = (
                    AuthState.CONNECTED if health.is_usable else AuthState.DISCONNECTED
                )
            except Exception as e:
                self._log.warning(f"Health check failed for {account_id}: {e}")
                account.health_status = "unknown"
        else:
            account.health_status = "unchecked"

        account.last_validated = datetime.now(timezone.utc).isoformat()
        self.update(account)
        return account

    def mark_used(self, account_id: str) -> None:
        """Update last_used timestamp for an account."""
        account = self.get(account_id)
        if account:
            account.last_used = datetime.now(timezone.utc).isoformat()
            self.update(account)

    # ------------------------------------------------------------------
    # Credential helpers (thin delegators to CredentialStore)
    # ------------------------------------------------------------------

    def store_credential(self, account_id: str, key: str, value: str) -> None:
        self._creds.store(account_id, key, value)

    def get_credential(self, account_id: str, key: str) -> Optional[str]:
        return self._creds.get(account_id, key)

    def delete_credentials(self, account_id: str) -> None:
        self._creds.delete(account_id)

    # ------------------------------------------------------------------
    # Bootstrap: ensure legacy default accounts are represented
    # ------------------------------------------------------------------

    def ensure_default_accounts(self, config: dict) -> None:
        """Create placeholder accounts from legacy config if none exist.

        Reads `providers` block in config and creates Account entries for
        any provider that has no accounts yet. This makes the accounts list
        non-empty on first use without requiring explicit `aidlc accounts add`.
        """
        providers_cfg = config.get("providers", {})
        if not isinstance(providers_cfg, dict):
            return

        existing = {a.account_id for a in self.list()}

        for provider_id, provider_data in providers_cfg.items():
            if not isinstance(provider_data, dict):
                continue
            accounts_data = provider_data.get("accounts", [])
            if not isinstance(accounts_data, list):
                continue
            for acc_data in accounts_data:
                if not isinstance(acc_data, dict):
                    continue
                aid = acc_data.get("id") or f"{provider_id}-default"
                if aid in existing:
                    continue
                account = Account(
                    account_id=aid,
                    provider_id=provider_id,
                    display_name=acc_data.get("display_name", f"{provider_id} ({aid})"),
                    membership_tier=_parse_tier(acc_data.get("tier", "unknown")),
                    role_tags=acc_data.get("role_tags", ["primary"]),
                    enabled=acc_data.get("enabled", True),
                )
                try:
                    self.add(account)
                except ValueError:
                    pass  # Already exists from a concurrent caller


def _parse_tier(value: str) -> MembershipTier:
    try:
        return MembershipTier(value)
    except ValueError:
        return MembershipTier.UNKNOWN
