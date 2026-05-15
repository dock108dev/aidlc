"""Debug artifact helpers for planning cycles."""

from __future__ import annotations

import json
from pathlib import Path

from .models import RunPhase
from .schemas import PlanningOutput


def preflight_routing_snapshot(planner) -> dict | None:
    """Best-effort router preview before the planning call starts."""
    resolve_fn = getattr(type(planner.cli), "resolve", None)
    if not callable(resolve_fn):
        return None
    try:
        decision = planner.cli.resolve(phase="planning")
    except Exception:
        return None
    provider_id = getattr(decision, "provider_id", None)
    model = getattr(decision, "model", None)
    if not provider_id and not model:
        return None
    return {
        "provider_id": provider_id,
        "account_id": getattr(decision, "account_id", None),
        "model": model,
        "reasoning": getattr(decision, "reasoning", None),
        "strategy_used": getattr(decision, "strategy_used", None),
        "fallback": getattr(decision, "fallback", None),
        "tier": getattr(decision, "tier", None),
        "quality_note": getattr(decision, "quality_note", None),
    }


def serialize_result_metadata(result: dict) -> dict:
    usage = result.get("usage")
    return {
        "success": bool(result.get("success")),
        "provider_id": result.get("provider_id"),
        "account_id": result.get("account_id"),
        "model_used": result.get("model_used"),
        "error": result.get("error"),
        "failure_type": result.get("failure_type"),
        "duration_seconds": result.get("duration_seconds"),
        "retries": result.get("retries"),
        "usage": usage if isinstance(usage, dict) else {},
        "routing_decision": (
            result.get("routing_decision")
            if isinstance(result.get("routing_decision"), dict)
            else None
        ),
        "raw_stdout_chars": len(result.get("raw_stdout") or ""),
        "raw_stderr_chars": len(result.get("raw_stderr") or ""),
        "output_chars": len(result.get("output") or ""),
    }


def write_cycle_debug_bundle(
    planner,
    *,
    cycle_num: int,
    prompt_path: Path,
    output_path: Path,
    result: dict,
    preflight_routing: dict | None,
    parse_status: str,
    parse_error: str | None = None,
    planning_output: PlanningOutput | None = None,
) -> None:
    output_dir = planner.run_dir / "provider_outputs"
    debug_path = output_dir / f"plan_cycle_{cycle_num:04d}.debug.json"
    raw_stdout = result.get("raw_stdout")
    raw_stderr = result.get("raw_stderr")
    raw_stdout_path = None
    raw_stderr_path = None
    if isinstance(raw_stdout, str) and raw_stdout:
        raw_stdout_path = output_dir / f"plan_cycle_{cycle_num:04d}.raw_stdout.txt"
        raw_stdout_path.write_text(raw_stdout, encoding="utf-8")
    if isinstance(raw_stderr, str) and raw_stderr:
        raw_stderr_path = output_dir / f"plan_cycle_{cycle_num:04d}.raw_stderr.txt"
        raw_stderr_path.write_text(raw_stderr, encoding="utf-8")
    parsed = {
        "parse_status": parse_status,
        "parse_error": parse_error,
    }
    if planning_output is not None:
        parsed.update(
            {
                "frontier_assessment": planning_output.frontier_assessment,
                "cycle_notes": planning_output.cycle_notes,
                "planning_complete": planning_output.planning_complete,
                "completion_reason": planning_output.completion_reason,
                "action_count": len(planning_output.actions),
                "actions": [
                    {
                        "action_type": action.action_type,
                        "issue_id": action.issue_id,
                        "title": action.title,
                        "priority": action.priority,
                        "dependencies": action.dependencies,
                        "labels": action.labels,
                        "acceptance_criteria_count": len(action.acceptance_criteria or []),
                    }
                    for action in planning_output.actions
                ],
            }
        )
    payload = {
        "cycle": cycle_num,
        "phase": planner.state.phase.value
        if hasattr(planner.state.phase, "value")
        else str(planner.state.phase),
        "is_finalization": planner.state.phase == RunPhase.PLAN_FINALIZATION,
        "prompt_chars": len(prompt_path.read_text(encoding="utf-8")),
        "prompt_path": prompt_path.name,
        "raw_output_path": output_path.name,
        "preflight_routing": preflight_routing,
        "result": serialize_result_metadata(result),
        "raw_stdout_path": raw_stdout_path.name if raw_stdout_path else None,
        "raw_stderr_path": raw_stderr_path.name if raw_stderr_path else None,
        "parsed": parsed,
    }
    debug_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
