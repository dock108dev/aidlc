"""Tests for the OpenAI provider adapter."""

import subprocess
from unittest.mock import MagicMock, patch

from aidlc.providers.openai_adapter import OpenAIAdapter, _parse_codex_jsonl


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


def test_parse_codex_jsonl_extracts_last_turn_and_agent_message():
    raw = (
        '{"type":"thread.started"}\n'
        '{"type":"item.completed","item":{"id":"1","item_type":"agent_message","text":"hi"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":20}}\n'
    )
    out, usage = _parse_codex_jsonl(raw)
    assert out == "hi"
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 20
    assert usage["cache_read_input_tokens"] == 40


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_exec_command_includes_json_flag(mock_popen, tmp_path):
    proc = MagicMock()
    proc.communicate.return_value = ('{"type":"turn.completed","usage":{}}\n', "")
    proc.returncode = 0
    mock_popen.return_value = proc
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-4o"}}},
        MagicMock(),
    )
    adapter.execute_prompt("p", tmp_path)
    cmd = mock_popen.call_args[0][0]
    assert cmd[:4] == ["codex", "exec", "--json", "--model"]
