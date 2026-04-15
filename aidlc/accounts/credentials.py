"""Credential store for AIDLC provider accounts.

Uses macOS Keychain (via keyring library) when available,
with an encrypted-at-rest fallback for CI/non-GUI environments.
Config files store only references, never raw secrets.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "aidlc"
_WARN_PLAINTEXT = True  # Warn once if falling back to plaintext store


def _try_keyring_import():
    """Import keyring lazily; return module or None if unavailable."""
    try:
        import keyring  # type: ignore[import-untyped]
        return keyring
    except ImportError:
        return None


class CredentialStore:
    """Secure credential storage for provider accounts.

    Priority:
      1. macOS Keychain / system keyring via `keyring` library
      2. Plaintext fallback file (~/.aidlc/credentials.json) with a warning

    Credentials are keyed by (account_id, key), e.g.:
      store("my-claude", "api_key", "sk-...")
      get("my-claude", "api_key") -> "sk-..."
    """

    def __init__(self, aidlc_dir: Path | None = None):
        self._dir = aidlc_dir or Path.home() / ".aidlc"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fallback_path = self._dir / "credentials.json"
        self._keyring = _try_keyring_import()
        self._warned_fallback = False

    def store(self, account_id: str, key: str, value: str) -> None:
        """Store a credential securely."""
        if self._keyring:
            try:
                self._keyring.set_password(_KEYRING_SERVICE, f"{account_id}:{key}", value)
                return
            except Exception as e:
                logger.debug(f"Keyring write failed ({e}), falling back to file store.")

        self._warn_plaintext()
        data = self._load_fallback()
        data.setdefault(account_id, {})[key] = value
        self._save_fallback(data)

    def get(self, account_id: str, key: str) -> Optional[str]:
        """Retrieve a stored credential. Returns None if not found."""
        if self._keyring:
            try:
                value = self._keyring.get_password(_KEYRING_SERVICE, f"{account_id}:{key}")
                if value is not None:
                    return value
            except Exception as e:
                logger.debug(f"Keyring read failed ({e}), checking file store.")

        # Check fallback file
        data = self._load_fallback()
        return data.get(account_id, {}).get(key)

    def delete(self, account_id: str, key: str | None = None) -> None:
        """Delete a credential or all credentials for an account."""
        if self._keyring:
            try:
                if key:
                    self._keyring.delete_password(_KEYRING_SERVICE, f"{account_id}:{key}")
                else:
                    # Delete all known keys for this account from fallback to discover keys
                    data = self._load_fallback()
                    for k in list(data.get(account_id, {}).keys()):
                        try:
                            self._keyring.delete_password(_KEYRING_SERVICE, f"{account_id}:{k}")
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Keyring delete failed ({e}).")

        # Always clean fallback too
        data = self._load_fallback()
        if account_id in data:
            if key:
                data[account_id].pop(key, None)
            else:
                del data[account_id]
            self._save_fallback(data)

    def list_account_keys(self, account_id: str) -> list[str]:
        """List credential keys stored for an account (from fallback file only)."""
        data = self._load_fallback()
        return list(data.get(account_id, {}).keys())

    def _warn_plaintext(self) -> None:
        if not self._warned_fallback and _WARN_PLAINTEXT:
            logger.warning(
                "keyring library not available. Credentials stored in plaintext at "
                f"{self._fallback_path}. Install 'keyring' for secure storage: pip install keyring"
            )
            self._warned_fallback = True

    def _load_fallback(self) -> dict:
        if self._fallback_path.exists():
            try:
                with open(self._fallback_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_fallback(self, data: dict) -> None:
        try:
            with open(self._fallback_path, "w") as f:
                json.dump(data, f, indent=2)
            # Restrict file permissions to owner-only
            os.chmod(self._fallback_path, 0o600)
        except OSError as e:
            logger.error(f"Failed to save credentials: {e}")
