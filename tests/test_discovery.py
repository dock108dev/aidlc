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
    # The current nomination bar is "would the planner benefit from a focused
    # note here" (not the older "would the planner have to guess" bar, which
    # under-fired by letting the model demonstrate competence in findings.md
    # while leaving the planner without options/tradeoffs material).
    lower = prompt.lower()
    assert "would the planner benefit from a focused note" in lower
    # Empty existing-research case still surfaces the section, not silence.
    assert "Existing Research" in prompt


class TestDiscoveryPromptTopicalGuidance:
    """Regression pins for the v4 prompt rewrite that combats empty-topics-list
    output. Background: discovery was returning zero topics on real BRAINDUMPs
    because the v3 prompt anchored the model on "answer in findings.md unless
    the planner would have to guess." The model demonstrated competence by
    answering current state in findings, while the planner needed
    options/tradeoffs/behavior material the docs don't cover.

    The v4 prompt:
      1. Adds a five-category topical checklist the model must walk for each
         BRAINDUMP-named system before deciding the topic list.
      2. Reframes the bar away from "guess?" toward "would the planner benefit
         from a focused note here?".
      3. Drops the load-bearing "anything you confidently know goes here, not
         into topics" sentence that pushed the model away from nominating.
      4. States that findings/topics are not mutually exclusive — knowing
         where a system lives doesn't preclude a topic on what to do about it.
    """

    def _prompt(self) -> str:
        return build_discovery_prompt("# B\n- something", "- repo: small")

    def test_version_bumped_past_v3(self):
        from aidlc.discovery_prompt import DISCOVERY_INSTRUCTIONS_VERSION

        assert DISCOVERY_INSTRUCTIONS_VERSION != "2026-04-25-v3"

    def test_topical_checklist_present(self):
        text = self._prompt().lower()
        # The five categories the model must walk for each named system.
        assert "current shape" in text or "current shape & contracts" in text
        assert "options & patterns" in text or "options" in text and "patterns" in text
        assert "cross-feature interactions" in text
        assert "prior decisions" in text and "constraints" in text
        # The "behavior under" category is the one that catches usability /
        # edge-case material findings.md doesn't cover.
        assert "behavior under" in text

    def test_findings_and_topics_are_not_mutually_exclusive(self):
        text = self._prompt().lower()
        # The reframe: knowing where a system lives doesn't preclude a topic.
        assert "not" in text and "mutually exclusive" in text

    def test_docs_answer_what_is_not_what_options(self):
        text = self._prompt().lower()
        # The new framing: docs/findings tell you "what is" — they don't
        # answer the planner's actual questions about options/tradeoffs.
        assert '"what is"' in text
        assert "what options" in text
        assert "tradeoffs" in text

    def test_old_strong_answer_anchor_removed(self):
        """The v3 line "Anything you confidently know from the scan goes here,
        not into topics." was the strongest anchor pushing zero-topic
        outputs. Make sure a future refactor doesn't restore it."""
        text = self._prompt()
        assert "Anything you confidently know from the scan goes here, not into topics" not in text

    def test_default_to_nominating_language_present(self):
        text = self._prompt().lower()
        # The new instruction explicitly biases toward nominating, with a
        # named-out reason for the inversion (the old bar under-fires on
        # product-feedback BRAINDUMPs).
        assert "default to nominating" in text or "be liberal about nominating" in text
        assert "under-fires" in text or "under fires" in text


def test_build_discovery_prompt_lists_existing_research_files():
    """When prior research files exist, the prompt lists them so the model
    won't re-nominate those topics."""
    prompt = build_discovery_prompt(
        "# Brain\n- ask",
        "- type: py",
        existing_research=["tutorial-graph-shape.md", "shelf-npc-signal.md"],
    )
    assert "Existing Research (already answered" in prompt
    assert ".aidlc/research/tutorial-graph-shape.md" in prompt
    assert ".aidlc/research/shelf-npc-signal.md" in prompt


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
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
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
    assert (run_dir / "claude_outputs" / "discovery.prompt.md").exists()
    debug_payload = json.loads((run_dir / "claude_outputs" / "discovery.debug.json").read_text())
    assert debug_payload["parsed"]["topic_count"] == 1
    assert debug_payload["result"]["success"] is True


def test_run_discovery_retries_implausibly_shallow_zero_topic_output(tmp_path, logger):
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n\n" + ("Need feature details.\n" * 300))
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    decision_type = type(
        "Decision",
        (),
        {},
    )

    def _decision(**kwargs):
        obj = decision_type()
        for key, value in kwargs.items():
            setattr(obj, key, value)
        return obj

    class FakeCLI:
        def __init__(self):
            self.execute_prompt = MagicMock()
            self.set_phase = MagicMock()
            self._decisions = [
                _decision(
                    provider_id="copilot",
                    account_id="copilot-default",
                    model="",
                    reasoning="first",
                    strategy_used="balanced",
                    fallback=False,
                    tier="standard",
                    quality_note=None,
                ),
                _decision(
                    provider_id="openai",
                    account_id=None,
                    model="gpt-5.4-mini",
                    reasoning="retry",
                    strategy_used="balanced",
                    fallback=False,
                    tier="standard",
                    quality_note=None,
                ),
            ]

        def resolve(self, phase="discovery"):
            return self._decisions.pop(0)

    cli = FakeCLI()
    cli.execute_prompt.side_effect = [
        {
            "success": True,
            "output": "# Findings\n\nTiny summary.\n\n```json\n[]\n```\n",
            "error": None,
            "retries": 0,
            "usage": {},
        },
        {
            "success": True,
            "output": (
                "# Findings\n\n"
                "Reviewed ui/main.gd and game/systems/shop.gd.\n\n"
                '```json\n[{"topic": "shop-flow", "question": "How does the shop flow branch today?", "scope": ["game/systems/shop.gd"]}]\n```\n'
            ),
            "error": None,
            "retries": 0,
            "usage": {},
        },
    ]
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
    findings_path, topics_path = run_discovery(
        state,
        config,
        cli,
        tmp_path,
        run_dir,
        logger,
        scan_result={"project_type": "gdscript", "total_docs": 62},
    )
    assert cli.execute_prompt.call_count == 2
    assert "Reviewed ui/main.gd" in findings_path.read_text()
    topics = json.loads(topics_path.read_text())
    assert topics[0]["topic"] == "shop-flow"
    assert (run_dir / "claude_outputs" / "discovery_retry.md").exists()
    retry_debug = json.loads(
        (run_dir / "claude_outputs" / "discovery_retry.debug.json").read_text()
    )
    assert retry_debug["parsed"]["topic_count"] == 1
    assert retry_debug["preflight_routing"]["provider_id"] == "openai"
    assert retry_debug["preflight_routing"]["model"] == "gpt-5.4-mini"


def test_run_discovery_does_not_retry_small_zero_topic_output(tmp_path, logger):
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- one ask\n")
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "# Findings\n\nEverything was clear.\n\n```json\n[]\n```\n",
        "error": None,
        "retries": 0,
        "usage": {},
    }
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
    run_discovery(
        state,
        config,
        cli,
        tmp_path,
        run_dir,
        logger,
        scan_result={"project_type": "py", "total_docs": 3},
    )
    assert cli.execute_prompt.call_count == 1


def test_discovery_artifacts_land_under_aidlc_not_target_repo_docs(tmp_path, logger):
    """SSOT: discovery findings + topics must land under
    ``.aidlc/discovery/`` (tool working state), never under the target
    repo's ``docs/discovery/`` tree (user-authored docs). Reintroducing
    the legacy path would silently leak generated content into the user's
    git diff."""
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- ask\n")
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "# Findings\n\nstuff\n\n```json\n[]\n```\n",
        "error": None,
        "retries": 0,
        "usage": {},
    }
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
    findings_path, topics_path = run_discovery(state, config, cli, tmp_path, run_dir, logger)
    # Positive: artifacts under .aidlc/.
    assert findings_path == tmp_path / ".aidlc" / "discovery" / "findings.md"
    assert topics_path == tmp_path / ".aidlc" / "discovery" / "topics.json"
    assert findings_path.exists()
    assert topics_path.exists()
    # Negative: the legacy path must NOT be created.
    assert not (tmp_path / "docs" / "discovery").exists()
    # state.created_artifacts records the path the orchestrator wrote — must
    # use the new prefix.
    paths = [a.get("path", "") for a in state.created_artifacts]
    assert all(p.startswith(".aidlc/") for p in paths if "discovery" in p)
    assert not any(p.startswith("docs/discovery/") for p in paths)


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
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
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
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
    test_logger = logging.getLogger("test.discovery.no_warn")
    with caplog.at_level(logging.WARNING, logger="test.discovery.no_warn"):
        run_discovery(state, config, cli, tmp_path, run_dir, test_logger)
    assert not any("no ```json topics fence" in rec.message for rec in caplog.records)


def test_run_discovery_passes_existing_research_into_prompt(tmp_path, logger):
    """If .aidlc/research/*.md is non-empty, run_discovery should inject those
    filenames into the prompt so the model doesn't re-nominate them."""
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- intent\n")
    research_dir = tmp_path / ".aidlc" / "research"
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
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
    run_discovery(state, config, cli, tmp_path, run_dir, logger)
    prompt_arg = cli.execute_prompt.call_args[0][0]
    assert ".aidlc/research/already-answered.md" in prompt_arg
    assert "Existing Research (already answered" in prompt_arg


def test_run_discovery_idempotent_when_artifacts_exist(tmp_path, logger):
    """Resume case: don't re-call the model if findings + topics are already on disk."""
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n")
    discovery_dir = tmp_path / ".aidlc" / "discovery"
    discovery_dir.mkdir(parents=True)
    (discovery_dir / "findings.md").write_text("# Findings\nold")
    (discovery_dir / "topics.json").write_text('[{"topic": "a", "question": "b", "scope": []}]')
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
    run_discovery(state, config, cli, tmp_path, run_dir, logger)
    cli.execute_prompt.assert_not_called()
    assert state.discovery_completed is True
    assert state.research_topics_total == 1


def test_run_discovery_logs_model_and_output_size(tmp_path, caplog):
    (tmp_path / "BRAINDUMP.md").write_text("# Brain\n- one ask\n")
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "# Findings\n\nx\n\n```json\n[]\n```\n",
        "error": None,
        "retries": 0,
        "usage": {},
        "provider_id": "copilot",
        "model_used": "default",
    }
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
    test_logger = logging.getLogger("test.discovery.model_log")
    with caplog.at_level(logging.INFO, logger="test.discovery.model_log"):
        run_discovery(state, config, cli, tmp_path, run_dir, test_logger)
    assert any(
        rec.message.startswith("Discovery model: copilot/default (")
        and "chars returned" in rec.message
        for rec in caplog.records
    )
    assert state.research_topics_total == 0


def test_run_discovery_no_braindump_writes_empty_artifacts(tmp_path, logger):
    """Defensive: if BRAINDUMP.md is missing, write placeholder artifacts and skip the model call."""
    run_dir = _make_run_dir(tmp_path)
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
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
    config = {"_project_root": str(tmp_path), "_aidlc_dir": str(tmp_path / ".aidlc")}
    findings_path, topics_path = run_discovery(state, config, cli, tmp_path, run_dir, logger)
    assert findings_path.exists()
    assert json.loads(topics_path.read_text()) == []
    assert state.discovery_completed is True
