"""Additional tests for aidlc.routing.result_signals."""

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from aidlc.routing import result_signals as rs


@pytest.mark.parametrize(
    "result,expected",
    [
        ({}, False),
        ({"failure_type": "token_exhausted"}, True),
        ({"failure_type": "quota_exceeded"}, True),
        ({"error": "You are out of tokens for this month"}, True),
        ({"output": "billing required to continue"}, True),
        ({"error": "rate limited"}, False),
    ],
)
def test_is_token_exhaustion_result(result, expected):
    assert rs.is_token_exhaustion_result(result) is expected


@pytest.mark.parametrize(
    "result,expected",
    [
        ({}, False),
        ({"failure_type": "429"}, True),
        ({"error": "Too many requests 429"}, True),
        ({"output": "try again later please"}, True),
        ({"error": "throttled"}, True),
    ],
)
def test_is_rate_limited_result(result, expected):
    assert rs.is_rate_limited_result(result) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", None),
        ("30", 30.0),
        ("2.5 s", 2.5),
        ("5 minutes", 300.0),
        ("1 hour", 3600.0),
        ("nope", None),
    ],
)
def test_parse_wait_seconds(text, expected):
    assert rs.parse_wait_seconds(text) == expected


def test_parse_timestamp_to_epoch_none_and_invalid():
    assert rs.parse_timestamp_to_epoch(None) is None
    assert rs.parse_timestamp_to_epoch(-1) is None


def test_parse_timestamp_to_epoch_unix_and_iso():
    assert rs.parse_timestamp_to_epoch(1700000000) == 1700000000.0
    assert rs.parse_timestamp_to_epoch("1700000000") == 1700000000.0
    out = rs.parse_timestamp_to_epoch("2020-01-01T00:00:00+00:00")
    assert out is not None
    assert datetime.fromtimestamp(out, tz=timezone.utc).year == 2020


def test_parse_restore_clock_time_pm():
    now = datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    msg = "Please try again at 3:30 PM today"
    t = rs.parse_restore_clock_time(msg, now)
    assert t is not None


def test_parse_restore_clock_time_codex_wrapped_again_at():
    """Codex often line-wraps so the clock is only on `again at 5:41 PM.`"""
    now = datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    msg = "purchase more credits or try\nagain at 5:41 PM."
    t = rs.parse_restore_clock_time(msg, now)
    assert t is not None


def test_extract_restore_retry_after_seconds():
    now = time.time()
    r = {"retry_after_seconds": 30}
    t = rs.extract_restore_time_epoch(r)
    assert t is not None
    assert t >= now + 29


def test_extract_restore_from_details():
    r = {"details": {"retry_after": "10s"}}
    t = rs.extract_restore_time_epoch(r)
    assert t is not None


def test_extract_restore_message_retry_after_minutes():
    with patch("aidlc.routing.result_signals.time.time", return_value=1000.0):
        r = {"error": "retry after 2 minutes", "output": ""}
        t = rs.extract_restore_time_epoch(r)
        assert t is not None


def test_extract_restore_epoch_in_message():
    r = {"output": "rate limit reset 1700000000"}
    t = rs.extract_restore_time_epoch(r)
    assert t is not None


def test_extract_restore_non_dict_result():
    assert rs.extract_restore_time_epoch("nope") is None  # type: ignore[arg-type]


def test_is_token_exhaustion_non_dict():
    assert rs.is_token_exhaustion_result("x") is False  # type: ignore[arg-type]


def test_is_rate_limited_non_dict():
    assert rs.is_rate_limited_result(42) is False  # type: ignore[arg-type]


def test_parse_timestamp_millis_13_digits():
    t = rs.parse_timestamp_to_epoch("1700000000123")
    assert t is not None


def test_parse_restore_clock_time_24h_next_day():
    noon_unix = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    msg = "try again at 09:00"
    out = rs.parse_restore_clock_time(msg, noon_unix - 60)
    assert out is not None


def test_extract_restore_from_restore_at_iso():
    r = {"restore_at": "2025-01-15T12:00:00+00:00"}
    t = rs.extract_restore_time_epoch(r)
    assert t is not None


def test_parse_natural_try_again_datetime():
    msg = "visit settings or try again at Apr 22nd, 2026 9:04 PM."
    t = rs.parse_natural_try_again_datetime(msg)
    assert t is not None
    dt = datetime.fromtimestamp(t)
    assert dt.year == 2026
    assert dt.month == 4
    assert dt.day == 22


def test_extract_restore_codex_long_date_in_message():
    with patch("aidlc.routing.result_signals.time.time", return_value=1000.0):
        r = {
            "error": "",
            "output": "try again at Apr 22nd, 2026 9:04 PM.",
        }
        t = rs.extract_restore_time_epoch(r)
        assert t is not None


def test_reclassify_quota_chatter_success():
    r = {"success": True, "error": "■ You've hit your usage limit. Upgrade to Pro."}
    out = rs.reclassify_quota_chatter_success(r)
    assert out["success"] is False
    assert out["failure_type"] == "rate_limited"


def test_reclassify_quota_chatter_leaves_normal_success():
    r = {"success": True, "output": "# Hello\n\nThis is a ROADMAP."}
    assert rs.reclassify_quota_chatter_success(r) == r


def test_reclassify_ignores_rate_limit_word_in_model_output():
    """Model output (success=True) may legitimately contain rate-limit phrasing.

    Regression: Claude-generated Grafana dashboard with a panel named
    'rate-limited count stat' triggered a bogus provider cooldown because the
    reclassifier scanned the output body for rate-limit keywords.
    """
    r = {
        "success": True,
        "error": None,
        "output": (
            '{"success": true, "files_changed": ["social-collection-health.json"], '
            '"notes": "5-panel dashboard with 2-hour rolling success rate and '
            'rate-limited count stat per handle"}'
        ),
        "provider_id": "claude",
    }
    assert rs.reclassify_quota_chatter_success(r) == r


def test_reclassify_ignores_try_again_later_prose_in_output():
    """Generated code/docs may contain 'try again later' advice without being rate-limited."""
    r = {
        "success": True,
        "error": None,
        "output": 'def on_429(): logger.warn("rate limit exceeded; try again later at 2s backoff")',
    }
    assert rs.reclassify_quota_chatter_success(r) == r


def test_plan_usage_limits_dashboard_copy_not_rate_limited():
    """Claude/Code UI 'Plan usage limits' must not trigger provider cooldown."""
    msg = """Plan usage limits
Max (20x)
Current session
Resets in 22 min
Learn more about usage limits
Weekly limits
30% used
"""
    assert rs.is_rate_limited_result({"success": False, "error": msg, "failure_type": "issue"}) is False


def test_doc_gap_rate_limiting_prose_not_rate_limited():
    """Doc-gap / design copy often says 'rate limiting' without an API rate-limit error."""
    msg = "Add graceful rate limiting middleware for the public API gateway."
    assert rs.is_rate_limited_result({"success": False, "error": msg, "failure_type": "issue"}) is False


def test_overloaded_servers_prose_not_rate_limited():
    assert (
        rs.is_rate_limited_result(
            {"success": False, "error": "Avoid overloaded servers during peak.", "failure_type": "issue"}
        )
        is False
    )


def test_format_rate_limit_diagnostics_includes_pattern():
    r = {"failure_type": "issue", "error": "upstream said rate limit exceeded", "output": ""}
    text = rs.format_rate_limit_diagnostics(
        r, raw_restore_epoch=None, cooldown_until_epoch=1_700_000_000.0, buffer_seconds=3600.0
    )
    assert "rate_limit_api_phrase" in text or "classification=" in text
    assert "cooldown_until_epoch=" in text


def test_reclassify_leaves_success_with_usage_limits_heading():
    r = {
        "success": True,
        "output": "# Design\n\n## Plan usage limits\n\nWe track usage per tenant.\n",
    }
    assert rs.reclassify_quota_chatter_success(r) == r
