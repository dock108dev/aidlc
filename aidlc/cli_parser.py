"""Argument parser builder for AIDLC CLI."""

import argparse
import textwrap


def build_parser(version: str) -> argparse.ArgumentParser:
    """Build top-level argparse parser for AIDLC.

    The CLI surface is intentionally narrow. The core flow is:

        aidlc init    Set up .aidlc/ + scaffold BRAINDUMP.md
        aidlc run     Run the full lifecycle from BRAINDUMP.md

    Everything else is admin sugar (status, reset, accounts, provider, usage,
    config). Standalone ``audit``, ``finalize``, ``improve``, ``plan``,
    ``validate`` commands were removed in the core-focus audit — audit and
    finalize run as part of ``run``; improve/plan/validate were either
    duplicating ``run`` or producing orthogonal artifacts.
    """
    parser = argparse.ArgumentParser(
        prog="aidlc",
        description=(
            "AIDLC — AI Development Life Cycle. "
            "Drop into any repo, write BRAINDUMP.md, run the lifecycle."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Quick start:
              aidlc init                Set up .aidlc/ and scaffold BRAINDUMP.md
              aidlc precheck            Check what docs/config are in place
              aidlc run                 Run full lifecycle (scan -> plan -> implement -> finalize)

            For existing repos:
              aidlc init                Scaffolds BRAINDUMP.md (edit it, then run)
              aidlc run --audit         Run a code audit before planning

            More info: https://github.com/highlyprofitable108/aidlc
        """),
    )
    parser.add_argument("--version", "-V", action="version", version=f"aidlc {version}")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    precheck_parser = subparsers.add_parser(
        "precheck",
        help="Check project readiness",
        description=(
            "Verify docs and config are in place before running. "
            "Auto-creates .aidlc/ with defaults if missing."
        ),
    )
    precheck_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    precheck_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show suggestions for all missing docs"
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize AIDLC in a project",
        description=(
            "Set up .aidlc/ directory with config, scaffold BRAINDUMP.md if missing, "
            "and optionally copy planning doc templates."
        ),
    )
    init_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    init_parser.add_argument(
        "--with-docs",
        action="store_true",
        help="Also copy the rest of the planning doc templates (ARCHITECTURE.md, ROADMAP.md, etc.)",
    )
    init_parser.add_argument(
        "--providers",
        action="store_true",
        help="Run provider setup wizard after init (validate, auth, configure)",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run AIDLC lifecycle",
        description="Run scan -> plan -> implement -> validate -> finalize -> report lifecycle.",
    )
    run_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    run_parser.add_argument("--config", "-c", help="Config file path")
    run_parser.add_argument("--plan-budget", help="Planning time budget (e.g., 4h, 30m)")
    run_parser.add_argument("--plan-only", action="store_true", help="Stop after planning")
    run_parser.add_argument(
        "--implement-only", action="store_true", help="Skip planning, implement existing issues"
    )
    run_parser.add_argument("--resume", action="store_true", help="Resume latest run")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="No provider calls (cycles capped at 3)"
    )
    run_parser.add_argument(
        "--max-plan-cycles", type=int, default=None, help="Max planning cycles (0=unlimited)"
    )
    run_parser.add_argument(
        "--max-impl-cycles", type=int, default=None, help="Max implementation cycles (0=unlimited)"
    )
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    run_parser.add_argument(
        "--audit",
        nargs="?",
        const="quick",
        choices=["quick", "full"],
        help="Audit existing code before planning (default: quick)",
    )
    run_parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip the validation test-and-fix loop after implementation",
    )
    run_parser.add_argument(
        "--skip-finalize", action="store_true", help="Skip finalization passes after implementation"
    )
    run_parser.add_argument(
        "--passes",
        help="Comma-separated finalization passes to run (default: all). Options: docs,cleanup",
    )
    run_parser.add_argument(
        "--revert-to-cycle",
        type=int,
        default=None,
        help="Revert planning state to the start of a specific cycle number, then exit",
    )
    run_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Reopen ALL failed issues to pending before resuming, regardless of "
            "failure_cause. Without this flag, only transient failures "
            "(token_exhausted, unknown) auto-reopen each cycle."
        ),
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Show latest run status",
        description="Display the status and issue breakdown of the most recent run.",
    )
    status_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")

    reset_parser = subparsers.add_parser(
        "reset",
        help="Clear stale .aidlc/ state",
        description=(
            "Delete .aidlc/runs, reports, issues, session, and run artifacts. "
            "Preserves config.json by default. Use --all to also delete config.json."
        ),
    )
    reset_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    reset_parser.add_argument(
        "--all",
        dest="reset_all",
        action="store_true",
        help="Also delete .aidlc/config.json (you'll need to re-init and re-auth)",
    )
    reset_parser.add_argument(
        "--keep-issues",
        action="store_true",
        help="Preserve .aidlc/issues/ (useful when resetting just runs)",
    )
    reset_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting",
    )
    reset_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )

    # --- accounts subcommand ---
    accounts_parser = subparsers.add_parser(
        "accounts",
        help="Manage provider accounts",
        description="Connect, list, validate, and remove provider accounts (Claude, Copilot, OpenAI).",
    )
    accounts_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    accounts_subparsers = accounts_parser.add_subparsers(
        dest="accounts_cmd", help="Accounts action"
    )
    accounts_subparsers.add_parser("list", help="List all registered accounts")
    accounts_add = accounts_subparsers.add_parser("add", help="Register a new account")
    accounts_add.add_argument(
        "--provider", required=True, help="Provider ID: claude | copilot | openai"
    )
    accounts_add.add_argument("--id", required=True, help="Unique account identifier")
    accounts_add.add_argument("--name", help="Display name")
    accounts_add.add_argument(
        "--tier", default="unknown", help="Membership tier: free | standard | pro | premium | api"
    )
    accounts_add.add_argument(
        "--tags", default="", help="Comma-separated role tags: primary,backup,premium,reserve,cheap"
    )
    accounts_remove = accounts_subparsers.add_parser("remove", help="Remove an account")
    accounts_remove.add_argument("--id", required=True, help="Account ID to remove")
    accounts_validate = accounts_subparsers.add_parser(
        "validate", help="Run health check on account(s)"
    )
    accounts_validate.add_argument("--id", help="Account ID to validate (default: all)")

    # --- provider subcommand ---
    provider_parser = subparsers.add_parser(
        "provider",
        help="Enable or disable AI providers",
        description="Enable or disable AI providers (claude, copilot, openai) in the project config.",
    )
    provider_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    provider_subparsers = provider_parser.add_subparsers(
        dest="provider_cmd", help="Provider action"
    )
    provider_subparsers.add_parser("list", help="Show provider enable/disable status")
    provider_enable = provider_subparsers.add_parser("enable", help="Enable a provider")
    provider_enable.add_argument("name", help="Provider name: claude | copilot | openai")
    provider_disable = provider_subparsers.add_parser("disable", help="Disable a provider")
    provider_disable.add_argument("name", help="Provider name: claude | copilot | openai")
    provider_auth = provider_subparsers.add_parser(
        "auth",
        help="Authenticate a provider (runs vendor login flow)",
    )
    provider_auth.add_argument("name", help="Provider name: claude | copilot | openai")
    provider_subparsers.add_parser(
        "reconnect",
        help="Re-authenticate all providers currently failing health check",
    )

    # --- usage subcommand ---
    usage_parser = subparsers.add_parser(
        "usage",
        help="Show token and cost usage across runs",
        description="Display token/cost usage table by provider, phase, model, or account.",
    )
    usage_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    usage_parser.add_argument(
        "--last",
        type=int,
        default=1,
        metavar="N",
        help="Number of most recent runs to include (default: 1; 0 = all)",
    )
    usage_parser.add_argument(
        "--by",
        choices=["provider", "phase", "model", "account"],
        default="provider",
        help="Group results by dimension (default: provider)",
    )
    usage_parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Only include runs on or after this date",
    )

    # --- config subcommand ---
    config_parser = subparsers.add_parser(
        "config",
        help="Show and manage configuration",
        description="Inspect active configuration and view effective runtime routing preview.",
    )
    config_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    config_parser.add_argument("--config", "-c", help="Config file path")
    config_subparsers = config_parser.add_subparsers(dest="config_cmd", help="Config action")
    config_show = config_subparsers.add_parser(
        "show", help="Show active config and routing preview"
    )
    config_show.add_argument(
        "--effective",
        action="store_true",
        help="Show full effective runtime preview (provider health, per-phase routing)",
    )
    config_show.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    config_show.add_argument("--config", "-c", help="Config file path")
    config_subparsers.add_parser("edit", help="Open config file in $EDITOR")
    config_subparsers.add_parser(
        "wizard",
        help="Interactive config setup (routing, providers, models)",
    )

    return parser
