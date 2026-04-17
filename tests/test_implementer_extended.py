"""Extended tests for aidlc.implementer — targeting uncovered lines."""

import json
import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from aidlc.implementer import Implementer
from aidlc.models import Issue, IssueStatus, RunState


@pytest.fixture
def logger():
    return logging.getLogger("test_impl_ext")


@pytest.fixture
def config(tmp_path):
    return {
        "_project_root": str(tmp_path),
        "_issues_dir": str(tmp_path / ".aidlc" / "issues"),
        "_reports_dir": str(tmp_path / ".aidlc" / "reports"),
        "checkpoint_interval_minutes": 999,
        "max_consecutive_failures": 3,
        "max_implementation_attempts": 3,
        "max_implementation_cycles": 0,
        "test_timeout_seconds": 5,
        "max_implementation_context_chars": 30000,
        "dry_run": False,
        "run_tests_command": None,
    }


def make_cli_success(output_json=None):
    cli = MagicMock()
    if output_json:
        output = f"```json\n{json.dumps(output_json)}\n```"
    else:
        output = "Done"
    cli.execute_prompt.return_value = {
        "success": True,
        "output": output,
        "error": None,
        "failure_type": None,
        "duration_seconds": 1.0,
        "retries": 0,
    }
    cli.set_complexity = MagicMock()
    return cli


def make_cli_fail():
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": False,
        "output": "",
        "error": "Claude failed",
        "failure_type": "issue",
        "duration_seconds": 1.0,
        "retries": 0,
    }
    return cli


def make_state_with_issue(issue_id="ISSUE-001", **overrides):
    s = RunState(run_id="test", config_name="default")
    issue_data = {
        "id": issue_id,
        "title": "Test",
        "description": "D",
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
    }
    issue_data.update(overrides)
    s.issues = [issue_data]
    s.total_issues = 1
    return s


class TestImplementIssueSuccess:
    @patch("aidlc.implementer.subprocess.run")
    def test_uses_standard_implementation_routing(self, mock_subproc, config, logger, tmp_path):
        mock_subproc.return_value = MagicMock(returncode=0, stdout="a.py\n")
        cli = make_cli_success(
            {
                "issue_id": "ISSUE-001",
                "success": True,
                "summary": "Done",
                "files_changed": ["a.py"],
                "tests_passed": True,
                "notes": "",
            }
        )
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is True
        cli.set_complexity.assert_called_once_with("normal")
        kwargs = cli.execute_prompt.call_args.kwargs
        assert "model_override" not in kwargs

    @patch("aidlc.implementer.subprocess.run")
    def test_escalates_complex_issue_to_complex_model(self, mock_subproc, config, logger, tmp_path):
        mock_subproc.return_value = MagicMock(returncode=0, stdout="a.py\n")
        config["implementation_complexity_acceptance_criteria_threshold"] = 2
        cli = make_cli_success(
            {
                "issue_id": "ISSUE-001",
                "success": True,
                "summary": "Done",
                "files_changed": ["a.py"],
                "tests_passed": True,
                "notes": "",
            }
        )
        state = make_state_with_issue(acceptance_criteria=["AC1", "AC2", "AC3"])
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is True
        cli.set_complexity.assert_called_once_with("complex")

    @patch("aidlc.implementer.subprocess.run")
    def test_escalates_retry_to_complex_model(self, mock_subproc, config, logger, tmp_path):
        mock_subproc.return_value = MagicMock(returncode=0, stdout="a.py\n")
        config["implementation_escalate_on_retry"] = True
        cli = make_cli_success(
            {
                "issue_id": "ISSUE-001",
                "success": True,
                "summary": "Done",
                "files_changed": ["a.py"],
                "tests_passed": True,
                "notes": "",
            }
        )
        state = make_state_with_issue(attempt_count=1)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is True
        cli.set_complexity.assert_called_once_with("complex")

    @patch("aidlc.implementer.subprocess.run")
    def test_successful_with_json_result(self, mock_subproc, config, logger, tmp_path):
        mock_subproc.return_value = MagicMock(returncode=0, stdout="a.py\n")
        cli = make_cli_success(
            {
                "issue_id": "ISSUE-001",
                "success": True,
                "summary": "Done",
                "files_changed": ["a.py"],
                "tests_passed": True,
                "notes": "",
            }
        )
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is True
        assert state.issues_implemented == 1

    @patch("aidlc.implementer.subprocess.run")
    def test_no_json_but_files_changed(self, mock_subproc, config, logger, tmp_path):
        """Non-JSON output with file changes should fail (legacy path removed)."""
        mock_subproc.return_value = MagicMock(returncode=0, stdout="src/main.py\n")
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True,
            "output": "I made the changes to main.py",
            "error": None,
            "failure_type": None,
            "duration_seconds": 1.0,
            "retries": 0,
        }
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is False

    @patch("aidlc.implementer.subprocess.run")
    def test_no_json_no_files_fails(self, mock_subproc, config, logger, tmp_path):
        """Non-JSON output with no file changes should fail."""
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True,
            "output": "I thought about it but made no changes",
            "error": None,
            "failure_type": None,
            "duration_seconds": 1.0,
            "retries": 0,
        }
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is False


class TestImplementIssueFail:
    def test_cli_failure(self, config, logger, tmp_path):
        cli = make_cli_fail()
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is False
        updated = state.get_issue("ISSUE-001")
        assert updated.status == IssueStatus.FAILED


class TestRunWithTests:
    @patch("aidlc.implementer.subprocess.run")
    def test_tests_pass(self, mock_subproc, config, logger, tmp_path):
        mock_subproc.side_effect = [
            MagicMock(returncode=0, stdout="a.py\n"),  # git diff (for validation)
            MagicMock(returncode=0, stdout="ok", stderr=""),  # test run
            MagicMock(returncode=0, stdout="a.py\n"),  # git diff (for validation again)
        ]
        config["run_tests_command"] = "echo pass"
        cli = make_cli_success(
            {
                "issue_id": "ISSUE-001",
                "success": True,
                "summary": "Done",
                "files_changed": ["a.py"],
                "tests_passed": True,
                "notes": "",
            }
        )
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is True

    @patch("aidlc.implementer.subprocess.run")
    def test_test_timeout_leads_to_failure(self, mock_subproc, config, logger, tmp_path):
        config["run_tests_command"] = "sleep 100"
        config["test_timeout_seconds"] = 1

        # All subprocess calls timeout
        mock_subproc.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=1)

        cli_mock = MagicMock()
        cli_mock.execute_prompt.side_effect = [
            {
                "success": True,
                "output": f"```json\n{json.dumps({'issue_id': 'ISSUE-001', 'success': True, 'summary': 'Done', 'files_changed': ['a.py'], 'tests_passed': True, 'notes': ''})}\n```",
                "error": None,
                "failure_type": None,
                "duration_seconds": 1.0,
                "retries": 0,
            },
            {
                "success": False,
                "output": "",
                "error": "fail",
                "failure_type": "issue",
                "duration_seconds": 1.0,
                "retries": 0,
            },
        ]
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli_mock, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is False  # Tests failed, fix failed


class TestRunTests:
    def test_no_test_command(self, config, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        assert impl._run_tests() is True
        assert impl._run_tests(capture_output=True) == ""

    def test_dry_run(self, config, logger, tmp_path):
        config["dry_run"] = True
        config["run_tests_command"] = "pytest"
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        assert impl._run_tests() is True
        assert impl._run_tests(capture_output=True) == "[DRY RUN] Tests passed"

    @patch("aidlc.implementer.subprocess.run")
    def test_test_pass(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        assert impl._run_tests() is True

    @patch("aidlc.implementer.subprocess.run")
    def test_test_fail(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.return_value = MagicMock(returncode=1, stdout="FAILED", stderr="err")
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        assert impl._run_tests() is False

    @patch("aidlc.implementer.subprocess.run")
    def test_capture_output(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.return_value = MagicMock(returncode=1, stdout="FAIL\n", stderr="error\n")
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        output = impl._run_tests(capture_output=True)
        assert "FAIL" in output
        assert "error" in output

    @patch("aidlc.implementer.subprocess.run")
    def test_test_exception(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.side_effect = OSError("cannot run")
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        assert impl._run_tests() is False
        output = impl._run_tests(capture_output=True)
        assert "Failed to run tests" in output

    @patch("aidlc.implementer.subprocess.run")
    def test_timeout_capture(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=5)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        assert impl._run_tests() is False
        output = impl._run_tests(capture_output=True)
        assert "timed out" in output.lower()


class TestVerificationPass:
    def test_marks_implemented_as_verified(self, config, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "A",
                "description": "D",
                "priority": "high",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": [],
                "status": "implemented",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 1,
                "max_attempts": 3,
            },
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl._verification_pass()
        assert state.issues[0]["status"] == "verified"
        assert state.issues_verified == 1


class TestConsecutiveFailures:
    def test_resorts_after_max_failures(self, config, logger, tmp_path):
        config["max_consecutive_failures"] = 2
        config["max_implementation_cycles"] = 5
        config["dry_run"] = False

        cli = make_cli_fail()
        state = make_state_with_issue()
        # Add more issues so we don't exhaust immediately
        for i in range(3):
            state.issues.append(
                {
                    "id": f"ISSUE-{i + 10:03d}",
                    "title": f"Issue {i}",
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
                }
            )
        state.total_issues = len(state.issues)

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        impl.run()
        # Should have run multiple cycles before giving up
        assert state.implementation_cycles > 0


class TestBlockedIssues:
    def test_run_stops_when_bypass_disabled(self, config, logger, tmp_path):
        config["max_implementation_cycles"] = 1
        cli = make_cli_success(
            {
                "issue_id": "ISSUE-001",
                "success": True,
                "summary": "Done",
                "files_changed": ["a.py"],
                "tests_passed": True,
                "notes": "",
            }
        )
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "Blocked",
                "description": "D",
                "priority": "high",
                "labels": [],
                "dependencies": ["ISSUE-999"],
                "acceptance_criteria": ["AC1"],
                "status": "pending",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 0,
                "max_attempts": 3,
            }
        ]
        state.total_issues = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        impl.run()
        assert "blocked by unmet dependencies" in (state.stop_reason or "")


class TestFixFailingTests:
    @patch("aidlc.implementer.subprocess.run")
    def test_fix_attempt(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        # First call: capture test output (fail), second: run tests after fix (pass)
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="FAILED test", stderr=""),
            MagicMock(returncode=0, stdout="ok", stderr=""),
        ]
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True,
            "output": "Fixed",
            "error": None,
            "failure_type": None,
            "duration_seconds": 1.0,
            "retries": 0,
        }
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        impl.test_command = "pytest"
        issue = Issue(id="ISSUE-001", title="T", description="D", acceptance_criteria=["AC1"])
        result = impl._fix_failing_tests(issue, model_override="opus")
        assert result is True
        kwargs = cli.execute_prompt.call_args.kwargs
        assert kwargs.get("model_override") == "opus"

    @patch("aidlc.implementer.subprocess.run")
    def test_fix_fails(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.return_value = MagicMock(returncode=1, stdout="FAILED", stderr="")
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": False,
            "output": "",
            "error": "fail",
            "failure_type": "issue",
            "duration_seconds": 1.0,
            "retries": 0,
        }
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        impl.test_command = "pytest"
        issue = Issue(id="ISSUE-001", title="T", description="D", acceptance_criteria=["AC1"])
        result = impl._fix_failing_tests(issue)
        assert result is False


class TestImplementationResilience:
    def test_stops_when_all_models_token_exhausted(self, config, logger, tmp_path):
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": False,
            "output": None,
            "error": "All available providers/models appear out of tokens or quota",
            "failure_type": "token_exhausted_all_models",
            "duration_seconds": 1.0,
            "retries": 0,
        }
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)

        ok = impl.run()

        assert ok is False
        assert state.stop_reason is not None
        assert "token" in state.stop_reason.lower()

    def test_syncs_issue_status_to_markdown(self, config, logger, tmp_path):
        config["dry_run"] = True
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()

        cli = make_cli_success(
            {
                "issue_id": "ISSUE-001",
                "success": True,
                "summary": "Done",
                "files_changed": ["a.py"],
                "tests_passed": True,
                "notes": "",
            }
        )

        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        impl._implement_issue(issue)

        issue_file = tmp_path / ".aidlc" / "issues" / "ISSUE-001.md"
        assert issue_file.exists()
        content = issue_file.read_text()
        assert "**Status**: implemented" in content

    def test_stops_when_no_models_available(self, config, logger, tmp_path):
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": False,
            "output": None,
            "error": "No available provider could execute the prompt.",
            "failure_type": "provider_unavailable",
            "duration_seconds": 1.0,
            "retries": 0,
        }
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)

        ok = impl.run()

        assert ok is False
        assert state.stop_reason is not None
        assert "no models/providers" in state.stop_reason.lower()


class TestRubyTestDetection:
    def test_ruby_rspec(self, config, logger, tmp_path):
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'")
        (tmp_path / "spec").mkdir()
        config["_project_root"] = str(tmp_path)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        assert impl._detect_test_command() == "bundle exec rspec"


class TestPreviousAttemptInPrompt:
    def test_includes_previous_notes(self, config, logger, tmp_path):
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "long ctx", logger)
        issue = Issue(id="ISSUE-001", title="T", description="D", acceptance_criteria=["AC1"])
        issue.attempt_count = 2
        issue.implementation_notes = "Previous attempt failed: syntax error"
        prompt = impl._build_implementation_prompt(issue)
        assert "Previous attempt" in prompt
        assert "syntax error" in prompt


class TestGetChangedFilesEdgeCases:
    @patch("aidlc.implementer_workspace.subprocess.run")
    def test_git_timeout(self, mock_run, config, logger, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        assert impl._get_changed_files() == []

    @patch("aidlc.implementer_workspace.subprocess.run")
    def test_git_not_found(self, mock_run, config, logger, tmp_path):
        mock_run.side_effect = FileNotFoundError()
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        assert impl._get_changed_files() == []


class TestErrorPayloadSampling:
    def test_limits_to_50_lines_from_last_500(self):
        from aidlc.implementer_signals import sample_error_payload

        payload_lines = [f"line {i}" for i in range(1, 801)]
        for i in range(730, 780):
            payload_lines[i] = f"ERROR detail {i}"
        payload = "\n".join(payload_lines)

        sampled = sample_error_payload(payload)
        sampled_lines = sampled.splitlines()

        assert len(sampled_lines) <= 50
        # Must come from last 500 lines only: original lines 301..800
        assert "line 200" not in sampled
        assert any("ERROR detail" in line for line in sampled_lines)

    def test_falls_back_to_tail_when_no_error_terms(self):
        payload = "\n".join(f"info {i}" for i in range(1, 601))
        from aidlc.implementer_signals import sample_error_payload

        sampled = sample_error_payload(payload)
        sampled_lines = sampled.splitlines()

        assert len(sampled_lines) == 50
        assert sampled_lines[0] == "info 551"
        assert sampled_lines[-1] == "info 600"
