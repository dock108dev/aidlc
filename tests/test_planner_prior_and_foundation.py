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
from unittest.mock import MagicMock

from aidlc.planner_helpers import (
    _enforce_prompt_budget,
    _render_foundation_docs_section,
    _render_prior_run_issues_section,
)


def _planner(tmp_path, prior_issues=None, doc_files=None, state_issues=None, config=None):
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
                "ISSUE-001", "verified", "Initial scoring", "Implemented in scorecard.py:45"
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
        state_issues=[{"id": "ISSUE-001", "title": "Already in state", "status": "verified"}],
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


def test_foundation_docs_section_renders_present_docs(tmp_path):
    arch = "# Architecture\n\nThis describes the system." + (" " * 50)
    roadmap = "# Roadmap\n\nPhases."
    p = _planner(tmp_path, doc_files=[_doc("ARCHITECTURE.md", arch), _doc("ROADMAP.md", roadmap)])
    out = "\n".join(_render_foundation_docs_section(p))
    assert "Foundation Docs (committed — incremental changes only)" in out
    assert "ARCHITECTURE.MD" in out
    assert "ROADMAP.MD" in out
    assert "Architecture" in out
    assert "Roadmap" in out


def test_foundation_docs_section_truncates_long_doc(tmp_path):
    long_arch = "# Architecture\n\n" + ("a" * 5000)
    p = _planner(
        tmp_path,
        doc_files=[_doc("ARCHITECTURE.md", long_arch)],
        config={"planning_foundation_doc_excerpt_chars": 1000},
    )
    out = "\n".join(_render_foundation_docs_section(p))
    assert "(truncated; full file at ARCHITECTURE.md)" in out


def test_foundation_docs_section_empty_when_no_foundation_docs(tmp_path):
    # Other docs present but no ROADMAP/ARCHITECTURE/DESIGN.
    p = _planner(tmp_path, doc_files=[_doc("README.md", "hi")])
    assert _render_foundation_docs_section(p) == []


# -- _enforce_prompt_budget priority --------------------------------------


def test_budget_drops_existing_first_then_prior_then_cycle_then_foundation(tmp_path):
    """When over budget, sections drop in this order; foundation drops last.

    ``_enforce_prompt_budget`` floors max_chars at 4000, so we use real-sized
    sections (~6kB each) and a budget that lets the schema + only some
    sections survive.
    """
    p = _planner(tmp_path, config={"max_planning_prompt_chars": 4000})
    big = "x" * 6000
    prompt = (
        "## Schema\nkeep me\n"
        f"## Existing Issues\nlongbody {big}\n"
        f"## Prior Run — Already Done\nlongbody {big}\n"
        f"## Previous Cycle\nlongbody {big}\n"
        f"## Foundation Docs\nlongbody {big}\n"
        "## Run State\nkeep me too\n"
    )
    assert len(prompt) > 4000  # confirm we trigger the shrink path
    shrunk = _enforce_prompt_budget(prompt, p)

    # Existing-issues section is dropped FIRST; its replacement pointer text
    # must appear once the function runs even one substitution.
    assert "Use .aidlc/planning_index.md and .aidlc/issues/*.md" in shrunk
    # Prior Run section dropped SECOND.
    assert "Prior issues exist on disk" in shrunk
    # Previous Cycle section dropped THIRD.
    assert "prior cycle notes" in shrunk
    # Foundation Docs section dropped LAST.
    assert "ROADMAP/ARCHITECTURE/DESIGN exist at project root" in shrunk
    # Schema and Run State are never dropped.
    assert "## Schema" in shrunk
    assert "## Run State" in shrunk
    assert "keep me too" in shrunk
    # Result fits the budget (with truncation marker tolerance).
    assert len(shrunk) <= 4500