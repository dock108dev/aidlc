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
    if failure_type in {
        "token_exhausted",
        "quota_exceeded",
        "token_exhausted_all_models",
    }:
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


# Model name patterns the engine recognizes as "this specific model is out".
# Used by is_model_exhausted_result to distinguish per-model from per-provider
# exhaustion when the provider's CLI names the model in its quota error.
_MODEL_NAME_PATTERNS: tuple[str, ...] = (
    r"\bclaude[-_]?(?:sonnet|opus|haiku)[-_\d.]*\b",
    r"\bsonnet\b",
    r"\bopus\b",
    r"\bhaiku\b",
    r"\bgpt[-_]?[345][-_.\dA-Za-z]*\b",
)


def is_model_exhausted_result(result: dict) -> bool:
    """True when provider failure indicates THIS specific model is exhausted.

    Distinguishes per-model exhaustion from per-provider/account exhaustion:
    if the error text names a model (e.g., ``claude-sonnet-4-5 has reached
    its quota``), it's per-model and the engine should walk the provider's
    ``model_fallback_chain`` before excluding the entire provider.

    A True return means: also-true for is_token_exhaustion_result, plus the
    error message names a model. False means: either not exhausted at all, or
    exhaustion is provider-wide (no model named).
    """
    if not is_token_exhaustion_result(result):
        return False
    message = "\n".join(
        [
            str(result.get("error") or ""),
            str(result.get("output") or ""),
        ]
    ).lower()
    if not message.strip():
        return False
    return any(re.search(pat, message) for pat in _MODEL_NAME_PATTERNS)


def is_model_rate_limited_result(result: dict) -> bool:
    """True when a temporary rate limit appears scoped to the current model."""
    if not is_rate_limited_result(result):
        return False
    message = "\n".join(
        [
            str(result.get("error") or ""),
            str(result.get("output") or ""),
        ]
    ).lower()
    if not message.strip():
        return False
    return any(re.search(pat, message) for pat in _MODEL_NAME_PATTERNS)


# (label, regex) — labels appear in routing diagnostics when a heuristic fires.
# Avoid substrings common in doc-gap / design prose (e.g. "rate limiting", "overloaded servers").
RATE_LIMIT_HEURISTIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("rate_limit_snake", r"rate_limit"),
    ("ratelimit_token", r"\bratelimit\b"),
    (
        "rate_limit_api_phrase",
        r"rate[\s_-]+limits?\s*(?:exceeded|reached|error|hit|429|\(|:)",
    ),
    ("rate_limited_word", r"\brate[\s_-]+limited\b"),
    ("being_rate_limited", r"being\s+rate\s+limited"),
    # Strong usage / quota refusal (not generic "usage limits" UI headings)
    ("hit_your_usage", r"you'?ve\s+hit\s+your\s+usage\b"),
    ("hit_usage_limit", r"hit\s+your\s+usage\s+limit"),
    ("usage_limit_blocked", r"usage\s+limit\s+(?:reached|exceeded|hit|blocked)"),
    ("exceeded_usage", r"exceeded\s+(?:your\s+)?(?:api\s+)?(?:rate\s+)?usage"),
    ("upgrade_to_pro", r"upgrade to pro"),
    ("purchase_credits", r"purchase more credits"),
    ("too_many_requests", r"too many requests"),
    ("too_many_requests_snake", r"too_many_requests"),
    ("http_429", r"\b429\b"),
    ("try_again_later", r"try\s+again\s+later\b"),
    ("try_again_at_clock", r"try\s+again\s+at\s+\d"),
    ("try_again_in_delay", r"try\s+again\s+in\s+\d"),
    ("again_at_clock", r"\bagain\s+at\s+\d"),
    ("request_limit_hit", r"request\s+limit\s*(?:exceeded|reached|hit)"),
    ("throttle", r"throttl"),
    ("resource_exhausted", r"resource.?exhausted"),
    (
        "capacity_exceeded",
        r"(?:overloaded|at)\s+capacity|capacity\s+(?:exceeded|reached|limit)",
    ),
    ("server_overloaded", r"(?:server|service|endpoint|upstream)\s+overloaded"),
    ("overloaded_error", r"overloaded\s+(?:error|exception|503|502|504)"),
    ("rpm_tpm_limits", r"(?:requests|tokens).{0,12}per.{0,8}minute"),
    ("tpm_token", r"\btpm\b"),
    ("rpm_token", r"\brpm\b"),
)


def first_rate_limit_heuristic_match(
    message_lower: str,
) -> tuple[str | None, str | None]:
    """Return (pattern_label, matched_text) for the first heuristic hit, or (None, None)."""
    if not message_lower.strip():
        return None, None
    for label, pat in RATE_LIMIT_HEURISTIC_PATTERNS:
        m = re.search(pat, message_lower)
        if m:
            return label, m.group(0).strip()[:200]
    return None, None


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
    label, _span = first_rate_limit_heuristic_match(message)
    return label is not None


def format_rate_limit_diagnostics(
    result: dict,
    *,
    raw_restore_epoch: float | None = None,
    cooldown_until_epoch: float | None = None,
    buffer_seconds: float | None = None,
) -> str:
    """Multi-line explanation for logs when treating a provider result as rate-limited."""
    lines: list[str] = []
    if not isinstance(result, dict):
        return "invalid result (not a dict)"
    ft = str(result.get("failure_type") or "").strip()
    if ft:
        lines.append(f"failure_type={ft!r}")
    err = str(result.get("error") or "")
    out = str(result.get("output") or "")
    combined_lower = f"{err}\n{out}".lower()
    if ft.lower() in {"rate_limited", "rate_limit", "429"}:
        lines.append("classification=provider_failure_type_keyword")
    else:
        label, span = first_rate_limit_heuristic_match(combined_lower)
        if label:
            lines.append(f"classification=heuristic pattern={label!r} matched_span={span!r}")
        else:
            lines.append("classification=heuristic (unexpected: no pattern after positive check)")
    preview = f"{err}\n{out}".strip()
    if len(preview) > 900:
        preview = preview[:450] + "\n… [truncated] …\n" + preview[-400:]
    lines.append(f"message_chars={len(err) + len(out)} preview:\n{preview}")
    if raw_restore_epoch is not None:
        lines.append(
            "parsed_restore_epoch="
            f"{datetime.fromtimestamp(raw_restore_epoch).isoformat(timespec='seconds')}"
        )
    else:
        lines.append("parsed_restore_epoch=None (no retry-after / try-again time in payload)")
    if cooldown_until_epoch is not None:
        lines.append(
            "cooldown_until_epoch="
            f"{datetime.fromtimestamp(cooldown_until_epoch).isoformat(timespec='seconds')}"
        )
        if buffer_seconds is not None and buffer_seconds > 0:
            lines.append(
                f"buffer_added_seconds≈{buffer_seconds:.0f} (routing_rate_limit_buffer_base × backoff step)"
            )
    return "\n".join(lines)


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


def parse_natural_try_again_datetime(message: str) -> float | None:
    """Parse Codex long-window lines like 'try again at Apr 22nd, 2026 9:04 PM'."""
    if not message:
        return None
    compact = " ".join(message.split())
    m = re.search(
        r"(?:try\s+again\s+at|again\s+at)\s+"
        r"([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,\s*(\d{4})\s+"
        r"(\d{1,2}):(\d{2})\s*([AP]M)",
        compact,
        re.IGNORECASE,
    )
    if not m:
        return None
    month_s, day_s, year_s, hour_s, minute_s, ampm = m.groups()
    day = int(day_s)
    year = int(year_s)
    hour = int(hour_s)
    minute = int(minute_s)
    for fmt in ("%b %d %Y %I:%M %p", "%B %d %Y %I:%M %p"):
        try:
            dt = datetime.strptime(
                f"{month_s} {day} {year} {hour}:{minute} {ampm.upper()}",
                fmt,
            )
            return dt.timestamp()
        except ValueError:
            continue
    return None


def reclassify_quota_chatter_success(result: dict) -> dict:
    """If a provider returned success=True but the error field reports quota/rate-limit text, mark failure.

    Only the 'error' field is inspected — never 'output'. A success=True result's 'output'
    is model-generated content (code, docs, dashboards, JSON). It can legitimately contain
    phrases like 'rate-limited' (e.g. a Grafana panel named 'rate-limited count stat')
    without implying the provider itself was throttled. Keyword-scanning that body produced
    false positives that triggered multi-hour cooldowns on perfectly healthy accounts.

    Adapters (Claude CLI, OpenAI/Codex) are authoritative: they inspect raw stdout/stderr
    before returning and set success=False with the appropriate failure_type when they see
    real quota signals. This function is a defensive net for adapters that accidentally set
    success=True while populating 'error' with a quota message.
    """
    if not isinstance(result, dict) or not result.get("success"):
        return result
    err = str(result.get("error") or "").strip()
    if not err:
        return result
    probe = {"error": err, "output": ""}
    if is_rate_limited_result(probe):
        out = dict(result)
        out["success"] = False
        out["failure_type"] = "rate_limited"
        return out
    if is_token_exhaustion_result(probe):
        out = dict(result)
        out["success"] = False
        out["failure_type"] = "token_exhausted"
        return out
    return result


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

    natural = parse_natural_try_again_datetime(message)
    if natural is not None:
        return natural

    return None
