"""CLI command handlers and display helpers."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .cli.config_cmd import run_config_wizard
from .cli.display import (
    bold as _bold,
)
from .cli.display import (
    cyan as _cyan,
)
from .cli.display import (
    dim as _dim,
)
from .cli.display import (
    get_template_dir as _get_template_dir,
)
from .cli.display import (
    green as _green,
)
from .cli.display import (
    print_banner as _print_banner,
)
from .cli.display import (
    print_precheck as _print_precheck,
)
from .cli.display import (
    red as _red,
)
from .cli.display import (
    yellow as _yellow,
)
from .cli.provider import cmd_provider_auth
from .config import load_config, write_default_config
from .state_manager import find_latest_run, load_state


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
    braindump_path = project_root / "BRAINDUMP.md"

    _print_banner(version)

    aidlc_existed = aidlc_dir.exists()
    braindump_existed = braindump_path.exists()
    providers_flag = getattr(args, "providers", False) is True

    if aidlc_existed and braindump_existed and not providers_flag:
        print(f"{_yellow('!')} .aidlc/ and BRAINDUMP.md already exist at {project_root}")
        print(f"  Use {_cyan('aidlc run')} to start, or delete .aidlc/ to re-init.")
        return

    if not aidlc_existed:
        from .config_detect import describe_detected, detect_config

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

    # BRAINDUMP.md is required and the only doc we scaffold. Repo state is
    # authoritative for "what is"; BRAINDUMP is authoritative for "what next".
    # Never overwrite an existing file.
    if not braindump_existed:
        template_dir = _get_template_dir()
        src = template_dir / "BRAINDUMP.md"
        if src.exists():
            shutil.copy2(src, braindump_path)
            print(f"  {_green('+')} BRAINDUMP.md (edit this — it's what AIDLC builds from)")
        else:
            print(f"  {_red('x')} BRAINDUMP.md template missing at {src}")
            sys.exit(1)

    print()
    print("Next steps:")
    print(f"  1. Open {_cyan('BRAINDUMP.md')} and describe what this cycle should deliver")
    print(
        f"  2. Run {_cyan('aidlc run')}  (discovery + research run automatically before planning)"
    )

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
        run_config_wizard(config_path)

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
                    raw = (
                        input(
                            f"\n  {_yellow('!')} {pname} is not authenticated. Run auth now? (y/n) [y]: "
                        )
                        .strip()
                        .lower()
                    )
                except EOFError:
                    raw = "n"
                if raw in ("", "y", "yes"):
                    print()
                    cmd_provider_auth(pname, config_loaded, show_health=False)

        print()
        print(f"  {_green('Provider setup complete.')}")
        print(f"  Check status anytime: {_cyan('aidlc provider list')}")


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
    # ISSUE-010: surface stale RUNNING/INTERRUPTED runs as ABANDONED
    # so users see a yellow ABANDONED badge instead of stale RUNNING.
    from .state_manager import mark_abandoned_if_stale

    mark_abandoned_if_stale(state, run_dir)

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
    elif state.status.value in ("interrupted", "abandoned"):
        # Yellow signals "needs attention but recoverable" — same as paused.
        status_str = _yellow(status_str.upper())

    print(f"  {_bold('Run:')}       {state.run_id}")
    print(f"  {_bold('Status:')}    {status_str}")
    print(f"  {_bold('Phase:')}     {state.phase.value}")
    print(f"  {_bold('Planning:')}  {plan_h:.1f}h / {plan_budget_h:.0f}h budget")
    print(f"  {_bold('Time:')}      {elapsed_h:.1f}h Claude CLI, {console_h:.1f}h console")
    print(
        f"  {_bold('Issues:')}    {state.total_issues} total, {state.issues_implemented} implemented, {state.issues_verified} verified, {state.issues_failed} failed"
    )

    if state.audit_depth != "none":
        print(
            f"  {_bold('Audit:')}     {state.audit_depth} ({'complete' if state.audit_completed else 'incomplete'})"
        )
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
# Reset (ISSUE-008)
# ---------------------------------------------------------------------------


def _reset_targets(aidlc_dir: Path, *, keep_issues: bool, reset_all: bool) -> list[Path]:
    """Return the list of paths inside .aidlc/ that ``aidlc reset`` would delete.

    Default targets: runs/, reports/, session/, audit_result.json,
    planning_index.md, CONFLICTS.md, run.lock — plus issues/ unless
    ``keep_issues`` is set. With ``reset_all`` also includes config.json.
    Files/dirs that don't exist are filtered out by the caller.
    """
    targets = [
        aidlc_dir / "runs",
        aidlc_dir / "reports",
        aidlc_dir / "session",
        aidlc_dir / "audit_result.json",
        aidlc_dir / "planning_index.md",
        aidlc_dir / "CONFLICTS.md",
        aidlc_dir / "run.lock",
    ]
    if not keep_issues:
        targets.append(aidlc_dir / "issues")
    if reset_all:
        targets.append(aidlc_dir / "config.json")
    return targets


def cmd_reset(args: argparse.Namespace, version: str) -> None:
    """Clear stale .aidlc/ state (ISSUE-008)."""
    project_root = Path(args.project or ".").resolve()
    aidlc_dir = project_root / ".aidlc"
    _print_banner(version)

    if not aidlc_dir.exists():
        print(f"{_yellow('!')} No .aidlc/ at {project_root}")
        print(f"  Nothing to reset. Run {_cyan('aidlc init')} to start.")
        return

    keep_issues = bool(getattr(args, "keep_issues", False))
    reset_all = bool(getattr(args, "reset_all", False))
    dry_run = bool(getattr(args, "dry_run", False))
    auto_yes = bool(getattr(args, "yes", False))

    candidates = _reset_targets(aidlc_dir, keep_issues=keep_issues, reset_all=reset_all)
    existing = [p for p in candidates if p.exists()]

    print(f"  {_bold('Project:')} {project_root}")
    if reset_all:
        print(
            f"  {_red('!')} --all selected: config.json will be deleted; you'll need to re-init/re-auth."
        )
    if keep_issues:
        print(f"  {_dim('Preserving:')} .aidlc/issues/")

    print()
    if not existing:
        print(f"  {_green('Already clean')} — nothing matching reset targets.")
        return

    print(f"  {_bold('Will delete:')}")
    for p in existing:
        kind = "dir" if p.is_dir() else "file"
        print(f"    {_red('-')} {p.relative_to(project_root)} ({kind})")

    if dry_run:
        print()
        print(f"  {_cyan('Dry run')} — no changes made.")
        return

    print()
    if not auto_yes:
        try:
            response = input(f"  {_yellow('?')} Proceed with deletion? (y/N) [N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            response = ""
        if response not in ("y", "yes"):
            print(f"  {_dim('Aborted.')}")
            return

    deleted = 0
    failed = []
    for p in existing:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            deleted += 1
        except OSError as exc:
            failed.append((p, exc))

    print()
    print(f"  {_green(str(deleted))} item(s) deleted.")
    if failed:
        print(f"  {_red(str(len(failed)))} failed:")
        for p, exc in failed:
            print(f"    {_red('x')} {p.relative_to(project_root)} — {exc}")
    if not reset_all and (aidlc_dir / "config.json").exists():
        print(f"  {_dim('Preserved:')} .aidlc/config.json")


# ---------------------------------------------------------------------------
# Accounts commands (delegate to aidlc.cli.accounts)
# ---------------------------------------------------------------------------


def cmd_accounts(args: argparse.Namespace, version: str) -> None:
    """Manage provider accounts."""
    from .cli.accounts import cmd_accounts as _cmd_accounts

    return _cmd_accounts(args, version)
