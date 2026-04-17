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
