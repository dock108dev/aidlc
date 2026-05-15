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


def test_extract_codex_failure_diagnostics_can_skip_stdout_tail():
    event = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "sed -n '1,20p' file.tsx",
                "aggregated_output": "source code",
                "exit_code": 0,
            },
        }
    )
    assert _extract_codex_failure_diagnostics("", event, include_stdout_tail=False) == ""
    assert "command_execution" in _extract_codex_failure_diagnostics("", event)


def test_extract_codex_failure_diagnostics_ignores_agent_message_payload():
    event = json.dumps(
        {
            "type": "agent_message",
            "message": '{"actions": [], "planning_complete": false}',
        }
    )

    assert _extract_codex_failure_diagnostics("", event, include_stdout_tail=False) == ""


def test_classify_openai_cli_failure_rate_limited():
    assert _classify_openai_cli_failure("429 too many requests") == "rate_limited"
    assert _classify_openai_cli_failure("something else") == "issue"
    assert _classify_openai_cli_failure("Prompt hides when unavailable") == "issue"
    assert _classify_openai_cli_failure("503 service unavailable") == "transient"


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


def test_parse_codex_jsonl_extracts_assistant_message_content_shape():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "msg1",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"actions": [], "planning_complete": false}',
                            }
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 4,
                        "output_tokens": 3,
                    },
                }
            ),
        ]
    )

    out, usage = _parse_codex_jsonl(raw)

    assert out == '{"actions": [], "planning_complete": false}'
    assert usage["input_tokens"] == 10
    assert usage["cache_read_input_tokens"] == 4


def test_parse_codex_jsonl_extracts_top_level_agent_message():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "agent_message", "message": "# Findings\n\nDone."}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
        ]
    )

    out, usage = _parse_codex_jsonl(raw)

    assert out == "# Findings\n\nDone."
    assert usage["input_tokens"] == 1


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


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_nonzero_exit_with_last_message_and_only_tool_json_is_success(mock_popen, tmp_path):
    def make_proc(cmd, **_kwargs):
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# Findings\n\nDone.")
        proc = MagicMock()
        proc.communicate.return_value = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "sed -n '1,20p' file.tsx",
                        "aggregated_output": "source code",
                        "exit_code": 0,
                    },
                }
            ),
            "",
        )
        proc.returncode = 1
        return proc

    mock_popen.side_effect = make_proc
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.5"}}},
        MagicMock(),
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    assert result["output"] == "# Findings\n\nDone."
    assert result["usage_source"] == "codex_last_message"


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_nonzero_exit_with_completed_turn_and_agent_message_is_success(mock_popen, tmp_path):
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "# Findings\n\nUsable.\n\n```json\n[]\n```",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 12,
                        "cached_input_tokens": 3,
                        "output_tokens": 4,
                    },
                }
            ),
        ]
    )
    proc = MagicMock()
    proc.communicate.return_value = (stdout, "")
    proc.returncode = 1
    mock_popen.return_value = proc
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.5"}}},
        MagicMock(),
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    assert result["output"].startswith("# Findings")
    assert result["usage_source"] == "codex_jsonl"
    assert result["continuation_session_id"] == "t1"
    assert result["usage"]["input_tokens"] == 12


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_nonzero_exit_with_completed_turn_and_message_content_is_success(mock_popen, tmp_path):
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    '{"frontier_assessment":"ok","actions":[{"action_type":"create_issue",'
                                    '"issue_id":"ISSUE-001","title":"Prompt hides when unavailable",'
                                    '"description":"Normal planning text.","priority":"high",'
                                    '"acceptance_criteria":["Prompt hides when unavailable."],'
                                    '"critical_gap":false}],"planning_complete":false}'
                                ),
                            }
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 12,
                        "cached_input_tokens": 3,
                        "output_tokens": 4,
                    },
                }
            ),
        ]
    )
    proc = MagicMock()
    proc.communicate.return_value = (stdout, "")
    proc.returncode = 1
    mock_popen.return_value = proc
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.5"}}},
        MagicMock(),
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    assert "Prompt hides when unavailable" in result["output"]
    assert result["failure_type"] is None
    assert result["usage_source"] == "codex_jsonl"


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_nonzero_exit_with_top_level_agent_message_is_success(mock_popen, tmp_path):
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps(
                {
                    "type": "agent_message",
                    "message": (
                        '{"frontier_assessment":"ok","actions":[],"planning_complete":false}'
                    ),
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 12,
                        "cached_input_tokens": 3,
                        "output_tokens": 4,
                    },
                }
            ),
        ]
    )
    proc = MagicMock()
    proc.communicate.return_value = (stdout, "")
    proc.returncode = 1
    mock_popen.return_value = proc
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.5"}}},
        MagicMock(),
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    assert '"frontier_assessment":"ok"' in result["output"]
    assert result["failure_type"] is None


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_nonzero_exit_completed_turn_still_honors_rate_limit(mock_popen, tmp_path):
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "partial answer"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )
    proc = MagicMock()
    proc.communicate.return_value = (stdout, "Rate limit reached for gpt-5.5")
    proc.returncode = 1
    mock_popen.return_value = proc
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.5"}}},
        MagicMock(),
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is False
    assert result["failure_type"] == "rate_limited"


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
    assert "--skip-git-repo-check" in cmd


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_exec_command_edit_mode_adds_dangerous_flag_without_full_auto(mock_popen, tmp_path):
    proc = MagicMock()
    proc.communicate.return_value = ('{"type":"turn.completed","usage":{}}\n', "")
    proc.returncode = 0
    mock_popen.return_value = proc
    logger = MagicMock()
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-4o"}}},
        logger,
    )

    adapter.execute_prompt("p", tmp_path, allow_edits=True)

    cmd = mock_popen.call_args[0][0]
    assert "--full-auto" not in cmd
    assert "--skip-git-repo-check" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    logger.warning.assert_called_once()


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_exec_command_edit_mode_warns_once_per_adapter(mock_popen, tmp_path):
    proc = MagicMock()
    proc.communicate.return_value = ('{"type":"turn.completed","usage":{}}\n', "")
    proc.returncode = 0
    mock_popen.return_value = proc
    logger = MagicMock()
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-4o"}}},
        logger,
    )

    adapter.execute_prompt("p1", tmp_path, allow_edits=True)
    adapter.execute_prompt("p2", tmp_path, allow_edits=True)

    assert logger.warning.call_count == 1


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_exec_command_resume_session_id_wins_for_codex_resume(mock_popen, tmp_path):
    proc = MagicMock()
    proc.communicate.return_value = ('{"type":"turn.completed","usage":{}}\n', "")
    proc.returncode = 0
    mock_popen.return_value = proc
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-4o"}}},
        MagicMock(),
    )

    adapter.execute_prompt(
        "p",
        tmp_path,
        continuation_session_id="continuation-thread",
        resume_session_id="resume-thread",
    )

    cmd = mock_popen.call_args[0][0]
    assert cmd[:6] == ["codex", "exec", "resume", "--json", "--model", "gpt-4o"]
    assert "--skip-git-repo-check" in cmd
    assert "continuation-thread" not in cmd
    assert cmd[-2] == "resume-thread"
    assert cmd[-1] == "p"


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_exec_command_adds_configured_reasoning_effort(mock_popen, tmp_path):
    proc = MagicMock()
    proc.communicate.return_value = ('{"type":"turn.completed","usage":{}}\n', "")
    proc.returncode = 0
    mock_popen.return_value = proc
    adapter = OpenAIAdapter(
        {
            "providers": {
                "openai": {
                    "cli_command": "codex",
                    "default_model": "gpt-5.5",
                    "model_reasoning_effort": "high",
                }
            }
        },
        MagicMock(),
    )

    adapter.execute_prompt("p", tmp_path)

    cmd = mock_popen.call_args[0][0]
    assert "-c" in cmd
    assert 'model_reasoning_effort="high"' in cmd
