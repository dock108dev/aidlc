"""Config show, edit, wizard, and effective-routing preview CLI."""

import argparse
import json
import logging
import os
import subprocess as _sp
import sys
from pathlib import Path

from ..accounts import AccountManager
from ..config import load_config
from ..routing import ProviderRouter
from .display import (
    bold,
    cyan,
    dim,
    green,
    print_banner,
    red,
    yellow,
)


def cmd_config_show(args: argparse.Namespace, version: str) -> None:
    """Show effective runtime config and routing preview."""
    subcmd = getattr(args, "config_cmd", "show")
    project_root = Path(getattr(args, "project", None) or ".").resolve()
    config_path = project_root / ".aidlc" / "config.json"

    print_banner(version)

    if subcmd == "edit":
        cmd_config_edit(config_path)
        return

    if subcmd == "wizard":
        run_config_wizard(config_path)
        return

    config = load_config(
        config_path=getattr(args, "config", None),
        project_root=str(project_root),
    )

    effective = getattr(args, "effective", False)

    if effective:
        print_effective_preview(config, project_root)
    else:
        print_config_summary(config)


def cmd_config_edit(config_path: Path) -> None:
    """Open .aidlc/config.json in $EDITOR."""
    if not config_path.exists():
        print(f"  {yellow('!')} No .aidlc/config.json found. Run {cyan('aidlc init')} first.")
        sys.exit(1)

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    print(f"  Opening {cyan(str(config_path))} in {editor}...")
    print()
    _sp.run([editor, str(config_path)])


def run_config_wizard(config_path: Path) -> None:
    """Interactive config wizard — prompts for key settings, writes back to config.json."""
    if not config_path.exists():
        print(f"  {yellow('!')} No .aidlc/config.json found. Run {cyan('aidlc init')} first.")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    print(f"  {bold('Config Wizard')} — press Enter to keep the current value, Ctrl-C to abort.")
    print()

    changes: dict = {}

    def _prompt(label: str, key: str, current) -> None:
        display = str(current) if current is not None else dim("(not set)")
        try:
            raw = input(f"  {label} [{display}]: ").strip()
        except EOFError:
            raw = ""
        if raw:
            changes[key] = raw

    def _prompt_choice(label: str, key: str, choices: list[str], current: str) -> None:
        opts = "/".join(cyan(c) if c == current else c for c in choices)
        try:
            raw = input(f"  {label} ({opts}) [{current}]: ").strip().lower()
        except EOFError:
            raw = ""
        if raw and raw in choices:
            changes[key] = raw
        elif raw:
            print(f"    {yellow('!')} Invalid choice '{raw}', keeping '{current}'.")

    _prompt_choice(
        "Routing strategy",
        "routing_strategy",
        ["balanced", "cheapest", "best_quality", "custom"],
        config.get("routing_strategy", "balanced"),
    )

    _prompt("Plan budget (hours)", "plan_budget_hours", config.get("plan_budget_hours", 4))

    print()
    print(f"  {bold('Providers')}")

    providers = config.get("providers", {})
    provider_changes: dict = {}

    for pname in ["claude", "copilot", "openai"]:
        pcfg = providers.get(pname, {})
        enabled = pcfg.get("enabled", pname == "claude")
        print()
        print(f"  {bold(pname)}")

        new_enabled = enabled
        try:
            raw = input(f"    Enable {pname}? (y/n) [{'y' if enabled else 'n'}]: ").strip().lower()
        except EOFError:
            raw = ""
        if raw in ("y", "yes"):
            new_enabled = True
        elif raw in ("n", "no"):
            new_enabled = False

        new_cmd = pcfg.get("cli_command", pname if pname != "copilot" else "copilot")
        try:
            raw = input(f"    CLI command [{new_cmd}]: ").strip()
        except EOFError:
            raw = ""
        if raw:
            new_cmd = raw

        new_model = pcfg.get("default_model", "")
        try:
            raw = input(f"    Default model [{new_model or '(inherit)'}]: ").strip()
        except EOFError:
            raw = ""
        if raw:
            new_model = raw

        provider_changes[pname] = {
            **pcfg,
            "enabled": new_enabled,
            "cli_command": new_cmd,
        }
        if new_model:
            provider_changes[pname]["default_model"] = new_model

    print()
    print(f"  {bold('Summary of changes:')}")
    print()

    had_changes = False
    if changes:
        for k, v in changes.items():
            print(f"    {cyan(k)}: {dim(str(config.get(k, '(not set)')))} → {green(str(v))}")
            had_changes = True

    for pname, new_pcfg in provider_changes.items():
        old_pcfg = providers.get(pname, {})
        for field in ("enabled", "cli_command", "default_model"):
            old_val = old_pcfg.get(field)
            new_val = new_pcfg.get(field)
            if old_val != new_val:
                print(
                    f"    {cyan(f'providers.{pname}.{field}')}: "
                    f"{dim(str(old_val))} → {green(str(new_val))}"
                )
                had_changes = True

    if not had_changes:
        print(f"    {dim('No changes.')}")
        return

    print()
    try:
        confirm = input("  Save? (y/n) [y]: ").strip().lower()
    except EOFError:
        confirm = "y"

    if confirm in ("", "y", "yes"):
        for k, v in changes.items():
            if k == "plan_budget_hours":
                try:
                    v = float(v)
                except ValueError:
                    pass
            config[k] = v

        if provider_changes:
            config.setdefault("providers", {})
            for pname, pcfg in provider_changes.items():
                config["providers"].setdefault(pname, {}).update(pcfg)

        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        print(f"  {green('+')} Config saved to {cyan(str(config_path))}")
    else:
        print(f"  {dim('Aborted — no changes written.')}")


def print_config_summary(config: dict) -> None:
    """Print key config values."""
    print(f"  {bold('Active Configuration')}")
    print()
    print(f"  {bold('Runtime profile:')}    {config.get('runtime_profile', 'standard')}")
    print(f"  {bold('Routing strategy:')}   {cyan(config.get('routing_strategy', 'balanced'))}")
    print(f"  {bold('Plan budget:')}        {config.get('plan_budget_hours', 4)}h")
    print(f"  {bold('Dry run:')}            {config.get('dry_run', False)}")
    print()

    providers = config.get("providers", {})
    if providers:
        print(f"  {bold('Providers:')}")
        for pid, pcfg in providers.items():
            if not isinstance(pcfg, dict):
                continue
            enabled = green("enabled") if pcfg.get("enabled", False) else dim("disabled")
            print(
                f"    {pid}: {enabled}  cmd={pcfg.get('cli_command', '?')}  "
                f"default_model={pcfg.get('default_model', '?')}"
            )
        print()

    enabled_providers = {
        pid: pcfg
        for pid, pcfg in providers.items()
        if isinstance(pcfg, dict) and pcfg.get("enabled", False)
    }
    display_providers = enabled_providers if enabled_providers else providers

    if display_providers:
        print(f"  {bold('Phase Models:')}")
        for pid, pcfg in display_providers.items():
            if not isinstance(pcfg, dict):
                continue
            phase_models = pcfg.get("phase_models") or {}
            default_model = pcfg.get("default_model", "?")
            print(f"    {cyan(pid)}  (default: {default_model})")
            phases = [
                "planning",
                "research",
                "implementation",
                "implementation_complex",
                "finalization",
                "audit",
            ]
            for phase in phases:
                model = phase_models.get(phase, default_model)
                print(f"      {phase:<30} {model}")
        print()
    print(f"  Tip: run {cyan('aidlc config show --effective')} for a full routing preview.")


def print_effective_preview(config: dict, project_root: Path) -> None:
    """Print a plain-English effective runtime preview."""
    logger = logging.getLogger("aidlc.config.preview")
    router = ProviderRouter(config, logger)

    try:
        manager = AccountManager()
        router.set_account_manager(manager)
    except Exception:
        manager = None

    print(f"  {bold('Effective Runtime Preview')}")
    print(f"  Project: {cyan(str(project_root))}")
    print(f"  Strategy: {cyan(config.get('routing_strategy', 'balanced'))}")
    print()

    print(f"  {bold('Provider Health:')}")
    providers_cfg = config.get("providers", {})
    for provider_id, pcfg in providers_cfg.items() if isinstance(providers_cfg, dict) else []:
        if not isinstance(pcfg, dict):
            continue
        adapter = router._adapters.get(provider_id)
        if adapter:
            health = adapter.validate_health()
            health_icon = green("●") if health.is_usable else red("●")
            print(f"    {health_icon} {provider_id}: {health.status.value} — {health.message[:60]}")
        else:
            print(f"    {dim('○')} {provider_id}: {dim('not loaded')}")
    print()

    preview = router.resolve_preview()
    print(f"  {bold('Phase Routing (what will run):')}")
    print(f"  {'Phase':<28} {'Provider':<10} {'Account':<20} {'Model':<25}")
    print(f"  {'-' * 28} {'-' * 10} {'-' * 20} {'-' * 25}")
    for phase, decision in preview.items():
        account_label = decision.account_id or dim("(default auth)")
        fallback_marker = yellow(" [fallback]") if decision.fallback else ""
        print(
            f"  {phase:<28} {decision.provider_id:<10} {account_label:<20} "
            f"{decision.model:<25}{fallback_marker}"
        )
    print()

    if manager:
        accounts = manager.list()
        if accounts:
            print(f"  {bold('Accounts:')}")
            for acc in accounts:
                icon = green("v") if acc.health_status == "healthy" else yellow("-")
                premium_tag = yellow(" [premium]") if acc.is_premium else ""
                print(
                    f"    [{icon}] {acc.account_id} ({acc.provider_id}) "
                    f"tier={acc.membership_tier.value}{premium_tag}"
                )
            print()
