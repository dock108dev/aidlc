"""Unit tests for aidlc.planner_helpers — prompts, planning index, foundation."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aidlc.audit_models import DocGap
from aidlc.models import Issue, IssueStatus, RunPhase, RunState
from aidlc.planner import Planner
from aidlc.planner_helpers import (
    _enforce_prompt_budget,
    _issue_number,
    _render_existing_issues_section,
    _render_foundation_docs_section,
    build_prompt,
    load_last_cycle_notes,
    render_issue_md,
    save_cycle_notes,
    write_planning_index,
)


@pytest.fixture
def logger():
    return logging.getLogger("test.planner_helpers")


def _base_config(tmp_path):
    aidlc_dir = tmp_path / ".aidlc"
    aidlc_dir.mkdir(parents=True, exist_ok=True)
    (aidlc_dir / "issues").mkdir(parents=True)
    (aidlc_dir / "runs").mkdir()
    run_dir = aidlc_dir / "runs" / "trun"
    run_dir.mkdir(parents=True)
    return {
        "_project_root": str(tmp_path),
        "_aidlc_dir": str(aidlc_dir),
        "_issues_dir": str(aidlc_dir / "issues"),
        "_runs_dir": str(aidlc_dir / "runs"),
        "max_planning_prompt_chars": 8000,
        "planning_last_cycle_notes_max_chars": 500,
        "planning_issue_index_max_items": 12,
        "planning_issue_index_include_all_until": 5,
    }, run_dir


def test_issue_number_suffix_and_invalid():
    assert _issue_number("ISSUE-042") == 42
    assert _issue_number("no-digits") == -1
    assert _issue_number("") == -1


def test_render_existing_issues_compact_branch(logger, tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    planner = SimpleNamespace(
        state=RunState(run_id="r", config_name="c"),
        config=cfg,
        logger=logger,
        run_dir=run_dir,
        project_root=tmp_path,
    )
    issues = []
    for i in range(20):
        issues.append(
            {
                "id": f"ISSUE-{100 + i:03d}",
                "title": "T" * 95,
                "priority": "high" if i < 3 else "medium",
                "status": "pending" if i < 8 else "verified",
                "dependencies": ["ISSUE-001"] if i == 0 else [],
            }
        )
    planner.state.issues = issues
    lines = _render_existing_issues_section(planner)
    text = "\n".join(lines)
    assert "Compact issue index" in text
    assert "Omitted from inline list" in text or len(lines) > 5
    assert "deps:" in text


def test_enforce_prompt_budget_shrinks_and_truncates(logger, tmp_path):
    cfg, _ = _base_config(tmp_path)
    # Effective budget is max(4000, configured); exceed a 5000 cap to exercise all shrink paths.
    cfg["max_planning_prompt_chars"] = 5000
    planner = SimpleNamespace(config=cfg, logger=logger)
    prompt = (
        "HEAD\n## Existing Issues\n"
        + "x" * 3000
        + "\n## Previous Cycle\n"
        + "y" * 3000
        + "\n## Tail\n"
        + "z" * 500
    )
    assert len(prompt) > 5000
    out = _enforce_prompt_budget(prompt, planner)
    assert len(out) < len(prompt)
    assert (
        "[planning prompt truncated" in out
        or "Use .aidlc/planning_index" in out
        or "See prior cycle notes" in out
    )


def test_write_planning_index_research_issues_other_docs(logger, tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    (tmp_path / "README.md").write_text("# R")
    rdir = tmp_path / ".aidlc" / "research"
    rdir.mkdir(parents=True)
    (rdir / "a.md").write_text("r")
    for i in range(35):
        (tmp_path / f"extra{i}.md").write_text("x")
    state = RunState(run_id="r", config_name="c")
    state.issues = [
        {
            "id": "ISSUE-1",
            "title": "A",
            "priority": "high",
            "status": "pending",
            "labels": ["x"],
        },
        {
            "id": "ISSUE-2",
            "title": "B",
            "priority": "low",
            "status": "verified",
            "labels": [],
        },
    ]
    cli = MagicMock()
    doc_files = [
        {"path": f"extra{i}.md", "content": "c", "size": 1, "priority": 2} for i in range(35)
    ]
    planner = Planner(state, run_dir, cfg, cli, "ctx", logger, doc_files=doc_files)
    path = write_planning_index(planner)
    assert path.exists()
    body = path.read_text()
    assert "Research" in body
    assert ".aidlc/research/a.md" in body
    assert "Other Project Docs" in body
    assert "and 5 more" in body or "more" in body


def test_write_planning_index_includes_discovery_section(logger, tmp_path):
    """Discovery findings + topics.json should be listed in the index when present."""
    cfg, run_dir = _base_config(tmp_path)
    discovery_dir = tmp_path / ".aidlc" / "discovery"
    discovery_dir.mkdir(parents=True)
    (discovery_dir / "findings.md").write_text("# Findings\nstuff")
    (discovery_dir / "topics.json").write_text("[]")
    state = RunState(run_id="r", config_name="c")
    planner = Planner(state, run_dir, cfg, MagicMock(), "ctx", logger)
    path = write_planning_index(planner)
    body = path.read_text()
    assert "## Discovery" in body
    assert ".aidlc/discovery/findings.md" in body
    assert ".aidlc/discovery/topics.json" in body


def test_build_prompt_doc_gaps_and_foundation(logger, tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    (tmp_path / "README.md").write_text("# ok")
    state = RunState(run_id="r", config_name="c")
    state.phase = RunPhase.PLANNING
    state.plan_budget_seconds = 3600.0
    state.plan_elapsed_seconds = 10.0
    gaps = [
        DocGap("a.md", 1, "TBD", "x" * 100, severity="critical"),
        DocGap("b.md", 2, "x", "y", severity="warning"),
    ]
    cli = MagicMock()
    planner = Planner(state, run_dir, cfg, cli, "ctx", logger, doc_gaps=gaps)
    # Verify mode is the SSOT for the completion-check prompt path —
    # `_offer_completion` was removed in the verify-mode pivot.
    planner._verify_mode = True
    with patch("aidlc.planner_helpers.write_planning_index", return_value=tmp_path / "idx.md"):
        prompt = build_prompt(planner, is_finalization=False)
    assert "Critical Doc Gaps" in prompt
    assert "non-critical" in prompt
    # Verify-mode prompt is injected when `_verify_mode=True`.
    assert "VERIFY MODE" in prompt


def test_build_prompt_points_at_discovery_and_research_without_embedding(logger, tmp_path):
    """SSOT: discovery findings + research files are referenced as
    *file pointers* in the planning prompt, NOT embedded inline. The
    model has file-read tools; embedding hundreds of KB of pre-built
    artifacts in every cycle is wasteful (cache_read tokens, prompt
    budget) and brittle (a partial / killed discovery run can write
    garbage that explodes the prompt)."""
    cfg, run_dir = _base_config(tmp_path)
    discovery_dir = tmp_path / ".aidlc" / "discovery"
    discovery_dir.mkdir(parents=True)
    (discovery_dir / "findings.md").write_text("# Findings\ntutorial system has 11 steps wired")
    research_dir = tmp_path / ".aidlc" / "research"
    research_dir.mkdir(parents=True)
    (research_dir / "tutorial-graph-shape.md").write_text("# Research")
    state = RunState(run_id="r", config_name="c")
    state.phase = RunPhase.PLANNING
    cli = MagicMock()
    planner = Planner(state, run_dir, cfg, cli, "ctx", logger)
    with patch("aidlc.planner_helpers.write_planning_index", return_value=tmp_path / "idx.md"):
        prompt = build_prompt(planner, is_finalization=False)
    # Section header + pointers are present...
    assert "Discovery & Research" in prompt
    assert ".aidlc/discovery/findings.md" in prompt
    assert ".aidlc/research/tutorial-graph-shape.md" in prompt
    # ...but the full content of findings.md is NOT embedded.
    assert "tutorial system has 11 steps wired" not in prompt


def test_build_prompt_does_not_explode_on_huge_findings_file(logger, tmp_path):
    """Regression: a 5 MB findings.md (e.g. partial output from an
    interrupted discovery run) used to balloon the planning prompt
    because the entire file was embedded verbatim. The pointer-only
    discovery section means the prompt size is independent of
    findings.md size."""
    cfg, run_dir = _base_config(tmp_path)
    discovery_dir = tmp_path / ".aidlc" / "discovery"
    discovery_dir.mkdir(parents=True)
    huge = "MARKER_AT_START\n" + ("x" * 5_000_000) + "\nMARKER_AT_END"
    (discovery_dir / "findings.md").write_text(huge)
    state = RunState(run_id="r", config_name="c")
    state.phase = RunPhase.PLANNING
    cli = MagicMock()
    planner = Planner(state, run_dir, cfg, cli, "ctx", logger)
    with patch("aidlc.planner_helpers.write_planning_index", return_value=tmp_path / "idx.md"):
        prompt = build_prompt(planner, is_finalization=False)
    # Pointer is in the prompt; the giant content is NOT.
    assert ".aidlc/discovery/findings.md" in prompt
    assert "MARKER_AT_START" not in prompt
    assert "MARKER_AT_END" not in prompt
    # Prompt is small (well under 1 MB; was ~5 MB embedded before).
    assert len(prompt) < 200_000


def test_render_foundation_section_renders_root_braindump(logger, tmp_path):
    """The foundation section renders the ROOT BRAINDUMP.md only — nested
    files like docs/audits/braindump.md must NOT shadow it. This is the
    regression that produced 16 issues from a stale audit doc instead of
    the active BRAINDUMP."""
    cfg, run_dir = _base_config(tmp_path)
    state = RunState(run_id="r", config_name="c")
    docs = [
        {
            "path": "docs/audits/braindump.md",
            "content": "STALE AUDIT — do not use",
            "size": 24,
            "priority": 1,
        },
        {
            "path": "BRAINDUMP.md",
            "content": "ACTIVE SCOPE — use this one",
            "size": 27,
            "priority": 1,
        },
    ]
    planner = Planner(state, run_dir, cfg, MagicMock(), "ctx", logger, doc_files=docs)
    section = "\n".join(_render_foundation_docs_section(planner))
    assert "ACTIVE SCOPE" in section
    assert "STALE AUDIT" not in section


def test_render_foundation_section_empty_without_root_braindump(logger, tmp_path):
    """No root BRAINDUMP.md → no foundation section rendered. Nested
    braindump files do not satisfy the contract."""
    cfg, run_dir = _base_config(tmp_path)
    state = RunState(run_id="r", config_name="c")
    docs = [
        {"path": "docs/audits/braindump.md", "content": "x", "size": 1, "priority": 1},
        {"path": "ARCHITECTURE.md", "content": "y", "size": 1, "priority": 1},
    ]
    planner = Planner(state, run_dir, cfg, MagicMock(), "ctx", logger, doc_files=docs)
    assert _render_foundation_docs_section(planner) == []


def test_render_issue_md_with_notes():
    issue = Issue(
        id="I-1",
        title="T",
        description="D",
        priority="high",
        acceptance_criteria=["A1"],
        status=IssueStatus.IMPLEMENTED,
        implementation_notes="did it",
    )
    md = render_issue_md(issue)
    assert "Implementation Notes" in md


def test_load_last_cycle_notes_corrupt(tmp_path):
    p = tmp_path / "planning_context.json"
    p.write_text("not json{{{")
    assert load_last_cycle_notes(tmp_path) == ""


def test_save_cycle_notes_roundtrip(tmp_path):
    save_cycle_notes(tmp_path, "frontier ok", "notes here", 3)
    assert "planning cycle (3)" in load_last_cycle_notes(tmp_path)
