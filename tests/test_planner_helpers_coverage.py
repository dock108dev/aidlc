"""Unit tests for aidlc.planner_helpers — research, prompts, planning index, foundation."""

import logging
from pathlib import Path
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
    execute_research,
    load_last_cycle_notes,
    render_issue_md,
    save_cycle_notes,
    write_planning_index,
)
from aidlc.schemas import PlanningAction


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
        "research_max_per_cycle": 5,
        "research_max_scope_files": 3,
        "research_max_source_chars": 30,
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
    rdir = tmp_path / "docs" / "research"
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
        {"path": f"extra{i}.md", "content": "c", "size": 1, "priority": 2}
        for i in range(35)
    ]
    planner = Planner(state, run_dir, cfg, cli, "ctx", logger, doc_files=doc_files)
    path = write_planning_index(planner)
    assert path.exists()
    body = path.read_text()
    assert "Completed Research" in body
    assert "Other Project Docs" in body
    assert "and 5 more" in body or "more" in body


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
    planner._offer_completion = True
    with patch(
        "aidlc.planner_helpers.write_planning_index", return_value=tmp_path / "idx.md"
    ):
        prompt = build_prompt(planner, is_finalization=False)
    assert "Critical Doc Gaps" in prompt
    assert "non-critical" in prompt
    assert "completion" in prompt.lower() or "offer" in prompt.lower()


def _fake_planner_for_research(tmp_path, run_dir, state, cli, cfg, **cfg_over):
    merged = {**cfg, **cfg_over}
    return SimpleNamespace(
        project_root=tmp_path,
        run_dir=run_dir,
        config=merged,
        logger=MagicMock(),
        cli=cli,
        state=state,
        _cycle_research_count=0,
        _research_count=0,
    )


def test_execute_research_deferred_when_cap(tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    planner = SimpleNamespace(
        project_root=tmp_path,
        run_dir=run_dir,
        config={**cfg, "research_max_per_cycle": 0},
        logger=MagicMock(),
        cli=cli,
        state=state,
        _cycle_research_count=1,
        _research_count=0,
    )
    action = PlanningAction(
        action_type="research",
        research_topic="Topic",
        research_question="Q?",
    )
    execute_research(planner, action)
    planner.cli.execute_prompt.assert_not_called()


def test_execute_research_skips_when_file_exists(tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    rdir = tmp_path / "docs" / "research"
    rdir.mkdir(parents=True)
    (rdir / "topic-one.md").write_text("exists")
    state = RunState(run_id="r", config_name="c")
    planner = _fake_planner_for_research(tmp_path, run_dir, state, MagicMock(), cfg)
    action = PlanningAction(
        action_type="research",
        research_topic="Topic One!",
        research_question="Q?",
    )
    execute_research(planner, action)
    planner.cli.execute_prompt.assert_not_called()


def test_execute_research_success_writes_files(tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    scope_rel = "src/scope.txt"
    (tmp_path / "src").mkdir()
    (tmp_path / scope_rel).write_text("line\n" * 20)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "# Findings\nBody here.",
        "error": None,
        "retries": 0,
        "usage": {},
    }
    planner = _fake_planner_for_research(tmp_path, run_dir, state, cli, cfg)
    action = PlanningAction(
        action_type="research",
        research_topic="My Topic",
        research_question="What?",
        research_scope=[scope_rel],
    )
    execute_research(planner, action)
    out = tmp_path / "docs" / "research" / "my-topic.md"
    assert out.exists()
    assert "Findings" in out.read_text()
    assert any("research" in a.get("type", "") for a in state.created_artifacts)
    assert (run_dir / "claude_outputs" / "research_my-topic.md").exists()


def test_execute_research_truncates_scope_and_warns_missing(tmp_path, caplog):
    cfg, run_dir = _base_config(tmp_path)
    (tmp_path / "a.txt").write_text("Z" * 200)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "ok",
        "error": None,
        "retries": 0,
        "usage": {},
    }
    planner = _fake_planner_for_research(tmp_path, run_dir, state, cli, cfg)
    action = PlanningAction(
        action_type="research",
        research_topic="Trunc",
        research_question="Q",
        research_scope=["a.txt", "missing.txt"],
    )
    with caplog.at_level(logging.WARNING):
        execute_research(planner, action)
    assert "Scope file not found" in caplog.text or planner.logger.warning.called
    cli.execute_prompt.assert_called_once()


def test_execute_research_scope_read_oserror(tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    scope_rel = "err.txt"
    p = tmp_path / scope_rel
    p.write_text("x")
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "body",
        "error": None,
        "retries": 0,
        "usage": {},
    }
    planner = _fake_planner_for_research(tmp_path, run_dir, state, cli, cfg)
    action = PlanningAction(
        action_type="research",
        research_topic="ErrRead",
        research_question="Q",
        research_scope=[scope_rel],
    )
    real_read = Path.read_text

    def boom(self, *a, **kw):
        if self.resolve() == p.resolve():
            raise OSError("read fail")
        return real_read(self, *a, **kw)

    with patch.object(Path, "read_text", boom):
        execute_research(planner, action)
    planner.logger.warning.assert_called()


def test_execute_research_cli_fails(tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": False,
        "output": "",
        "error": "boom",
        "retries": 0,
        "usage": {},
    }
    planner = _fake_planner_for_research(tmp_path, run_dir, state, cli, cfg)
    action = PlanningAction(
        action_type="research",
        research_topic="FailCli",
        research_question="Q",
    )
    execute_research(planner, action)
    planner.logger.error.assert_called()


def test_execute_research_empty_output(tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "",
        "error": None,
        "retries": 0,
        "usage": {},
    }
    planner = _fake_planner_for_research(tmp_path, run_dir, state, cli, cfg)
    action = PlanningAction(
        action_type="research",
        research_topic="EmptyOut",
        research_question="Q",
    )
    execute_research(planner, action)
    planner.logger.warning.assert_called()


def test_execute_research_permission_chatter_retry_then_ok(tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    chatter = "The write tool needs your permission to save docs/research/x.md"
    cli.execute_prompt.side_effect = [
        {"success": True, "output": chatter, "error": None, "retries": 0, "usage": {}},
        {
            "success": True,
            "output": "# Real\nResearch",
            "error": None,
            "retries": 0,
            "usage": {},
        },
    ]
    planner = _fake_planner_for_research(tmp_path, run_dir, state, cli, cfg)
    action = PlanningAction(
        action_type="research",
        research_topic="Perm Retry",
        research_question="Q?",
    )
    execute_research(planner, action)
    assert cli.execute_prompt.call_count == 2
    assert (tmp_path / "docs" / "research" / "perm-retry.md").exists()


def test_execute_research_retry_still_fails(tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    bad = "approve the write permission"
    cli.execute_prompt.side_effect = [
        {"success": True, "output": bad, "error": None, "retries": 0, "usage": {}},
        {"success": False, "output": "", "error": "nope", "retries": 0, "usage": {}},
    ]
    planner = _fake_planner_for_research(tmp_path, run_dir, state, cli, cfg)
    action = PlanningAction(
        action_type="research",
        research_topic="Bad Retry",
        research_question="Q?",
    )
    execute_research(planner, action)
    planner.logger.error.assert_called()


def test_execute_research_retry_still_chatter(tmp_path):
    cfg, run_dir = _base_config(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    bad = "needs your write permission"
    cli.execute_prompt.side_effect = [
        {"success": True, "output": bad, "error": None, "retries": 0, "usage": {}},
        {"success": True, "output": bad, "error": None, "retries": 0, "usage": {}},
    ]
    planner = _fake_planner_for_research(tmp_path, run_dir, state, cli, cfg)
    action = PlanningAction(
        action_type="research",
        research_topic="Chatter2",
        research_question="Q?",
    )
    execute_research(planner, action)
    planner.logger.error.assert_called()


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


# `upsert_doc_file` was removed along with the foundation-doc framework —
# the planner no longer authors or recomputes status for support docs.
