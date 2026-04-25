"""Accounts management subcommand for CLI."""

import argparse
import logging
import sys
from pathlib import Path

from aidlc.accounts import Account, AccountManager, AuthState, MembershipTier
from aidlc.config import load_config
from aidlc.routing import ProviderRouter

from .display import bold, cyan, dim, green, red, yellow


def cmd_accounts(args: argparse.Namespace, version: str) -> None:
    """Manage provider accounts."""
    from .display import print_banner

    subcmd = getattr(args, "accounts_cmd", "list")
    print_banner(version)

    manager = AccountManager()

    if subcmd == "list":
        list_accounts(manager)
    elif subcmd == "add":
        add_account(args, manager)
    elif subcmd == "remove":
        remove_account(args, manager)
    elif subcmd == "validate":
        validate_account(args, manager)
    else:
        print(f"Unknown accounts subcommand: {subcmd}")
        sys.exit(1)


def list_accounts(manager: AccountManager) -> None:
    """List all registered accounts."""
    accounts = manager.list()
    if not accounts:
        print("  No accounts registered.")
        print()
        print(f"  Add one with: {cyan('aidlc accounts add --provider claude --id my-account')}")
        return

    print(f"  {bold('Registered Accounts')} ({len(accounts)} total)")
    print()
    for acc in accounts:
        health_icon = (
            green("●")
            if acc.health_status == "healthy"
            else (
                yellow("●")
                if acc.health_status in ("limited", "rate_limited", "unknown", "unchecked")
                else red("●")
            )
        )
        auth_label = (
            acc.auth_state.value if hasattr(acc.auth_state, "value") else str(acc.auth_state)
        )
        enabled_label = green("enabled") if acc.enabled else dim("disabled")
        tier = (
            acc.membership_tier.value
            if hasattr(acc.membership_tier, "value")
            else str(acc.membership_tier)
        )
        tags = ", ".join(acc.role_tags) if acc.role_tags else dim("no tags")
        print(f"  {health_icon} {bold(acc.account_id)}")
        print(f"     Provider:  {acc.provider_id}")
        print(f"     Name:      {acc.display_name or dim('(unnamed)')}")
        print(f"     Status:    {enabled_label}  auth={auth_label}  health={acc.health_status}")
        print(f"     Tier:      {tier}")
        print(f"     Tags:      {tags}")
        if acc.last_validated:
            print(f"     Validated: {acc.last_validated[:19]}")
        print()


def add_account(args: argparse.Namespace, manager: AccountManager) -> None:
    """Add a new account."""
    account_id = getattr(args, "id", None)
    provider_id = getattr(args, "provider", None)
    if not account_id or not provider_id:
        print(f"{red('x')} --id and --provider are required.")
        sys.exit(1)

    tier_str = getattr(args, "tier", "unknown") or "unknown"
    try:
        tier = MembershipTier(tier_str)
    except ValueError:
        print(f"{yellow('!')} Unknown tier '{tier_str}'. Using 'unknown'.")
        tier = MembershipTier.UNKNOWN

    tags_str = getattr(args, "tags", "") or ""
    tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else ["primary"]

    account = Account(
        account_id=account_id,
        provider_id=provider_id,
        display_name=getattr(args, "name", "") or f"{provider_id} ({account_id})",
        membership_tier=tier,
        role_tags=tags,
        auth_state=AuthState.UNKNOWN,
    )
    try:
        manager.add(account)
        print(f"{green('+')} Account '{account_id}' added ({provider_id}, tier={tier_str})")
        print(f"  Run {cyan(f'aidlc accounts validate --id {account_id}')} to check health.")
    except ValueError as e:
        print(f"{red('x')} {e}")
        sys.exit(1)


def remove_account(args: argparse.Namespace, manager: AccountManager) -> None:
    """Remove an account."""
    account_id = getattr(args, "id", None)
    if not account_id:
        print(f"{red('x')} --id is required.")
        sys.exit(1)
    removed = manager.remove(account_id, remove_credentials=True)
    if removed:
        print(f"{green('-')} Account '{account_id}' removed.")
    else:
        print(f"{yellow('!')} Account '{account_id}' not found.")


def validate_account(args: argparse.Namespace, manager: AccountManager) -> None:
    """Validate accounts' health and auth status."""
    account_id = getattr(args, "id", None)
    project_root = Path(getattr(args, "project", None) or ".").resolve()
    config = load_config(project_root=str(project_root))

    logger = logging.getLogger("aidlc.accounts.validate")
    router = ProviderRouter(config, logger)

    if account_id:
        account = manager.get(account_id)
        if not account:
            print(f"{red('x')} Account '{account_id}' not found.")
            sys.exit(1)
        adapter = router._adapters.get(account.provider_id)
        updated = manager.validate(account_id, adapter=adapter)
        health_label = (
            green(updated.health_status)
            if updated.health_status == "healthy"
            else (
                yellow(updated.health_status)
                if updated.health_status in ("limited", "unknown")
                else red(updated.health_status)
            )
        )
        print(f"  {bold(account_id)}: {health_label}  auth={updated.auth_state.value}")
    else:
        # Validate all accounts
        accounts = manager.list()
        if not accounts:
            print("  No accounts to validate.")
            return
        print(f"  Validating {len(accounts)} account(s)...")
        print()
        for acc in accounts:
            adapter = router._adapters.get(acc.provider_id)
            updated = manager.validate(acc.account_id, adapter=adapter)
            icon = green("v") if updated.health_status == "healthy" else yellow("!")
            print(f"  [{icon}] {acc.account_id} ({acc.provider_id}): {updated.health_status}")
