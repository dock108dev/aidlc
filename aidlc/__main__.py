"""CLI entry point for AIDLC.

Usage:
    aidlc run                              # full lifecycle
    aidlc run --plan-budget 2h             # custom planning budget
    aidlc run --plan-only                  # planning only
    aidlc run --implement-only             # implementation only (uses existing issues)
    aidlc run --resume                     # resume previous run
    aidlc run --dry-run                    # simulate without Claude calls
    aidlc run --project /path/to/repo      # target a specific repo

    aidlc init                             # set up .aidlc/ in current repo
    aidlc status                           # show status of latest run
"""

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .runner import run_full
from .state_manager import find_latest_run, load_state


def parse_budget(budget_str: str) -> float:
    """Parse a budget string like '4h', '30m', '2.5h' into hours."""
    budget_str = budget_str.strip().lower()
    if budget_str.endswith("h"):
        return float(budget_str[:-1])
    elif budget_str.endswith("m"):
        return float(budget_str[:-1]) / 60
    else:
        return float(budget_str)


def cmd_run(args: argparse.Namespace) -> None:
    project_root = args.project or str(Path.cwd())

    config = load_config(
        config_path=args.config,
        project_root=project_root,
    )

    if args.plan_budget:
        config["plan_budget_hours"] = parse_budget(args.plan_budget)
    if args.max_plan_cycles is not None:
        config["max_planning_cycles"] = args.max_plan_cycles
    if args.max_impl_cycles is not None:
        config["max_implementation_cycles"] = args.max_impl_cycles

    run_full(
        config=config,
        resume=args.resume,
        dry_run=args.dry_run,
        plan_only=args.plan_only,
        implement_only=args.implement_only,
        verbose=args.verbose,
    )


def cmd_init(args: argparse.Namespace) -> None:
    project_root = Path(args.project or ".").resolve()
    aidlc_dir = project_root / ".aidlc"

    if aidlc_dir.exists():
        print(f".aidlc/ already exists at {project_root}")
        print("Use 'aidlc run --resume' to resume, or delete .aidlc/ to start fresh.")
        return

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

    print(f"Initialized AIDLC in {project_root}")
    print(f"  Config: {aidlc_dir / 'config.json'}")
    print(f"  Issues: {aidlc_dir / 'issues/'}")
    print()
    print("Edit .aidlc/config.json to customize, then run:")
    print("  aidlc run")


def cmd_status(args: argparse.Namespace) -> None:
    project_root = Path(args.project or ".").resolve()
    runs_dir = project_root / ".aidlc" / "runs"

    if not runs_dir.exists():
        print("No AIDLC runs found. Run 'aidlc init' first.")
        return

    run_dir = find_latest_run(runs_dir)
    if not run_dir:
        print("No runs found.")
        return

    state = load_state(run_dir)
    plan_h = state.plan_elapsed_seconds / 3600
    plan_budget_h = state.plan_budget_seconds / 3600
    elapsed_h = state.elapsed_seconds / 3600

    print(f"Run: {state.run_id}")
    print(f"Status: {state.status.value}")
    print(f"Phase: {state.phase.value}")
    print(f"Planning: {plan_h:.1f}h / {plan_budget_h:.0f}h budget")
    print(f"Total elapsed: {elapsed_h:.1f}h")
    print(f"Issues: {state.total_issues} total, {state.issues_implemented} implemented, {state.issues_verified} verified, {state.issues_failed} failed")
    if state.stop_reason:
        print(f"Stop reason: {state.stop_reason}")

    # Show issue list
    if state.issues:
        print(f"\nIssues:")
        for d in state.issues:
            status_icon = {
                "pending": " ",
                "in_progress": ">",
                "implemented": "+",
                "verified": "v",
                "failed": "x",
                "blocked": "!",
                "skipped": "-",
            }.get(d.get("status", "pending"), "?")
            print(f"  [{status_icon}] {d['id']}: {d['title']} ({d.get('status', 'pending')})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AIDLC — AI Development Life Cycle",
        prog="aidlc",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # run
    run_parser = subparsers.add_parser("run", help="Run AIDLC lifecycle")
    run_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    run_parser.add_argument("--config", "-c", help="Config file path")
    run_parser.add_argument("--plan-budget", help="Planning time budget (e.g., 4h, 30m)")
    run_parser.add_argument("--plan-only", action="store_true", help="Stop after planning")
    run_parser.add_argument("--implement-only", action="store_true", help="Skip planning, implement existing issues")
    run_parser.add_argument("--resume", action="store_true", help="Resume latest run")
    run_parser.add_argument("--dry-run", action="store_true", help="No Claude CLI calls (cycles capped by max_planning_cycles/max_implementation_cycles, default 3)")
    run_parser.add_argument("--max-plan-cycles", type=int, default=None, help="Max planning cycles (0=unlimited, default: 0, dry-run default: 3)")
    run_parser.add_argument("--max-impl-cycles", type=int, default=None, help="Max implementation cycles (0=unlimited, default: 0, dry-run default: 3)")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize AIDLC in a project")
    init_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")

    # status
    status_parser = subparsers.add_parser("status", help="Show latest run status")
    status_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
