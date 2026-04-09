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


def scan_project(state: RunState, config: dict, logger, cli=None) -> tuple[str, dict]:
    """Scan the project and return (context_string, scan_result).

    For large projects, this also generates a doc manifest and optional
    project brief to ensure Claude sees the full scope even when individual
    docs don't fit in the context budget.
    """
    logger.info("Scanning project...")
    state.phase = RunPhase.SCANNING

    scanner = ProjectScanner(Path(config["_project_root"]), config)
    scan_result = scanner.scan()

    state.docs_scanned = scan_result["total_docs"]
    state.scanned_docs = [d["path"] for d in scan_result["doc_files"]]

    # Build base context
    context = scanner.build_context_prompt(scan_result)

    # For large projects, add a doc manifest so Claude knows what exists
    # even when docs don't fit in the context budget
    doc_files = scan_result["doc_files"]
    total_doc_chars = sum(d["size"] for d in doc_files)
    max_context = config.get("max_context_chars", 80000)

    if total_doc_chars > max_context:
        from .context_prep import build_doc_manifest, build_project_brief

        logger.info(
            f"Large project detected: {total_doc_chars:,} chars of docs vs "
            f"{max_context:,} context budget. Building doc manifest..."
        )

        # Add manifest of ALL docs (even ones that don't fit)
        manifest = build_doc_manifest(doc_files)
        context = context + "\n\n" + manifest

        # Generate project brief via Claude if available
        if cli and not config.get("dry_run"):
            logger.info("Generating project brief from all documentation...")
            brief = build_project_brief(
                doc_files, cli, Path(config["_project_root"]), logger,
                max_brief_chars=config.get("project_brief_max_chars", 20000),
            )
            if brief:
                # Save brief to .aidlc/ for reuse on resume
                brief_path = Path(config["_aidlc_dir"]) / "project_brief.md"
                brief_path.write_text(brief)
                context = f"## Project Brief\n\n{brief}\n\n{context}"
                logger.info(f"Project brief generated ({len(brief):,} chars)")
            else:
                logger.warning("Could not generate project brief — continuing with raw docs")
        else:
            # Check for cached brief from previous run
            brief_path = Path(config["_aidlc_dir"]) / "project_brief.md"
            if brief_path.exists():
                brief = brief_path.read_text()
                context = f"## Project Brief\n\n{brief}\n\n{context}"
                logger.info(f"Loaded cached project brief ({len(brief):,} chars)")

    state.project_context = context[:2000]  # Save summary to state

    logger.info(f"Scanned {scan_result['total_docs']} docs, project type: {scan_result['project_type']}")

    existing = scan_result.get("existing_issues", [])
    if existing:
        logger.info(f"Found {len(existing)} existing issues from previous runs")

    return context, scan_result


def run_full(
    config: dict,
    resume: bool = False,
    dry_run: bool = False,
    plan_only: bool = False,
    implement_only: bool = False,
    verbose: bool = False,
    audit: str | None = None,
    skip_finalize: bool = False,
    finalize_passes: list[str] | None = None,
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
        project_context, scan_result = scan_project(state, config, logger, cli=cli)
        save_state(state, run_dir)

        # DOC-GAP DETECTION — scan docs for TBD/placeholder markers
        doc_gaps = []
        if config.get("doc_gap_detection_enabled", True) and not implement_only:
            from .doc_gap_detector import detect_doc_gaps
            doc_gaps = detect_doc_gaps(Path(config["_project_root"]), config)
            if doc_gaps:
                critical = sum(1 for g in doc_gaps if g.severity == "critical")
                logger.info(
                    f"Found {len(doc_gaps)} doc gap(s) "
                    f"({critical} critical, {len(doc_gaps) - critical} other)"
                )

        # PLAN
        if not implement_only:
            if state.phase in (RunPhase.INIT, RunPhase.SCANNING, RunPhase.PLANNING, RunPhase.PLAN_FINALIZATION):
                planner = Planner(
                    state, run_dir, config, cli, project_context, logger,
                    doc_gaps=doc_gaps,
                    doc_files=scan_result.get("doc_files", []),
                )
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

        # FINALIZE (optional) — audit, cleanup, docs consolidation
        if (
            not plan_only
            and not skip_finalize
            and config.get("finalize_enabled", True)
            and state.issues
        ):
            from .finalizer import Finalizer

            state.phase = RunPhase.FINALIZING
            logger.info("Starting finalization passes...")
            finalizer = Finalizer(state, run_dir, config, cli, project_context, logger)
            finalizer.run(passes=finalize_passes)
            save_state(state, run_dir)

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
