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
    aidlc run --dry-run                    # no AI provider calls
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import get_reports_dir, get_run_dir
from .implementer import Implementer
from .issue_model import issue_progress_rank
from .logger import setup_logger
from .models import Issue, RunPhase, RunState, RunStatus
from .planner import Planner
from .reporting import generate_run_report
from .resume_reconcile import reconcile_issues_on_resume
from .routing import ProviderRouter
from .scanner import ProjectScanner
from .state_manager import RunLock, find_latest_run, generate_run_id, load_state, save_state

# Phases after planning: resuming here must not trigger another planning session.
_POST_PLANNING_PHASES = frozenset(
    {
        RunPhase.IMPLEMENTING,
        RunPhase.VERIFYING,
        RunPhase.VALIDATING,
        RunPhase.FINALIZING,
        RunPhase.REPORTING,
        RunPhase.DONE,
    }
)


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
            # ISSUE-010: surface stale RUNNING/INTERRUPTED runs as ABANDONED
            # so the user can tell crashed runs from live ones.
            from .state_manager import mark_abandoned_if_stale

            if mark_abandoned_if_stale(state, run_dir):
                print(
                    f"Previous run {state.run_id} appears abandoned "
                    f"(stale {state.phase.value}). Starting new run; "
                    f"delete .aidlc/runs/{run_dir.name}/ to remove it."
                )
            elif state.status in (RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.ABANDONED):
                print(f"Previous run {state.run_id} is {state.status.value}. Starting new run.")
            else:
                print(f"Resuming run {state.run_id} (phase: {state.phase.value})")
                (run_dir / "claude_outputs").mkdir(exist_ok=True)
                # Register interrupt handler so a Ctrl-C during this resume
                # marks the run INTERRUPTED rather than leaving stale RUNNING.
                _register_interrupt_handlers(state, run_dir)
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
    try:
        os.chmod(run_dir / "config_snapshot.json", 0o600)
    except OSError:
        pass

    _register_interrupt_handlers(state, run_dir)
    return state, run_dir


# ISSUE-010: signal/atexit handlers flip RUNNING → INTERRUPTED so resume can
# detect crashed sessions. Module-level state ensures we don't double-register.
_HANDLERS_REGISTERED = False
_HANDLER_STATE: tuple[RunState, Path] | None = None


def _register_interrupt_handlers(state: RunState, run_dir: Path) -> None:
    """Register atexit + SIGINT/SIGTERM hooks that mark a RUNNING run INTERRUPTED.

    A run killed externally (Ctrl-C, OOM, SIGTERM) used to leave
    ``status=running``, indistinguishable from a still-active run. The handler
    flips it to INTERRUPTED only when the process exits while status is RUNNING
    — clean exits leave the status set by the runner alone.
    """
    global _HANDLERS_REGISTERED, _HANDLER_STATE
    _HANDLER_STATE = (state, run_dir)
    if _HANDLERS_REGISTERED:
        return

    import atexit
    import signal

    def _on_exit():
        if _HANDLER_STATE is None:
            return
        s, d = _HANDLER_STATE
        if s.status == RunStatus.RUNNING:
            s.status = RunStatus.INTERRUPTED
            try:
                save_state(s, d)
            except Exception:
                pass

    def _on_signal(signum, frame):
        # Mark and exit non-zero so the shell sees an interrupted run.
        _on_exit()
        sys.exit(130 if signum == signal.SIGINT else 143)

    atexit.register(_on_exit)
    try:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    except (ValueError, OSError):
        # Non-main thread or restricted env: atexit alone is still useful.
        pass
    _HANDLERS_REGISTERED = True


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

    # The active provider has file access (allow_edits=True) so it can read docs directly.
    # No need to paste everything into the prompt or generate summaries.
    # Just note total doc size for logging.
    doc_files = scan_result["doc_files"]
    total_doc_chars = sum(d["size"] for d in doc_files)
    if total_doc_chars > 80000:
        logger.info(
            f"Large project: {total_doc_chars:,} chars across {len(doc_files)} docs "
            "(provider will read files directly)"
        )

    state.project_context = context[:2000]  # Save summary to state

    logger.info(
        f"Scanned {scan_result['total_docs']} docs, project type: {scan_result['project_type']}"
    )

    existing = scan_result.get("existing_issues", [])
    if existing:
        logger.info(f"Found {len(existing)} existing issues from previous runs")

    return context, scan_result


def hydrate_existing_issues(state: RunState, scan_result: dict, logger) -> None:
    """Load parsed issue files from scan results into run state.

    Issue markdown under .aidlc/issues is the usual source for metadata on cold start.
    On **resume**, run state already reflects persisted progress; we **never downgrade**
    status from a saved issue because the markdown file is still ``pending``.
    """
    existing = scan_result.get("existing_issues", []) or []
    loaded = 0
    for entry in existing:
        parsed = entry.get("parsed_issue")
        if not isinstance(parsed, dict) or not parsed.get("id"):
            continue
        incoming = Issue.from_dict(parsed)
        current = state.get_issue(incoming.id)
        if current is not None:
            cr = issue_progress_rank(current.status)
            ir = issue_progress_rank(incoming.status)
            if cr > ir:
                continue
            if cr == ir and len((current.implementation_notes or "")) > len(
                (incoming.implementation_notes or "")
            ):
                incoming.implementation_notes = current.implementation_notes
        state.update_issue(incoming)
        loaded += 1

    if loaded:
        state.total_issues = len(state.issues)
        logger.info(f"Hydrated {loaded} existing issue(s) into run state")


def run_full(
    config: dict,
    resume: bool = False,
    dry_run: bool = False,
    plan_only: bool = False,
    implement_only: bool = False,
    verbose: bool = False,
    audit: str | None = None,
    skip_finalize: bool = False,
    skip_validation: bool = False,
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

    # Init provider router for all AI execution phases.
    cli = ProviderRouter(config, logger)
    if not cli.check_available():
        logger.warning("No AI provider available.")
        if not config.get("dry_run"):
            logger.error(
                "Install a supported provider CLI (claude, copilot, codex) or use --dry-run. Exiting."
            )
            lock.release()
            sys.exit(1)

    state.status = RunStatus.RUNNING
    phase_before_scan = state.phase

    try:
        # AUDIT (optional) — analyze existing code before planning
        if audit and not implement_only:
            if state.phase in (RunPhase.INIT, RunPhase.AUDITING):
                from .auditor import CodeAuditor

                state.phase = RunPhase.AUDITING
                cli.set_phase("audit")
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
        hydrate_existing_issues(state, scan_result, logger)

        resume_skip_planning = (
            resume and not implement_only and phase_before_scan in _POST_PLANNING_PHASES
        )
        if resume_skip_planning:
            state.phase = phase_before_scan
            logger.info(
                f"Resume: restoring phase '{phase_before_scan.value}' — "
                "skipping new planning (scan refreshed context only)."
            )
            reconcile_issues_on_resume(state, Path(config["_project_root"]), logger, config)

        save_state(state, run_dir)

        # DOC-GAP DETECTION — scan docs for TBD/placeholder markers
        doc_gaps = []
        if (
            config.get("doc_gap_detection_enabled", True)
            and not implement_only
            and not resume_skip_planning
        ):
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
            if state.phase in (
                RunPhase.INIT,
                RunPhase.SCANNING,
                RunPhase.PLANNING,
                RunPhase.PLAN_FINALIZATION,
            ):
                cli.set_phase("planning")
                planner = Planner(
                    state,
                    run_dir,
                    config,
                    cli,
                    project_context,
                    logger,
                    doc_gaps=doc_gaps,
                    doc_files=scan_result.get("doc_files", []),
                    existing_issues=scan_result.get("existing_issues", []),
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
                cli.set_phase("implementation")
                implementer = Implementer(state, run_dir, config, cli, project_context, logger)
                verification_ok = implementer.run()
                save_state(state, run_dir)
                logger.info(
                    f"Implementation complete: "
                    f"{state.issues_implemented} implemented, "
                    f"{state.issues_verified} verified, "
                    f"{state.issues_failed} failed"
                )
                if not verification_ok:
                    state.status = RunStatus.PAUSED
                    if not state.stop_reason:
                        state.stop_reason = "Implementation stopped: final verification failed"
                    logger.error(state.stop_reason)
                    save_state(state, run_dir)
                    # Do not return here; always proceed to validation
            else:
                logger.warning("No issues to implement. Did planning produce any issues?")

        # VALIDATE (optional) — test, parse failures, fix, re-test loop
        if (
            not plan_only
            and not skip_validation
            and config.get("validation_enabled", True)
            and state.issues
        ):
            from .validator import Validator

            logger.info("Starting validation loop...")
            validator = Validator(state, run_dir, config, cli, project_context, logger)
            is_stable = validator.run()
            save_state(state, run_dir)
            if is_stable:
                logger.info("Validation passed — project is stable")
            else:
                logger.warning(
                    f"Validation incomplete: {state.validation_cycles} cycles, "
                    f"{state.validation_issues_created} fix issues created"
                )
                if config.get("strict_validation") or config.get("fail_on_validation_incomplete"):
                    state.status = RunStatus.PAUSED
                    state.stop_reason = "Validation incomplete under strict validation settings"
                    logger.error(state.stop_reason)
                    save_state(state, run_dir)
                    return

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
        save_state(state, run_dir)
        report_dir = get_reports_dir(config, state.run_id)
        generate_run_report(state, report_dir)
        logger.info(f"Run {state.run_id} finished: {state.status.value}")
        logger.info(f"State: {run_dir}/state.json")
        logger.info(f"Reports: {report_dir}/")
        lock.release()
