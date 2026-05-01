"""Extended tests for aidlc.implementer — targeting uncovered lines."""

import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aidlc._proc import ProcResult
from aidlc.implementer import Implementer
from aidlc.implementer_helpers import FixTestsOutcome
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


def make_cli_outage():
    """CLI mock that returns the same shape claude_cli.py emits when the
    outage budget is exceeded — used to drive Fix C / Fix D paths."""
    cli = MagicMock()
    cli.execute_prompt.return_value = {
        "success": False,
        "output": "",
        "error": (
            "Claude has been unavailable for an extended period "
            "(2h outage window reached). Please check Claude status and retry."
        ),
        "failure_type": "service_down",
        "duration_seconds": 1.0,
        "retries": 0,
    }
    cli.set_complexity = MagicMock()
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
    @patch("aidlc.implementer.run_with_group_kill")
    def test_uses_standard_implementation_routing(self, mock_subproc, config, logger, tmp_path):
        mock_subproc.return_value = ProcResult(0, "a.py\n", "", False)
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
        assert kwargs.get("model_override") is None
        cont = kwargs.get("session_continuation") or {}
        assert cont.get("claude") and len(cont["claude"]) == 36
        assert cont.get("copilot") and len(cont["copilot"]) == 36

    @patch("aidlc.implementer.run_with_group_kill")
    def test_escalates_complex_issue_to_complex_model(self, mock_subproc, config, logger, tmp_path):
        mock_subproc.return_value = ProcResult(0, "a.py\n", "", False)
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

    @patch("aidlc.implementer.run_with_group_kill")
    def test_escalates_retry_to_complex_model(self, mock_subproc, config, logger, tmp_path):
        mock_subproc.return_value = ProcResult(0, "a.py\n", "", False)
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

    @patch("aidlc.implementer.run_with_group_kill")
    def test_successful_with_json_result(self, mock_subproc, config, logger, tmp_path):
        mock_subproc.return_value = ProcResult(0, "a.py\n", "", False)
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

    @patch("aidlc.implementer_workspace.subprocess.run")
    def test_no_json_but_files_changed_is_accepted_via_git_diff(
        self, mock_git, config, logger, tmp_path
    ):
        """When Claude wrote files via tools but the JSON envelope is
        missing/garbled (mid-output timeout, trailing prose), the
        implementer trusts the git diff and accepts the work — the test
        step is the real gate. The previous reject-and-retry policy cost
        ~$5/attempt for nothing."""
        mock_git.return_value = MagicMock(returncode=0, stdout="src/main.py\n")
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
        assert result is True
        assert state.issues[0]["status"] == "implemented"
        assert state.issues[0]["files_changed"] == ["src/main.py"]

    @patch("aidlc.implementer_workspace.subprocess.run")
    def test_no_json_no_files_fails(self, mock_git, config, logger, tmp_path):
        """Non-JSON output with no file changes should fail."""
        mock_git.return_value = MagicMock(returncode=0, stdout="")
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
    @patch("aidlc.implementer.run_with_group_kill")
    @patch("aidlc.implementer_workspace.subprocess.run")
    def test_tests_pass(self, mock_git, mock_rgk, config, logger, tmp_path):
        mock_git.return_value = MagicMock(returncode=0, stdout="a.py\n")
        mock_rgk.return_value = ProcResult(0, "ok", "", False)
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

    @patch("aidlc.implementer.run_with_group_kill")
    @patch("aidlc.implementer_workspace.subprocess.run")
    def test_test_timeout_leads_to_failure(self, mock_git, mock_rgk, config, logger, tmp_path):
        config["run_tests_command"] = "sleep 100"
        config["test_timeout_seconds"] = 1

        mock_git.return_value = MagicMock(returncode=0, stdout="a.py\n")
        mock_rgk.return_value = ProcResult(0, "", "", True)

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

    @patch("aidlc.implementer.run_with_group_kill")
    def test_test_pass(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.return_value = ProcResult(0, "ok", "", False)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        assert impl._run_tests() is True

    @patch("aidlc.implementer.run_with_group_kill")
    def test_test_fail(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.return_value = ProcResult(1, "FAILED", "err", False)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        assert impl._run_tests() is False

    @patch("aidlc.implementer.run_with_group_kill")
    def test_capture_output(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.return_value = ProcResult(1, "FAIL\n", "error\n", False)
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        output = impl._run_tests(capture_output=True)
        assert "FAIL" in output
        assert "error" in output

    @patch("aidlc.implementer.run_with_group_kill")
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

    @patch("aidlc.implementer.run_with_group_kill")
    def test_timeout_capture(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.return_value = ProcResult(0, "", "", True)
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
    @patch("aidlc.implementer.run_with_group_kill")
    def test_fix_attempt(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        # First call: capture test output (fail), second: run tests after fix (pass)
        mock_run.side_effect = [
            ProcResult(1, "FAILED test", "", False),
            ProcResult(0, "ok", "", False),
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
        assert result.tests_now_passing is True
        kwargs = cli.execute_prompt.call_args.kwargs
        assert kwargs.get("model_override") == "opus"

    @patch("aidlc.implementer.run_with_group_kill")
    def test_fix_fails(self, mock_run, config, logger, tmp_path):
        config["run_tests_command"] = "pytest"
        mock_run.return_value = ProcResult(1, "FAILED", "", False)
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
        assert result.tests_now_passing is False


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


class TestImplementerMoreBranches:
    @patch("aidlc.implementer.sort_issues_for_implementation", return_value=False)
    def test_run_stops_when_initial_sort_detects_cycle(self, _mock_sort, config, logger, tmp_path):
        config["dry_run"] = True
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, make_cli_success(), "ctx", logger)
        assert impl.run() is False
        assert "Dependency cycle" in (state.stop_reason or "")

    def test_is_complex_issue_logs_reasons(self, config, logger, tmp_path, caplog):
        config["dry_run"] = True
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.escalate_on_retry = True
        impl.complexity_ac_threshold = 2
        impl.complexity_dep_threshold = 2
        impl.complexity_description_threshold = 5
        impl.complexity_labels = {"hot"}
        issue = Issue(
            id="I-1",
            title="T",
            description="x" * 10,
            labels=["hot"],
            dependencies=["A", "B"],
            acceptance_criteria=["a", "b", "c"],
            attempt_count=2,
        )
        with caplog.at_level(logging.INFO):
            assert impl._is_complex_issue(issue) is True
        assert "implementation_complex" in caplog.text

    def test_log_provider_result_requested_vs_actual(self, config, logger, tmp_path, caplog):
        config["dry_run"] = True
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        issue = Issue(id="I-1", title="t", description="d")
        with caplog.at_level(logging.INFO):
            impl._log_provider_result(
                issue,
                {
                    "provider_id": "openai",
                    "model_used": "gpt-small",
                    "routing_decision": {"model": "gpt-large"},
                },
            )
        assert "requested" in caplog.text

    @patch("aidlc.implementer.run_with_group_kill")
    def test_run_tests_timeout_capture_output(self, mock_run, config, logger, tmp_path):
        mock_run.return_value = ProcResult(0, "", "", True)
        config["dry_run"] = False
        config["run_tests_command"] = "pytest"
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        out = impl._run_tests(capture_output=True)
        assert "timed out" in out.lower()

    @patch.object(Implementer, "_fix_failing_tests")
    @patch("aidlc.implementer.parse_implementation_result")
    @patch.object(Implementer, "_get_changed_files")
    @patch.object(Implementer, "_run_tests")
    def test_implement_issue_success_when_json_success_false_but_debt_accepted(
        self, mock_rt, mock_gcf, mock_parse, mock_fix, config, logger, tmp_path
    ):
        from aidlc.schemas import ImplementationResult

        mock_parse.return_value = ImplementationResult(
            issue_id="ISSUE-001",
            success=False,
            summary="done",
            files_changed=["a.gd"],
            tests_passed=False,
        )
        mock_gcf.return_value = (["a.gd"], True)
        mock_rt.return_value = False
        mock_fix.return_value = FixTestsOutcome(
            tests_now_passing=False,
            accepted_pre_existing_debt=True,
            follow_up_documentation="pre-existing unrelated suite issues " * 3,
        )
        config["dry_run"] = False
        config["run_tests_command"] = "pytest"
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True,
            "output": "{}",
            "error": None,
            "failure_type": None,
            "duration_seconds": 0.0,
            "retries": 0,
        }
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        impl.test_command = "pytest"
        issue = Issue.from_dict(state.issues[0])
        assert impl._implement_issue(issue) is True
        assert issue.status == IssueStatus.IMPLEMENTED

    @patch("aidlc.implementer.parse_implementation_result")
    @patch.object(Implementer, "_get_changed_files")
    @patch.object(Implementer, "_run_tests", return_value=True)
    def test_implement_issue_bad_json_with_file_changes_accepted_via_git(
        self, _rt, mock_gcf, mock_parse, config, logger, tmp_path
    ):
        """Bad JSON + git diff shows files changed → accept and proceed.
        Tests gate the outcome (not the JSON envelope)."""
        mock_parse.side_effect = ValueError("bad")
        mock_gcf.return_value = (["x.py"], True)
        config["dry_run"] = False
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True,
            "output": "not json",
            "error": None,
            "failure_type": None,
            "duration_seconds": 0.0,
            "retries": 0,
        }
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        impl.test_command = None
        issue = Issue.from_dict(state.issues[0])
        assert impl._implement_issue(issue) is True
        assert state.issues[0]["status"] == "implemented"
        assert state.issues[0]["files_changed"] == ["x.py"]

    @patch("aidlc.implementer.parse_implementation_result")
    @patch.object(Implementer, "_get_changed_files")
    @patch.object(Implementer, "_run_tests", return_value=True)
    def test_implement_issue_bad_json_change_detection_broken(
        self, _rt, mock_gcf, mock_parse, config, logger, tmp_path
    ):
        mock_parse.side_effect = ValueError("bad")
        mock_gcf.return_value = (["x.py"], False)
        config["dry_run"] = False
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True,
            "output": "x",
            "error": None,
            "failure_type": None,
            "duration_seconds": 0.0,
            "retries": 0,
        }
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        impl.test_command = None
        issue = Issue.from_dict(state.issues[0])
        assert impl._implement_issue(issue) is False

    @patch("aidlc.implementer.parse_implementation_result")
    @patch.object(Implementer, "_get_changed_files")
    @patch.object(Implementer, "_run_tests", return_value=True)
    def test_implement_issue_success_strict_change_detection_no_changes_fails(
        self, _rt, mock_gcf, mock_parse, config, logger, tmp_path
    ):
        from aidlc.schemas import ImplementationResult

        mock_parse.return_value = ImplementationResult(
            issue_id="ISSUE-001",
            success=True,
            summary="ok",
            files_changed=[],
            tests_passed=True,
        )
        mock_gcf.return_value = ([], True)
        config["dry_run"] = False
        config["strict_change_detection"] = True
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": True,
            "output": "{}",
            "error": None,
            "failure_type": None,
            "duration_seconds": 0.0,
            "retries": 0,
        }
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        impl.test_command = None
        issue = Issue.from_dict(state.issues[0])
        assert impl._implement_issue(issue) is False

    @patch.object(Implementer, "_run_tests", return_value=False)
    def test_verification_pass_fail_on_final_test(self, mock_rt, config, logger, tmp_path):
        config["fail_on_final_test_failure"] = True
        config["run_tests_command"] = "pytest"
        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "T",
                "description": "D",
                "priority": "high",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": ["A"],
                "status": "implemented",
                "implementation_notes": "",
                "verification_result": "",
                "files_changed": ["a.py"],
                "attempt_count": 1,
                "max_attempts": 3,
            }
        ]
        state.total_issues = 1
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.test_command = "pytest"
        assert impl._verification_pass() is False
        assert "Final verification failed" in (state.stop_reason or "")


class TestSyncIssueMarkdown:
    def test_sync_issue_logs_oserror(self, config, logger, tmp_path):
        config["dry_run"] = True
        state = RunState(run_id="t", config_name="c")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        impl = Implementer(state, run_dir, config, MagicMock(), "ctx", logger)
        impl.autosync_issue_status_sync = True
        issue = Issue(
            id="ISSUE-001",
            title="T",
            description="D",
            status=IssueStatus.IMPLEMENTED,
            acceptance_criteria=["a"],
        )
        real_write = Path.write_text

        def selective_write(self, *a, **kw):
            if self.name == "ISSUE-001.md":
                raise OSError("full disk")
            return real_write(self, *a, **kw)

        with patch.object(Path, "write_text", selective_write):
            impl._sync_issue_markdown(issue)


class TestAutosyncProgress:
    @patch("aidlc.implementer.prune_aidlc_data")
    @patch("aidlc.implementer.git_push_current_branch")
    @patch("aidlc.implementer.git_commit_cycle_snapshot")
    @patch("aidlc.finalizer.Finalizer")
    def test_autosync_runs_after_each_cycle_when_interval_is_one(
        self,
        mock_finalizer_cls,
        mock_commit,
        mock_push,
        mock_prune,
        config,
        logger,
        tmp_path,
    ):
        config["dry_run"] = False
        config["autosync_every_implementation_cycles"] = 1
        mock_commit.return_value = False
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
        impl.run()
        mock_finalizer_cls.assert_called()
        mock_commit.assert_called()
        mock_prune.assert_called()
        mock_push.assert_not_called()

    @patch.object(Implementer, "_emit_run_checkpoint_summary")
    @patch("aidlc.implementer.prune_aidlc_data")
    @patch("aidlc.implementer.git_push_current_branch")
    @patch("aidlc.implementer.git_commit_cycle_snapshot")
    @patch("aidlc.finalizer.Finalizer")
    def test_autosync_commit_triggers_checkpoint_summary(
        self,
        mock_finalizer_cls,
        mock_commit,
        mock_push,
        mock_prune,
        mock_emit_summary,
        config,
        logger,
        tmp_path,
    ):
        config["dry_run"] = False
        config["autosync_every_implementation_cycles"] = 1
        mock_commit.return_value = True
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
        impl.run()
        mock_emit_summary.assert_called()

    @patch("aidlc.implementer.prune_aidlc_data")
    @patch("aidlc.implementer.git_push_current_branch")
    @patch("aidlc.implementer.git_commit_cycle_snapshot")
    @patch("aidlc.finalizer.Finalizer")
    def test_autosync_skips_pre_push_finalize_when_disabled(
        self,
        mock_finalizer_cls,
        mock_commit,
        mock_push,
        mock_prune,
        config,
        logger,
        tmp_path,
    ):
        config["dry_run"] = False
        config["autosync_every_implementation_cycles"] = 1
        config["autosync_finalize_before_push"] = False
        mock_commit.return_value = False
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
        impl.run()
        mock_finalizer_cls.assert_not_called()
        mock_commit.assert_called()


class TestImplementIssueOutageRollback:
    """Fix C: outage failures must not consume issue attempts."""

    def test_outage_does_not_burn_attempt_count(self, config, logger, tmp_path):
        cli = make_cli_outage()
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        result = impl._implement_issue(issue)
        assert result is False

        updated = state.get_issue("ISSUE-001")
        # Increment from line ~369 was rolled back: still 0 attempts.
        assert updated.attempt_count == 0
        assert updated.status == IssueStatus.PENDING
        assert updated.failure_cause is None
        assert "[outage]" in updated.implementation_notes
        assert "service outage during implementation" in (state.stop_reason or "").lower()

    def test_resume_after_outage_returns_pending(self, config, logger, tmp_path):
        cli = make_cli_outage()
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        impl._implement_issue(issue)
        # The whole point of Fix C: the issue is still pickable for retry.
        pending = state.get_pending_issues()
        assert len(pending) == 1
        assert pending[0].id == "ISSUE-001"

    def test_outage_branch_runs_before_token_exhausted_branch(self, config, logger, tmp_path):
        # Make a CLI that emits both signals — service_down failure_type and
        # token-exhausted message text. Outage branch must win, otherwise
        # the issue would be tagged FAILED with FAILURE_CAUSE_TOKEN_EXHAUSTED
        # and the run would stop instead of pausing.
        cli = MagicMock()
        cli.execute_prompt.return_value = {
            "success": False,
            "output": "",
            "error": (
                "Claude has been unavailable for an extended period (outage). "
                "all available providers/models are out of tokens"
            ),
            "failure_type": "service_down",
            "duration_seconds": 1.0,
            "retries": 0,
        }
        cli.set_complexity = MagicMock()
        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        issue = Issue.from_dict(state.issues[0])
        impl._implement_issue(issue)
        updated = state.get_issue("ISSUE-001")
        assert updated.status == IssueStatus.PENDING
        assert updated.failure_cause is None


class TestRunLoopOutagePause:
    """Fix D: cross-call outage pause."""

    @patch("aidlc.implementer.time.sleep")
    def test_run_loop_pauses_and_clears_stop_reason(self, mock_sleep, config, logger, tmp_path):
        # First call: outage. Second call: success. Run loop should pause
        # between them, clear the outage stop_reason, and not enter the
        # early-stop block.
        outage_response = {
            "success": False,
            "output": "",
            "error": "Claude has been unavailable for an extended period (outage)",
            "failure_type": "service_down",
            "duration_seconds": 1.0,
            "retries": 0,
        }
        success_response = {
            "success": True,
            "output": (
                "```json\n"
                + json.dumps(
                    {
                        "issue_id": "ISSUE-001",
                        "success": True,
                        "summary": "Done",
                        "files_changed": [],
                        "tests_passed": True,
                        "notes": "",
                    }
                )
                + "\n```"
            ),
            "error": None,
            "failure_type": None,
            "duration_seconds": 1.0,
            "retries": 0,
        }
        cli = MagicMock()
        cli.execute_prompt.side_effect = [outage_response, success_response]
        cli.set_complexity = MagicMock()

        state = make_state_with_issue()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "claude_outputs").mkdir()
        config["implementation_outage_pause_seconds"] = 7
        # Cap the run so an unexpected loop doesn't hang the test.
        config["max_implementation_cycles"] = 5

        impl = Implementer(state, run_dir, config, cli, "ctx", logger)
        impl.run()

        # The implementer must have called sleep with the configured pause.
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        assert 7 in sleep_calls
        # The issue eventually succeeded, so it's implemented (or verified).
        updated = state.get_issue("ISSUE-001")
        assert updated.status in (IssueStatus.IMPLEMENTED, IssueStatus.VERIFIED)


class TestSignalHelpers:
    def test_is_service_outage_failure_type_match(self):
        from aidlc.implementer_signals import is_service_outage

        assert is_service_outage({"failure_type": "service_down"}) is True
        assert is_service_outage({"failure_type": "SERVICE_DOWN"}) is True

    def test_is_service_outage_message_match(self):
        from aidlc.implementer_signals import is_service_outage

        result = {
            "failure_type": "transient",
            "error": "Claude has been unavailable for an extended period (outage)",
        }
        assert is_service_outage(result) is True

    def test_is_service_outage_negative_cases(self):
        from aidlc.implementer_signals import is_service_outage

        assert is_service_outage({}) is False
        assert is_service_outage({"failure_type": "issue"}) is False
        assert is_service_outage({"failure_type": "transient", "error": "rate limit"}) is False
        assert is_service_outage(None) is False
        assert is_service_outage("not a dict") is False

    def test_is_service_outage_stop_reason(self):
        from aidlc.implementer_signals import (
            SERVICE_OUTAGE_STOP_REASON,
            is_service_outage_stop_reason,
        )

        assert is_service_outage_stop_reason(SERVICE_OUTAGE_STOP_REASON) is True
        assert (
            is_service_outage_stop_reason("Claude service outage during implementation; foo")
            is True
        )
        assert is_service_outage_stop_reason("token exhausted") is False
        assert is_service_outage_stop_reason(None) is False
        assert is_service_outage_stop_reason("") is False


class TestResetOutageFailedAttempts:
    def test_resets_only_outage_marked_issues(self, logger):
        from aidlc.implementer_helpers import (
            OUTAGE_MARKER,
            reset_outage_failed_attempts,
        )

        state = RunState(run_id="t", config_name="c")
        state.issues = [
            {
                "id": "ISSUE-001",
                "title": "outage-stuck",
                "description": "",
                "priority": "high",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": [],
                "status": "failed",
                "failure_cause": "failed_unknown",
                "implementation_notes": (
                    f"\nAttempt 3 failed (sample): timeout {OUTAGE_MARKER} attempt rolled back"
                ),
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 3,
                "max_attempts": 3,
            },
            {
                "id": "ISSUE-002",
                "title": "legitimately-broken",
                "description": "",
                "priority": "high",
                "labels": [],
                "dependencies": [],
                "acceptance_criteria": [],
                "status": "failed",
                "failure_cause": "failed_dependency",
                "implementation_notes": "real dependency error",
                "verification_result": "",
                "files_changed": [],
                "attempt_count": 3,
                "max_attempts": 3,
            },
        ]
        n = reset_outage_failed_attempts(state, logger, lambda issue: None)
        assert n == 1
        i1 = state.get_issue("ISSUE-001")
        i2 = state.get_issue("ISSUE-002")
        assert i1.status == IssueStatus.PENDING
        assert i1.attempt_count == 0
        assert i1.failure_cause is None
        # The non-outage issue must not be touched.
        assert i2.status == IssueStatus.FAILED
        assert i2.attempt_count == 3
        assert i2.failure_cause == "failed_dependency"

    def test_no_outage_issues_returns_zero(self, logger):
        from aidlc.implementer_helpers import reset_outage_failed_attempts

        state = RunState(run_id="t", config_name="c")
        state.issues = []
        assert reset_outage_failed_attempts(state, logger, lambda issue: None) == 0
