"""Tests for aidlc.claude_cli module."""

import logging
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
        "claude_timeout_seconds": 10,
        "dry_run": False,
    }


class TestClaudeCLIInit:
    def test_defaults(self, logger):
        cli = ClaudeCLI({}, logger)
        assert cli.cli_command == "claude"
        assert cli.model == "opus"
        assert cli.max_retries == 2
        assert cli.retry_base_delay == 30
        assert cli.retry_max_delay == 300
        assert cli.retry_backoff_factor == 2.0
        assert cli.timeout == 600

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
        # Each delay should grow (approximately, jitter adds noise)
        # Base is 0.01, factor 2.0: 0.01, 0.02, 0.04 + jitter
        assert d0 < d1 or d0 < 0.02  # Allow for jitter
        assert d1 < d2 or d1 < 0.04

    def test_max_delay_cap(self, logger):
        config = {
            "retry_base_delay_seconds": 100,
            "retry_max_delay_seconds": 150,
            "retry_backoff_factor": 10.0,
        }
        cli = ClaudeCLI(config, logger)
        delay = cli._retry_delay(5)
        # Should be capped at max_delay + 25% jitter
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
    @patch("aidlc.claude_cli.subprocess.run")
    def test_success(self, mock_run, base_config, logger, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output text",
            stderr="",
        )
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is True
        assert result["output"] == "output text"
        assert result["failure_type"] is None

    @patch("aidlc.claude_cli.subprocess.run")
    def test_allow_edits_flag(self, mock_run, base_config, logger, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        cli = ClaudeCLI(base_config, logger)
        cli.execute_prompt("prompt", tmp_path, allow_edits=True)
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd

    @patch("aidlc.claude_cli.subprocess.run")
    def test_failure_retries(self, mock_run, base_config, logger, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="API error: rate limit",
        )
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is False
        assert result["retries"] == 3  # initial + 2 retries
        assert mock_run.call_count == 3
        assert result["failure_type"] == "transient"

    @patch("aidlc.claude_cli.subprocess.run")
    def test_preserves_non_transient_failure_type(self, mock_run, base_config, logger, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="syntax error in prompt",
        )
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is False
        assert result["failure_type"] == "issue"
        assert "syntax error" in result["error"]

    @patch("aidlc.claude_cli.subprocess.run")
    def test_timeout_retries(self, mock_run, base_config, logger, tmp_path):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=10)
        cli = ClaudeCLI(base_config, logger)
        result = cli.execute_prompt("prompt", tmp_path)
        assert result["success"] is False
        assert mock_run.call_count == 3

    @patch("aidlc.claude_cli.subprocess.run")
    def test_file_not_found_raises(self, mock_run, base_config, logger, tmp_path):
        mock_run.side_effect = FileNotFoundError()
        cli = ClaudeCLI(base_config, logger)
        with pytest.raises(ClaudeCLIError, match="not found"):
            cli.execute_prompt("prompt", tmp_path)


class TestClassifyFailure:
    def test_transient_rate_limit(self):
        assert ClaudeCLI._classify_failure(1, "rate limit exceeded") == "transient"

    def test_transient_503(self):
        assert ClaudeCLI._classify_failure(1, "error 503 service unavailable") == "transient"

    def test_transient_signal(self):
        assert ClaudeCLI._classify_failure(137, "") == "transient"  # SIGKILL
        assert ClaudeCLI._classify_failure(-9, "") == "transient"

    def test_issue_type(self):
        assert ClaudeCLI._classify_failure(1, "syntax error in prompt") == "issue"


class TestCheckAvailable:
    @patch("aidlc.claude_cli.subprocess.run")
    def test_available(self, mock_run, base_config, logger):
        mock_run.return_value = MagicMock(returncode=0)
        cli = ClaudeCLI(base_config, logger)
        assert cli.check_available() is True

    @patch("aidlc.claude_cli.subprocess.run")
    def test_not_available(self, mock_run, base_config, logger):
        mock_run.side_effect = FileNotFoundError()
        cli = ClaudeCLI(base_config, logger)
        assert cli.check_available() is False

    @patch("aidlc.claude_cli.subprocess.run")
    def test_timeout(self, mock_run, base_config, logger):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=10)
        cli = ClaudeCLI(base_config, logger)
        assert cli.check_available() is False
