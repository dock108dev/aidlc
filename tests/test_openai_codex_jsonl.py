"""Branch coverage for OpenAI codex JSONL parsing."""

import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from aidlc.providers.base import HealthStatus
from aidlc.providers.openai_adapter import (
    OpenAIAdapter,
    _parse_codex_jsonl,
    extract_codex_thread_id,
)


def test_extract_codex_thread_id_from_thread_started():
    tid = "0199a213-81c0-7800-8aa1-bbab2a035a53"
    line = json.dumps({"type": "thread.started", "thread_id": tid})
    assert extract_codex_thread_id(line) == tid
    assert extract_codex_thread_id("") is None


def test_openai_build_command_resume_includes_thread_and_prompt():
    log = logging.getLogger("t_codex_cmd")
    cfg = {"providers": {"openai": {"cli_command": "codex", "default_model": "gpt-5"}}}
    ad = OpenAIAdapter(cfg, log)
    cmd = ad._build_command("gpt-5", True, "next task", "thread-uuid-1")
    assert cmd[:6] == ["codex", "exec", "resume", "--json", "--model", "gpt-5"]
    assert "--full-auto" in cmd
    assert cmd[-2] == "thread-uuid-1"
    assert cmd[-1] == "next task"


def test_parse_codex_jsonl_empty():
    text, usage = _parse_codex_jsonl("")
    assert text == ""
    assert usage == {}


def test_parse_codex_jsonl_skips_non_json_lines():
    stdout = "not json\n{broken\n"
    text, usage = _parse_codex_jsonl(stdout)
    assert text == ""
    assert usage == {}


def test_parse_codex_jsonl_skips_non_object():
    stdout = json.dumps([1, 2, 3])
    text, usage = _parse_codex_jsonl(stdout)
    assert text == ""


def test_parse_codex_jsonl_turn_completed_usage():
    line1 = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}})
    line2 = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "item_type": "assistant_message",
                "text": "  hello  ",
            },
        }
    )
    text, usage = _parse_codex_jsonl(f"{line1}\n{line2}")
    assert text.strip() == "hello"
    assert usage["input_tokens"] == 3
    assert usage["output_tokens"] == 2
    assert usage["cache_read_input_tokens"] == 0


def test_parse_codex_jsonl_uses_agent_message_type():
    line = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "z"},
        }
    )
    text, _u = _parse_codex_jsonl(line)
    assert text == "z"


def test_parse_codex_jsonl_skips_bad_item_shape():
    line = json.dumps({"type": "item.completed", "item": "nope"})
    text, _ = _parse_codex_jsonl(line)
    assert text == ""


def test_parse_codex_jsonl_skips_wrong_item_type():
    line = json.dumps(
        {
            "type": "item.completed",
            "item": {"item_type": "other", "text": "x"},
        }
    )
    text, _ = _parse_codex_jsonl(line)
    assert text == ""


def test_openai_dry_run_execute():
    log = logging.getLogger("t_openai")
    cfg = {"dry_run": True, "providers": {"openai": {}}}
    ad = OpenAIAdapter(cfg, log)
    out = ad.execute_prompt("hi", Path("/tmp"))
    assert out["success"] is True
    assert out["usage_source"] == "dry_run"


def test_openai_check_available_true_when_dry_run():
    log = logging.getLogger("t_openai")
    ad = OpenAIAdapter({"dry_run": True, "providers": {"openai": {}}}, log)
    assert ad.check_available() is True


def test_openai_get_default_model_phase():
    log = logging.getLogger("t_openai")
    cfg = {"providers": {"openai": {"phase_models": {"plan": "gpt-x"}, "default_model": "gpt-4o"}}}
    ad = OpenAIAdapter(cfg, log)
    assert ad.get_default_model("plan") == "gpt-x"
    assert ad.get_default_model() == "gpt-4o"


@patch("subprocess.run")
def test_openai_validate_health_healthy(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="codex 1.2\n")
    log = logging.getLogger("t_openai")
    ad = OpenAIAdapter({"providers": {"openai": {}}}, log)
    hr = ad.validate_health()
    assert hr.status == HealthStatus.HEALTHY


@patch("subprocess.run")
def test_openai_validate_health_not_installed(mock_run):
    mock_run.side_effect = FileNotFoundError()
    log = logging.getLogger("t_openai")
    ad = OpenAIAdapter({"providers": {"openai": {}}}, log)
    hr = ad.validate_health()
    assert hr.status == HealthStatus.NOT_INSTALLED


@patch("subprocess.run")
def test_openai_validate_health_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="codex", timeout=1)
    log = logging.getLogger("t_openai")
    ad = OpenAIAdapter({"providers": {"openai": {}}}, log)
    hr = ad.validate_health()
    assert hr.status == HealthStatus.UNREACHABLE
