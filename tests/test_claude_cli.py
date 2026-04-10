"""Tests for aidlc.claude_cli module."""

import logging
import itertools
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from aidlc.claude_cli import ClaudeCLI, ClaudeCLIError


@pytest.fixture
def logger():
    return logging.getLogger("test_claude_cli")


@pytest.fixture
def base_config():
    return {
        "claude_cli_command": "claude",
        "claude_model": "opus",
        "retry_max_attempts": 2,
        "retry_base_delay_seconds": 0.01,  # Fast for tests
        "retry_max_delay_seconds": 0.05,
        "retry_backoff_factor": 2.0,
        "claude_long_run_warn_seconds": 300,
        "dry_run": False,
    }


def _mock_popen_success(stdout="output text", stderr=""):
    """Create a mock Popen that succeeds immediately."""
    proc = MagicMock()
    proc.poll.return_value = 0  # Process finished
    proc.wait.return_value = 0
    proc.returncode = 0
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.read.return_value = stdout
    proc.stderr = MagicMock()
    proc.stderr.read.return_value = stderr
    return proc


def _mock_popen_failure(returncode=1, stderr="error"):
    """Create a mock Popen that fails."""
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.wait.return_value = returncode
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.read.return_value = ""
    proc.stderr = MagicMock()
    proc.stderr.read.return_value = stderr
    return proc


class TestClaudeCLIInit:
    def test_defaults(self, logger):
        cli = ClaudeCLI({}, logger)
        assert cli.cli_command == "claude"
        assert cli.model == "opus"
        assert cli.max_retries == 2
        assert cli.retry_base_delay == 30
        assert cli.retry_max_delay == 300
        assert cli.retry_backoff_factor == 2.0

    def test_custom_config(self, base_config, logger):
        cli = ClaudeCLI(base_config, logger)
        assert cli.retry_base_delay == 0.01
        assert cli.retry_max_delay == 0.05
        assert cli.retry_backoff_factor == 2.0


class TestRetryDelay:
    def test_exponential_growth(self, base_config, logger):
        cli = ClaudeCLI(base_config, logger)
        d0 = cli._retry_delay(0)
        d1 = cli._retry_delay(1)
        d2 = cli._retry_delay(2)
        assert d0 < d1 or d0 < 0.02
        assert d1 < d2 or d1 < 0.04

    def test_max_delay_cap(self, logger):
        config = {
            "retry_base_delay_seconds": 100,
            "retry_max_delay_seconds": 150,
            "retry_backoff_factor": 10.0,
        }
        cli = ClaudeCLI(config, logger)
        delay = cli._retry_delay(5)
        assert delay <= 150 * 1.25 + 1


class TestDryRun:
    def test_dry_run_returns_success(self, logger, tmp_path):
        config = {"dry_run": True}
        cli = ClaudeCLI(config, logger)
        result = cli.execute_prompt("test prompt", tmp_path)
        assert result["success"] is True
        assert result["output"] == "[DRY RUN] No execution"
        assert result["duration_seconds"] == 0.0
        assert result["retries"] == 0

    def test_dry_run_check_available(self, logger):
        config = {"dry_run": True}
        cli = ClaudeCLI(config, logger)
        assert cli.check_available() is True


class TestExecutePrompt:
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_success(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_success("output text")
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is True
        assert result["output"] == "output text"
        assert result["failure_type"] is None

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_allow_edits_flag(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_success("ok")
        cli = ClaudeCLI(base_config, logger)
        cli.execute_prompt("prompt", tmp_path, allow_edits=True)
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_failure_retries(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_failure(1, "API error: rate limit")
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is False
        assert result["retries"] == 3  # initial + 2 retries
        assert mock_popen.call_count == 3
        assert result["failure_type"] == "transient"

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_preserves_non_transient_failure_type(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_failure(1, "syntax error in prompt")
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is False
        assert result["failure_type"] == "issue"
        assert "syntax error" in result["error"]

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_file_not_found_raises(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.side_effect = FileNotFoundError()
        cli = ClaudeCLI(base_config, logger)
        with pytest.raises(ClaudeCLIError, match="not found"):
            cli.execute_prompt("prompt", tmp_path)

    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_model_override(self, mock_popen, base_config, logger, tmp_path):
        mock_popen.return_value = _mock_popen_success("ok")
        cli = ClaudeCLI(base_config, logger)
        cli.execute_prompt("prompt", tmp_path, model_override="sonnet")
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "sonnet"

    @patch("aidlc.claude_cli.time.time")
    @patch("aidlc.claude_cli.subprocess.Popen")
    def test_hard_timeout_terminates_process(self, mock_popen, mock_time, base_config, logger, tmp_path):
        proc = MagicMock()
        proc.poll.side_effect = [None, 124]
        proc.wait.side_effect = [0]
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.read.return_value = ""
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = ""
        mock_popen.return_value = proc

        base_config["claude_hard_timeout_seconds"] = 1
        base_config["retry_max_attempts"] = 0
        clock = itertools.count(start=0.0, step=1.2)
        mock_time.side_effect = lambda: next(clock)

        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is False
        assert result["failure_type"] == "timeout"
        assert proc.terminate.called


class TestClassifyFailure:
    def test_transient_rate_limit(self):
        assert ClaudeCLI._classify_failure(1, "rate limit exceeded") == "transient"

    def test_transient_503(self):
        assert ClaudeCLI._classify_failure(1, "error 503 service unavailable") == "transient"

    def test_transient_connection_error(self):
        assert ClaudeCLI._classify_failure(1, "connection refused") == "transient"

    def test_non_transient_issue(self):
        assert ClaudeCLI._classify_failure(1, "syntax error") == "issue"

    def test_signal_death_is_transient(self):
        assert ClaudeCLI._classify_failure(137, "") == "transient"
        assert ClaudeCLI._classify_failure(-9, "") == "transient"
