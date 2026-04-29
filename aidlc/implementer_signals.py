"""Provider availability and error-shaping helpers for implementation runs."""


def is_all_models_token_exhausted(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    failure_type = str(result.get("failure_type") or "").lower()
    if failure_type == "token_exhausted_all_models":
        return True
    message = "\n".join([str(result.get("error") or ""), str(result.get("output") or "")]).lower()
    return "all available providers/models" in message and "token" in message


SERVICE_OUTAGE_STOP_REASON = "Claude service outage during implementation; pausing run."


def is_service_outage(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    if str(result.get("failure_type") or "").lower() == "service_down":
        return True
    msg = str(result.get("error") or "").lower()
    return "extended period" in msg and "outage" in msg


def is_service_outage_stop_reason(reason: str | None) -> bool:
    return bool(reason) and "service outage during implementation" in reason.lower()


def is_no_models_available(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    failure_type = str(result.get("failure_type") or "").lower()
    if failure_type in {
        "rate_limited_all_models",
        "provider_unavailable",
        "no_models_available",
    }:
        return True
    message = "\n".join([str(result.get("error") or ""), str(result.get("output") or "")]).lower()
    return any(
        phrase in message
        for phrase in (
            "no models",
            "no available provider",
            "all providers are rate limited",
        )
    )


def compact_error_text(value: object, max_len: int = 360) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown provider error"
    compact = " ".join(text.split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


def sample_error_payload(
    value: object,
    tail_max_lines: int = 500,
    max_sample_lines: int = 50,
) -> str:
    """Extract relevant error lines from the last tail_max_lines of payload."""
    text = str(value or "")
    if not text.strip():
        return "unknown provider error"

    lines = text.splitlines()
    if not lines:
        return "unknown provider error"

    tail = lines[-max(1, int(tail_max_lines)) :]
    max_lines = max(1, int(max_sample_lines))

    anchor_terms = (
        "error",
        "exception",
        "traceback",
        "failed",
        "failure",
        "quota",
        "rate limit",
        "timeout",
    )

    selected_indexes: list[int] = []
    seen: set[int] = set()

    for idx in range(len(tail) - 1, -1, -1):
        lowered = tail[idx].lower()
        if not any(term in lowered for term in anchor_terms):
            continue

        start = max(0, idx - 2)
        end = min(len(tail), idx + 3)
        for line_idx in range(start, end):
            if line_idx in seen:
                continue
            seen.add(line_idx)
            selected_indexes.append(line_idx)
            if len(selected_indexes) >= max_lines:
                break
        if len(selected_indexes) >= max_lines:
            break

    if not selected_indexes:
        sampled = tail[-max_lines:]
    else:
        ordered = sorted(selected_indexes)
        if len(ordered) > max_lines:
            ordered = ordered[-max_lines:]
        sampled = [tail[i] for i in ordered]

    return "\n".join(sampled).strip() or "unknown provider error"


def should_stop_for_provider_availability(reason: str | None) -> bool:
    if not reason:
        return False
    text = reason.lower()
    return any(
        phrase in text
        for phrase in (
            "out of tokens",
            "no models/providers",
            "provider unavailable",
            "rate limit",
        )
    )
