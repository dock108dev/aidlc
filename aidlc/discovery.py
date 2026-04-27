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

    cli.set_phase("discovery")
    logger.info("Running discovery — investigating repo against BRAINDUMP intent...")
    result = cli.execute_prompt(prompt, project_root)
    state.record_provider_result(result, config, phase="discovery")

    raw_output = result.get("output") or ""
    outputs_dir = run_dir / "claude_outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "discovery.md").write_text(raw_output, encoding="utf-8")

    if not result.get("success"):
        logger.error(f"Discovery model call failed: {result.get('error')}")
        # Write empty artifacts so resume doesn't loop indefinitely; planning still runs.
        findings_path.write_text(
            "# Findings\n\n_Discovery model call failed; planning will proceed without findings._\n"
        )
        topics_path.write_text("[]\n")
        state.discovery_completed = True
        state.research_topics_total = 0
        return findings_path, topics_path

    findings, topics = parse_discovery_output(raw_output)
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
