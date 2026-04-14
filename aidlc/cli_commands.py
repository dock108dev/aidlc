"""CLI command handlers and display helpers."""

import argparse
import json
import shutil
import sys
from pathlib import Path

from .config import load_config
from .state_manager import find_latest_run, load_state

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


def _get_template_dir() -> Path:
    """Return bundled project_template directory path."""
    pkg_template = Path(__file__).parent / "project_template"
    if pkg_template.exists():
        return pkg_template
    repo_template = Path(__file__).parent.parent / "project_template"
    if repo_template.exists():
        return repo_template
    raise FileNotFoundError("project_template directory not found")


def _print_banner(version: str):
    print(_bold("AIDLC") + _dim(f" v{version}") + " — AI Development Life Cycle")
    print()


def _print_precheck(result, project_root: Path, verbose: bool = False) -> None:
    """Print precheck results to console."""
    from .precheck import REQUIRED_DOCS, RECOMMENDED_DOCS, OPTIONAL_DOCS

    if result.config_created:
        print(f"  {_green('+')} Auto-created {_cyan('.aidlc/')} with default config")
        print(f"    Config: {_dim(str(project_root / '.aidlc' / 'config.json'))}")
        print("    Edit to set plan_budget_hours, run_tests_command, etc.")
        print()

    if result.has_source_code:
        print(f"  {_bold('Project:')} {result.project_type} {_dim('(source code detected)')}")
        if "STATUS.md" not in [
            *result.optional_found,
            *result.recommended_found,
            *result.required_found,
        ]:
            print(
                f"    Tip: run {_cyan('aidlc audit')} to auto-generate STATUS.md + ARCHITECTURE.md"
            )
    else:
        print(f"  {_bold('Project:')} {_dim('no source code detected (new project?)')}")
    print()

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

    print(f"  {_bold('Optional')}")
    for doc in OPTIONAL_DOCS:
        if doc in result.optional_found:
            print(f"    {_green('v')} {doc}")
        else:
            info = OPTIONAL_DOCS[doc]
            print(f"    {_dim('-')} {doc} — {info['purpose']}")
    print()

    found = sum(
        [len(result.required_found), len(result.recommended_found), len(result.optional_found)]
    )
    total = len(REQUIRED_DOCS) + len(RECOMMENDED_DOCS) + len(OPTIONAL_DOCS)
    score = result.score

    if score == "not ready":
        print(f"  {_bold('Readiness:')} {_red('NOT READY')} — missing required doc(s)")
        print(
            f"    Create the required files above, then run {_cyan('aidlc precheck')} again."
        )
    elif score == "excellent":
        print(f"  {_bold('Readiness:')} {_green('EXCELLENT')} ({found}/{total} docs) — ready to run")
    elif score == "good":
        print(f"  {_bold('Readiness:')} {_green('GOOD')} ({found}/{total} docs) — ready to run")
    else:
        print(
            f"  {_bold('Readiness:')} {_yellow('MINIMAL')} ({found}/{total} docs) — can run, but more docs = better plans"
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
        aidlc_dir.mkdir()
        (aidlc_dir / "issues").mkdir()
        (aidlc_dir / "runs").mkdir()
        (aidlc_dir / "reports").mkdir()

        from .config_detect import detect_config, describe_detected

        default_config = {
            "plan_budget_hours": 4,
            "checkpoint_interval_minutes": 15,
            "claude_model": "opus",
            "max_implementation_attempts": 3,
            "run_tests_command": None,
        }
        detected = detect_config(project_root)
        for key, value in detected.items():
            if not key.startswith("_") and value is not None:
                default_config[key] = value

        with open(aidlc_dir / "config.json", "w") as config_file:
            json.dump(default_config, config_file, indent=2)

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


def cmd_audit(args: argparse.Namespace, version: str) -> None:
    """Run standalone code audit."""
    from .auditor import CodeAuditor
    from .logger import setup_logger
    from .claude_cli import ClaudeCLI

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
        cli = ClaudeCLI(config, logger)
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
    from .claude_cli import ClaudeCLI
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
    cli = ClaudeCLI(config, logger)
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
    from .claude_cli import ClaudeCLI

    project_root = Path(args.project or ".").resolve()
    config = load_config(config_path=getattr(args, "config", None), project_root=str(project_root))
    _print_banner(version)

    (project_root / ".aidlc").mkdir(exist_ok=True)
    logger = setup_logger("plan", project_root / ".aidlc", verbose=args.verbose)
    cli = ClaudeCLI(config, logger)
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
    from .claude_cli import ClaudeCLI
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
    cli = ClaudeCLI(config, logger)
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
