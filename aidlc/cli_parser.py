"""Argument parser builder for AIDLC CLI."""

import argparse
import textwrap


def build_parser(version: str) -> argparse.ArgumentParser:
    """Build top-level argparse parser for AIDLC."""
    parser = argparse.ArgumentParser(
        prog="aidlc",
        description="AIDLC — AI Development Life Cycle. Drop into any repo, plan with a time budget, implement until done.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Quick start:
              aidlc precheck            Check what docs are needed
              aidlc init --with-docs    Set up AIDLC + copy planning templates
              aidlc run                 Run full lifecycle

            For existing repos:
              aidlc precheck            See what's missing
              aidlc audit               Generate STATUS.md from your code
              aidlc run --audit         Audit first, then run lifecycle

            More info: https://github.com/highlyprofitable108/aidlc
        """),
    )
    parser.add_argument("--version", "-V", action="version", version=f"aidlc {version}")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    precheck_parser = subparsers.add_parser(
        "precheck",
        help="Check project readiness",
        description="Verify docs and config are in place before running. Auto-creates .aidlc/ with defaults if missing.",
    )
    precheck_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    precheck_parser.add_argument("--verbose", "-v", action="store_true", help="Show suggestions for all missing docs")

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize AIDLC in a project",
        description="Set up .aidlc/ directory with config and optionally copy planning doc templates.",
    )
    init_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    init_parser.add_argument(
        "--with-docs",
        action="store_true",
        help="Copy planning doc templates (ROADMAP.md, ARCHITECTURE.md, etc.) into the project",
    )
    init_parser.add_argument(
        "--providers",
        action="store_true",
        help="Run provider setup wizard after init (validate, auth, configure)",
    )

    improve_parser = subparsers.add_parser(
        "improve",
        help="Targeted improvement cycle",
        description="Audit a specific area, research improvements, plan and implement fixes.",
    )
    improve_parser.add_argument("concern", nargs="?", default=None, help="What to improve (e.g., 'economy feels flat', 'needs better UI')")
    improve_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    improve_parser.add_argument("--config", "-c", help="Config file path")
    improve_parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    improve_parser.add_argument("--plan-only", action="store_true", help="Create improvement issues but don't implement")

    plan_parser = subparsers.add_parser(
        "plan",
        help="Interactive planning session",
        description="Guided wizard + doc generation + Claude refinement for planning docs.",
    )
    plan_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    plan_parser.add_argument("--config", "-c", help="Config file path")
    plan_parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    plan_parser.add_argument("--skip-wizard", action="store_true", help="Skip wizard, go straight to Claude refinement")
    plan_parser.add_argument("--wizard-only", action="store_true", help="Run wizard and generate drafts, no Claude session")
    plan_parser.add_argument("--review", action="store_true", help="Review existing docs and suggest improvements")

    audit_parser = subparsers.add_parser(
        "audit",
        help="Audit existing codebase",
        description="Analyze existing code and generate STATUS.md + ARCHITECTURE.md.",
    )
    audit_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    audit_parser.add_argument("--full", action="store_true", help="Full audit with Claude semantic analysis")
    audit_parser.add_argument("--config", "-c", help="Config file path")
    audit_parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    run_parser = subparsers.add_parser(
        "run",
        help="Run AIDLC lifecycle",
        description="Run scan -> plan -> implement -> validate -> finalize -> report lifecycle.",
    )
    run_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    run_parser.add_argument("--config", "-c", help="Config file path")
    run_parser.add_argument("--plan-budget", help="Planning time budget (e.g., 4h, 30m)")
    run_parser.add_argument("--plan-only", action="store_true", help="Stop after planning")
    run_parser.add_argument("--implement-only", action="store_true", help="Skip planning, implement existing issues")
    run_parser.add_argument("--resume", action="store_true", help="Resume latest run")
    run_parser.add_argument("--dry-run", action="store_true", help="No Claude CLI calls (cycles capped at 3)")
    run_parser.add_argument("--max-plan-cycles", type=int, default=None, help="Max planning cycles (0=unlimited)")
    run_parser.add_argument("--max-impl-cycles", type=int, default=None, help="Max implementation cycles (0=unlimited)")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    run_parser.add_argument("--audit", nargs="?", const="quick", choices=["quick", "full"], help="Audit existing code before planning (default: quick)")
    run_parser.add_argument("--skip-validation", action="store_true", help="Skip the validation test-and-fix loop after implementation")
    run_parser.add_argument("--skip-finalize", action="store_true", help="Skip finalization passes after implementation")
    run_parser.add_argument("--passes", help="Comma-separated finalization passes to run (default: all). Options: ssot,security,abend,docs,cleanup")
    run_parser.add_argument("--revert-to-cycle", type=int, default=None, help="Revert planning state to the start of a specific cycle number, then exit")

    finalize_parser = subparsers.add_parser(
        "finalize",
        help="Run finalization passes",
        description="Run post-implementation audit, cleanup, and documentation passes.",
    )
    finalize_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    finalize_parser.add_argument("--passes", help="Comma-separated passes to run (default: all). Options: ssot,security,abend,docs,cleanup")
    finalize_parser.add_argument("--config", "-c", help="Config file path")
    finalize_parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    status_parser = subparsers.add_parser(
        "status",
        help="Show latest run status",
        description="Display the status and issue breakdown of the most recent run.",
    )
    status_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")

    # --- accounts subcommand ---
    accounts_parser = subparsers.add_parser(
        "accounts",
        help="Manage provider accounts",
        description="Connect, list, validate, and remove provider accounts (Claude, Copilot, OpenAI).",
    )
    accounts_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    accounts_subparsers = accounts_parser.add_subparsers(dest="accounts_cmd", help="Accounts action")
    accounts_subparsers.add_parser("list", help="List all registered accounts")
    accounts_add = accounts_subparsers.add_parser("add", help="Register a new account")
    accounts_add.add_argument("--provider", required=True, help="Provider ID: claude | copilot | openai")
    accounts_add.add_argument("--id", required=True, help="Unique account identifier")
    accounts_add.add_argument("--name", help="Display name")
    accounts_add.add_argument("--tier", default="unknown",
                              help="Membership tier: free | standard | pro | premium | api")
    accounts_add.add_argument("--tags", default="",
                              help="Comma-separated role tags: primary,backup,premium,reserve,cheap")
    accounts_remove = accounts_subparsers.add_parser("remove", help="Remove an account")
    accounts_remove.add_argument("--id", required=True, help="Account ID to remove")
    accounts_validate = accounts_subparsers.add_parser("validate", help="Run health check on account(s)")
    accounts_validate.add_argument("--id", help="Account ID to validate (default: all)")

    # --- provider subcommand ---
    provider_parser = subparsers.add_parser(
        "provider",
        help="Enable or disable AI providers",
        description="Enable or disable AI providers (claude, copilot, openai) in the project config.",
    )
    provider_parser.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    provider_subparsers = provider_parser.add_subparsers(dest="provider_cmd", help="Provider action")
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
    config_show = config_subparsers.add_parser("show", help="Show active config and routing preview")
    config_show.add_argument("--effective", action="store_true",
                             help="Show full effective runtime preview (provider health, per-phase routing)")
    config_show.add_argument("--project", "-p", help="Project root directory (default: cwd)")
    config_show.add_argument("--config", "-c", help="Config file path")
    config_subparsers.add_parser("edit", help="Open config file in $EDITOR")
    config_subparsers.add_parser(
        "wizard",
        help="Interactive config setup (routing, providers, models)",
    )

    return parser
