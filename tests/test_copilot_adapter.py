"""Tests for the GitHub Copilot provider adapter."""

import logging
from unittest.mock import MagicMock, patch

from aidlc.providers.copilot_adapter import CopilotAdapter


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
    assert cmd == ["copilot", "-p", "hello", "--allow-all", "-s"]


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
    assert cmd == ["copilot", "-p", "hello", "--allow-all", "-s", "--model", "gpt-4.1"]
    assert result["success"] is True
    assert result["model_used"] == "gpt-4.1"


def test_returns_empty_default_model_when_unset():
    adapter = CopilotAdapter(
        {"providers": {"copilot": {"default_model": "", "phase_models": {"planning": ""}}}},
        logging.getLogger("test.copilot"),
    )

    assert adapter.get_default_model() == ""
    assert adapter.get_default_model("planning") == ""
