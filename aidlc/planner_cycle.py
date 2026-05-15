"""Single-cycle planning orchestration."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from .models import RunPhase
from .routing.helpers import routed_model_from_result
from .schemas import PlanningOutput, parse_planning_output
from .state_manager import save_state


def planning_cycle(planner) -> bool | None:
    """Execute one planning cycle and apply any valid actions."""
    planner.state.planning_cycles += 1
    cycle_num = planner.state.planning_cycles
    is_finalization = planner.state.phase == RunPhase.PLAN_FINALIZATION

    planner.logger.info(
        f"=== Planning Cycle {cycle_num} {'(FINALIZATION)' if is_finalization else ''} ==="
    )

    prompt = planner._build_prompt(is_finalization)
    planner.logger.debug(f"Prompt size: {len(prompt)} chars")
    preflight_routing = planner._preflight_routing_snapshot()
    if preflight_routing:
        planner.logger.info(
            "  Planning Cycle %s selected route: %s/%s",
            cycle_num,
            preflight_routing.get("provider_id"),
            preflight_routing.get("model"),
        )

    start_time = time.time()
    use_threading = planner.config.get("claude_planning_cli_threading", True)
    model_pin = planner.state.planning_pinned_model if use_threading else None
    if use_threading:
        if not planner.state.planning_claude_session_id:
            planner.state.planning_claude_session_id = str(uuid.uuid4())
        if not planner.state.planning_copilot_session_id:
            planner.state.planning_copilot_session_id = str(uuid.uuid4())
            save_state(planner.state, planner.run_dir)
    session_cont = planner._planning_session_continuation() if use_threading else None
    session_resume = planner._planning_session_resume() if use_threading else None
    result = planner.cli.execute_prompt(
        prompt,
        planner.project_root,
        allow_edits=True,
        model_override=model_pin,
        session_continuation=session_cont,
        session_resume=session_resume,
    )
    if use_threading and result.get("success") and result.get("provider_id") == "claude":
        delay = float(planner.config.get("claude_session_release_delay_seconds", 2.0))
        if delay > 0:
            time.sleep(delay)
    planner._log_provider_result(cycle_num, result)
    planner.state.record_provider_result(result, planner.config, phase="planning")
    _capture_planning_session(planner, result, use_threading)

    duration = time.time() - start_time
    planner.state.plan_elapsed_seconds += duration
    planner.state.elapsed_seconds += duration

    output_text = result.get("output", "")
    output_dir = planner.run_dir / "provider_outputs"
    output_dir.mkdir(exist_ok=True)
    prompt_path = output_dir / f"plan_cycle_{cycle_num:04d}.prompt.md"
    output_path = output_dir / f"plan_cycle_{cycle_num:04d}.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    output_path.write_text(output_text or "", encoding="utf-8")

    if not result["success"]:
        planner._write_cycle_debug_bundle(
            cycle_num=cycle_num,
            prompt_path=prompt_path,
            output_path=output_path,
            result=result,
            preflight_routing=preflight_routing,
            parse_status="provider_failure",
            parse_error=result.get("error"),
        )
        planner.logger.error(f"Cycle {cycle_num} failed: {result.get('error')}")
        return False

    planning_output = _parse_planning_output(planner, cycle_num, output_text, output_dir, result)
    if planning_output is None:
        planner._write_cycle_debug_bundle(
            cycle_num=cycle_num,
            prompt_path=prompt_path,
            output_path=output_path,
            result=result,
            preflight_routing=preflight_routing,
            parse_status="parse_error",
            parse_error="no parseable output",
        )
        return False

    planner._write_cycle_debug_bundle(
        cycle_num=cycle_num,
        prompt_path=prompt_path,
        output_path=output_path,
        result=result,
        preflight_routing=preflight_routing,
        parse_status="ok",
        planning_output=planning_output,
    )

    known_ids = {d["id"] for d in planner.state.issues} | {
        Path(e["path"]).stem
        for e in planner.existing_issues
        if e.get("path") and Path(e["path"]).stem.upper().startswith("ISSUE")
    }
    batch_new_ids = {
        a.issue_id
        for a in planning_output.actions
        if a.action_type == "create_issue" and a.issue_id
    }

    validation_errors = planning_output.validate(
        is_finalization=is_finalization,
        known_issue_ids=known_ids,
    )
    for err in validation_errors:
        planner.logger.warning(f"Validation: {err}")

    _save_cycle_notes(planner, planning_output, cycle_num)
    _capture_completion_signal(planner, planning_output)

    if not planning_output.actions:
        planner.logger.info("No actions proposed — frontier may be clear")
        return None

    planner.logger.info(f"Cycle {cycle_num}: {len(planning_output.actions)} actions proposed")
    return _apply_valid_actions(
        planner,
        cycle_num,
        planning_output,
        is_finalization,
        known_ids,
        batch_new_ids,
    )


def _capture_planning_session(planner, result: dict, use_threading: bool) -> None:
    if (
        planner.config.get("claude_planning_cli_threading", True)
        and result.get("success")
        and not planner.state.planning_pinned_model
    ):
        model = routed_model_from_result(result)
        if model:
            planner.state.planning_pinned_model = model
            save_state(planner.state, planner.run_dir)

    if use_threading and result.get("success") and result.get("provider_id") == "openai":
        ext = result.get("continuation_session_id")
        if ext and not planner.state.planning_openai_thread_id:
            planner.state.planning_openai_thread_id = ext
            save_state(planner.state, planner.run_dir)

    if use_threading and result.get("success") and result.get("provider_id") == "claude":
        ext = result.get("continuation_session_id")
        if ext:
            planner.state.planning_claude_session_id = ext
        if not planner.state.planning_claude_session_resumable:
            planner.state.planning_claude_session_resumable = True
        save_state(planner.state, planner.run_dir)


def _parse_planning_output(
    planner, cycle_num: int, output_text: str, output_dir: Path, result: dict
):
    if planner.config.get("dry_run"):
        return PlanningOutput(
            frontier_assessment=f"[DRY RUN] Cycle {cycle_num}",
            actions=[],
            cycle_notes="Dry run",
        )

    try:
        return parse_planning_output(output_text)
    except ValueError as e:
        debug_path = output_dir / f"plan_cycle_{cycle_num:04d}.debug.json"
        planner.logger.error(f"Failed to parse cycle {cycle_num}: {e}")
        planner.logger.error(
            "Cycle %s returned no parseable output from %s/%s. "
            "See %s for full prompt/result/debug details.",
            cycle_num,
            result.get("provider_id"),
            result.get("model_used"),
            debug_path.name,
        )
        return None


def _save_cycle_notes(planner, planning_output: PlanningOutput, cycle_num: int) -> None:
    planner._save_cycle_notes(
        planning_output.frontier_assessment,
        planning_output.cycle_notes,
        cycle_num,
    )
    planner._last_cycle_notes = (
        f"Last planning cycle ({cycle_num}) assessment: "
        f"{planning_output.frontier_assessment}\n"
        f"Notes: {planning_output.cycle_notes}"
    )


def _capture_completion_signal(planner, planning_output: PlanningOutput) -> None:
    if planning_output.planning_complete and planner._verify_mode:
        reason = planning_output.completion_reason or "planning completed"
        planner._pending_completion_reason = f"Planning complete — {reason}"
        planner.logger.info(f"Model signaled planning_complete (accepted in verify mode): {reason}")
    elif planning_output.planning_complete:
        planner.logger.debug(
            "Model signaled planning_complete outside verify mode — ignored "
            "(only honored when VERIFY MODE instructions are in the prompt)."
        )


def _apply_valid_actions(
    planner,
    cycle_num: int,
    planning_output: PlanningOutput,
    is_finalization: bool,
    known_ids: set[str],
    batch_new_ids: set[str],
) -> bool:
    applied = 0
    action_errors = []
    total_actions = len(planning_output.actions)
    for action in planning_output.actions:
        errors = action.validate(
            is_finalization=is_finalization,
            known_issue_ids=known_ids,
            batch_issue_ids=batch_new_ids,
        )
        if errors:
            planner.logger.warning(f"Skipping invalid action: {errors}")
            action_errors.append(errors)
            continue

        try:
            planner._apply_action(action)
            applied += 1
            if action.issue_id:
                known_ids.add(action.issue_id)
        except Exception as e:
            planner.logger.error(f"Failed to apply action: {e}")
            action_errors.append(str(e))

    if action_errors and applied == 0:
        planner.logger.error(f"Cycle {cycle_num} failed: all {len(action_errors)} actions errored")
        return False

    if action_errors and total_actions:
        failure_ratio = len(action_errors) / total_actions
        ratio_threshold = planner.config.get("planning_action_failure_ratio_threshold", 0.6)
        if failure_ratio >= ratio_threshold:
            planner.logger.error(
                f"Cycle {cycle_num} failed: action failure ratio {failure_ratio:.0%} "
                f"exceeds threshold {ratio_threshold:.0%} "
                f"({len(action_errors)}/{total_actions} actions failed)"
            )
            return False
        planner.logger.warning(
            f"Cycle {cycle_num} partial success: {len(action_errors)}/{total_actions} "
            "actions failed but below failure threshold"
        )

    sanitized_changes = planner._sanitize_issue_dependencies()
    if sanitized_changes:
        planner.logger.info(
            f"Cycle {cycle_num}: normalized dependency graph "
            f"({sanitized_changes} change{'s' if sanitized_changes != 1 else ''})"
        )

    planner.logger.info(f"Cycle {cycle_num} complete: {applied} actions applied")
    return True
