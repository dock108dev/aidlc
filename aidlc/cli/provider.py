"""Provider enable/disable, auth, and reconnect CLI."""

import argparse
import json
import logging
import subprocess as _sp
import sys
from pathlib import Path

from ..config import load_config
from ..routing.engine import ProviderRouter
from .display import (
    bold,
    cyan,
    dim,
    green,
    print_banner,
    red,
    yellow,
)

KNOWN_PROVIDERS = {"claude", "copilot", "openai"}

_PROVIDER_AUTH_COMMANDS: dict[str, tuple[list[str], str]] = {
    "claude": (
        ["claude", "auth", "login"],
        "Run: claude auth login",
    ),
    "copilot": (
        ["copilot", "login"],
        "Run: copilot login  (install first: brew install copilot-cli)",
    ),
    "openai": (
        ["codex", "login"],
        "Run: codex login",
    ),
}


def cmd_provider(args: argparse.Namespace, version: str) -> None:
    """Enable or disable a provider in the project config."""
    subcmd = getattr(args, "provider_cmd", "list")
    print_banner(version)

    project_root = Path(getattr(args, "project", None) or ".").resolve()
    config_path = project_root / ".aidlc" / "config.json"

    if subcmd == "list" or subcmd is None:
        cmd_provider_list(config_path)
    elif subcmd in ("enable", "disable"):
        name = getattr(args, "name", None)
        cmd_provider_toggle(config_path, name, enabled=(subcmd == "enable"))
    elif subcmd == "auth":
        name = getattr(args, "name", None)
        if not name:
            print(f"{red('x')} Provider name is required.")
            sys.exit(1)
        config = load_config(project_root=str(project_root))
        cmd_provider_auth(name, config)
    elif subcmd == "reconnect":
        if not config_path.exists():
            print(
                f"  {yellow('!')} No .aidlc/config.json found. Run {cyan('aidlc init')} first."
            )
            sys.exit(1)

        with open(config_path) as f:
            raw = json.load(f)

        providers_cfg = raw.get("providers", {})
        enabled_names = [
            n
            for n, c in providers_cfg.items()
            if isinstance(c, dict) and c.get("enabled", False)
        ]

        if not enabled_names:
            print("  No providers enabled.")
            return

        config = load_config(project_root=str(project_root))
        logger = logging.getLogger("aidlc.provider.reconnect")
        router = ProviderRouter(config, logger)

        print(f"  {bold('Provider health check...')}")
        print()

        needs_auth = []
        for name in enabled_names:
            adapter = router._adapters.get(name)
            if adapter is None:
                print(f"  {dim('○')} {name}: not loaded (disabled in routing)")
                continue
            health = adapter.validate_health()
            icon = green("●") if health.is_usable else red("●")
            print(f"  {icon} {name}: {health.status.value}")
            if not health.is_usable:
                needs_auth.append(name)

        print()
        if not needs_auth:
            print(f"  {green('All providers healthy — nothing to reconnect.')}")
            return

        print(f"  {yellow('!')} Reconnecting: {', '.join(needs_auth)}")
        print()
        for name in needs_auth:
            print(f"  {bold(f'--- {name} ---')}")
            cmd_provider_auth(name, config, show_health=False)
            print()
    else:
        print(f"Unknown provider subcommand: {subcmd}")
        sys.exit(1)


def cmd_provider_list(config_path: Path) -> None:
    if not config_path.exists():
        print(
            f"  {yellow('!')} No .aidlc/config.json found. Run {cyan('aidlc init')} first."
        )
        return

    with open(config_path) as f:
        config = json.load(f)

    providers = config.get("providers", {})
    if not providers:
        print("  No provider config found.")
        return

    print(f"  {bold('Providers')}")
    print()
    for pname, cfg in providers.items():
        enabled = cfg.get("enabled", True)
        status = green("enabled") if enabled else dim("disabled")
        model = cfg.get("default_model", "?")
        bullet = "●" if enabled else "○"
        print(f"  {bullet} {bold(pname):<20}  {status}  (model: {model})")
    print()
    print(
        f"  Toggle: {cyan('aidlc provider enable <name>')} / {cyan('aidlc provider disable <name>')}"
    )


def cmd_provider_toggle(config_path: Path, name: str, enabled: bool) -> None:
    if not name:
        print(f"{red('x')} Provider name is required.")
        sys.exit(1)

    if name not in KNOWN_PROVIDERS:
        print(
            f"{yellow('!')} Unknown provider '{name}'. Known: {', '.join(sorted(KNOWN_PROVIDERS))}"
        )
        sys.exit(1)

    if not config_path.exists():
        print(
            f"  {yellow('!')} No .aidlc/config.json found. Run {cyan('aidlc init')} first."
        )
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    config.setdefault("providers", {}).setdefault(name, {})["enabled"] = enabled

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    action = green("enabled") if enabled else dim("disabled")
    icon = green("+") if enabled else "-"
    print(f"  {icon} Provider '{bold(name)}' {action}")
    print(f"  Config: {cyan(str(config_path))}")


def cmd_provider_auth(name: str, config: dict, show_health: bool = True) -> None:
    """Run vendor login flow for a provider, preserving TTY."""
    if name not in KNOWN_PROVIDERS:
        print(
            f"{red('x')} Unknown provider '{name}'. Known: {', '.join(sorted(KNOWN_PROVIDERS))}"
        )
        sys.exit(1)

    auth_cmd, fallback_instructions = _PROVIDER_AUTH_COMMANDS[name]
    if name == "copilot":
        providers_cfg = config.get("providers", {})
        provider_cfg = (
            providers_cfg.get("copilot", {}) if isinstance(providers_cfg, dict) else {}
        )
        cli_command = provider_cfg.get("cli_command", "copilot")
        if cli_command == "gh":
            auth_cmd = ["gh", "auth", "login"]
            fallback_instructions = (
                "Run: gh auth login, then install the Copilot extension if needed."
            )

    logger = logging.getLogger("aidlc.provider.auth")
    router = ProviderRouter(config, logger)
    adapter = router._adapters.get(name)

    if adapter is None:
        print(f"  {yellow('!')} Provider '{name}' is disabled — enable it first with:")
        print(f"    {cyan(f'aidlc provider enable {name}')}")
        return

    if show_health:
        before = adapter.validate_health()
        before_icon = green("●") if before.is_usable else yellow("●")
        print(f"  {before_icon} {name} health before: {before.status.value}")
        if before.is_usable:
            print(f"  {dim('Already authenticated. Proceeding anyway...')}")
        print()

    print(f"  {bold(f'Launching {name} auth flow...')}")
    print(f"  {dim('(running: ' + ' '.join(auth_cmd) + ')')}")
    print()

    try:
        result = _sp.run(auth_cmd)
        exit_code = result.returncode
    except FileNotFoundError:
        print(f"\n  {red('x')} {name} CLI not found on PATH.")
        print(f"  {fallback_instructions}")
        return

    print()
    if exit_code == 0:
        after = adapter.validate_health()
        after_icon = green("●") if after.is_usable else red("●")
        print(f"  {after_icon} {name} health after: {after.status.value}")
        if after.is_usable:
            print(f"  {green('Auth successful.')}")
        else:
            print(
                f"  {yellow('!')} Auth command exited 0 but health check still failing: {after.message}"
            )
    else:
        print(f"  {yellow('!')} Auth command exited with code {exit_code}.")
        print(f"  Manual fallback: {fallback_instructions}")
