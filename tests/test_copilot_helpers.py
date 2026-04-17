"""Pure-helper coverage for GitHub Copilot adapter parsing."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aidlc.providers.base import HealthStatus
from aidlc.providers.copilot_adapter import (
    CopilotAdapter,
    _parse_copilot_usage_blob,
    _parse_int_loose,
    _strip_copilot_trailing_stats,
)


@pytest.mark.parametrize(
    "s,expected",
    [
        ("1,234", 1234),
        ("1_000", 1000),
        ("", 0),
        ("42", 42),
    ],
)
def test_parse_int_loose(s, expected):
    assert _parse_int_loose(s) == expected


def test_parse_usage_empty():
    assert _parse_copilot_usage_blob("") == {}
    assert _parse_copilot_usage_blob("   ") == {}


def test_parse_usage_primary_pattern():
    blob = "Input tokens: 10\nOutput tokens: 20\n"
    u = _parse_copilot_usage_blob(blob)
    assert u["input_tokens"] == 10
    assert u["output_tokens"] == 20


def test_parse_usage_slash_pattern():
    blob = "1,000 in / 2,000 out"
    u = _parse_copilot_usage_blob(blob)
    assert u["input_tokens"] == 1000
    assert u["output_tokens"] == 2000


def test_parse_usage_two_total_lines():
    blob = "input tokens: 5\noutput tokens: 7"
    u = _parse_copilot_usage_blob(blob)
    assert u["input_tokens"] == 5
    assert u["output_tokens"] == 7


def test_parse_usage_single_total_allocates_input():
    blob = "total tokens: 99"
    u = _parse_copilot_usage_blob(blob)
    assert u["input_tokens"] == 99
    assert u["output_tokens"] == 0


def test_strip_trailing_stats_removes_token_lines():
    stdout = "answer line\nTokens: 1 in / 2 out\n"
    assert "Tokens" not in _strip_copilot_trailing_stats(stdout)


def test_strip_trailing_stats_keeps_body():
    stdout = "only body"
    assert _strip_copilot_trailing_stats(stdout) == "only body"


def test_copilot_dry_run():
    log = logging.getLogger("tcop")
    cfg = {"dry_run": True, "providers": {"copilot": {}}}
    ad = CopilotAdapter(cfg, log)
    r = ad.execute_prompt("x", Path("/tmp"))
    assert r["usage_source"] == "dry_run"


def test_build_command_omits_model_for_default_and_auto():
    log = logging.getLogger("tcop")
    cfg = {"providers": {"copilot": {"silent": False}}}
    ad = CopilotAdapter(cfg, log)
    cmd_default = ad._build_command("", False, "ping")
    assert "--model" not in cmd_default
    cmd_auto = ad._build_command("Auto", False, "ping")
    assert "--model" not in cmd_auto


def test_build_command_includes_model_and_silent():
    log = logging.getLogger("tcop")
    cfg = {"providers": {"copilot": {"silent": True}}}
    ad = CopilotAdapter(cfg, log)
    cmd = ad._build_command("gpt-4", False, "ping")
    assert "-s" in cmd
    assert "--model" in cmd


@patch("aidlc.providers.copilot_adapter.subprocess.run")
def test_validate_health_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="1.2.3\n")
    log = logging.getLogger("tcop")
    ad = CopilotAdapter({"providers": {"copilot": {}}}, log)
    hr = ad.validate_health()
    assert hr.status == HealthStatus.HEALTHY
