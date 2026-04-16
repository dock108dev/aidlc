"""CLI command handlers and display helpers."""

import argparse
import shutil
import sys
from pathlib import Path

from .config import load_config, write_default_config
from .state_manager import find_latest_run, load_state
from .cli.display import (
    bold as _bold,
    green as _green,
    yellow as _yellow,
    red as _red,
    dim as _dim,
    cyan as _cyan,
    print_banner as _print_banner,
    print_precheck as _print_precheck,
    get_template_dir as _get_template_dir,
)


def cmd_precheck(args: argparse.Namespace, version: str) -> None:
    """Run pre-flight readiness check."""
    from .precheck import run_precheck

    project_root = Path(args.project or ".").resolve()
    _print_banner(version)
    print(f"Checking {_cyan(str(project_root))}...")
    print()

    result = run_precheck(project_root, auto_init=True)
    _print_precheck(result, project_root, verbose=args.verbose)
    if not result.ready:
        sys.exit(1)


def cmd_init(args: argparse.Namespace, version: str) -> None:
    """Initialize AIDLC in a project directory."""
    project_root = Path(args.project or ".").resolve()
    aidlc_dir = project_root / ".aidlc"

    _print_banner(version)

    if aidlc_dir.exists() and not args.with_docs:
        print(f"{_yellow('!')} .aidlc/ already exists at {project_root}")
        print(
            f"  Use {_cyan('aidlc run --resume')} to resume, or delete .aidlc/ to start fresh."
        )
        return

    if not aidlc_dir.exists():
        from .config_detect import detect_config, describe_detected

        detected = detect_config(project_root)
        write_default_config(aidlc_dir, detected_overrides=detected)

        desc = describe_detected(detected)
        if desc:
            print()
            print(f"  {_bold('Auto-detected:')}")
            for line in desc:
                print(f"    {_green('+')} {line}")

        gitignore = project_root / ".gitignore"
        ignore_entry = "\n# AIDLC working directory\n.aidlc/runs/\n.aidlc/reports/\n"
        if gitignore.exists():
            content = gitignore.read_text()
            if ".aidlc/" not in content:
                with open(gitignore, "a") as gitignore_file:
                    gitignore_file.write(ignore_entry)
        else:
            gitignore.write_text(ignore_entry.lstrip())

        print(f"{_green('+')} Initialized .aidlc/ in {project_root}")
        print(f"  {_dim('Config:')}  {aidlc_dir / 'config.json'}")
        print(f"  {_dim('Issues:')}  {aidlc_dir / 'issues/'}")

    if args.with_docs:
        template_dir = _get_template_dir()
        if not template_dir.exists():
            print(f"{_red('x')} Template directory not found at {template_dir}")
            print(
                "  This can happen if aidlc was installed from a wheel without package data."
            )
            sys.exit(1)

        copied = 0
        skipped = 0
        for src_file in sorted(template_dir.rglob("*")):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(template_dir)
            dest = project_root / rel
            if dest.exists():
                skipped += 1
                print(f"  {_dim('skip')} {rel} (already exists)")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
            copied += 1
            print(f"  {_green('+')} {rel}")

        print()
        print(f"  {_green(str(copied))} template files copied, {skipped} skipped (already exist)")

    print()
    print("Next steps:")
    if args.with_docs:
        print(f"  1. Edit {_cyan('ARCHITECTURE.md')} and {_cyan('DESIGN.md')} as needed")
        print(
            f"  2. Optionally edit {_cyan('ROADMAP.md')} if you want phase-based planning"
        )
        print(f"  3. Run {_cyan('aidlc run')}")
    else:
        print(
            "  1. Add architecture/design context docs (README.md, ARCHITECTURE.md, DESIGN.md)"
        )
        print(f"     Or run {_cyan('aidlc init --with-docs')} to copy templates")
        print("  2. ROADMAP.md is optional and can be generated/refined later")
        print(f"  3. Run {_cyan('aidlc run')}")

    # Provider setup wizard (--providers flag)
    if getattr(args, "providers", False) is True:
        import json
        import logging
        from .routing.engine import ProviderRouter

        config_path = aidlc_dir / "config.json"
        print()
        print(f"  {_bold('--- Provider Setup ---')}")
        print()

        # Step 1: config wizard for provider enable/disable
        _cmd_config_wizard(config_path)

        # Step 2: validate all enabled providers
        print()
        print(f"  {_bold('Validating providers...')}")
        print()

        with open(config_path) as f:
            current_config = json.load(f)

        config_loaded = load_config(project_root=str(project_root))
        logger = logging.getLogger("aidlc.init.providers")
        router = ProviderRouter(config_loaded, logger)

        providers_cfg = current_config.get("providers", {})
        for pname, pcfg in providers_cfg.items():
            if not isinstance(pcfg, dict) or not pcfg.get("enabled", False):
                continue
            adapter = router._adapters.get(pname)
            if adapter is None:
                continue
            health = adapter.validate_health()
            icon = _green("●") if health.is_usable else _red("●")
            print(f"  {icon} {pname}: {health.status.value}")

            if not health.is_usable:
                try:
                    raw = input(f"\n  {_yellow('!')} {pname} is not authenticated. Run auth now? (y/n) [y]: ").strip().lower()
                except EOFError:
                    raw = "n"
                if raw in ("", "y", "yes"):
                    print()
                    _cmd_provider_auth(pname, config_loaded, show_health=False)

        print()
        print(f"  {_green('Provider setup complete.')}")
        print(f"  Check status anytime: {_cyan('aidlc provider list')}")


def cmd_audit(args: argparse.Namespace, version: str) -> None:
    """Run standalone code audit."""
    from .auditor import CodeAuditor
    from .logger import setup_logger
    from .routing import ProviderRouter

    project_root = Path(args.project or ".").resolve()
    config = load_config(config_path=getattr(args, "config", None), project_root=str(project_root))
    depth = "full" if args.full else "quick"

    _print_banner(version)
    print(f"Auditing {_cyan(str(project_root))} ({depth} scan)...")
    print()

    (project_root / ".aidlc").mkdir(exist_ok=True)
    logger = setup_logger("audit", project_root / ".aidlc", verbose=args.verbose)

    cli = None
    if depth == "full":
        cli = ProviderRouter(config, logger)
        if not cli.check_available():
            print(f"{_red('x')} Claude CLI not available.")
            print("  Use quick scan (without --full) or install Claude CLI.")
            sys.exit(1)

    auditor = CodeAuditor(project_root=project_root, config=config, cli=cli, logger=logger)
    result = auditor.run(depth=depth)

    print(f"{_green('Audit complete')} ({depth} scan)")
    print()
    print(f"  {_bold('Project type:')}   {result.project_type}")
    print(f"  {_bold('Frameworks:')}     {', '.join(result.frameworks) or _dim('none detected')}")
    print(f"  {_bold('Modules:')}        {len(result.modules)}")
    print(f"  {_bold('Entry points:')}   {len(result.entry_points)}")
    print(f"  {_bold('Source files:')}   {result.source_stats.get('total_files', 0)}")
    print(f"  {_bold('Total lines:')}    {result.source_stats.get('total_lines', 0):,}")
    if result.test_coverage:
        tc = result.test_coverage
        est = tc.estimated_coverage
        fw = f" ({tc.test_framework})" if tc.test_framework else ""
        print(f"  {_bold('Test coverage:')}  {est}{fw}")
    if result.tech_debt:
        print(f"  {_bold('Tech debt:')}      {len(result.tech_debt)} markers")
    print()
    print(f"  {_bold('Generated:')} {', '.join(result.generated_docs)}")
    if result.conflicts:
        print()
        print(f"  {_yellow('!')} Found {len(result.conflicts)} conflict(s) with existing docs.")
        print(f"    Review: {_cyan(str(project_root / '.aidlc' / 'CONFLICTS.md'))}")
    else:
        print(f"  {_green('No conflicts')} with existing docs.")
    print()
    print(f"Next: run {_cyan('aidlc run')} to plan and implement, or {_cyan('aidlc run --audit')} to re-audit first.")


def cmd_improve(args: argparse.Namespace, version: str) -> None:
    """Run targeted improvement cycle."""
    from .improve import ImprovementCycle
    from .logger import setup_logger
    from .routing import ProviderRouter
    from .scanner import ProjectScanner

    project_root = Path(args.project or ".").resolve()
    config = load_config(config_path=getattr(args, "config", None), project_root=str(project_root))
    _print_banner(version)

    concern = args.concern
    if not concern:
        print("  What would you like to improve?")
        examples = 'Examples: "economy feels flat", "customers look robotic", "needs better UI"'
        print(f"  {_dim(examples)}")
        try:
            concern = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not concern:
            print(f"  {_yellow('!')} No concern provided.")
            return

    (project_root / ".aidlc").mkdir(exist_ok=True)
    logger = setup_logger("improve", project_root / ".aidlc", verbose=args.verbose)
    cli = ProviderRouter(config, logger)
    if not cli.check_available() and not config.get("dry_run"):
        print(f"{_red('x')} Claude CLI not available.")
        sys.exit(1)

    scanner = ProjectScanner(project_root, config)
    scan_result = scanner.scan()
    project_context = scanner.build_context_prompt(scan_result)

    cycle = ImprovementCycle(project_root, config, cli, logger, project_context)
    result = cycle.run(user_concern=concern, auto_implement=not getattr(args, "plan_only", False))
    if result.get("status") == "complete":
        print(f"\n  {result['implemented']} improvement(s) applied.")
    print()


def cmd_plan(args: argparse.Namespace, version: str) -> None:
    """Run interactive planning session."""
    from .plan_session import PlanSession
    from .logger import setup_logger
    from .routing import ProviderRouter

    project_root = Path(args.project or ".").resolve()
    config = load_config(config_path=getattr(args, "config", None), project_root=str(project_root))
    _print_banner(version)

    (project_root / ".aidlc").mkdir(exist_ok=True)
    logger = setup_logger("plan", project_root / ".aidlc", verbose=args.verbose)
    cli = ProviderRouter(config, logger)
    if not cli.check_available() and not config.get("dry_run"):
        print(f"{_red('x')} Claude CLI not available.")
        sys.exit(1)

    session = PlanSession(project_root, config, cli, logger)
    session.run(
        skip_wizard=getattr(args, "skip_wizard", False),
        wizard_only=getattr(args, "wizard_only", False),
        review_only=getattr(args, "review", False),
    )


def cmd_finalize(args: argparse.Namespace, version: str) -> None:
    """Run finalization passes standalone."""
    from .finalizer import Finalizer
    from .logger import setup_logger
    from .scanner import ProjectScanner
    from .state_manager import save_state as _save

    project_root = Path(args.project or ".").resolve()
    config = load_config(config_path=getattr(args, "config", None), project_root=str(project_root))
    _print_banner(version)

    runs_dir = project_root / ".aidlc" / "runs"
    if not runs_dir.exists():
        print(f"{_red('x')} No AIDLC runs found. Run {_cyan('aidlc run')} first.")
        sys.exit(1)
    run_dir = find_latest_run(runs_dir)
    if not run_dir:
        print(f"{_red('x')} No runs found.")
        sys.exit(1)

    state = load_state(run_dir)
    logger = setup_logger(state.run_id, run_dir, verbose=args.verbose)
    from .routing import ProviderRouter
    cli = ProviderRouter(config, logger)
    if not cli.check_available() and not config.get("dry_run"):
        print(f"{_red('x')} Claude CLI not available.")
        sys.exit(1)

    passes_str = getattr(args, "passes", None)
    passes = passes_str.split(",") if passes_str else None

    scanner = ProjectScanner(project_root, config)
    scan_result = scanner.scan()
    project_context = scanner.build_context_prompt(scan_result)

    print(f"Finalizing run {_cyan(state.run_id)}...")
    print(f"  Passes: {', '.join(passes) if passes else 'all'}")
    print()

    finalizer = Finalizer(state, run_dir, config, cli, project_context, logger)
    finalizer.run(passes=passes)
    _save(state, run_dir)

    print()
    print(f"{_green('Finalization complete')}")
    print(f"  Passes completed: {', '.join(state.finalize_passes_completed)}")
    print(f"  Reports: {_cyan('docs/audits/')}")
    print(f"  Futures: {_cyan('AIDLC_FUTURES.md')}")


def cmd_status(args: argparse.Namespace, version: str) -> None:
    """Show latest run status."""
    project_root = Path(args.project or ".").resolve()
    runs_dir = project_root / ".aidlc" / "runs"
    _print_banner(version)

    if not runs_dir.exists():
        print(f"No AIDLC runs found. Run {_cyan('aidlc init')} first.")
        return
    run_dir = find_latest_run(runs_dir)
    if not run_dir:
        print("No runs found.")
        return

    state = load_state(run_dir)
    plan_h = state.plan_elapsed_seconds / 3600
    plan_budget_h = state.plan_budget_seconds / 3600
    elapsed_h = state.elapsed_seconds / 3600
    console_h = state.console_seconds / 3600

    status_str = state.status.value
    if state.status.value == "complete":
        status_str = _green(status_str)
    elif state.status.value == "failed":
        status_str = _red(status_str)
    elif state.status.value == "paused":
        status_str = _yellow(status_str)
    elif state.status.value == "running":
        status_str = _cyan(status_str)

    print(f"  {_bold('Run:')}       {state.run_id}")
    print(f"  {_bold('Status:')}    {status_str}")
    print(f"  {_bold('Phase:')}     {state.phase.value}")
    print(f"  {_bold('Planning:')}  {plan_h:.1f}h / {plan_budget_h:.0f}h budget")
    print(f"  {_bold('Time:')}      {elapsed_h:.1f}h Claude CLI, {console_h:.1f}h console")
    print(f"  {_bold('Issues:')}    {state.total_issues} total, {state.issues_implemented} implemented, {state.issues_verified} verified, {state.issues_failed} failed")

    if state.audit_depth != "none":
        print(f"  {_bold('Audit:')}     {state.audit_depth} ({'complete' if state.audit_completed else 'incomplete'})")
    if state.stop_reason:
        print(f"  {_bold('Stopped:')}   {state.stop_reason}")

    if state.issues:
        print()
        print(f"  {_bold('Issues:')}")
        for issue in state.issues:
            status = issue.get("status", "pending")
            icon_map = {
                "pending": _dim(" "),
                "in_progress": _cyan(">"),
                "implemented": _green("+"),
                "verified": _green("v"),
                "failed": _red("x"),
                "blocked": _yellow("!"),
                "skipped": _dim("-"),
            }
            icon = icon_map.get(status, "?")
            title = issue.get("title", "untitled")
            print(f"    [{icon}] {issue['id']}: {title} {_dim(f'({status})')}")


# ---------------------------------------------------------------------------
# Accounts commands (moved to cli.accounts)
# ---------------------------------------------------------------------------

def cmd_accounts(args: argparse.Namespace, version: str) -> None:
    """Manage provider accounts."""
    from .cli.accounts import cmd_accounts as _cmd_accounts
    return _cmd_accounts(args, version)


# ---------------------------------------------------------------------------
# Provider enable / disable commands
# ---------------------------------------------------------------------------

_KNOWN_PROVIDERS = {"claude", "copilot", "openai"}


def cmd_provider(args: argparse.Namespace, version: str) -> None:
    """Enable or disable a provider in the project config."""
    subcmd = getattr(args, "provider_cmd", "list")
    _print_banner(version)

    project_root = Path(getattr(args, "project", None) or ".").resolve()
    config_path = project_root / ".aidlc" / "config.json"

    if subcmd == "list" or subcmd is None:
        _cmd_provider_list(config_path)
    elif subcmd in ("enable", "disable"):
        name = getattr(args, "name", None)
        _cmd_provider_toggle(config_path, name, enabled=(subcmd == "enable"))
    elif subcmd == "auth":
        name = getattr(args, "name", None)
        if not name:
            print(f"{_red('x')} Provider name is required.")
            sys.exit(1)
        config = load_config(project_root=str(project_root))
        _cmd_provider_auth(name, config)
    elif subcmd == "reconnect":
        import json
        import logging
        from .providers.base import HealthStatus
        from .routing.engine import ProviderRouter

        if not config_path.exists():
            print(f"  {_yellow('!')} No .aidlc/config.json found. Run {_cyan('aidlc init')} first.")
            sys.exit(1)

        with open(config_path) as f:
            raw = json.load(f)

        providers_cfg = raw.get("providers", {})
        enabled_names = [
            n for n, c in providers_cfg.items()
            if isinstance(c, dict) and c.get("enabled", False)
        ]

        if not enabled_names:
            print("  No providers enabled.")
            return

        config = load_config(project_root=str(project_root))
        logger = logging.getLogger("aidlc.provider.reconnect")
        router = ProviderRouter(config, logger)

        print(f"  {_bold('Provider health check...')}")
        print()

        needs_auth = []
        for name in enabled_names:
            adapter = router._adapters.get(name)
            if adapter is None:
                print(f"  {_dim('○')} {name}: not loaded (disabled in routing)")
                continue
            health = adapter.validate_health()
            icon = _green("●") if health.is_usable else _red("●")
            print(f"  {icon} {name}: {health.status.value}")
            if not health.is_usable:
                needs_auth.append(name)

        print()
        if not needs_auth:
            print(f"  {_green('All providers healthy — nothing to reconnect.')}")
            return

        print(f"  {_yellow('!')} Reconnecting: {', '.join(needs_auth)}")
        print()
        for name in needs_auth:
            print(f"  {_bold(f'--- {name} ---')}")
            _cmd_provider_auth(name, config, show_health=False)
            print()
    else:
        print(f"Unknown provider subcommand: {subcmd}")
        sys.exit(1)


def _cmd_provider_list(config_path: Path) -> None:
    import json

    if not config_path.exists():
        print(f"  {_yellow('!')} No .aidlc/config.json found. Run {_cyan('aidlc init')} first.")
        return

    with open(config_path) as f:
        config = json.load(f)

    providers = config.get("providers", {})
    if not providers:
        print("  No provider config found.")
        return

    print(f"  {_bold('Providers')}")
    print()
    for pname, cfg in providers.items():
        enabled = cfg.get("enabled", True)
        status = _green("enabled") if enabled else _dim("disabled")
        model = cfg.get("default_model", "?")
        bullet = "●" if enabled else "○"
        print(f"  {bullet} {_bold(pname):<20}  {status}  (model: {model})")
    print()
    print(
        f"  Toggle: {_cyan('aidlc provider enable <name>')} / {_cyan('aidlc provider disable <name>')}"
    )


def _cmd_provider_toggle(config_path: Path, name: str, enabled: bool) -> None:
    import json

    if not name:
        print(f"{_red('x')} Provider name is required.")
        sys.exit(1)

    if name not in _KNOWN_PROVIDERS:
        print(f"{_yellow('!')} Unknown provider '{name}'. Known: {', '.join(sorted(_KNOWN_PROVIDERS))}")
        sys.exit(1)

    if not config_path.exists():
        print(f"  {_yellow('!')} No .aidlc/config.json found. Run {_cyan('aidlc init')} first.")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    config.setdefault("providers", {}).setdefault(name, {})["enabled"] = enabled

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    action = _green("enabled") if enabled else _dim("disabled")
    icon = _green("+") if enabled else "-"
    print(f"  {icon} Provider '{_bold(name)}' {action}")
    print(f"  Config: {_cyan(str(config_path))}")


# Auth commands per provider: (binary, auth_args, fallback_instructions)
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


def _cmd_provider_auth(name: str, config: dict, show_health: bool = True) -> None:
    """Run vendor login flow for a provider, preserving TTY."""
    import logging
    import subprocess as _sp
    from .providers.base import HealthStatus
    from .routing.engine import ProviderRouter

    if name not in _KNOWN_PROVIDERS:
        print(f"{_red('x')} Unknown provider '{name}'. Known: {', '.join(sorted(_KNOWN_PROVIDERS))}")
        sys.exit(1)

    auth_cmd, fallback_instructions = _PROVIDER_AUTH_COMMANDS[name]
    if name == "copilot":
        providers_cfg = config.get("providers", {})
        provider_cfg = providers_cfg.get("copilot", {}) if isinstance(providers_cfg, dict) else {}
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
        print(f"  {_yellow('!')} Provider '{name}' is disabled — enable it first with:")
        print(f"    {_cyan(f'aidlc provider enable {name}')}")
        return

    if show_health:
        before = adapter.validate_health()
        before_icon = _green("●") if before.is_usable else _yellow("●")
        print(f"  {before_icon} {name} health before: {before.status.value}")
        if before.is_usable:
            print(f"  {_dim('Already authenticated. Proceeding anyway...')}")
        print()

    print(f"  {_bold(f'Launching {name} auth flow...')}")
    print(f"  {_dim('(running: ' + ' '.join(auth_cmd) + ')')}")
    print()

    try:
        result = _sp.run(auth_cmd)
        exit_code = result.returncode
    except FileNotFoundError:
        print(f"\n  {_red('x')} {name} CLI not found on PATH.")
        print(f"  {fallback_instructions}")
        return

    print()
    if exit_code == 0:
        after = adapter.validate_health()
        after_icon = _green("●") if after.is_usable else _red("●")
        print(f"  {after_icon} {name} health after: {after.status.value}")
        if after.is_usable:
            print(f"  {_green('Auth successful.')}")
        else:
            print(f"  {_yellow('!')} Auth command exited 0 but health check still failing: {after.message}")
    else:
        print(f"  {_yellow('!')} Auth command exited with code {exit_code}.")
        print(f"  Manual fallback: {fallback_instructions}")


# ---------------------------------------------------------------------------
# Config show / effective runtime preview
# ---------------------------------------------------------------------------

def cmd_config_show(args: argparse.Namespace, version: str) -> None:
    """Show effective runtime config and routing preview."""
    subcmd = getattr(args, "config_cmd", "show")
    project_root = Path(getattr(args, "project", None) or ".").resolve()
    config_path = project_root / ".aidlc" / "config.json"

    _print_banner(version)

    if subcmd == "edit":
        _cmd_config_edit(config_path)
        return

    if subcmd == "wizard":
        _cmd_config_wizard(config_path)
        return

    config = load_config(
        config_path=getattr(args, "config", None),
        project_root=str(project_root),
    )

    effective = getattr(args, "effective", False)

    if effective:
        _print_effective_preview(config, project_root)
    else:
        _print_config_summary(config)


def _cmd_config_edit(config_path: Path) -> None:
    """Open .aidlc/config.json in $EDITOR."""
    import os
    import subprocess as _sp

    if not config_path.exists():
        print(f"  {_yellow('!')} No .aidlc/config.json found. Run {_cyan('aidlc init')} first.")
        sys.exit(1)

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    print(f"  Opening {_cyan(str(config_path))} in {editor}...")
    print()
    _sp.run([editor, str(config_path)])


def _cmd_config_wizard(config_path: Path) -> None:
    """Interactive config wizard — prompts for key settings, writes back to config.json."""
    import json

    if not config_path.exists():
        print(f"  {_yellow('!')} No .aidlc/config.json found. Run {_cyan('aidlc init')} first.")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    print(f"  {_bold('Config Wizard')} — press Enter to keep the current value, Ctrl-C to abort.")
    print()

    changes: dict = {}

    def _prompt(label: str, key: str, current) -> None:
        display = str(current) if current is not None else _dim("(not set)")
        try:
            raw = input(f"  {label} [{display}]: ").strip()
        except EOFError:
            raw = ""
        if raw:
            changes[key] = raw

    def _prompt_choice(label: str, key: str, choices: list[str], current: str) -> None:
        opts = "/".join(
            _cyan(c) if c == current else c for c in choices
        )
        try:
            raw = input(f"  {label} ({opts}) [{current}]: ").strip().lower()
        except EOFError:
            raw = ""
        if raw and raw in choices:
            changes[key] = raw
        elif raw:
            print(f"    {_yellow('!')} Invalid choice '{raw}', keeping '{current}'.")

    def _prompt_bool(label: str, key: str, current: bool) -> None:
        display = "y" if current else "n"
        try:
            raw = input(f"  {label} (y/n) [{display}]: ").strip().lower()
        except EOFError:
            raw = ""
        if raw in ("y", "yes"):
            changes[key] = True
        elif raw in ("n", "no"):
            changes[key] = False

    # --- Routing strategy ---
    _prompt_choice(
        "Routing strategy",
        "routing_strategy",
        ["balanced", "cheapest", "best_quality", "custom"],
        config.get("routing_strategy", "balanced"),
    )

    # --- Plan budget ---
    _prompt("Plan budget (hours)", "plan_budget_hours", config.get("plan_budget_hours", 4))

    print()
    print(f"  {_bold('Providers')}")

    providers = config.get("providers", {})
    provider_changes: dict = {}

    for pname in ["claude", "copilot", "openai"]:
        pcfg = providers.get(pname, {})
        enabled = pcfg.get("enabled", pname == "claude")
        print()
        print(f"  {_bold(pname)}")

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
    print(f"  {_bold('Summary of changes:')}")
    print()

    had_changes = False
    if changes:
        for k, v in changes.items():
            print(f"    {_cyan(k)}: {_dim(str(config.get(k, '(not set)')))} → {_green(str(v))}")
            had_changes = True

    for pname, new_pcfg in provider_changes.items():
        old_pcfg = providers.get(pname, {})
        for field in ("enabled", "cli_command", "default_model"):
            old_val = old_pcfg.get(field)
            new_val = new_pcfg.get(field)
            if old_val != new_val:
                print(f"    {_cyan(f'providers.{pname}.{field}')}: {_dim(str(old_val))} → {_green(str(new_val))}")
                had_changes = True

    if not had_changes:
        print(f"    {_dim('No changes.')}")
        return

    print()
    try:
        confirm = input("  Save? (y/n) [y]: ").strip().lower()
    except EOFError:
        confirm = "y"

    if confirm in ("", "y", "yes"):
        for k, v in changes.items():
            # Coerce numeric fields
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

        print(f"  {_green('+')} Config saved to {_cyan(str(config_path))}")
    else:
        print(f"  {_dim('Aborted — no changes written.')}")


def _print_config_summary(config: dict) -> None:
    """Print key config values."""
    print(f"  {_bold('Active Configuration')}")
    print()
    print(f"  {_bold('Runtime profile:')}    {config.get('runtime_profile', 'standard')}")
    print(f"  {_bold('Routing strategy:')}   {_cyan(config.get('routing_strategy', 'balanced'))}")
    print(f"  {_bold('Plan budget:')}        {config.get('plan_budget_hours', 4)}h")
    print(f"  {_bold('Dry run:')}            {config.get('dry_run', False)}")
    print()

    providers = config.get("providers", {})
    if providers:
        print(f"  {_bold('Providers:')}")
        for pid, pcfg in providers.items():
            if not isinstance(pcfg, dict):
                continue
            enabled = _green("enabled") if pcfg.get("enabled", False) else _dim("disabled")
            print(f"    {pid}: {enabled}  cmd={pcfg.get('cli_command', '?')}  default_model={pcfg.get('default_model', '?')}")
        print()

    # Show per-phase models for each enabled provider (or all if none enabled)
    enabled_providers = {pid: pcfg for pid, pcfg in providers.items()
                         if isinstance(pcfg, dict) and pcfg.get("enabled", False)}
    display_providers = enabled_providers if enabled_providers else providers

    if display_providers:
        print(f"  {_bold('Phase Models:')}")
        for pid, pcfg in display_providers.items():
            if not isinstance(pcfg, dict):
                continue
            phase_models = pcfg.get("phase_models") or {}
            default_model = pcfg.get("default_model", "?")
            print(f"    {_cyan(pid)}  (default: {default_model})")
            phases = ["planning", "research", "implementation", "implementation_complex", "finalization", "audit"]
            for phase in phases:
                model = phase_models.get(phase, default_model)
                print(f"      {phase:<30} {model}")
        print()
    elif not providers:
        # Legacy fallback: no providers config at all
        print(f"  {_bold('Models (legacy keys):')}")
        print(f"    claude_model:                   {config.get('claude_model', '?')}")
        print(f"    claude_model_planning:          {config.get('claude_model_planning', '?')}")
        print(f"    claude_model_implementation:    {config.get('claude_model_implementation', '?')}")
        print(f"    claude_model_implementation_complex: {config.get('claude_model_implementation_complex', '?')}")
        print(f"    claude_model_finalization:      {config.get('claude_model_finalization', '?')}")
        print()

    print(f"  Tip: run {_cyan('aidlc config show --effective')} for a full routing preview.")


def _print_effective_preview(config: dict, project_root: Path) -> None:
    """Print a plain-English effective runtime preview."""
    import logging
    from .routing import ProviderRouter
    from .accounts import AccountManager

    logger = logging.getLogger("aidlc.config.preview")
    router = ProviderRouter(config, logger)

    # Wire in account manager if available
    try:
        manager = AccountManager()
        router.set_account_manager(manager)
    except Exception:
        manager = None

    print(f"  {_bold('Effective Runtime Preview')}")
    print(f"  Project: {_cyan(str(project_root))}")
    print(f"  Strategy: {_cyan(config.get('routing_strategy', 'balanced'))}")
    print()

    # Provider health summary
    print(f"  {_bold('Provider Health:')}")
    providers_cfg = config.get("providers", {})
    for provider_id, pcfg in (providers_cfg.items() if isinstance(providers_cfg, dict) else []):
        if not isinstance(pcfg, dict):
            continue
        adapter = router._adapters.get(provider_id)
        if adapter:
            health = adapter.validate_health()
            health_icon = _green("●") if health.is_usable else _red("●")
            print(f"    {health_icon} {provider_id}: {health.status.value} — {health.message[:60]}")
        else:
            print(f"    {_dim('○')} {provider_id}: {_dim('not loaded')}")
    print()

    # Per-phase routing preview
    preview = router.resolve_preview()
    print(f"  {_bold('Phase Routing (what will run):')}")
    print(f"  {'Phase':<28} {'Provider':<10} {'Account':<20} {'Model':<25}")
    print(f"  {'-'*28} {'-'*10} {'-'*20} {'-'*25}")
    for phase, decision in preview.items():
        account_label = decision.account_id or _dim("(default auth)")
        fallback_marker = _yellow(" [fallback]") if decision.fallback else ""
        print(
            f"  {phase:<28} {decision.provider_id:<10} {account_label:<20} "
            f"{decision.model:<25}{fallback_marker}"
        )
    print()

    # Accounts summary
    if manager:
        accounts = manager.list()
        if accounts:
            print(f"  {_bold('Accounts:')}")
            for acc in accounts:
                icon = _green("v") if acc.health_status == "healthy" else _yellow("-")
                premium_tag = _yellow(" [premium]") if acc.is_premium else ""
                print(
                    f"    [{icon}] {acc.account_id} ({acc.provider_id}) "
                    f"tier={acc.membership_tier.value}{premium_tag}"
                )
            print()


# ---------------------------------------------------------------------------
# Usage command
# ---------------------------------------------------------------------------

def cmd_usage(args: argparse.Namespace, version: str) -> None:
    """Show token/cost usage table across runs."""
    from datetime import datetime, timezone

    project_root = Path(getattr(args, "project", None) or ".").resolve()
    runs_dir = project_root / ".aidlc" / "runs"
    by = getattr(args, "by", "provider")
    last_n = getattr(args, "last", 1)
    since_str = getattr(args, "since", None)

    _print_banner(version)

    if not runs_dir.exists():
        print(f"  {_dim('No runs found at')} {runs_dir}")
        print(f"  Run {_cyan('aidlc run --dry-run')} to create a run first.")
        return

    # Collect run directories sorted newest-first
    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and (d / "state.json").exists()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )

    if last_n > 0:
        run_dirs = run_dirs[:last_n]

    if since_str:
        try:
            since_dt = datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            run_dirs = [
                d for d in run_dirs
                if datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc) >= since_dt
            ]
        except ValueError:
            print(f"  {_yellow('!')} Invalid --since date '{since_str}'. Use YYYY-MM-DD.")
            sys.exit(1)

    if not run_dirs:
        print("  No matching runs found.")
        return

    # Accumulate usage across selected runs
    totals: dict[str, dict] = {}  # key -> {calls, succeeded, input_tok, output_tok, cost_*}
    run_count = 0

    for run_dir in run_dirs:
        try:
            state = load_state(run_dir)
        except Exception:
            continue
        run_count += 1

        if by == "provider":
            pdata = getattr(state, "provider_account_usage", {}) or {}
            if pdata:
                for provider_id, acct_map in pdata.items():
                    for _acct_id, usage in acct_map.items():
                        _acc(totals, provider_id, usage)
            else:
                _acc_legacy(totals, "claude", state)

        elif by == "account":
            pdata = getattr(state, "provider_account_usage", {}) or {}
            if pdata:
                for provider_id, acct_map in pdata.items():
                    for acct_id, usage in acct_map.items():
                        _acc(totals, f"{provider_id}/{acct_id}", usage)
            else:
                _acc_legacy(totals, "claude/default", state)

        elif by == "phase":
            phase_data = getattr(state, "phase_usage", {}) or {}
            if phase_data:
                for phase, usage in phase_data.items():
                    _acc(totals, phase, usage)
            else:
                _acc_legacy(totals, "all_phases", state)

        elif by == "model":
            model_data = getattr(state, "claude_model_usage", {}) or {}
            if model_data:
                for model, usage in model_data.items():
                    _acc(totals, model, usage)
            else:
                _acc_legacy(totals, "unknown_model", state)

    if not totals:
        print("  No usage data found in selected run(s).")
        return

    col_key = max((len(k) for k in totals), default=10) + 2
    col_key = max(col_key, 22)
    header_key = by.capitalize() if by != "account" else "Provider/Account"

    print(f"  {_bold(f'Usage — last {run_count} run(s), grouped by {by}')}")
    print()
    print(
        f"  {header_key:<{col_key}} {'Calls':>7} {'Success':>8} "
        f"{'Input tok':>11} {'Output tok':>11} {'Est. USD':>10}"
    )
    print(f"  {'-' * col_key} {'-'*7} {'-'*8} {'-'*11} {'-'*11} {'-'*10}")

    grand = {"calls": 0, "succeeded": 0, "input": 0, "output": 0, "cost": 0.0}
    for key, row in sorted(totals.items()):
        cost = row.get("cost_usd_exact") or row.get("cost_usd_estimated") or 0.0
        print(
            f"  {key:<{col_key}} {row['calls']:>7} {row['succeeded']:>8} "
            f"{row['input_tokens']:>11,} {row['output_tokens']:>11,} "
            f"${cost:>9.4f}"
        )
        grand["calls"] += row["calls"]
        grand["succeeded"] += row["succeeded"]
        grand["input"] += row["input_tokens"]
        grand["output"] += row["output_tokens"]
        grand["cost"] += cost

    print(f"  {'─' * col_key} {'─'*7} {'─'*8} {'─'*11} {'─'*11} {'─'*10}")
    print(
        f"  {_bold('TOTAL'):<{col_key + 7}} {grand['calls']:>7} {grand['succeeded']:>8} "
        f"{grand['input']:>11,} {grand['output']:>11,} "
        f"${grand['cost']:>9.4f}"
    )
    print()


def _acc(totals: dict, key: str, usage: dict) -> None:
    """Accumulate a usage dict into totals."""
    if key not in totals:
        totals[key] = {
            "calls": 0, "succeeded": 0, "input_tokens": 0,
            "output_tokens": 0, "cost_usd_exact": 0.0, "cost_usd_estimated": 0.0,
        }
    t = totals[key]
    t["calls"] += usage.get("calls", 0)
    t["succeeded"] += usage.get("calls_succeeded", usage.get("succeeded", 0))
    t["input_tokens"] += usage.get("input_tokens", 0)
    t["output_tokens"] += usage.get("output_tokens", 0)
    t["cost_usd_exact"] += usage.get("cost_usd_exact", 0.0) or 0.0
    t["cost_usd_estimated"] += usage.get("cost_usd_estimated", 0.0) or 0.0


def _acc_legacy(totals: dict, key: str, state) -> None:
    """Accumulate legacy claude_* RunState fields into totals."""
    usage = {
        "calls": getattr(state, "claude_calls_total", 0),
        "calls_succeeded": getattr(state, "claude_calls_succeeded", 0),
        "input_tokens": getattr(state, "claude_total_input_tokens", 0),
        "output_tokens": getattr(state, "claude_output_tokens", 0),
        "cost_usd_exact": getattr(state, "claude_cost_usd_exact", 0.0) or 0.0,
        "cost_usd_estimated": getattr(state, "claude_cost_usd_estimated", 0.0) or 0.0,
    }
    _acc(totals, key, usage)
