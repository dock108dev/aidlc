"""Tests for the OpenAI provider adapter."""

import logging
import subprocess
from unittest.mock import MagicMock, patch

from aidlc.providers.openai_adapter import OpenAIAdapter


def _mock_popen_success(stdout="ok", stderr=""):
    proc = MagicMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = 0
    return proc


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_logs_heartbeat_while_running(mock_popen, tmp_path):
    proc = MagicMock()
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="codex", timeout=1),
        ("ok", ""),
    ]
    proc.returncode = 0
    mock_popen.return_value = proc
    logger = MagicMock()
    adapter = OpenAIAdapter(
        {
            "providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.4"}},
            "claude_long_run_warn_seconds": 1,
            "claude_hard_timeout_seconds": 10,
        },
        logger,
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    logger.info.assert_any_call("OpenAI CLI still running (elapsed=0s, model=gpt-5.4)")
