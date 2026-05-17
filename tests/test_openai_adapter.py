"""Tests for the OpenAI provider adapter."""

import json
import subprocess
from unittest.mock import MagicMock, patch

from aidlc.providers.codex_output import (
    classify_openai_cli_failure,
    codex_exit_zero_is_quota_blocker,
    extract_codex_failure_diagnostics,
    parse_codex_jsonl,
)
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
            "provider_call_timeout_seconds": 10,
        },
        logger,
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    logger.info.assert_any_call("OpenAI CLI still running (elapsed=0s, model=gpt-5.4)")


def testextract_codex_failure_diagnostics_prefers_stderr_then_jsonl():
    j = json.dumps({"type": "error", "message": "Rate limit exceeded — try again in 60s"})
    assert "Rate limit" in extract_codex_failure_diagnostics("", f"{j}\n")
    combined = extract_codex_failure_diagnostics("outerr", j)
    assert "outerr" in combined
    assert "Rate limit" in combined


def testextract_codex_failure_diagnostics_codex_plaintext_tui():
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
    d = extract_codex_failure_diagnostics("", stdout)
    assert "usage limit" in d.lower()
    assert "5:41 pm" in d.lower()
    assert classify_openai_cli_failure(d) == "rate_limited"


def testextract_codex_failure_diagnostics_nested_openai_error():
    payload = {"error": {"type": "rate_limit_error", "message": "Too many requests"}}
    text = json.dumps(payload)
    diag = extract_codex_failure_diagnostics("", text)
    assert "too many" in diag.lower() or "rate_limit" in diag.lower()


def testextract_codex_failure_diagnostics_ignores_non_error_stdout():
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
    assert extract_codex_failure_diagnostics("", event) == ""


def testextract_codex_failure_diagnostics_ignores_agent_message_payload():
    event = json.dumps(
        {
            "type": "agent_message",
            "message": '{"actions": [], "planning_complete": false}',
        }
    )

    assert extract_codex_failure_diagnostics("", event) == ""


def testclassify_openai_cli_failure_rate_limited():
    assert classify_openai_cli_failure("429 too many requests") == "rate_limited"
    assert classify_openai_cli_failure("something else") == "issue"
    assert classify_openai_cli_failure("Prompt hides when unavailable") == "issue"
    assert classify_openai_cli_failure("503 service unavailable") == "transient"


def testparse_codex_jsonl_extracts_last_turn_and_agent_message():
    raw = (
        '{"type":"thread.started"}\n'
        '{"type":"item.completed","item":{"id":"1","item_type":"agent_message","text":"hi"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":20}}\n'
    )
    out, usage = parse_codex_jsonl(raw)
    assert out == "hi"
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 20
    assert usage["cache_read_input_tokens"] == 40


def testparse_codex_jsonl_extracts_assistant_message_content_shape():
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

    out, usage = parse_codex_jsonl(raw)

    assert out == '{"actions": [], "planning_complete": false}'
    assert usage["input_tokens"] == 10
    assert usage["cache_read_input_tokens"] == 4


def testparse_codex_jsonl_raw_scans_assistant_content_when_line_is_prefixed():
    event = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "# Findings\n\nRecovered."}],
            },
        }
    )
    raw = "\n".join(
        [
            "prefix " + event,
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5}}),
        ]
    )

    out, usage = parse_codex_jsonl(raw)

    assert out == "# Findings\n\nRecovered."
    assert usage["input_tokens"] == 5


def testparse_codex_jsonl_extracts_top_level_agent_message():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "agent_message", "message": "# Findings\n\nDone."}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
        ]
    )

    out, usage = parse_codex_jsonl(raw)

    assert out == "# Findings\n\nDone."
    assert usage["input_tokens"] == 1


def testparse_codex_jsonl_extracts_plain_output_before_turn_completed():
    raw = "\n".join(
        [
            "Reading additional input from stdin...",
            "# Findings",
            "",
            "Useful discovery output.",
            "",
            "```json",
            "[]",
            "```",
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5}}),
        ]
    )

    out, usage = parse_codex_jsonl(raw)

    assert out.startswith("# Findings")
    assert "Useful discovery output" in out
    assert '{"type": "turn.completed"' not in out
    assert usage["input_tokens"] == 5


def testparse_codex_jsonl_does_not_treat_raw_event_as_plain_output():
    raw = "\n".join(
        [
            '{"type":"item.completed","item":"not an assistant message"}',
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5}}),
        ]
    )

    out, usage = parse_codex_jsonl(raw)

    assert out == ""
    assert usage["input_tokens"] == 5


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
    assert result["output"] is None
    assert "raw_stdout" in result


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
def test_exit_zero_with_rate_limit_substring_in_tool_output_is_success(mock_popen, tmp_path):
    """End-to-end regression: codex exits 0, runs ``cat .aidlc/config.json`` as
    a tool call, that config legitimately contains keys like
    ``routing_rate_limit_cooldown_seconds``. The substring ``rate_limit`` must
    not trigger the quota-blocker — discovery should land as a normal success
    with the findings doc from ``--output-last-message``.

    Reproduces the bug where discovery against a fresh repo logged
    ``Discovery model call failed: # Findings ...`` because the assistant text
    + JSONL stdout (containing the config dump) was being scanned for
    rate-limit patterns and substring-matching ``routing_rate_limit_*`` keys.
    """

    def make_proc(cmd, **_kwargs):
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# Findings\n\nRepo state summary.\n\n```json\n[]\n```")
        proc = MagicMock()
        proc.communicate.return_value = (
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "command_execution",
                                "command": "cat .aidlc/config.json",
                                "aggregated_output": (
                                    '{"routing_rate_limit_cooldown_seconds": 300, '
                                    '"routing_rate_limit_buffer_base_seconds": 3600}'
                                ),
                                "exit_code": 0,
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "agent_message",
                                "text": "# Findings\n\nRepo state summary.\n\n```json\n[]\n```",
                            },
                        }
                    ),
                    json.dumps({"type": "turn.completed", "usage": {}}),
                ]
            ),
            "",
        )
        proc.returncode = 0
        return proc

    mock_popen.side_effect = make_proc
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.5"}}},
        MagicMock(),
    )

    result = adapter.execute_prompt("hello", tmp_path)

    assert result["success"] is True, f"unexpected failure: {result.get('error')!r}"
    assert result["failure_type"] is None
    assert result["error"] is None
    assert "# Findings" in (result.get("output") or "")


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
def test_nonzero_exit_with_plain_output_and_completed_turn_is_success(mock_popen, tmp_path):
    stdout = "\n".join(
        [
            "Reading additional input from stdin...",
            "# Findings",
            "",
            "Useful discovery output.",
            "",
            "```json",
            "[]",
            "```",
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5}}),
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
    assert result["raw_stdout"] == stdout
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
    ok, _ = codex_exit_zero_is_quota_blocker('{"type":"turn.completed","usage":{}}\n', "", "")
    assert ok is False


def test_codex_exit_zero_blocker_ignores_rate_limit_substring_in_tool_output():
    """Tool output that embeds the user's own repo content — e.g. a config key
    like ``routing_rate_limit_cooldown_seconds`` printed by ``cat .aidlc/config.json`` —
    must not be classified as a quota blocker. The substring pattern
    ``rate_limit`` would otherwise false-positive on every config dump, killing
    real discovery responses with success=False / error=<full output>."""
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "cat .aidlc/config.json",
                        "aggregated_output": (
                            '{"routing_rate_limit_cooldown_seconds": 300, '
                            '"routing_rate_limit_buffer_base_seconds": 3600}'
                        ),
                        "exit_code": 0,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "# Findings\n\nDiscussion of rate-limit handling for the GitHub API.",
                    },
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )
    parsed_out = "# Findings\n\nDiscussion of rate-limit handling for the GitHub API."
    blocked, diag = codex_exit_zero_is_quota_blocker(stdout, "", parsed_out)
    assert blocked is False
    assert diag == ""


def test_codex_exit_zero_blocker_still_fires_on_real_quota_text():
    """A genuine TUI quota message (plain-text 'usage limit' / 'try again at'
    line in stdout) must still trigger — that's what the helper exists for."""
    stdout = "■ You've hit your usage limit. Upgrade to Pro to keep going.\nTry again at 5:41 PM.\n"
    blocked, diag = codex_exit_zero_is_quota_blocker(stdout, "", "")
    assert blocked is True
    assert "usage limit" in diag.lower()


def test_codex_exit_zero_blocker_fires_on_stderr_rate_limit():
    """Real provider rate-limit diagnostic in stderr must still be honored."""
    blocked, diag = codex_exit_zero_is_quota_blocker(
        '{"type":"turn.completed","usage":{}}\n',
        "Rate limit reached for gpt-5.5",
        "",
    )
    assert blocked is True
    assert "rate limit" in diag.lower()


def test_extract_diagnostics_ignores_rate_limit_substring_in_jsonl_agent_message():
    """A research/discovery JSONL line whose agent_message ``text`` discusses
    API rate limits must not be classified as a Codex TUI quota message.

    Reproduces the bug where a single ``item.completed`` event containing
    ``"text":"...iTunes Search API rate limit handling..."`` got matched by
    the ``"rate limit"`` plain-hint pass, populated the diagnostic, and made
    the routing engine cooldown a healthy provider for an hour.
    """
    agent_msg = (
        "# Local Storage And Cache Shape\n\n"
        "## API integration notes\n\n"
        "The iTunes Search API enforces a rate limit (try again after a "
        "short backoff). For GitHub, monitor X-RateLimit-Remaining and "
        "honor try again at responses on 429."
    )
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_0",
                        "type": "agent_message",
                        "text": agent_msg,
                    },
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
        ]
    )
    assert extract_codex_failure_diagnostics("", stdout) == ""
    blocked, _ = codex_exit_zero_is_quota_blocker(stdout, "", agent_msg)
    assert blocked is False


@patch("aidlc.providers.openai_adapter.subprocess.Popen")
def test_research_jsonl_discussing_rate_limits_lands_as_success(mock_popen, tmp_path):
    """End-to-end regression: a research call whose JSONL output contains a
    long agent_message about API rate-limit handling must return success.
    Previously this got the provider cooldown-quarantined for an hour with
    ``failure_type='rate_limited'``."""
    research_text = (
        "# Local Storage And Cache Shape For Repeatable Runs\n\n"
        "## Scope\n\nThis design answers the storage question implied by "
        "BRAINDUMP.md.\n\n## API guidance\n\n"
        "When calling the iTunes Search API, respect the rate limit and "
        "honor try again at headers. The GitHub API similarly enforces a "
        "rate limit per token; back off and try again in a few minutes "
        "rather than hammering the endpoint."
    )

    def make_proc(cmd, **_kwargs):
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(research_text)
        proc = MagicMock()
        proc.communicate.return_value = (
            "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "t1"}),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "item_0",
                                "type": "agent_message",
                                "text": research_text,
                            },
                        }
                    ),
                    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 50}}),
                ]
            ),
            "",
        )
        proc.returncode = 0
        return proc

    mock_popen.side_effect = make_proc
    adapter = OpenAIAdapter(
        {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5.5"}}},
        MagicMock(),
    )

    result = adapter.execute_prompt("research please", tmp_path)

    assert result["success"] is True, f"unexpected failure: {result.get('error')!r}"
    assert result["failure_type"] is None
    assert result["error"] is None
    assert "Local Storage And Cache Shape" in (result.get("output") or "")


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
