"""Tests for aidlc.implementer module."""

import json
import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aidlc.implementer import Implementer
from aidlc.models import RunState, RunPhase, Issue, IssueStatus


@pytest.fixture
def logger():
    return logging.getLogger("test_implementer")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "checkpoint_interval_minutes": 999,
        "max_consecutive_failures": 3,
        "max_implementation_attempts": 3,
        "max_implementation_cycles": 5,
        "test_timeout_seconds": 30,
        "max_implementation_context_chars": 30000,
        "dry_run": True,
        "run_tests_command": None,
    }


@pytest.fixture
def state_with_issues():
    s = RunState(run_id="test_impl", config_name="default")
    s.issues = [
        {
            "id": "ISSUE-001",
            "title": "First Issue",
            "description": "Do the first thing",
            "priority": "high",
            "labels": [],
            "dependencies": [],
            "acceptance_criteria": ["AC1"],
            "status": "pending",
            "implementation_notes": "",
            "verification_result": "",
            "files_changed": [],
            "attempt_count": 0,
            "max_attempts": 3,
        },
    ]
    s.total_issues = 1
    return s


@pytest.fixture
def cli():
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": True,
        "output": "[DRY RUN] No execution",
        "error": None,
        "failure_type": None,
        "duration_seconds": 0.0,
        "retries": 0,
    }
    return cli


class TestImplementer:
    def test_dry_run_completes(self, state_with_issues, config, cli, logger, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state_with_issues, run_dir, config, cli, "context", logger)
        impl.run()
        assert state_with_issues.issues_implemented >= 1

    def test_cycle_cap(self, state_with_issues, config, cli, logger, tmp_path):
        config["max_implementation_cycles"] = 1
        # Add many issues
        for i in range(5):
            state_with_issues.issues.append({
                "id": f"ISSUE-{i+10:03d}",
                "title": f"Issue {i+10}",
                "description": "D",
                "priority": "medium",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": ["AC"],
                "status": "pending",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 0,
                "max_attempts": 3,
            })
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state_with_issues, run_dir, config, cli, "context", logger)
        impl.run()
        assert state_with_issues.implementation_cycles <= 1


class TestSortIssues:
    def test_priority_ordering(self, config, cli, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {"id": "ISSUE-001", "title": "Low", "priority": "low", "dependencies": [], "status": "pending"},
            {"id": "ISSUE-002", "title": "High", "priority": "high", "dependencies": [], "status": "pending"},
            {"id": "ISSUE-003", "title": "Med", "priority": "medium", "dependencies": [], "status": "pending"},
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        impl._sort_issues()
        ids = [d["id"] for d in state.issues]
        assert ids.index("ISSUE-002") < ids.index("ISSUE-003")
        assert ids.index("ISSUE-003") < ids.index("ISSUE-001")

    def test_dependency_ordering(self, config, cli, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {"id": "ISSUE-002", "title": "Second", "priority": "high", "dependencies": ["ISSUE-001"], "status": "pending"},
            {"id": "ISSUE-001", "title": "First", "priority": "high", "dependencies": [], "status": "pending"},
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        impl._sort_issues()
        ids = [d["id"] for d in state.issues]
        assert ids.index("ISSUE-001") < ids.index("ISSUE-002")

    def test_circular_dependency_detected(self, config, cli, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {"id": "ISSUE-001", "title": "A", "priority": "high", "dependencies": ["ISSUE-002"], "status": "pending"},
            {"id": "ISSUE-002", "title": "B", "priority": "high", "dependencies": ["ISSUE-001"], "status": "pending"},
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        result = impl._sort_issues()
        # Should detect cycle and refuse auto-breaking by default
        assert result is False
        assert len(state.issues) == 2

    def test_circular_dependency_autobreak_when_enabled(self, config, cli, logger, tmp_path):
        config["auto_break_dependency_cycles"] = True
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {"id": "ISSUE-001", "title": "A", "priority": "high", "dependencies": ["ISSUE-002"], "status": "pending"},
            {"id": "ISSUE-002", "title": "B", "priority": "high", "dependencies": ["ISSUE-001"], "status": "pending"},
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        result = impl._sort_issues()
        assert result is True


class TestDetectTestCommand:
    def test_python_pytest(self, config, cli, logger, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'")
        (tmp_path / "tests").mkdir()
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "python -m pytest"

    def test_node_npm_test(self, config, cli, logger, tmp_path):
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "npm test"

    def test_rust_cargo(self, config, cli, logger, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname='test'")
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "cargo test"

    def test_go(self, config, cli, logger, tmp_path):
        (tmp_path / "go.mod").write_text("module test")
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "go test ./..."

    def test_makefile_with_test(self, config, cli, logger, tmp_path):
        (tmp_path / "Makefile").write_text("test:\n\techo test")
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd == "make test"

    def test_no_tests(self, config, cli, logger, tmp_path):
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        cmd = impl._detect_test_command()
        assert cmd is None


class TestBuildImplementationPrompt:
    def test_contains_issue_info(self, config, cli, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "project context here", logger)
        issue = Issue(
            id="ISSUE-001",
            title="Test Issue",
            description="Description",
            priority="high",
            acceptance_criteria=["AC1", "AC2"],
        )
        prompt = impl._build_implementation_prompt(issue)
        assert "ISSUE-001" in prompt
        assert "Test Issue" in prompt
        assert "AC1" in prompt
        assert "project context here" in prompt

    def test_context_capped(self, config, cli, logger, tmp_path):
        config["max_implementation_context_chars"] = 100
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        long_context = "x" * 1000
        impl = Implementer(state, run_dir, config, cli, long_context, logger)
        issue = Issue(id="ISSUE-001", title="T", description="D")
        prompt = impl._build_implementation_prompt(issue)
        # The context portion should be truncated to ~100 chars
        assert prompt.count("x") <= 110  # Allow small overhead from formatting


class TestGetChangedFiles:
    @patch("aidlc.implementer.subprocess.run")
    def test_returns_changed_files(self, mock_run, config, cli, logger, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="src/main.py\nsrc/utils.py\n",
        )
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        files = impl._get_changed_files()
        assert files == ["src/main.py", "src/utils.py"]

    @patch("aidlc.implementer.subprocess.run")
    def test_returns_empty_on_no_changes(self, mock_run, config, cli, logger, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "context", logger)
        files = impl._get_changed_files()
        assert files == []
