"""CLI entry point for AIDLC.

Usage:
    aidlc precheck                         # check readiness, report missing docs
    aidlc init                             # set up .aidlc/ in current repo
    aidlc init --with-docs                 # also copy planning doc templates

    aidlc audit                            # quick scan of existing codebase
    aidlc audit --full                     # deep audit with Claude analysis

    aidlc run                              # full lifecycle (scan → plan → implement)
    aidlc run --audit                      # audit existing code first, then plan
    aidlc run --audit full                 # full audit, then plan
    aidlc run --plan-budget 2h             # custom planning budget
    aidlc run --plan-only                  # planning only, stop before implementation
    aidlc run --implement-only             # skip planning, implement existing issues
    aidlc run --resume                     # resume a paused/interrupted run
    aidlc run --dry-run                    # simulate without Claude calls

    aidlc status                           # show status of latest run
"""

import argparse
import json
import shutil
import sys
import textwrap
from pathlib import Path

from . import __version__
from .config import load_config
from .runner import run_full
from .state_manager import find_latest_run, load_state

# ANSI color helpers — degrade gracefully when piped
_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if _USE_COLOR else text


def _green(text: str) -> str:
    return f"\033[32m{text}\033[0m" if _USE_COLOR else text


def _yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m" if _USE_COLOR else text


def _red(text: str) -> str:
    return f"\033[31m{text}\033[0m" if _USE_COLOR else text


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if _USE_COLOR else text


def _cyan(text: str) -> str:
    return f"\033[36m{text}\033[0m" if _USE_COLOR else text


# ── Helpers ──────────────────────────────────────────────────────────


def parse_budget(budget_str: str) -> float:
    """Parse a budget string like '4h', '30m', '2.5h' into hours."""
    budget_str = budget_str.strip().lower()
    if budget_str.endswith("h"):
        return float(budget_str[:-1])
    elif budget_str.endswith("m"):
        return float(budget_str[:-1]) / 60
    else:
        return float(budget_str)


def _get_template_dir() -> Path:
    """Return path to the bundled project_template/ directory.

    Checks inside the package first (works when installed from wheel),
    then falls back to the repo-level directory (works in editable/dev mode).
    """
    # Inside the package (installed mode)
    pkg_template = Path(__file__).parent / "project_template"
    if pkg_template.exists():
        return pkg_template
    # Repo root (editable dev mode)
    repo_template = Path(__file__).parent.parent / "project_template"
    if repo_template.exists():
        return repo_template
    raise FileNotFoundError("project_template directory not found")


def _print_banner():
    print(_bold("AIDLC") + _dim(f" v{__version__}") + " — AI Development Life Cycle")
    print()


# ── Precheck ─────────────────────────────────────────────────────────


def _print_precheck(result, project_root: Path, verbose: bool = False) -> None:
    """Print precheck results to console."""
    from .precheck import REQUIRED_DOCS, RECOMMENDED_DOCS, OPTIONAL_DOCS

    # Config auto-creation notice
    if result.config_created:
        print(f"  {_green('+')} Auto-created {_cyan('.aidlc/')} with default config")
        print(f"    Config: {_dim(str(project_root / '.aidlc' / 'config.json'))}")
        print(f"    Edit to set plan_budget_hours, run_tests_command, etc.")
        print()

    # Project detection
    if result.has_source_code:
        print(f"  {_bold('Project:')} {result.project_type} {_dim('(source code detected)')}")
        if "STATUS.md" not in [*result.optional_found, *result.recommended_found, *result.required_found]:
            print(f"    Tip: run {_cyan('aidlc audit')} to auto-generate STATUS.md + ARCHITECTURE.md")
    else:
        print(f"  {_bold('Project:')} {_dim('no source code detected (new project?)')}")
    print()

    # Required docs
    print(f"  {_bold('Required')}")
    for doc in REQUIRED_DOCS:
        if doc in result.required_found:
            print(f"    {_green('v')} {doc}")
        else:
            info = REQUIRED_DOCS[doc]
            print(f"    {_red('x')} {doc} — {info['purpose']}")
            for line in info["suggestion"].split("\n"):
                print(f"      {_dim(line)}")
    print()

    # Recommended docs
    print(f"  {_bold('Recommended')}")
    for doc in RECOMMENDED_DOCS:
        if doc in result.recommended_found:
            print(f"    {_green('v')} {doc}")
        else:
            info = RECOMMENDED_DOCS[doc]
            print(f"    {_yellow('-')} {doc} — {info['purpose']}")
            if verbose:
                for line in info["suggestion"].split("\n"):
                    print(f"      {_dim(line)}")
    print()

    # Optional docs
    print(f"  {_bold('Optional')}")
    for doc in OPTIONAL_DOCS:
        if doc in result.optional_found:
            print(f"    {_green('v')} {doc}")
        else:
            info = OPTIONAL_DOCS[doc]
            print(f"    {_dim('-')} {doc} — {info['purpose']}")
    print()

    # Summary
    found = len(result.required_found) + len(result.recommended_found) + len(result.optional_found)
    total = len(REQUIRED_DOCS) + len(RECOMMENDED_DOCS) + len(OPTIONAL_DOCS)
    score = result.score

    if score == "not ready":
        print(f"  {_bold('Readiness:')} {_red('NOT READY')} — missing required doc(s)")
        print(f"    Create the required files above, then run {_cyan('aidlc precheck')} again.")
    elif score == "excellent":
        print(f"  {_bold('Readiness:')} {_green('EXCELLENT')} ({found}/{total} docs) — ready to run")
    elif score == "good":
        print(f"  {_bold('Readiness:')} {_green('GOOD')} ({found}/{total} docs) — ready to run")
    else:
        print(f"  {_bold('Readiness:')} {_yellow('MINIMAL')} ({found}/{total} docs) — can run, but more docs = better plans")


def cmd_precheck(args: argparse.Namespace) -> None:
    """Run pre-flight readiness check."""
    from .precheck import run_precheck

    project_root = Path(args.project or ".").resolve()

    _print_banner()
    print(f"Checking {_cyan(str(project_root))}...")
    print()

    result = run_precheck(project_root, auto_init=True)
    _print_precheck(result, project_root, verbose=args.verbose)

    if not result.ready:
        sys.exit(1)


# ── Commands ─────────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize AIDLC in a project directory."""
    project_root = Path(args.project or ".").resolve()
    aidlc_dir = project_root / ".aidlc"

    _print_banner()

    if aidlc_dir.exists() and not args.with_docs:
        print(f"{_yellow('!')} .aidlc/ already exists at {project_root}")
        print(f"  Use {_cyan('aidlc run --resume')} to resume, or delete .aidlc/ to start fresh.")
        return

    # Create .aidlc structure
    if not aidlc_dir.exists():
        aidlc_dir.mkdir()
        (aidlc_dir / "issues").mkdir()
        (aidlc_dir / "runs").mkdir()
        (aidlc_dir / "reports").mkdir()

        # Write default config
        default_config = {
            "plan_budget_hours": 4,
            "checkpoint_interval_minutes": 15,
            "claude_model": "opus",
            "max_implementation_attempts": 3,
            "run_tests_command": None,
        }
        with open(aidlc_dir / "config.json", "w") as f:
            json.dump(default_config, f, indent=2)

        # Add to .gitignore
        gitignore = project_root / ".gitignore"
        ignore_entry = "\n# AIDLC working directory\n.aidlc/runs/\n.aidlc/reports/\n"
        if gitignore.exists():
            content = gitignore.read_text()
            if ".aidlc/" not in content:
                with open(gitignore, "a") as f:
                    f.write(ignore_entry)
        else:
            gitignore.write_text(ignore_entry.lstrip())

        print(f"{_green('+')} Initialized .aidlc/ in {project_root}")
        print(f"  {_dim('Config:')}  {aidlc_dir / 'config.json'}")
        print(f"  {_dim('Issues:')}  {aidlc_dir / 'issues/'}")

    # Copy template docs if requested
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
    print(f"Next steps:")
    if args.with_docs:
        print(f"  1. Edit {_cyan('ROADMAP.md')} with your phased delivery plan")
        print(f"  2. Edit {_cyan('ARCHITECTURE.md')} and {_cyan('DESIGN.md')} as needed")
        print(f"  3. Run {_cyan('aidlc run')}")
    else:
        print(f"  1. Add your planning docs (ROADMAP.md, ARCHITECTURE.md, etc.)")
        print(f"     Or run {_cyan('aidlc init --with-docs')} to copy templates")
        print(f"  2. Run {_cyan('aidlc run')}")


def cmd_audit(args: argparse.Namespace) -> None:
    """Run a standalone code audit."""
    from .auditor import CodeAuditor
    from .logger import setup_logger
    from .claude_cli import ClaudeCLI

    project_root = Path(args.project or ".").resolve()
    config = load_config(
        config_path=getattr(args, "config", None),
        project_root=str(project_root),
    )
    depth = "full" if args.full else "quick"

    _print_banner()
    print(f"Auditing {_cyan(str(project_root))} ({depth} scan)...")
    print()

    # Ensure .aidlc exists for output
    (project_root / ".aidlc").mkdir(exist_ok=True)

    logger = setup_logger("audit", project_root / ".aidlc", verbose=args.verbose)

    cli = None
    if depth == "full":
        cli = ClaudeCLI(config, logger)
        if not cli.check_available():
            print(f"{_red('x')} Claude CLI not available.")
            print(f"  Use quick scan (without --full) or install Claude CLI.")
            sys.exit(1)

    auditor = CodeAuditor(
        project_root=project_root,
        config=config,
        cli=cli,
        logger=logger,
    )
    result = auditor.run(depth=depth)

    # Print summary
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
        est = tc.estimated_coverage if hasattr(tc, "estimated_coverage") else "unknown"
        fw = f" ({tc.test_framework})" if hasattr(tc, "test_framework") and tc.test_framework else ""
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


def cmd_run(args: argparse.Namespace) -> None:
    """Run the full AIDLC lifecycle."""
    project_root = args.project or str(Path.cwd())
    project_path = Path(project_root).resolve()
    config = load_config(
        config_path=args.config,
        project_root=project_root,
    )

    skip_precheck_requested = getattr(args, "skip_precheck", False)
    strict_mode = bool(config.get("strict_mode", False))
    allow_skip_precheck = bool(config.get("allow_skip_precheck", True))
    if skip_precheck_requested and (strict_mode or not allow_skip_precheck):
        print(f"{_red('x')} --skip-precheck is disabled by configuration.")
        print(
            f"  strict_mode={strict_mode}, allow_skip_precheck={allow_skip_precheck}. "
            "Remove --skip-precheck or relax config."
        )
        sys.exit(2)

    skip_precheck = args.resume or args.implement_only or skip_precheck_requested
    if skip_precheck_requested and not (args.resume or args.implement_only):
        print(f"  {_yellow('!')} Running with precheck bypassed (--skip-precheck).")
        print()

    # Run precheck before lifecycle (unless resuming or implementing only)
    if not skip_precheck:
        from .precheck import run_precheck

        _print_banner()
        print(f"Pre-flight check...")
        print()

        result = run_precheck(project_path, auto_init=True)
        _print_precheck(result, project_path, verbose=args.verbose)

        if not result.ready:
            print()
            print(f"  Fix the required items above, then run {_cyan('aidlc run')} again.")
            print(f"  Or use {_cyan('aidlc run --skip-precheck')} to proceed anyway.")
            sys.exit(1)

        print()
        print(f"  Starting lifecycle...")
        print()

    if args.plan_budget:
        config["plan_budget_hours"] = parse_budget(args.plan_budget)
    if args.max_plan_cycles is not None:
        config["max_planning_cycles"] = args.max_plan_cycles
    if args.max_impl_cycles is not None:
        config["max_implementation_cycles"] = args.max_impl_cycles

    audit = getattr(args, "audit", None)

    run_full(
        config=config,
        resume=args.resume,
        dry_run=args.dry_run,
        plan_only=args.plan_only,
        implement_only=args.implement_only,
        verbose=args.verbose,
        audit=audit,
    )


def cmd_status(args: argparse.Namespace) -> None:
    """Show status of the latest run."""
    project_root = Path(args.project or ".").resolve()
    runs_dir = project_root / ".aidlc" / "runs"

    _print_banner()

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

    # Status color
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
    print(f"  {_bold('Elapsed:')}   {elapsed_h:.1f}h")
    print(f"  {_bold('Issues:')}    {state.total_issues} total, {state.issues_implemented} implemented, {state.issues_verified} verified, {state.issues_failed} failed")

    if state.audit_depth != "none":
        print(f"  {_bold('Audit:')}     {state.audit_depth} ({'complete' if state.audit_completed else 'incomplete'})")

    if state.stop_reason:
        print(f"  {_bold('Stopped:')}   {state.stop_reason}")

    # Show issue list
    if state.issues:
        print()
        print(f"  {_bold('Issues:')}")
        for d in state.issues:
            status = d.get("status", "pending")
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
            title = d.get("title", "untitled")
            print(f"    [{icon}] {d['id']}: {title} {_dim(f'({status})')}")


# ── Parser ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aidlc",
        description="AIDLC — AI Development Life Cycle. Drop into any repo, plan with a time budget, implement until done.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Quick start:
              aidlc precheck            Check what docs are needed
              aidlc init --with-docs    Set up AIDLC + copy planning templates
              aidlc run                 Plan and implement

            For existing repos:
              aidlc precheck            See what's missing
              aidlc audit               Generate STATUS.md from your code
              aidlc run --audit          Audit first, then plan and implement

            More info: https://github.com/highlyprofitable108/aidlc
        """),
    )
    parser.add_argument(
        "--version", "-V", action="version",
        version=f"aidlc {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # ── precheck ──
    precheck_parser = subparsers.add_parser(
        "precheck",
        help="Check project readiness",
        description="Verify docs and config are in place before running. Auto-creates .aidlc/ with defaults if missing.",
    )
    precheck_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    precheck_parser.add_argument("--verbose", "-v", action="store_true", help="Show suggestions for all missing docs")

    # ── init ──
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize AIDLC in a project",
        description="Set up .aidlc/ directory with config and optionally copy planning doc templates.",
    )
    init_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    init_parser.add_argument(
        "--with-docs", action="store_true",
        help="Copy planning doc templates (ROADMAP.md, ARCHITECTURE.md, etc.) into the project",
    )

    # ── audit ──
    audit_parser = subparsers.add_parser(
        "audit",
        help="Audit existing codebase",
        description="Analyze existing code and generate STATUS.md + ARCHITECTURE.md.",
    )
    audit_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    audit_parser.add_argument("--full", action="store_true", help="Full audit with Claude semantic analysis")
    audit_parser.add_argument("--config", "-c", help="Config file path")
    audit_parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    # ── run ──
    run_parser = subparsers.add_parser(
        "run",
        help="Run AIDLC lifecycle",
        description="Run the full scan -> plan -> implement -> report lifecycle.",
    )
    run_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    run_parser.add_argument("--config", "-c", help="Config file path")
    run_parser.add_argument("--plan-budget", help="Planning time budget (e.g., 4h, 30m)")
    run_parser.add_argument("--plan-only", action="store_true", help="Stop after planning")
    run_parser.add_argument("--implement-only", action="store_true", help="Skip planning, implement existing issues")
    run_parser.add_argument("--resume", action="store_true", help="Resume latest run")
    run_parser.add_argument(
        "--dry-run", action="store_true",
        help="No Claude CLI calls (cycles capped at 3)",
    )
    run_parser.add_argument("--max-plan-cycles", type=int, default=None, help="Max planning cycles (0=unlimited)")
    run_parser.add_argument("--max-impl-cycles", type=int, default=None, help="Max implementation cycles (0=unlimited)")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    run_parser.add_argument(
        "--audit", nargs="?", const="quick", choices=["quick", "full"],
        help="Audit existing code before planning (default: quick)",
    )
    run_parser.add_argument(
        "--skip-precheck", action="store_true",
        help="Skip the pre-flight readiness check",
    )

    # ── status ──
    status_parser = subparsers.add_parser(
        "status",
        help="Show latest run status",
        description="Display the status and issue breakdown of the most recent run.",
    )
    status_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")

    # Parse and dispatch
    args = parser.parse_args()

    if args.command == "precheck":
        cmd_precheck(args)
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "audit":
        cmd_audit(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()
        print()
        print(f"Run {_cyan('aidlc precheck')} to check readiness, or {_cyan('aidlc init')} to get started.")


if __name__ == "__main__":
    main()
