"""Discovery phase — single pre-planning model pass.

Reads BRAINDUMP.md + repo (provider has file tools), writes:
  - .aidlc/discovery/findings.md
  - .aidlc/discovery/topics.json

These are tool-generated artifacts (not user-authored docs), so they live
under ``.aidlc/`` alongside ``runs/``, ``issues/``, etc. — not under the
target repo's ``docs/`` tree.

Idempotent: if both artifacts already exist, skip the model call (resume).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .discovery_prompt import build_discovery_prompt, parse_discovery_output


def _read_braindump(project_root: Path) -> str:
    bd = project_root / "BRAINDUMP.md"
    if not bd.exists():
        return ""
    try:
        return bd.read_text(encoding="utf-8")
    except OSError:
        return ""


def _build_repo_summary(project_root: Path, scan_result: dict | None) -> str:
    """Short pointer block. Provider uses real file tools — this is just framing."""
    lines: list[str] = []
    if scan_result:
        ptype = scan_result.get("project_type") or "unknown"
        total_docs = scan_result.get("total_docs") or len(scan_result.get("doc_files", []))
        lines.append(f"- Project type: {ptype}")
        lines.append(f"- Doc files scanned: {total_docs}")
    try:
        top_entries = sorted(p.name for p in project_root.iterdir() if not p.name.startswith("."))[
            :30
        ]
    except OSError:
        top_entries = []
    if top_entries:
        lines.append("- Top-level entries: " + ", ".join(top_entries))
    if not lines:
        lines.append("- (no scan summary available)")
    return "\n".join(lines)


def _should_retry_shallow_discovery(
    braindump: str,
    scan_result: dict | None,
    findings: str,
    topics: list[dict],
) -> bool:
    """Heuristic: discovery on a large input should not return a tiny no-topic stub."""
    if topics:
        return False
    total_docs = 0
    if scan_result:
        total_docs = int(scan_result.get("total_docs") or len(scan_result.get("doc_files", [])) or 0)
    braindump_len = len((braindump or "").strip())
    findings_len = len((findings or "").strip())
    return (
        braindump_len >= 2500
        and total_docs >= 20
        and findings_len < 600
    )


def _build_discovery_retry_prompt(base_prompt: str) -> str:
    """Augment discovery when the first answer was implausibly shallow."""
    return (
        base_prompt
        + "\n\n## Retry Guardrail\n"
        + "A previous discovery attempt returned implausibly shallow findings with zero "
        + "research topics. Re-read BRAINDUMP.md and inspect the repository directly.\n"
        + "- Walk every major BRAINDUMP area and cite the concrete files you inspected.\n"
        + "- If any integration, contract, flow, or subsystem would require planner guesswork, "
        + "emit a separate research topic for it.\n"
        + "- If you still return zero topics, the findings must explicitly cover the major "
        + "BRAINDUMP areas well enough that a planner would not have to guess.\n"
        + "- Do not answer with a short summary; produce the full findings + JSON topics format.\n"
    )


def _preflight_routing_snapshot(cli) -> dict | None:
    """Best-effort routing preview before the discovery call starts."""
    resolve_fn = getattr(type(cli), "resolve", None)
    if not callable(resolve_fn):
        return None
    try:
        decision = cli.resolve(phase="discovery")
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


def _serialize_result_metadata(result: dict) -> dict:
    """Keep the discovery debug bundle JSON-safe and high-signal."""
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
            result.get("routing_decision") if isinstance(result.get("routing_decision"), dict) else None
        ),
        "raw_stdout_chars": len(result.get("raw_stdout") or ""),
        "raw_stderr_chars": len(result.get("raw_stderr") or ""),
    }


def _write_discovery_debug_bundle(
    *,
    outputs_dir: Path,
    attempt_slug: str,
    prompt: str,
    result: dict,
    findings: str,
    topics: list[dict],
    braindump: str,
    repo_summary: str,
    scan_result: dict | None,
    preflight_routing: dict | None,
) -> None:
    """Persist the exact discovery inputs/outputs needed for forensic debugging."""
    prompt_path = outputs_dir / f"{attempt_slug}.prompt.md"
    raw_output_path = outputs_dir / f"{attempt_slug}.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    raw_output_path.write_text(result.get("output") or "", encoding="utf-8")
    debug_payload = {
        "attempt": attempt_slug,
        "preflight_routing": preflight_routing,
        "scan_result": scan_result or {},
        "repo_summary": repo_summary,
        "braindump_chars": len((braindump or "").strip()),
        "prompt_path": prompt_path.name,
        "raw_output_path": raw_output_path.name,
        "result": _serialize_result_metadata(result),
        "parsed": {
            "findings_chars": len((findings or "").strip()),
            "topic_count": len(topics),
            "topics": topics,
            "has_json_fence": "```json" in (result.get("output") or ""),
        },
    }
    (outputs_dir / f"{attempt_slug}.debug.json").write_text(
        json.dumps(debug_payload, indent=2) + "\n",
        encoding="utf-8",
    )


def run_discovery(
    state,
    config: dict,
    cli,
    project_root: Path,
    run_dir: Path,
    logger: logging.Logger,
    scan_result: dict | None = None,
) -> tuple[Path, Path]:
    """Execute discovery; return (findings_path, topics_path).

    Idempotent: if both artifacts already exist on disk, skip the model call
    and return the existing paths. The caller is responsible for setting
    `state.phase` before/after this call.
    """
    aidlc_dir = Path(config["_aidlc_dir"])
    discovery_dir = aidlc_dir / "discovery"
    discovery_dir.mkdir(parents=True, exist_ok=True)
    findings_path = discovery_dir / "findings.md"
    topics_path = discovery_dir / "topics.json"

    if findings_path.exists() and topics_path.exists():
        logger.info(
            f"Discovery artifacts already present at {findings_path.relative_to(project_root)} "
            "and topics.json — skipping discovery model call."
        )
        state.discovery_completed = True
        try:
            existing_topics = json.loads(topics_path.read_text(encoding="utf-8") or "[]")
            if isinstance(existing_topics, list):
                state.research_topics_total = len(existing_topics)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug(f"Could not measure existing topics.json on resume: {exc}")
        return findings_path, topics_path

    braindump = _read_braindump(project_root)
    if not braindump.strip():
        logger.warning(
            "No BRAINDUMP.md at project root; writing empty discovery artifacts and skipping model call."
        )
        findings_path.write_text("# Findings\n\n_No BRAINDUMP.md found at project root._\n")
        topics_path.write_text("[]\n")
        state.discovery_completed = True
        state.research_topics_total = 0
        return findings_path, topics_path

    repo_summary = _build_repo_summary(project_root, scan_result)
    research_dir = aidlc_dir / "research"
    existing_research: list[str] = []
    if research_dir.exists():
        existing_research = sorted(p.name for p in research_dir.glob("*.md"))
    prompt = build_discovery_prompt(braindump, repo_summary, existing_research=existing_research)
    preflight_routing = _preflight_routing_snapshot(cli)
    if preflight_routing:
        logger.info(
            "Discovery selected route: "
            f"{preflight_routing.get('provider_id')}/{preflight_routing.get('model')}"
        )

    cli.set_phase("discovery")
    logger.info("Running discovery — investigating repo against BRAINDUMP intent...")
    result = cli.execute_prompt(prompt, project_root)
    _log_model_result(logger, "Discovery", result)
    state.record_provider_result(result, config, phase="discovery")

    outputs_dir = run_dir / "claude_outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    if not result.get("success"):
        _write_discovery_debug_bundle(
            outputs_dir=outputs_dir,
            attempt_slug="discovery",
            prompt=prompt,
            result=result,
            findings="",
            topics=[],
            braindump=braindump,
            repo_summary=repo_summary,
            scan_result=scan_result,
            preflight_routing=preflight_routing,
        )
        logger.error(f"Discovery model call failed: {result.get('error')}")
        # Write empty artifacts so resume doesn't loop indefinitely; planning still runs.
        findings_path.write_text(
            "# Findings\n\n_Discovery model call failed; planning will proceed without findings._\n"
        )
        topics_path.write_text("[]\n")
        state.discovery_completed = True
        state.research_topics_total = 0
        return findings_path, topics_path

    raw_output = result.get("output") or ""
    findings, topics = parse_discovery_output(raw_output)
    _write_discovery_debug_bundle(
        outputs_dir=outputs_dir,
        attempt_slug="discovery",
        prompt=prompt,
        result=result,
        findings=findings,
        topics=topics,
        braindump=braindump,
        repo_summary=repo_summary,
        scan_result=scan_result,
        preflight_routing=preflight_routing,
    )
    if _should_retry_shallow_discovery(braindump, scan_result, findings, topics):
        logger.warning(
            "Discovery result looks implausibly shallow for this BRAINDUMP/repo size "
            "(%s chars findings, 0 topics). Retrying discovery once with stricter instructions.",
            f"{len(findings):,}",
        )
        retry_prompt = _build_discovery_retry_prompt(prompt)
        retry_preflight_routing = _preflight_routing_snapshot(cli)
        if retry_preflight_routing:
            logger.info(
                "Discovery retry selected route: "
                f"{retry_preflight_routing.get('provider_id')}/{retry_preflight_routing.get('model')}"
            )
        retry_result = cli.execute_prompt(retry_prompt, project_root)
        _log_model_result(logger, "Discovery retry", retry_result)
        state.record_provider_result(retry_result, config, phase="discovery")
        retry_output = retry_result.get("output") or ""
        if retry_result.get("success"):
            raw_output = retry_output
            findings, topics = parse_discovery_output(raw_output)
            _write_discovery_debug_bundle(
                outputs_dir=outputs_dir,
                attempt_slug="discovery_retry",
                prompt=retry_prompt,
                result=retry_result,
                findings=findings,
                topics=topics,
                braindump=braindump,
                repo_summary=repo_summary,
                scan_result=scan_result,
                preflight_routing=retry_preflight_routing,
            )
        else:
            _write_discovery_debug_bundle(
                outputs_dir=outputs_dir,
                attempt_slug="discovery_retry",
                prompt=retry_prompt,
                result=retry_result,
                findings="",
                topics=[],
                braindump=braindump,
                repo_summary=repo_summary,
                scan_result=scan_result,
                preflight_routing=retry_preflight_routing,
            )
            logger.warning(
                f"Discovery retry failed; using initial discovery result: {retry_result.get('error')}"
            )
    if not findings:
        logger.warning("Discovery output had no findings markdown; writing placeholder.")
        findings = "# Findings\n\n_Discovery returned no findings markdown._"
    if not topics:
        logger.info("Discovery proposed no research topics.")

    # Truncation sanity check: a normal discovery output ends with a
    # ```json fenced topics block. When the output is killed mid-flight
    # (Ctrl-C, SIGTERM, or claude_stall_kill_seconds), no fence is
    # emitted and parse_discovery_output dutifully treats the entire raw
    # output as findings markdown — sometimes hundreds of KB of partial
    # tool-use blobs. The user should know to re-run discovery rather
    # than ship that noise into planning. Heuristic: no JSON fence AND
    # >50 KB of "findings".
    if "```json" not in raw_output and len(findings) > 50_000:
        logger.warning(
            "Discovery output is %s chars with no ```json topics fence — "
            "this strongly suggests the model was interrupted mid-output. "
            "The findings file is being saved but is likely partial/garbled; "
            "delete .aidlc/discovery/findings.md and .aidlc/discovery/topics.json "
            "and re-run aidlc to get clean findings.",
            f"{len(findings):,}",
        )

    findings_path.write_text(findings.rstrip() + "\n", encoding="utf-8")
    topics_path.write_text(json.dumps(topics, indent=2) + "\n", encoding="utf-8")

    state.discovery_completed = True
    state.research_topics_total = len(topics)
    state.created_artifacts.append(
        {
            "path": str(findings_path.relative_to(project_root)),
            "type": "discovery",
            "action": "create",
        }
    )
    state.created_artifacts.append(
        {
            "path": str(topics_path.relative_to(project_root)),
            "type": "discovery",
            "action": "create",
        }
    )

    logger.info(
        f"Discovery complete: findings → {findings_path.relative_to(project_root)}, "
        f"{len(topics)} research topic(s) → {topics_path.relative_to(project_root)}"
    )
    return findings_path, topics_path


def _log_model_result(logger: logging.Logger, label: str, result: dict) -> None:
    """Log provider/model and output size for a phase result."""
    provider = str(result.get("provider_id") or "unknown")
    model = str(result.get("model_used") or "unknown")
    output_len = len(result.get("output") or "")
    logger.info(f"{label} model: {provider}/{model} ({output_len:,} chars returned)")
