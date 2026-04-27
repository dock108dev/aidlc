"""Tests for the GitHub Copilot provider adapter."""

import logging
import subprocess
from unittest.mock import MagicMock, patch

from aidlc.providers.copilot_adapter import (
    CopilotAdapter,
    _parse_copilot_usage_blob,
    _strip_copilot_trailing_stats,
)


def _mock_popen_success(stdout="ok", stderr=""):
    proc = MagicMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = 0
    return proc


def test_omits_model_flag_when_no_model_is_configured(tmp_path):
    adapter = CopilotAdapter(
        {
            "providers": {
                "copilot": {
                    "cli_command": "copilot",
                    "default_model": "",
                }
            }
        },
        logging.getLogger("test.copilot"),
    )

    cmd = adapter._build_command("", allow_edits=False, prompt="hello")
    assert cmd == ["copilot", "-p", "hello", "--allow-all", "--no-ask-user"]


@patch("aidlc.providers.copilot_adapter.subprocess.Popen")
def test_passes_explicit_model_when_configured(mock_popen, tmp_path):
    mock_popen.return_value = _mock_popen_success()
    adapter = CopilotAdapter(
        {
            "providers": {
                "copilot": {
                    "cli_command": "copilot",
                    "default_model": "gpt-4.1",
                }
            }
        },
        logging.getLogger("test.copilot"),
    )

    result = adapter.execute_prompt("hello", tmp_path)

    cmd = mock_popen.call_args[0][0]
    assert cmd == [
        "copilot",
        "-p",
        "hello",
        "--allow-all",
        "--no-ask-user",
        "--model",
        "gpt-4.1",
    ]
    assert result["success"] is True
    assert result["model_used"] == "gpt-4.1"


def test_returns_empty_default_model_when_unset():
    adapter = CopilotAdapter(
        {"providers": {"copilot": {"default_model": "", "phase_models": {"planning": ""}}}},
        logging.getLogger("test.copilot"),
    )

    assert adapter.get_default_model() == ""
    assert adapter.get_default_model("planning") == ""


@patch("aidlc.providers.copilot_adapter.subprocess.Popen")
def test_logs_heartbeat_while_running(mock_popen, tmp_path):
    proc = MagicMock()
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="copilot", timeout=1),
        ("ok", ""),
    ]
    proc.returncode = 0
    mock_popen.return_value = proc
    logger = MagicMock()
    adapter = CopilotAdapter(
        {
            "providers": {"copilot": {"cli_command": "copilot", "default_model": ""}},
            "claude_long_run_warn_seconds": 1,
            "provider_call_timeout_seconds": 10,
        },
        logger,
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    logger.info.assert_any_call("Copilot CLI still running (elapsed=0s, model=default)")


def test_silent_flag_restores_dash_s():
    adapter = CopilotAdapter(
        {
            "providers": {
                "copilot": {
                    "cli_command": "copilot",
                    "default_model": "",
                    "silent": True,
                }
            }
        },
        logging.getLogger("test.copilot"),
    )
    cmd = adapter._build_command("", allow_edits=False, prompt="x")
    assert cmd == ["copilot", "-p", "x", "--allow-all", "--no-ask-user", "-s"]


def test_parse_copilot_usage_blob_input_output_pair():
    blob = "Some banner\nInput tokens: 1,234\nOutput tokens: 567\n"
    u = _parse_copilot_usage_blob(blob)
    assert u["input_tokens"] == 1234
    assert u["output_tokens"] == 567


def test_parse_copilot_usage_slash_form():
    blob = "done\n12,000 in / 400 out\n"
    u = _parse_copilot_usage_blob(blob)
    assert u["input_tokens"] == 12000
    assert u["output_tokens"] == 400


def test_strip_copilot_trailing_stats():
    raw = "Answer line one\nAnswer two\nInput tokens: 10\n"
    assert _strip_copilot_trailing_stats(raw) == "Answer line one\nAnswer two"
