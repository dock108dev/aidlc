"""CLI entry point for AIDLC."""

import argparse
import sys
from pathlib import Path

from . import __version__
from .cli.accounts import cmd_accounts as _cmd_accounts
from .cli.config_cmd import cmd_config_show as _cmd_config_show
from .cli.provider import cmd_provider as _cmd_provider
from .cli.usage_cmd import cmd_usage as _cmd_usage
from .cli_commands import (
    _cyan,
    _print_banner,
    _print_precheck,
    _red,
)
from .cli_commands import (
    cmd_init as _cmd_init,
)
from .cli_commands import (
    cmd_precheck as _cmd_precheck,
)
from .cli_commands import (
    cmd_reset as _cmd_reset,
)
from .cli_commands import (
    cmd_status as _cmd_status,
)
from .cli_parser import build_parser
from .config import load_config
from .runner import run_full


def parse_budget(budget_str: str) -> float:
    """Parse a budget string like '4h', '30m', '2.5h' into hours."""
    budget_str = budget_str.strip().lower()
    if budget_str.endswith("h"):
        return float(budget_str[:-1])
    if budget_str.endswith("m"):
        return float(budget_str[:-1]) / 60
    return float(budget_str)


def cmd_precheck(args: argparse.Namespace) -> None:
    _cmd_precheck(args, __version__)


def cmd_init(args: argparse.Namespace) -> None:
    _cmd_init(args, __version__)


def cmd_status(args: argparse.Namespace) -> None:
    _cmd_status(args, __version__)


def cmd_reset(args: argparse.Namespace) -> None:
    _cmd_reset(args, __version__)


def cmd_accounts(args: argparse.Namespace) -> None:
    _cmd_accounts(args, __version__)


def cmd_provider(args: argparse.Namespace) -> None:
    _cmd_provider(args, __version__)


def cmd_usage(args: argparse.Namespace) -> None:
    _cmd_usage(args, __version__)


def cmd_config_show(args: argparse.Namespace) -> None:
    _cmd_config_show(args, __version__)


def cmd_run(args: argparse.Namespace) -> None:
    """Run the full AIDLC lifecycle."""
    project_root = args.project or str(Path.cwd())
    project_path = Path(project_root).resolve()
    config = load_config(config_path=args.config, project_root=project_root)

    revert_cycle = getattr(args, "revert_to_cycle", None)
    if revert_cycle is not None:
        from .models import RunStatus
        from .state_manager import (
            find_latest_run,
            list_cycle_snapshots,
            load_cycle_snapshot,
        )
        from .state_manager import (
            save_state as _save,
        )

        _print_banner(__version__)
        runs_dir = project_path / ".aidlc" / "runs"
        run_dir = find_latest_run(runs_dir)
        if not run_dir:
            print(f"{_red('x')} No runs found.")
            sys.exit(1)

        available = list_cycle_snapshots(run_dir)
        if not available:
            print(f"{_red('x')} No cycle snapshots found. Snapshots are created during planning.")
            sys.exit(1)

        if revert_cycle not in available:
            print(f"{_red('x')} No snapshot for cycle {revert_cycle}.")
            print(f"  Available cycles: {', '.join(str(c) for c in available)}")
            sys.exit(1)

        state = load_cycle_snapshot(run_dir, revert_cycle)
        state.status = RunStatus.PAUSED
        state.stop_reason = f"Reverted to cycle {revert_cycle}"
        _save(state, run_dir)
        print(f"{_cyan('Reverted')} to start of planning cycle {revert_cycle}")
        print(f"  Issues: {state.issues_created}")
        print(f"  Planning cycles: {state.planning_cycles}")
        print(f"  Run: {_cyan('aidlc run --resume')} to continue from here")
        return

    skip_precheck = args.resume or args.implement_only
    if not skip_precheck:
        from .precheck import run_precheck

        _print_banner(__version__)
        print("Pre-flight check...")
        print()
        result = run_precheck(project_path, auto_init=True)
        _print_precheck(result, project_path, verbose=args.verbose)
        if not result.ready:
            print()
            print(f"  Fix the required items above, then run {_cyan('aidlc run')} again.")
            sys.exit(1)
        print()
        print("  Starting lifecycle...")
        print()

    if args.plan_budget:
        config["plan_budget_hours"] = parse_budget(args.plan_budget)
    if args.max_plan_cycles is not None:
        config["max_planning_cycles"] = args.max_plan_cycles
    if args.max_impl_cycles is not None:
        config["max_implementation_cycles"] = args.max_impl_cycles
    # --retry-failed forces reopen of every failed issue, not just the
    # transient ones. Stashed on config under an internal key so the
    # implementer can pick it up without changing the public function signature.
    if getattr(args, "retry_failed", False):
        config["_retry_failed_flag"] = True

    skip_finalize = getattr(args, "skip_finalize", False)
    skip_validation = getattr(args, "skip_validation", False)
    passes_str = getattr(args, "passes", None)
    finalize_passes = passes_str.split(",") if passes_str else None

    if config.get("runtime_profile") == "production":
        if skip_validation:
            print(f"{_red('x')} --skip-validation is disabled in runtime_profile=production.")
            sys.exit(1)
        if skip_finalize:
            print(f"{_red('x')} --skip-finalize is disabled in runtime_profile=production.")
            sys.exit(1)

    run_full(
        config=config,
        resume=args.resume,
        dry_run=args.dry_run,
        plan_only=args.plan_only,
        implement_only=args.implement_only,
        verbose=args.verbose,
        skip_finalize=skip_finalize,
        skip_validation=skip_validation,
        finalize_passes=finalize_passes,
    )


def main() -> None:
    parser = build_parser(__version__)
    args = parser.parse_args()

    if args.command == "precheck":
        cmd_precheck(args)
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "reset":
        cmd_reset(args)
    elif args.command == "accounts":
        cmd_accounts(args)
    elif args.command == "provider":
        cmd_provider(args)
    elif args.command == "usage":
        cmd_usage(args)
    elif args.command == "config":
        # config show (and future config subcommands)
        cmd_config_show(args)
    else:
        parser.print_help()
        print()
        print(
            f"Run {_cyan('aidlc precheck')} to check readiness, or {_cyan('aidlc init')} to get started."
        )


if __name__ == "__main__":
    main()
