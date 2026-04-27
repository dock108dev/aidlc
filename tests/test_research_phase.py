"""Tests for the research phase (aidlc/research_phase.py)."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aidlc.models import RunState
from aidlc.research_phase import (
    execute_research_topic,
    run_research_phase,
)


@pytest.fixture
def logger():
    return logging.getLogger("test.research_phase")


def _make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / ".aidlc" / "runs" / "trun"
    run_dir.mkdir(parents=True)
    return run_dir


def _config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_aidlc_dir": str(tmp_path / ".aidlc"),
        "research_max_scope_files": 5,
        "research_max_source_chars": 200,
        "research_phase_max_topics": 20,
    }


def _ok_result(output: str) -> dict:
    return {"success": True, "output": output, "error": None, "retries": 0, "usage": {}}


def test_execute_research_topic_writes_file(tmp_path, logger):
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = _ok_result("# Findings\nbody here")
    run_dir = _make_run_dir(tmp_path)
    wrote = execute_research_topic(
        "tutorial-graph",
        "How is it wired?",
        [],
        cli,
        tmp_path,
        run_dir,
        state,
        _config(tmp_path),
        logger,
    )
    assert wrote is True
    out = tmp_path / ".aidlc" / "research" / "tutorial-graph.md"
    assert out.exists()
    assert "Findings" in out.read_text()
    assert (run_dir / "claude_outputs" / "research_tutorial-graph.md").exists()


def test_research_artifacts_land_under_aidlc_not_target_repo_docs(tmp_path, logger):
    """SSOT: research artifacts must land under ``.aidlc/research/`` (tool
    working state), never under the target repo's ``docs/research/`` tree
    (user-authored docs). Reintroducing the legacy path would silently
    leak generated content into the user's git diff."""
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = _ok_result("# Findings\nstuff")
    run_dir = _make_run_dir(tmp_path)
    execute_research_topic(
        "ssot-path-check",
        "Where do research files land?",
        [],
        cli,
        tmp_path,
        run_dir,
        state,
        _config(tmp_path),
        logger,
    )
    assert (tmp_path / ".aidlc" / "research" / "ssot-path-check.md").exists()
    # Negative: the legacy path must NOT be created.
    assert not (tmp_path / "docs" / "research").exists()
    # state.created_artifacts records the path the model wrote — must use
    # the new prefix.
    paths = [a.get("path", "") for a in state.created_artifacts]
    assert all(p.startswith(".aidlc/") for p in paths if "research" in p)
    assert not any(p.startswith("docs/research/") for p in paths)


def test_execute_research_topic_skips_when_file_exists(tmp_path, logger):
    rdir = tmp_path / ".aidlc" / "research"
    rdir.mkdir(parents=True)
    (rdir / "already.md").write_text("done")
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    run_dir = _make_run_dir(tmp_path)
    wrote = execute_research_topic(
        "Already!",
        "Q?",
        [],
        cli,
        tmp_path,
        run_dir,
        state,
        _config(tmp_path),
        logger,
    )
    assert wrote is False
    cli.execute_prompt.assert_not_called()


def test_execute_research_topic_internal_repo_scope_archaeology(tmp_path, logger):
    """Repo-archaeology: scope can be internal source files; their contents reach the prompt."""
    (tmp_path / "game" / "systems").mkdir(parents=True)
    target = tmp_path / "game" / "systems" / "tutorial.gd"
    target.write_text("# 11-step graph\nfunc step_1():\n    pass\n")
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = _ok_result("# Findings\nThe graph has 11 steps.")
    run_dir = _make_run_dir(tmp_path)
    execute_research_topic(
        "tutorial-current-behavior",
        "How is the 11-step graph wired?",
        ["game/systems/tutorial.gd"],
        cli,
        tmp_path,
        run_dir,
        state,
        _config(tmp_path),
        logger,
    )
    out = tmp_path / ".aidlc" / "research" / "tutorial-current-behavior.md"
    assert out.exists()
    assert "11 steps" in out.read_text()
    prompt_arg = cli.execute_prompt.call_args[0][0]
    assert "game/systems/tutorial.gd" in prompt_arg
    assert "11-step graph" in prompt_arg


def test_execute_research_topic_directory_scope_lists_source_files(tmp_path, logger):
    """When discovery nominates a directory as scope (e.g. game/autoload/), the
    research prompt should list the source files inside instead of warning
    'not found'. Files are listed (not inlined) so the model uses its read
    tools — keeps prompt budget under control."""
    autoload = tmp_path / "game" / "autoload"
    autoload.mkdir(parents=True)
    (autoload / "camera_authority.gd").write_text("# camera handoff")
    (autoload / "input_router.gd").write_text("# input")
    (autoload / "binary.dat").write_bytes(b"\x00\x01")  # ignored — not a source ext
    (autoload / ".hidden.gd").write_text("ignored")  # hidden — ignored
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = _ok_result("# Findings\nok")
    run_dir = _make_run_dir(tmp_path)
    execute_research_topic(
        "camera-authority-handoff",
        "How does the camera-authority handoff work?",
        ["game/autoload/"],
        cli,
        tmp_path,
        run_dir,
        state,
        _config(tmp_path),
        logger,
    )
    prompt_arg = cli.execute_prompt.call_args[0][0]
    assert "Scope Directories" in prompt_arg
    assert "game/autoload" in prompt_arg
    assert "camera_authority.gd" in prompt_arg
    assert "input_router.gd" in prompt_arg
    assert "binary.dat" not in prompt_arg
    assert ".hidden" not in prompt_arg


def test_execute_research_topic_cli_failure_returns_false(tmp_path, logger):
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": False,
        "output": "",
        "error": "boom",
        "retries": 0,
        "usage": {},
    }
    run_dir = _make_run_dir(tmp_path)
    wrote = execute_research_topic(
        "x",
        "y?",
        [],
        cli,
        tmp_path,
        run_dir,
        state,
        _config(tmp_path),
        logger,
    )
    assert wrote is False
    assert not (tmp_path / ".aidlc" / "research" / "x.md").exists()


def test_execute_research_topic_permission_chatter_retry(tmp_path, logger):
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    chatter = "The write tool needs your permission to save docs/research/x.md"
    cli.execute_prompt.side_effect = [
        _ok_result(chatter),
        _ok_result("# Findings\nclean retry body"),
    ]
    run_dir = _make_run_dir(tmp_path)
    wrote = execute_research_topic(
        "Perm-Retry",
        "Q?",
        [],
        cli,
        tmp_path,
        run_dir,
        state,
        _config(tmp_path),
        logger,
    )
    assert wrote is True
    assert cli.execute_prompt.call_count == 2
    assert (tmp_path / ".aidlc" / "research" / "perm-retry.md").exists()


def test_run_research_phase_iterates_topics(tmp_path, logger):
    """run_research_phase loads topics.json, calls execute_research_topic per entry."""
    discovery_dir = tmp_path / ".aidlc" / "discovery"
    discovery_dir.mkdir(parents=True)
    topics = [
        {"topic": "a", "question": "qa?", "scope": []},
        {"topic": "b", "question": "qb?", "scope": []},
    ]
    (discovery_dir / "topics.json").write_text(json.dumps(topics))
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = _ok_result("# Findings\nok")
    run_dir = _make_run_dir(tmp_path)
    written = run_research_phase(state, _config(tmp_path), cli, tmp_path, run_dir, logger)
    assert written == 2
    assert state.research_topics_total == 2
    assert state.research_topics_completed == 2
    cli.set_phase.assert_called_with("research")


def test_run_research_phase_skip_existing_topics(tmp_path, logger):
    """Topics whose output already exists are skipped (resume + idempotency)."""
    discovery_dir = tmp_path / ".aidlc" / "discovery"
    discovery_dir.mkdir(parents=True)
    (discovery_dir / "topics.json").write_text(
        json.dumps([{"topic": "kept", "question": "?", "scope": []}])
    )
    rdir = tmp_path / ".aidlc" / "research"
    rdir.mkdir(parents=True)
    (rdir / "kept.md").write_text("already there")
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    run_dir = _make_run_dir(tmp_path)
    written = run_research_phase(state, _config(tmp_path), cli, tmp_path, run_dir, logger)
    assert written == 0
    cli.execute_prompt.assert_not_called()


def test_run_research_phase_no_topics_file_noop(tmp_path, logger):
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    run_dir = _make_run_dir(tmp_path)
    written = run_research_phase(state, _config(tmp_path), cli, tmp_path, run_dir, logger)
    assert written == 0
    cli.execute_prompt.assert_not_called()


def test_run_research_phase_caps_topic_count(tmp_path, logger):
    discovery_dir = tmp_path / ".aidlc" / "discovery"
    discovery_dir.mkdir(parents=True)
    topics = [{"topic": f"t{i}", "question": "?", "scope": []} for i in range(5)]
    (discovery_dir / "topics.json").write_text(json.dumps(topics))
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = _ok_result("# Findings\nok")
    run_dir = _make_run_dir(tmp_path)
    cfg = {**_config(tmp_path), "research_phase_max_topics": 2}
    written = run_research_phase(state, cfg, cli, tmp_path, run_dir, logger)
    assert written == 2


def test_run_research_phase_continues_on_per_topic_exception(tmp_path, logger):
    """A single topic raising shouldn't abort the rest of the loop."""
    discovery_dir = tmp_path / ".aidlc" / "discovery"
    discovery_dir.mkdir(parents=True)
    (discovery_dir / "topics.json").write_text(
        json.dumps(
            [
                {"topic": "boom", "question": "?", "scope": []},
                {"topic": "okay", "question": "?", "scope": []},
            ]
        )
    )
    state = RunState(run_id="r", config_name="c")
    cli = MagicMock()
    cli.execute_prompt.return_value = _ok_result("# Findings\nx")
    run_dir = _make_run_dir(tmp_path)

    real_execute = execute_research_topic

    def maybe_raise(topic, *a, **kw):
        if topic == "boom":
            raise RuntimeError("simulated")
        return real_execute(topic, *a, **kw)

    with patch("aidlc.research_phase.execute_research_topic", side_effect=maybe_raise):
        written = run_research_phase(state, _config(tmp_path), cli, tmp_path, run_dir, logger)
    assert written == 1
