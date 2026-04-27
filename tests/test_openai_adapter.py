"""Tests for the OpenAI provider adapter."""

import json
import subprocess
from unittest.mock import MagicMock, patch

from aidlc.providers.openai_adapter import (
    OpenAIAdapter,
    _classify_openai_cli_failure,
    _codex_exit_zero_is_quota_blocker,
    _extract_codex_failure_diagnostics,
    _parse_codex_jsonl,
)


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
            "provider_call_timeout_seconds": 10,
        },
        logger,
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    logger.info.assert_any_call("OpenAI CLI still running (elapsed=0s, model=gpt-5.4)")


def test_extract_codex_failure_diagnostics_prefers_stderr_then_jsonl():
    j = json.dumps({"type": "error", "message": "Rate limit exceeded — try again in 60s"})
    assert "Rate limit" in _extract_codex_failure_diagnostics("", f"{j}\n")
    combined = _extract_codex_failure_diagnostics("outerr", j)
    assert "outerr" in combined
    assert "Rate limit" in combined


def test_extract_codex_failure_diagnostics_codex_plaintext_tui():
    """Simulates `codex exec` stderr empty, stdout = formatted TUI + usage message (no JSONL)."""
    stdout = """╭──────────────────────────────────────────────╮
│ model:     gpt-5.4 medium   /model to change │
╰──────────────────────────────────────────────╯

  Tip: Try the Codex App.

• Model changed to gpt-5.4 medium


› testy


■ You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro),
visit https://chatgpt.com/codex/settings/usage to purchase more credits or try
again at 5:41 PM.
"""
    d = _extract_codex_failure_diagnostics("", stdout)
    assert "usage limit" in d.lower()
    assert "5:41 pm" in d.lower()
    assert _classify_openai_cli_failure(d) == "rate_limited"


def test_extract_codex_failure_diagnostics_nested_openai_error():
    payload = {"error": {"type": "rate_limit_error", "message": "Too many requests"}}
    text = json.dumps(payload)
    diag = _extract_codex_failure_diagnostics("", text)
    assert "too many" in diag.lower() or "rate_limit" in diag.lower()


def test_classify_openai_cli_failure_rate_limited():
    assert _classify_openai_cli_failure("429 too many requests") == "rate_limited"
    assert _classify_openai_cli_failure("something else") == "issue"


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
def test_nonzero_exit_classifies_rate_limit_from_stdout_jsonl(mock_popen, tmp_path):
    err_line = json.dumps(
        {
            "type": "error",
            "message": "Rate limit reached for gpt-5.4 in organization org-x",
        }
    )
    proc = MagicMock()
    proc.communicate.return_value = (f"{err_line}\n", "")
    proc.returncode = 1
    mock_popen.return_value = proc
    adapter = OpenAIAdapter(
        {
            "providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.4"}},
            "provider_call_timeout_seconds": 30,
        },
        MagicMock(),
    )
    result = adapter.execute_prompt("hello", tmp_path)
    assert result["success"] is False
    assert result["failure_type"] == "rate_limited"
    assert "rate limit" in result["error"].lower()
    assert result["output"] is not None


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_zero_exit_with_usage_tui_is_failure(mock_popen, tmp_path):
    """Codex may exit 0 while only printing quota / TUI text (no JSONL completion)."""
    stdout = """■ You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro),
visit https://chatgpt.com/codex/settings/usage to purchase more credits or try
again at 5:41 PM.
"""
    proc = MagicMock()
    proc.communicate.return_value = (stdout, "")
    proc.returncode = 0
    mock_popen.return_value = proc
    adapter = OpenAIAdapter(
        {
            "providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.4"}},
            "provider_call_timeout_seconds": 30,
        },
        MagicMock(),
    )
    result = adapter.execute_prompt("hello", tmp_path)
    assert result["success"] is False
    assert result["failure_type"] == "rate_limited"
    assert "usage limit" in (result.get("error") or "").lower()


def test_codex_exit_zero_blocker_helper_negative():
    ok, _ = _codex_exit_zero_is_quota_blocker('{"type":"turn.completed","usage":{}}\n', "", "")
    assert ok is False


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
