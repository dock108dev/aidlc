"""Tests for the discovery phase (aidlc/discovery.py + discovery_prompt.py)."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aidlc.discovery import run_discovery
from aidlc.discovery_prompt import (
    build_discovery_prompt,
    parse_discovery_output,
    sanitize_topic_slug,
)
from aidlc.models import RunState


@pytest.fixture
def logger():
    return logging.getLogger("test.discovery")


def _make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / ".aidlc" / "runs" / "trun"
    run_dir.mkdir(parents=True)
    return run_dir


def test_build_discovery_prompt_includes_braindump_and_format():
    prompt = build_discovery_prompt("# Brain\n- ask one", "- type: py\n- docs: 3")
    assert "BRAINDUMP.md (the owner's intent)" in prompt
    assert "ask one" in prompt
    assert "Repo Summary" in prompt
    assert "Output Format" in prompt
    assert "```json" in prompt
    # The "would the planner have to guess" bar must be in the prompt — that's
    # what stops discovery from nominating topics it already knows the answer to.
    assert "would the planner have to guess" in prompt.lower()
    # Empty existing-research case still surfaces the section, not silence.
    assert "Existing Research" in prompt


def test_build_discovery_prompt_lists_existing_research_files():
    """When prior research files exist, the prompt lists them so the model
    won't re-nominate those topics."""
    prompt = build_discovery_prompt(
        "# Brain\n- ask",
        "- type: py",
        existing_research=["tutorial-graph-shape.md", "shelf-npc-signal.md"],
    )
    assert "Existing Research (already answered" in prompt
    assert "docs/research/tutorial-graph-shape.md" in prompt
    assert "docs/research/shelf-npc-signal.md" in prompt


def test_parse_discovery_output_splits_findings_and_topics():
    raw = (
        "# Findings\n\n"
        "Tutorial graph has 11 steps in game/systems/tutorial.gd.\n\n"
        '```json\n[{"topic": "tutorial-graph", "question": "How is it wired?", '
        '"scope": ["game/systems/tutorial.gd"]}]\n```\n'
    )
    findings, topics = parse_discovery_output(raw)
    assert "11 steps" in findings
    assert len(topics) == 1
    assert topics[0]["topic"] == "tutorial-graph"
    assert topics[0]["scope"] == ["game/systems/tutorial.gd"]


def test_parse_discovery_output_handles_no_topics_block():
    """If the model returns markdown but no JSON fence, treat the whole thing as findings."""
    raw = "# Findings\n\nEverything was clear from the scan."
    findings, topics = parse_discovery_output(raw)
    assert "Everything was clear" in findings
    assert topics == []


def test_parse_discovery_output_drops_malformed_json_with_warning():
    raw = "# Findings\n\nstuff\n\n```json\nnot really json{{\n```\n"
    findings, topics = parse_discovery_output(raw)
    assert "stuff" in findings
    assert topics == []


def test_parse_discovery_output_skips_invalid_topic_entries():
    """Topics missing required fields are silently dropped, not crashed."""
    raw = (
        "# Findings\n\nx\n\n"
        "```json\n["
        '{"topic": "good", "question": "ok?"}, '
        '{"topic": "missing-question"}, '
        '{"question": "missing-topic"}, '
        '"not-an-object"]\n```\n'
    )
    findings, topics = parse_discovery_output(raw)
    assert len(topics) == 1
    assert topics[0]["topic"] == "good"


def test_sanitize_topic_slug():
    assert sanitize_topic_slug("Hello World!") == "hello-world"
    assert sanitize_topic_slug("Hello World").endswith("world")
    assert sanitize_topic_slug("") == "topic"


def test_run_discovery_writes_findings_and_topics(tmp_path, logger):
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- one ask\n")
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": (
            "# Findings\n\nTutorial wired.\n\n"
            '```json\n[{"topic": "x", "question": "y?", "scope": []}]\n```\n'
        ),
        "error": None,
        "retries": 0,
        "usage": {},
    }
    config = {"_project_root": str(tmp_path)}
    findings_path, topics_path = run_discovery(
        state, config, cli, tmp_path, run_dir, logger, scan_result={"project_type": "py"}
    )
    assert findings_path.exists()
    assert topics_path.exists()
    assert "Tutorial wired" in findings_path.read_text()
    topics = json.loads(topics_path.read_text())
    assert topics[0]["topic"] == "x"
    assert state.discovery_completed is True
    assert state.research_topics_total == 1
    cli.set_phase.assert_called_with("discovery")
    # Raw output is also persisted under the run dir for inspection.
    assert (run_dir / "claude_outputs" / "discovery.md").exists()


def test_run_discovery_warns_on_truncated_output(tmp_path, caplog):
    """Regression: when discovery is killed mid-output (hard timeout, etc.),
    the model never emits the closing ```json topics fence. The current
    parser then dutifully treats the entire raw output as findings markdown
    — sometimes hundreds of KB. Emit a clear warning telling the user to
    re-run discovery rather than silently shipping that noise into
    planning."""
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- one ask\n")
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    # 60 KB of "findings" with NO ```json fence — looks just like the
    # output of a discovery run that hit hard_timeout mid-stream.
    fake_truncated = "# Findings\n\n" + ("partial tool output line\n" * 2500)
    cli.execute_prompt.return_value = {
        "success": True,
        "output": fake_truncated,
        "error": None,
        "retries": 0,
        "usage": {},
    }
    config = {"_project_root": str(tmp_path)}
    test_logger = logging.getLogger("test.discovery.warn")
    with caplog.at_level(logging.WARNING, logger="test.discovery.warn"):
        run_discovery(state, config, cli, tmp_path, run_dir, test_logger)
    assert any(
        "no ```json topics fence" in rec.message and "interrupted mid-output" in rec.message
        for rec in caplog.records
    )


def test_run_discovery_no_warning_for_normal_output(tmp_path, caplog):
    """Sanity: a normal-shape discovery output (with the topics JSON fence)
    must not trigger the truncation warning, even if findings is large."""
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- ask\n")
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    big_findings = "# Findings\n\n" + ("real finding line\n" * 3000)
    cli.execute_prompt.return_value = {
        "success": True,
        "output": big_findings + "\n```json\n[]\n```\n",
        "error": None,
        "retries": 0,
        "usage": {},
    }
    config = {"_project_root": str(tmp_path)}
    test_logger = logging.getLogger("test.discovery.no_warn")
    with caplog.at_level(logging.WARNING, logger="test.discovery.no_warn"):
        run_discovery(state, config, cli, tmp_path, run_dir, test_logger)
    assert not any("no ```json topics fence" in rec.message for rec in caplog.records)


def test_run_discovery_passes_existing_research_into_prompt(tmp_path, logger):
    """If docs/research/*.md is non-empty, run_discovery should inject those
    filenames into the prompt so the model doesn't re-nominate them."""
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- intent\n")
    research_dir = tmp_path / "docs" / "research"
    research_dir.mkdir(parents=True)
    (research_dir / "already-answered.md").write_text("# Research")
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "# Findings\n\nx\n\n```json\n[]\n```\n",
        "error": None,
        "retries": 0,
        "usage": {},
    }
    config = {"_project_root": str(tmp_path)}
    run_discovery(state, config, cli, tmp_path, run_dir, logger)
    prompt_arg = cli.execute_prompt.call_args[0][0]
    assert "docs/research/already-answered.md" in prompt_arg
    assert "Existing Research (already answered" in prompt_arg


def test_run_discovery_idempotent_when_artifacts_exist(tmp_path, logger):
    """Resume case: don't re-call the model if findings + topics are already on disk."""
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n")
    discovery_dir = tmp_path / "docs" / "discovery"
    discovery_dir.mkdir(parents=True)
    (discovery_dir / "findings.md").write_text("# Findings\nold")
    (discovery_dir / "topics.json").write_text('[{"topic": "a", "question": "b", "scope": []}]')
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    config = {"_project_root": str(tmp_path)}
    run_discovery(state, config, cli, tmp_path, run_dir, logger)
    cli.execute_prompt.assert_not_called()
    assert state.discovery_completed is True
    assert state.research_topics_total == 1


def test_run_discovery_no_braindump_writes_empty_artifacts(tmp_path, logger):
    """Defensive: if BRAINDUMP.md is missing, write placeholder artifacts and skip the model call."""
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    config = {"_project_root": str(tmp_path)}
    findings_path, topics_path = run_discovery(state, config, cli, tmp_path, run_dir, logger)
    cli.execute_prompt.assert_not_called()
    assert "No BRAINDUMP.md" in findings_path.read_text()
    assert json.loads(topics_path.read_text()) == []
    assert state.research_topics_total == 0


def test_run_discovery_model_failure_writes_placeholder(tmp_path, logger):
    """If the model call fails, planning should still proceed — write empty artifacts."""
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- one\n")
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": False,
        "output": "",
        "error": "boom",
        "retries": 0,
        "usage": {},
    }
    config = {"_project_root": str(tmp_path)}
    findings_path, topics_path = run_discovery(state, config, cli, tmp_path, run_dir, logger)
    assert findings_path.exists()
    assert json.loads(topics_path.read_text()) == []
    assert state.discovery_completed is True
