"""Main runner for AIDLC — orchestrates the full lifecycle.

Flow:
    1. SCAN — Discover project docs and build context
    2. PLAN — Time-constrained planning session (creates issues)
    3. IMPLEMENT — Loop through issues until all are done
    4. REPORT — Generate final summary

Usage:
    aidlc run                              # full lifecycle, 4h planning budget
    aidlc run --plan-budget 2h             # custom planning budget
    aidlc run --plan-only                  # planning only
    aidlc run --implement-only             # skip planning, use existing issues
    aidlc run --resume                     # resume previous run
    aidlc run --dry-run                    # no Claude calls
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config, get_run_dir, get_reports_dir, get_issues_dir
from .models import RunState, RunStatus, RunPhase
from .state_manager import generate_run_id, save_state, load_state, checkpoint, find_latest_run, RunLock
from .logger import setup_logger
from .claude_cli import ClaudeCLI
from .scanner import ProjectScanner
from .planner import Planner
from .implementer import Implementer
from .reporting import generate_run_report


def init_run(config: dict, resume: bool, dry_run: bool) -> tuple[RunState, Path]:
    """Initialize or resume a run."""
    if dry_run:
        config["dry_run"] = True

    runs_dir = Path(config["_runs_dir"])
    runs_dir.mkdir(parents=True, exist_ok=True)

    if resume:
        run_dir = find_latest_run(runs_dir)
        if run_dir:
            state = load_state(run_dir)
            if state.status in (RunStatus.COMPLETE, RunStatus.FAILED):
                print(f"Previous run {state.run_id} is {state.status.value}. Starting new run.")
            else:
                print(f"Resuming run {state.run_id} (phase: {state.phase.value})")
                (run_dir / "claude_outputs").mkdir(exist_ok=True)
                return state, run_dir
        else:
            print("No previous run found. Starting new run.")

    # New run
    run_id = generate_run_id("aidlc")
    run_dir = get_run_dir(config, run_id)
    state = RunState(
        run_id=run_id,
        config_name=config.get("run_name", "default"),
        project_root=config["_project_root"],
        plan_budget_seconds=config.get("plan_budget_hours", 4) * 3600,
    )
    state.started_at = datetime.now(timezone.utc).isoformat()
    save_state(state, run_dir)
    (run_dir / "claude_outputs").mkdir(exist_ok=True)

    # Save config snapshot
    with open(run_dir / "config_snapshot.json", "w") as f:
        serializable = {k: v for k, v in config.items() if not k.startswith("_")}
        json.dump(serializable, f, indent=2)

    return state, run_dir


def scan_project(state: RunState, config: dict, logger) -> str:
    """Scan the project and return context string."""
    logger.info("Scanning project...")
    state.phase = RunPhase.SCANNING

    scanner = ProjectScanner(Path(config["_project_root"]), config)
    scan_result = scanner.scan()

    state.docs_scanned = scan_result["total_docs"]
    state.scanned_docs = [d["path"] for d in scan_result["doc_files"]]

    context = scanner.build_context_prompt(scan_result)
    state.project_context = context[:2000]  # Save summary to state (full context kept in memory)

    logger.info(f"Scanned {scan_result['total_docs']} docs, project type: {scan_result['project_type']}")

    # Load any existing issues from previous runs
    existing = scan_result.get("existing_issues", [])
    if existing:
        logger.info(f"Found {len(existing)} existing issues from previous runs")

    return context


def run_full(
    config: dict,
    resume: bool = False,
    dry_run: bool = False,
    plan_only: bool = False,
    implement_only: bool = False,
    verbose: bool = False,
    audit: str | None = None,
) -> None:
    """Run the full AIDLC lifecycle."""

    # Acquire run lock to prevent concurrent runs
    aidlc_dir = Path(config["_aidlc_dir"])
    lock = RunLock(aidlc_dir)
    try:
        lock.acquire()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Init
    state, run_dir = init_run(config, resume, dry_run)
    logger = setup_logger(state.run_id, run_dir, verbose=verbose)

    logger.info(f"Run ID: {state.run_id}")
    logger.info(f"Project: {config['_project_root']}")
    logger.info(f"Plan budget: {state.plan_budget_seconds / 3600:.1f}h")
    logger.info(f"Dry run: {config.get('dry_run', False)}")

    # Init Claude CLI
    cli = ClaudeCLI(config, logger)
    if not cli.check_available():
        logger.warning("Claude CLI not available.")
        if not config.get("dry_run"):
            logger.error("Install Claude CLI or use --dry-run. Exiting.")
            lock.release()
            sys.exit(1)

    wall_start = time.time()
    state.status = RunStatus.RUNNING

    try:
        # AUDIT (optional) — analyze existing code before planning
        if audit and not implement_only:
            if state.phase in (RunPhase.INIT, RunPhase.AUDITING):
                from .auditor import CodeAuditor

                state.phase = RunPhase.AUDITING
                state.audit_depth = audit
                logger.info(f"Running {audit} code audit...")

                auditor = CodeAuditor(
                    project_root=Path(config["_project_root"]),
                    config=config,
                    cli=cli if audit == "full" else None,
                    logger=logger,
                )
                audit_result = auditor.run(depth=audit)
                state.audit_completed = True

                if audit_result.conflicts:
                    state.audit_conflicts = [c.to_dict() for c in audit_result.conflicts]
                    state.status = RunStatus.PAUSED
                    state.stop_reason = (
                        f"Audit found {len(audit_result.conflicts)} conflict(s). "
                        f"Review .aidlc/CONFLICTS.md and run 'aidlc run --resume'."
                    )
                    save_state(state, run_dir)
                    logger.warning(state.stop_reason)
                    lock.release()
                    return

                save_state(state, run_dir)
                logger.info("Audit complete, proceeding to scan.")

        # SCAN — always scan (even on resume, to get fresh context)
        project_context = scan_project(state, config, logger)
        save_state(state, run_dir)

        # PLAN
        if not implement_only:
            if state.phase in (RunPhase.INIT, RunPhase.SCANNING, RunPhase.PLANNING, RunPhase.PLAN_FINALIZATION):
                planner = Planner(state, run_dir, config, cli, project_context, logger)
                planner.run()
                save_state(state, run_dir)
                logger.info(f"Planning complete: {state.issues_created} issues created")

        if plan_only:
            state.stop_reason = "Plan-only mode"
            logger.info("Plan-only mode. Stopping before implementation.")
        else:
            # IMPLEMENT
            if state.issues:
                implementer = Implementer(state, run_dir, config, cli, project_context, logger)
                implementer.run()
                save_state(state, run_dir)
                logger.info(
                    f"Implementation complete: "
                    f"{state.issues_implemented} implemented, "
                    f"{state.issues_verified} verified, "
                    f"{state.issues_failed} failed"
                )
            else:
                logger.warning("No issues to implement. Did planning produce any issues?")

        # REPORT
        state.phase = RunPhase.REPORTING
        report_dir = get_reports_dir(config, state.run_id)
        report_path = generate_run_report(state, report_dir)
        logger.info(f"Report: {report_path}")

        state.phase = RunPhase.DONE
        state.status = RunStatus.COMPLETE
        if not state.stop_reason:
            state.stop_reason = "All work completed"

    except KeyboardInterrupt:
        logger.info("Interrupted. Saving state for resume.")
        state.status = RunStatus.PAUSED
        state.stop_reason = "User interrupt (Ctrl+C)"

    except Exception as e:
        logger.exception(f"Unhandled error: {e}")
        state.status = RunStatus.FAILED
        state.stop_reason = f"Error: {e}"

    finally:
        state.wall_clock_seconds += time.time() - wall_start
        save_state(state, run_dir)
        report_dir = get_reports_dir(config, state.run_id)
        generate_run_report(state, report_dir)
        logger.info(f"Run {state.run_id} finished: {state.status.value}")
        logger.info(f"State: {run_dir}/state.json")
        logger.info(f"Reports: {report_dir}/")
        lock.release()
