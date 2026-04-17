"""Parse provider results for token exhaustion, rate limits, and retry-after times."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta


def is_token_exhaustion_result(result: dict) -> bool:
    """True when provider failure indicates exhausted token/quota budget."""
    if not isinstance(result, dict):
        return False
    failure_type = str(result.get("failure_type") or "").lower()
    if failure_type in {"token_exhausted", "quota_exceeded", "token_exhausted_all_models"}:
        return True

    message = "\n".join(
        [
            str(result.get("error") or ""),
            str(result.get("output") or ""),
        ]
    ).lower()
    if not message.strip():
        return False

    patterns = (
        r"out of tokens",
        r"token budget",
        r"token quota",
        r"quota exceeded",
        r"insufficient quota",
        r"billing.*required",
        r"credits? exhausted",
        r"you exceeded your current quota",
        r"monthly token.*limit",
    )
    return any(re.search(pat, message) for pat in patterns)


def is_rate_limited_result(result: dict) -> bool:
    """True when provider failure indicates temporary rate limiting."""
    if not isinstance(result, dict):
        return False
    failure_type = str(result.get("failure_type") or "").lower()
    if failure_type in {"rate_limited", "rate_limit", "429"}:
        return True

    message = "\n".join(
        [
            str(result.get("error") or ""),
            str(result.get("output") or ""),
        ]
    ).lower()
    if not message.strip():
        return False

    patterns = (
        r"rate.?limit",
        r"rate_limit",
        r"ratelimit",
        r"usage.?limit",
        # Codex CLI plain text: "You've hit your usage limit … try again at 5:41 PM"
        r"hit your usage",
        r"purchase more credits",
        r"too many requests",
        r"too_many_requests",
        r"\b429\b",
        r"try again later",
        r"try again at",
        r"try again in",
        r"\bagain at\s+\d",  # line-wrapped "… try" + "again at 5:41 PM"
        r"request limit",
        r"throttl",
        r"resource.?exhausted",
        r"capacity",
        r"overloaded",
        r"slow down",
        r"requests.*per.*minute",
        r"tokens.*per.*minute",
        r"\btpm\b",
        r"\brpm\b",
    )
    return any(re.search(pat, message) for pat in patterns)


def parse_wait_seconds(value: str) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds)", text)
    if match:
        return float(match.group(1))
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(m|min|mins|minute|minutes)", text)
    if match:
        return float(match.group(1)) * 60.0
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours)", text)
    if match:
        return float(match.group(1)) * 3600.0
    return None


def parse_timestamp_to_epoch(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return float(value / 1000 if value > 1_000_000_000_000 else value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"\d{10,13}", text):
            numeric = int(text)
            return float(numeric / 1000 if len(text) == 13 else numeric)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def parse_restore_clock_time(message: str, now_epoch: float) -> float | None:
    """Parse local-time phrases like 'try again at 8:55 PM' into epoch seconds."""
    if not message:
        return None

    now_dt = datetime.fromtimestamp(now_epoch)
    match_12h = re.search(
        r"(?:try\s+again\s+at|again\s+at)\s+(\d{1,2}):(\d{2})\s*([AaPp][Mm])\b",
        message,
    )
    if match_12h:
        hour = int(match_12h.group(1))
        minute = int(match_12h.group(2))
        ampm = match_12h.group(3).lower()
        if hour == 12:
            hour = 0
        if ampm == "pm":
            hour += 12
        candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.timestamp() <= now_epoch:
            candidate = candidate + timedelta(days=1)
        return candidate.timestamp()

    match_24h = re.search(
        r"(?:try\s+again\s+at|again\s+at)\s+([01]?\d|2[0-3]):([0-5]\d)\b",
        message,
    )
    if match_24h:
        hour = int(match_24h.group(1))
        minute = int(match_24h.group(2))
        candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.timestamp() <= now_epoch:
            candidate = candidate + timedelta(days=1)
        return candidate.timestamp()

    return None


def extract_restore_time_epoch(result: dict) -> float | None:
    """Best-effort extraction of a rate-limit restore time from provider output."""
    if not isinstance(result, dict):
        return None

    now = time.time()
    details = result.get("details") if isinstance(result.get("details"), dict) else {}

    def _extract_from_mapping(mapping: dict) -> float | None:
        for key in ("retry_after_seconds", "retry_after_sec", "retry_after_s"):
            value = mapping.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return now + float(value)

        value = mapping.get("retry_after")
        if isinstance(value, (int, float)) and value > 0:
            return now + float(value)
        if isinstance(value, str):
            parsed_wait = parse_wait_seconds(value)
            if parsed_wait is not None:
                return now + parsed_wait

        for key in (
            "restore_at",
            "reset_at",
            "next_available_at",
            "available_at",
            "rate_limit_reset",
            "rate_limit_reset_at",
        ):
            value = mapping.get(key)
            parsed_epoch = parse_timestamp_to_epoch(value)
            if parsed_epoch is not None:
                return parsed_epoch
        return None

    for mapping in (result, details):
        parsed = _extract_from_mapping(mapping)
        if parsed is not None:
            return parsed

    message = "\n".join(
        [
            str(result.get("error") or ""),
            str(result.get("output") or ""),
        ]
    )
    lowered = message.lower()

    wait_patterns = (
        r"retry\s+after\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s)\b",
        r"retry\s+after\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|m)\b",
        r"retry\s+after\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|h)\b",
        r"try\s+again\s+in\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s)\b",
        r"try\s+again\s+in\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|m)\b",
        r"try\s+again\s+in\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|h)\b",
    )
    for pattern in wait_patterns:
        match = re.search(pattern, lowered)
        if match:
            amount = float(match.group(1))
            unit = match.group(2)
            multiplier = 1.0
            if unit.startswith("m"):
                multiplier = 60.0
            elif unit.startswith("h"):
                multiplier = 3600.0
            return now + (amount * multiplier)

    epoch_match = re.search(r"(?:reset|restore|available).*?(\d{10,13})", lowered)
    if epoch_match:
        raw = epoch_match.group(1)
        value = int(raw)
        return float(value / 1000 if len(raw) == 13 else value)

    clock_restore = parse_restore_clock_time(message, now)
    if clock_restore is not None:
        return clock_restore

    return None
