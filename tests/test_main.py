"""Tests for aidlc.__main__ module."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from aidlc.__main__ import parse_budget, cmd_init, cmd_status, main


class TestParseBudget:
    def test_hours(self):
        assert parse_budget("4h") == 4.0
        assert parse_budget("2.5h") == 2.5

    def test_minutes(self):
        assert parse_budget("30m") == 0.5
        assert parse_budget("90m") == 1.5

    def test_bare_number(self):
        assert parse_budget("4") == 4.0

    def test_whitespace(self):
        assert parse_budget("  4h  ") == 4.0

    def test_case_insensitive(self):
        assert parse_budget("4H") == 4.0
        assert parse_budget("30M") == 0.5


class TestCmdInit:
    def test_creates_aidlc_dir(self, tmp_path):
        args = MagicMock()
        args.project = str(tmp_path)
        args.with_docs = False
        cmd_init(args)

        aidlc_dir = tmp_path / ".aidlc"
        assert aidlc_dir.exists()
        assert (aidlc_dir / "issues").is_dir()
        assert (aidlc_dir / "runs").is_dir()
        assert (aidlc_dir / "reports").is_dir()
        assert (aidlc_dir / "config.json").exists()

        config = json.loads((aidlc_dir / "config.json").read_text())
        assert "plan_budget_hours" in config

    def test_creates_gitignore(self, tmp_path):
        args = MagicMock()
        args.project = str(tmp_path)
        args.with_docs = False
        cmd_init(args)

        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert ".aidlc/runs/" in content

    def test_appends_to_existing_gitignore(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n")

        args = MagicMock()
        args.project = str(tmp_path)
        args.with_docs = False
        cmd_init(args)

        content = gitignore.read_text()
        assert "*.pyc" in content
        assert ".aidlc/" in content

    def test_already_initialized(self, tmp_path, capsys):
        (tmp_path / ".aidlc").mkdir()
        args = MagicMock()
        args.project = str(tmp_path)
        args.with_docs = False
        cmd_init(args)

        captured = capsys.readouterr()
        assert "already exists" in captured.out


class TestCmdStatus:
    def test_no_runs(self, tmp_path, capsys):
        args = MagicMock()
        args.project = str(tmp_path)
        cmd_status(args)
        captured = capsys.readouterr()
        assert "No AIDLC runs found" in captured.out

    def test_shows_status(self, tmp_path, capsys):
        from aidlc.models import RunState, RunStatus, RunPhase
        from aidlc.state_manager import save_state

        # Set up .aidlc/runs/ with a run
        runs_dir = tmp_path / ".aidlc" / "runs" / "test_run"
        runs_dir.mkdir(parents=True)
        state = RunState(run_id="test_run", config_name="default")
        state.status = RunStatus.COMPLETE
        state.phase = RunPhase.DONE
        state.plan_budget_seconds = 3600
        state.total_issues = 5
        state.issues_implemented = 3
        state.stop_reason = "All done"
        save_state(state, runs_dir)

        args = MagicMock()
        args.project = str(tmp_path)
        cmd_status(args)
        captured = capsys.readouterr()
        assert "test_run" in captured.out
        assert "complete" in captured.out

    def test_shows_issues(self, tmp_path, capsys):
        from aidlc.models import RunState, RunStatus
        from aidlc.state_manager import save_state

        runs_dir = tmp_path / ".aidlc" / "runs" / "test_run"
        runs_dir.mkdir(parents=True)
        state = RunState(run_id="test_run", config_name="default")
        state.issues = [
            {"id": "ISSUE-001", "title": "Test", "status": "verified"},
            {"id": "ISSUE-002", "title": "Failed", "status": "failed"},
        ]
        save_state(state, runs_dir)

        args = MagicMock()
        args.project = str(tmp_path)
        cmd_status(args)
        captured = capsys.readouterr()
        assert "ISSUE-001" in captured.out
        assert "ISSUE-002" in captured.out


class TestMain:
    def test_no_command_shows_help(self, capsys):
        with patch("sys.argv", ["aidlc"]):
            main()
        # Should not crash — just prints help

    @patch("aidlc.__main__.run_full")
    def test_run_command(self, mock_run):
        with patch("sys.argv", ["aidlc", "run", "--dry-run", "--skip-precheck"]):
            main()
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["dry_run"] is True

    @patch("aidlc.__main__.load_config")
    def test_strict_mode_blocks_skip_precheck(self, mock_load_config):
        mock_load_config.return_value = {
            "_project_root": ".",
            "_aidlc_dir": "./.aidlc",
            "_runs_dir": "./.aidlc/runs",
            "_reports_dir": "./.aidlc/reports",
            "_issues_dir": "./.aidlc/issues",
            "strict_mode": True,
            "allow_skip_precheck": False,
        }
        with patch("sys.argv", ["aidlc", "run", "--skip-precheck"]):
            with pytest.raises(SystemExit):
                main()
