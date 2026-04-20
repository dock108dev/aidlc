def cmd_validate(args: argparse.Namespace, version: str) -> None:
    """Run validation phase ad hoc on the latest run state."""
    from .validator import Validator
    from .logger import setup_logger
    from .state_manager import find_latest_run, load_state, save_state
    from .routing import ProviderRouter
    import sys
    from pathlib import Path

    project_root = Path(args.project or ".").resolve()
    runs_dir = project_root / ".aidlc" / "runs"
    if not runs_dir.exists():
        print("No AIDLC runs found. Run aidlc init first.")
        sys.exit(1)
    run_dir = find_latest_run(runs_dir)
    if not run_dir:
        print("No runs found.")
        sys.exit(1)
    state = load_state(run_dir)
    config_path = getattr(args, "config", None)
    from .config import load_config
    config = load_config(config_path=config_path, project_root=str(project_root))
    logger = setup_logger("validate", project_root / ".aidlc", verbose=args.verbose)
    cli = ProviderRouter(config, logger)
    print(f"Running validation on run {state.run_id}...")
    validator = Validator(state, run_dir, config, cli, None, logger)
    is_stable = validator.run()
    save_state(state, run_dir)
    if is_stable:
        print("Validation passed — project is stable")
    else:
        print(f"Validation incomplete: {state.validation_cycles} cycles, {state.validation_issues_created} fix issues created")
"""CLI command handlers and display helpers."""

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

    _print_banner(version)

    if aidlc_dir.exists() and not args.with_docs:
        print(f"{_yellow('!')} .aidlc/ already exists at {project_root}")
        print(f"  Use {_cyan('aidlc run --resume')} to resume, or delete .aidlc/ to start fresh.")
        return

    if not aidlc_dir.exists():
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

    if args.with_docs:
        template_dir = _get_template_dir()
        if not template_dir.exists():
            print(f"{_red('x')} Template directory not found at {template_dir}")
            print("  This can happen if aidlc was installed from a wheel without package data.")
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
        print(f"  2. Optionally edit {_cyan('ROADMAP.md')} if you want phase-based planning")
        print(f"  3. Run {_cyan('aidlc run')}")
    else:
        print("  1. Add architecture/design context docs (README.md, ARCHITECTURE.md, DESIGN.md)")
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
    print(
        f"Next: run {_cyan('aidlc run')} to plan and implement, or {_cyan('aidlc run --audit')} to re-audit first."
    )


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
    from .logger import setup_logger
    from .plan_session import PlanSession
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
# Accounts commands (delegate to aidlc.cli.accounts)
# ---------------------------------------------------------------------------


def cmd_accounts(args: argparse.Namespace, version: str) -> None:
    """Manage provider accounts."""
    from .cli.accounts import cmd_accounts as _cmd_accounts

    return _cmd_accounts(args, version)
