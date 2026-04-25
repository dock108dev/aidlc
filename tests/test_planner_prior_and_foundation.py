"""Tests for planner prompt sections introduced by ISSUE-005 and ISSUE-006:

- ``_render_prior_run_issues_section`` — prior-run issues from disk get rendered
  under "Prior Run — Already Done (do not redo)" framing.
- ``_render_foundation_docs_section`` — ROADMAP/ARCHITECTURE/DESIGN excerpts
  appear in the planning prompt with "committed — incremental changes only".
- ``_enforce_prompt_budget`` — the new sections drop in the right priority order.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from aidlc.planner_helpers import (
    _enforce_prompt_budget,
    _render_foundation_docs_section,
    _render_prior_run_issues_section,
)


def _planner(
    tmp_path, prior_issues=None, doc_files=None, state_issues=None, config=None
):
    """Build a minimal planner stub for the helpers under test."""
    issues_dir = tmp_path / ".aidlc" / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "_project_root": str(tmp_path),
        "_aidlc_dir": str(tmp_path / ".aidlc"),
        "_issues_dir": str(issues_dir),
        "max_planning_prompt_chars": 60000,
    }
    if config:
        cfg.update(config)
    state = SimpleNamespace(
        issues=list(state_issues or []),
        plan_elapsed_seconds=0,
        plan_budget_seconds=14400,
        planning_cycles=0,
        issues_created=0,
        files_created=0,
        phase=SimpleNamespace(value="planning"),
    )
    return SimpleNamespace(
        config=cfg,
        state=state,
        existing_issues=list(prior_issues or []),
        doc_files=list(doc_files or []),
        logger=logging.getLogger("test.planner.prior_foundation"),
    )


def _prior_entry(issue_id: str, status: str, title: str, notes: str = ""):
    return {
        "path": f".aidlc/issues/{issue_id}.md",
        "content": f"# {issue_id}: {title}\n",
        "parsed_issue": {
            "id": issue_id,
            "title": title,
            "status": status,
            "priority": "medium",
            "implementation_notes": notes,
        },
    }


# -- ISSUE-005 -------------------------------------------------------------


def test_prior_run_section_renders_when_disk_issues_present(tmp_path):
    p = _planner(
        tmp_path,
        prior_issues=[
            _prior_entry(
                "ISSUE-001",
                "verified",
                "Initial scoring",
                "Implemented in scorecard.py:45",
            ),
            _prior_entry("ISSUE-002", "implemented", "Cup detection"),
        ],
    )
    out = "\n".join(_render_prior_run_issues_section(p))
    assert "Prior Run — Already Done (do not redo)" in out
    assert "ISSUE-001 [verified]: Initial scoring" in out
    assert "scorecard.py:45" in out  # notes excerpt rendered
    assert "ISSUE-002 [implemented]: Cup detection" in out


def test_prior_run_section_empty_when_no_disk_issues(tmp_path):
    p = _planner(tmp_path, prior_issues=[])
    assert _render_prior_run_issues_section(p) == []


def test_prior_run_section_dedupes_against_current_state(tmp_path):
    """An issue already in state.issues isn't duplicated in the prior section."""
    p = _planner(
        tmp_path,
        prior_issues=[
            _prior_entry("ISSUE-001", "verified", "Already in state"),
            _prior_entry("ISSUE-009", "implemented", "Only on disk"),
        ],
        state_issues=[
            {"id": "ISSUE-001", "title": "Already in state", "status": "verified"}
        ],
    )
    out = "\n".join(_render_prior_run_issues_section(p))
    assert "ISSUE-001" not in out
    assert "ISSUE-009 [implemented]: Only on disk" in out


def test_prior_run_section_skips_unparsable_entries(tmp_path):
    p = _planner(
        tmp_path,
        prior_issues=[
            {"path": "stub.md", "content": "x", "parsed_issue": None},
            _prior_entry("ISSUE-005", "pending", "Real one"),
        ],
    )
    out = "\n".join(_render_prior_run_issues_section(p))
    assert "ISSUE-005" in out


# -- ISSUE-006 -------------------------------------------------------------


def _doc(path, content):
    return {"path": path, "content": content, "priority": 1, "size": len(content)}


def test_foundation_section_only_renders_root_braindump(tmp_path):
    """The foundation section is BRAINDUMP-only. Support docs (ROADMAP,
    ARCHITECTURE, DESIGN, CLAUDE) are no longer injected — the planner
    reads them on demand instead. That's the whole simplification."""
    brain = "# Brain Dump\n\nI want a 9-hole mini-golf course with named holes."
    arch = "# Architecture\n\nThree.js + Cannon-es."
    roadmap = "# Roadmap\n\nPhases."
    p = _planner(
        tmp_path,
        doc_files=[
            _doc("BRAINDUMP.md", brain),
            _doc("ARCHITECTURE.md", arch),
            _doc("ROADMAP.md", roadmap),
        ],
    )
    out = "\n".join(_render_foundation_docs_section(p))
    assert "BRAINDUMP — Scope Source" in out
    assert "9-hole mini-golf" in out
    # Support docs are NOT inlined.
    assert "Three.js" not in out
    assert "Phases." not in out


def test_foundation_section_frames_braindump_as_authoritative(tmp_path):
    """Framing must call BRAINDUMP authoritative and warn other docs are
    reference-only — that's what stops the planner from chasing roadmap
    phases or audit findings BRAINDUMP told it to ignore."""
    brain = "# Brain Dump\n\nI want a 9-hole mini-golf course with named holes."
    p = _planner(tmp_path, doc_files=[_doc("BRAINDUMP.md", brain)])
    out = "\n".join(_render_foundation_docs_section(p))
    assert "authoritative" in out.lower()
    assert "exclusion" in out.lower() or "cut" in out.lower()
    assert "research" in out  # research trigger preserved


def test_foundation_section_renders_braindump_in_full(tmp_path):
    """BRAINDUMP is rendered uncapped — truncating it is how its asks go
    missing. There is no support-doc excerpt cap to honor anymore."""
    big_brain = "# Brain Dump\n\n" + "\n".join(
        f"- Ask {i}: concrete requirement number {i}" for i in range(200)
    )
    p = _planner(
        tmp_path,
        doc_files=[_doc("BRAINDUMP.md", big_brain)],
        config={"planning_foundation_doc_excerpt_chars": 1000},
    )
    out = "\n".join(_render_foundation_docs_section(p))
    assert "Ask 0:" in out
    assert "Ask 199:" in out
    assert "truncated" not in out


def test_foundation_section_prefers_root_braindump_over_nested(tmp_path):
    """Nested files like docs/audits/braindump.md must NOT shadow the root
    BRAINDUMP. This is the regression that produced 16 issues from a stale
    audit doc instead of the active scope source."""
    p = _planner(
        tmp_path,
        doc_files=[
            _doc("docs/audits/braindump.md", "STALE AUDIT — do not use"),
            _doc("BRAINDUMP.md", "ACTIVE SCOPE — use this one"),
        ],
    )
    out = "\n".join(_render_foundation_docs_section(p))
    assert "ACTIVE SCOPE" in out
    assert "STALE AUDIT" not in out


def test_foundation_section_empty_without_root_braindump(tmp_path):
    # Other docs present (including a nested braindump) but no root one.
    p = _planner(
        tmp_path,
        doc_files=[
            _doc("README.md", "hi"),
            _doc("ARCHITECTURE.md", "x"),
            _doc("docs/audits/braindump.md", "stale"),
        ],
    )
    assert _render_foundation_docs_section(p) == []


# -- _enforce_prompt_budget priority --------------------------------------


def test_budget_drops_existing_first_then_prior_then_cycle_then_braindump(tmp_path):
    """Drop order under budget pressure: existing issues → prior run →
    previous cycle → BRAINDUMP scope source (last resort, replaced with a
    pointer)."""
    p = _planner(tmp_path, config={"max_planning_prompt_chars": 4000})
    big = "x" * 6000
    prompt = (
        "## Schema\nkeep me\n"
        f"## Existing Issues\nlongbody {big}\n"
        f"## Prior Run — Already Done\nlongbody {big}\n"
        f"## Previous Cycle\nlongbody {big}\n"
        f"## BRAINDUMP — Scope Source (authoritative)\nlongbody {big}\n"
        "## Run State\nkeep me too\n"
    )
    assert len(prompt) > 4000
    shrunk = _enforce_prompt_budget(prompt, p)

    assert "Use .aidlc/planning_index.md and .aidlc/issues/*.md" in shrunk
    assert "Prior issues exist on disk" in shrunk
    assert "prior cycle notes" in shrunk
    assert "Read BRAINDUMP.md at the project root" in shrunk
    assert "## Schema" in shrunk
    assert "## Run State" in shrunk
    assert "keep me too" in shrunk
    assert len(shrunk) <= 4500
