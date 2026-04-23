"""Drive coverage for aidlc.plan_session (ANSI helpers, PlanSession branches, I/O edges)."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from aidlc.plan_session import PlanSession, _bold, _cyan, _dim, _green, _yellow


@pytest.fixture
def tty_on(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)


@pytest.fixture
def tty_off(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)


def test_ansi_helpers_tty_on(tty_on):
    assert "\033[1m" in _bold("x")
    assert "\033[2m" in _dim("x")
    assert "\033[36m" in _cyan("x")
    assert "\033[32m" in _green("x")
    assert "\033[33m" in _yellow("x")


def test_ansi_helpers_tty_off(tty_off):
    assert _bold("hi") == "hi"
    assert _dim("hi") == "hi"


def test_save_and_load_session_answers(tmp_path):
    cfg = {"providers": {"claude": {"cli_command": "claude"}}}
    cli = MagicMock()
    log = MagicMock()
    ps = PlanSession(tmp_path, cfg, cli, log)
    data = {"project_name": "P", "brain_dump": ""}
    ps._save_session_answers(data)
    loaded = ps._load_session_answers()
    assert loaded == data


def test_load_session_answers_missing(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    assert ps._load_session_answers() is None


def test_load_session_answers_corrupt(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.session_dir.mkdir(parents=True)
    (ps.session_dir / "wizard_answers.json").write_text("{")
    assert ps._load_session_answers() is None


def test_get_existing_context_empty(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    assert ps._get_existing_context() == ""


def test_get_existing_context_audit_and_research(tmp_path):
    aidlc = tmp_path / ".aidlc"
    aidlc.mkdir()
    (aidlc / "audit_result.json").write_text(
        json.dumps(
            {
                "project_type": "python",
                "source_stats": {"total_files": 3, "total_lines": 100},
                "frameworks": ["pytest"],
                "modules": [{"name": "core", "role": "lib", "file_count": 2}],
            }
        )
    )
    rd = tmp_path / "docs" / "research"
    rd.mkdir(parents=True)
    (rd / "note.md").write_text("r")
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ctx = ps._get_existing_context()
    assert "Existing Codebase" in ctx
    assert "Research Available" in ctx


def test_get_existing_context_audit_invalid_json(tmp_path):
    (tmp_path / ".aidlc").mkdir()
    (tmp_path / ".aidlc" / "audit_result.json").write_text("{bad")
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    assert ps._get_existing_context() == ""


def test_identify_research_empty_brain_dump(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    assert ps._identify_research({"brain_dump": ""}) == []


def test_identify_research_cli_failure(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": False}
    assert ps._identify_research({"brain_dump": "ideas"}) == []


def test_identify_research_parses_json_array(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    topics = [{"topic": "t1", "question": "q?", "priority": "high"}]
    ps.cli.execute_prompt.return_value = {
        "success": True,
        "output": f"here {json.dumps(topics)} tail",
    }
    out = ps._identify_research({"brain_dump": "x", "project_name": "Pn"})
    assert out == topics


def test_identify_research_bad_json(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": True, "output": "no array here"}
    assert ps._identify_research({"brain_dump": "b"}) == []


def test_run_research_skips_existing_file(tmp_path, capsys):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    rd = tmp_path / "docs" / "research"
    rd.mkdir(parents=True)
    (rd / "done-topic.md").write_text("old")
    ps._run_research([{"topic": "done topic", "question": "q", "priority": "low", "category": "c"}])
    captured = capsys.readouterr().out
    assert "skip" in captured.lower() or "already" in captured.lower()


def test_run_research_writes_on_success(tmp_path, capsys):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": True, "output": "# Body\nok"}
    ps._run_research(
        [{"topic": "fresh-topic", "question": "why", "priority": "high", "category": "x"}]
    )
    outf = tmp_path / "docs" / "research" / "fresh-topic.md"
    assert outf.exists()
    assert "Body" in outf.read_text()


def test_run_research_orders_high_priority_first(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    order = []

    def side_effect(prompt, root, **kwargs):
        order.append(prompt)
        return {"success": True, "output": "# ok"}

    ps.cli.execute_prompt.side_effect = side_effect
    ps._run_research(
        [
            {"topic": "low", "question": "l", "priority": "low"},
            {"topic": "hi", "question": "h", "priority": "high"},
        ]
    )
    assert len(order) == 2
    assert "hi" in order[0] or "Research: hi" in order[0]


def test_run_research_permission_retry_then_success(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    chatter = "The write tool needs your permission before I can save docs/research/x.md"
    good = "# fixed\nok"
    ps.cli.execute_prompt.side_effect = [
        {"success": True, "output": chatter},
        {"success": True, "output": good},
    ]
    ps._run_research([{"topic": "perm", "question": "q", "priority": "high"}])
    p = tmp_path / "docs" / "research" / "perm.md"
    assert p.exists()
    assert "fixed" in p.read_text()


def test_run_research_permission_retry_still_bad(tmp_path, capsys):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    bad = "The write tool needs your permission before I can save docs/research/x.md"
    ps.cli.execute_prompt.side_effect = [
        {"success": True, "output": bad},
        {"success": True, "output": bad},
    ]
    ps._run_research([{"topic": "badperm", "question": "q", "priority": "high"}])
    out = capsys.readouterr().out.lower()
    assert "invalid" in out or "!" in out


def test_run_research_failed_call(tmp_path, capsys):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": False}
    ps._run_research([{"topic": "failt", "question": "q", "priority": "low"}])
    assert "failed" in capsys.readouterr().out.lower() or "!" in capsys.readouterr().out


def test_generate_docs_skips_substantial_existing(tmp_path, capsys):
    long = "x" * 600
    (tmp_path / "ROADMAP.md").write_text(long)
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps._generate_docs({"brain_dump": "", "project_name": "P", "tech_stack": "py", "one_liner": "o"})
    assert "skip" in capsys.readouterr().out.lower()


def test_generate_docs_builds_drafts(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": True, "output": "  draft body  "}
    drafts = ps._generate_docs(
        {
            "brain_dump": "features",
            "project_name": "Z",
            "tech_stack": "rust",
            "one_liner": "l",
        }
    )
    assert "ROADMAP.md" in drafts
    assert "draft body" in drafts["ARCHITECTURE.md"]
    # ISSUE-002: doc-generation must use allow_edits=False so Claude
    # returns the body as text instead of writing the file via Write
    # (which would let _save_drafts overwrite it with stdout-summary).
    for call in ps.cli.execute_prompt.call_args_list:
        assert call.kwargs.get("allow_edits") is False


def test_save_drafts_writes_full_body_to_root(tmp_path):
    """Regression for the doc-overwrite bug.

    Before the ISSUE-002 fix, the wizard ran Claude with allow_edits=True;
    Claude wrote the full body via Write and returned a chat-summary stdout,
    which _save_drafts then wrote on top of the body. This test exercises the
    end-to-end path with stdout containing the body (the new contract) and
    asserts the project-root file equals that body.
    """
    body = (
        "# ARCHITECTURE\n\n## Overview\n\n"
        "This is the full architecture document body, > 200 chars long, "
        "exactly the kind of payload Claude returns when allow_edits=False "
        "forces it to emit the doc as text rather than wielding its Write "
        "tool side-effects.\n"
    )
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": True, "output": body}
    drafts = ps._generate_docs(
        {
            "brain_dump": "f",
            "project_name": "Reg",
            "tech_stack": "py",
            "one_liner": "l",
        }
    )
    ps._save_drafts(drafts)
    assert (tmp_path / "ARCHITECTURE.md").read_text() == body.strip()
    # The .generated audit copy must also contain the full body.
    backup_dirs = [p for p in ps.session_dir.iterdir() if p.is_dir()]
    assert backup_dirs
    assert (backup_dirs[0] / "ARCHITECTURE.md.generated").read_text() == body.strip()


def test_generate_docs_cli_failure_logs(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": False}
    ps._generate_docs({"brain_dump": "", "project_name": "P", "tech_stack": "t", "one_liner": ""})
    ps.logger.warning.assert_called()


def test_save_drafts_creates_backup(tmp_path):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    (tmp_path / "DESIGN.md").write_text("old")
    ps._save_drafts({"DESIGN.md": "new"})
    assert (tmp_path / "DESIGN.md").read_text() == "new"
    backups = list(ps.session_dir.iterdir())
    assert backups


def test_launch_refinement_subprocess(tmp_path, monkeypatch):
    ps = PlanSession(
        tmp_path, {"providers": {"claude": {"cli_command": "echo"}}}, MagicMock(), MagicMock()
    )
    ps.session_dir.mkdir(parents=True)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr("aidlc.plan_session.subprocess.run", fake_run)
    ps._launch_refinement({"project_name": "MyProj"})
    assert calls


def test_launch_refinement_swallows_file_not_found(tmp_path, monkeypatch):
    ps = PlanSession(
        tmp_path,
        {"providers": {"claude": {"cli_command": "missing-bin-xyz"}}},
        MagicMock(),
        MagicMock(),
    )
    ps.session_dir.mkdir(parents=True)

    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr("aidlc.plan_session.subprocess.run", boom)
    ps._launch_refinement({"project_name": "X"})  # should not raise


@patch("aidlc.plan_session.run_wizard")
def test_run_wizard_only_short_circuits(mock_wizard, tmp_path, capsys):
    mock_wizard.return_value = {
        "project_name": "A",
        "one_liner": "L",
        "tech_stack": "py",
        "brain_dump": "",
    }
    ps = PlanSession(
        tmp_path, {"providers": {"claude": {"cli_command": "c"}}}, MagicMock(), MagicMock()
    )
    ps.cli.execute_prompt.return_value = {"success": True, "output": "doc"}
    ps.run(wizard_only=True)
    out = capsys.readouterr().out
    assert "aidlc plan" in out or "Next" in out


@patch("aidlc.plan_session.run_wizard")
def test_run_skip_wizard_loads_saved(mock_wizard, tmp_path):
    cfg = {"providers": {"claude": {"cli_command": "c"}}}
    cli = MagicMock()
    cli.execute_prompt.return_value = {"success": True, "output": "x"}
    ps = PlanSession(tmp_path, cfg, cli, MagicMock())
    ps.session_dir.mkdir(parents=True)
    (ps.session_dir / "wizard_answers.json").write_text(
        json.dumps(
            {
                "project_name": "S",
                "one_liner": "",
                "tech_stack": "go",
                "brain_dump": "",
            }
        )
    )
    ps.run(skip_wizard=True, wizard_only=True)
    mock_wizard.assert_not_called()


@patch("aidlc.plan_session.run_wizard")
def test_run_skip_wizard_falls_back_when_no_saved(mock_wizard, tmp_path):
    mock_wizard.return_value = {
        "project_name": "F",
        "brain_dump": "",
        "tech_stack": "",
        "one_liner": "",
    }
    ps = PlanSession(
        tmp_path, {"providers": {"claude": {"cli_command": "c"}}}, MagicMock(), MagicMock()
    )
    ps.cli.execute_prompt.return_value = {"success": True, "output": "d"}
    ps.run(skip_wizard=True, wizard_only=True)
    mock_wizard.assert_called_once()


@patch("aidlc.plan_session.run_wizard")
@patch.object(PlanSession, "_launch_refinement")
def test_run_full_invokes_refinement(mock_launch, mock_wizard, tmp_path, capsys):
    mock_wizard.return_value = {
        "project_name": "Full",
        "brain_dump": "",
        "tech_stack": "py",
        "one_liner": "x",
    }
    ps = PlanSession(
        tmp_path, {"providers": {"claude": {"cli_command": "c"}}}, MagicMock(), MagicMock()
    )
    ps.cli.execute_prompt.return_value = {"success": True, "output": "generated"}
    ps.run(wizard_only=False)
    mock_launch.assert_called_once()


def test_run_review_no_docs(tmp_path, capsys):
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.run(review_only=True)
    assert "No docs" in capsys.readouterr().out or "missing" in capsys.readouterr().out.lower()


def test_run_review_writes_file(tmp_path, capsys):
    (tmp_path / "README.md").write_text("# Readme\n" + "b" * 100)
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": True, "output": "## Review\nok"}
    ps.run(review_only=True)
    rp = tmp_path / "docs" / "audits" / "doc-review.md"
    assert rp.exists()


def test_run_review_truncates_long_output(tmp_path, capsys):
    (tmp_path / "README.md").write_text("x" * 200)
    long_out = "L" * 2500
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": True, "output": long_out}
    ps.run(review_only=True)
    assert (
        "doc-review" in capsys.readouterr().out.lower()
        or "full review" in capsys.readouterr().out.lower()
    )


def test_run_review_cli_failure(tmp_path, capsys):
    (tmp_path / "README.md").write_text("hi")
    ps = PlanSession(tmp_path, {}, MagicMock(), MagicMock())
    ps.cli.execute_prompt.return_value = {"success": False, "error": "nope"}
    ps.run(review_only=True)
    assert "failed" in capsys.readouterr().out.lower() or "!" in capsys.readouterr().out
