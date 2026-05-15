"""Codex CLI output parsing and failure classification helpers."""

from __future__ import annotations

import json


def extract_codex_thread_id(stdout: str) -> str | None:
    """Return Codex ``thread_id`` from JSONL ``thread.started`` events, if any."""
    for obj in _iter_codex_json_objects(stdout or ""):
        if obj.get("type") != "thread.started":
            continue
        tid = obj.get("thread_id")
        if isinstance(tid, str) and tid.strip():
            return tid.strip()
    return None


def parse_codex_jsonl(stdout: str) -> tuple[str, dict]:
    """Return assistant text and normalized usage from ``codex exec --json``."""
    output_text = ""
    last_usage: dict = {}
    for obj in _iter_codex_json_objects(stdout or ""):
        if obj.get("type") == "turn.completed" and isinstance(obj.get("usage"), dict):
            last_usage = obj["usage"]
        text = _extract_codex_event_text(obj)
        if isinstance(text, str) and text.strip():
            output_text = text

    if not output_text:
        output_text = _extract_codex_agent_message_text(stdout or "")
    if not output_text:
        output_text = _extract_codex_plain_final_text(stdout or "")

    usage: dict = {}
    if last_usage:
        inp = int(last_usage.get("input_tokens", 0) or 0)
        cached = int(last_usage.get("cached_input_tokens", 0) or 0)
        out = int(last_usage.get("output_tokens", 0) or 0)
        usage = {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": 0,
        }
    return output_text, usage


def extract_codex_failure_diagnostics(stderr: str, stdout: str) -> str:
    """Return explicit Codex failure details without falling back to raw stdout tails."""
    parts: list[str] = []
    err = (stderr or "").strip()
    if err:
        parts.append(err)

    for obj in _iter_codex_json_objects(stdout or ""):
        typ = str(obj.get("type") or "").lower()
        if "error" in typ:
            for key in ("message", "text", "detail"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    parts.append(val.strip())

        nested = obj.get("error")
        if isinstance(nested, dict):
            for key in ("message", "type", "code", "param"):
                val = nested.get(key)
                if val is not None and str(val).strip():
                    parts.append(str(val).strip())
        elif isinstance(nested, str) and nested.strip():
            parts.append(nested.strip())

        msg = obj.get("message")
        if isinstance(msg, str) and msg.strip() and ("error" in typ or "failed" in typ):
            parts.append(msg.strip())

        item = obj.get("item")
        if isinstance(item, dict):
            itype = str(item.get("item_type") or item.get("type") or "").lower()
            if "error" in itype or "failed" in itype:
                for key in ("text", "message", "error"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        parts.append(val.strip())

    # Codex TUI quota messages are often plain text, not JSONL.
    plain_hints = (
        "usage limit",
        "rate limit",
        "try again at",
        "try again in",
        "again at",
        "too many requests",
        "purchase more credit",
        "hit your usage",
    )
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if line and any(h in line.lower() for h in plain_hints):
            parts.append(line)

    return "\n".join(dict.fromkeys(parts)).strip()


def codex_exit_zero_is_quota_blocker(stdout: str, stderr: str, parsed_out: str) -> tuple[bool, str]:
    """True when Codex exits 0 with quota/TUI text instead of a completion."""
    from ..routing import result_signals as rs

    diagnostic = extract_codex_failure_diagnostics(stderr or "", stdout or "")
    merged = "\n".join([diagnostic, parsed_out.strip(), (stdout or "").strip()])
    probe = {"error": diagnostic or merged, "output": merged}
    if not merged.strip():
        return False, ""
    if rs.is_rate_limited_result(probe) or rs.is_token_exhaustion_result(probe):
        return True, (diagnostic or merged).strip()[:20000]
    return False, ""


def classify_openai_cli_failure(diagnostic: str) -> str:
    """Map combined stderr/stdout diagnostic to a normalized failure type."""
    from ..routing import result_signals as rs

    if not diagnostic.strip():
        return "issue"
    probe = {"error": diagnostic, "output": ""}
    if rs.is_rate_limited_result(probe):
        return "rate_limited"
    if rs.is_token_exhaustion_result(probe):
        return "token_exhausted"
    low = diagnostic.lower()
    if any(
        kw in low
        for kw in (
            "503",
            "502",
            "504",
            "timeout",
            "timed out",
            "connection reset",
            "econnreset",
            "service unavailable",
            "server unavailable",
            "upstream unavailable",
            "provider unavailable",
            "api unavailable",
            "bad gateway",
        )
    ):
        return "transient"
    return "issue"


def codex_nonzero_output_is_usable(
    *,
    stdout: str,
    stderr: str,
    output_text: str,
    diagnostic: str,
) -> bool:
    """True when Codex produced a completed answer despite a non-zero exit."""
    if not (output_text or "").strip():
        return False

    failure_type = classify_openai_cli_failure(diagnostic or stderr or "")
    if failure_type in {"rate_limited", "token_exhausted", "transient"}:
        return False

    return _codex_stdout_has_completed_turn(stdout)


def _extract_codex_plain_final_text(stdout: str) -> str:
    text = (stdout or "").strip()
    if not text:
        return ""

    turn_positions = [
        pos
        for marker in ('\n{"type":"turn.completed"', '\n{"type": "turn.completed"')
        if (pos := text.find(marker)) != -1
    ]
    if not turn_positions:
        return ""

    text = text[: min(turn_positions)].strip()
    lines = text.splitlines()
    while lines and lines[0].strip() == "Reading additional input from stdin...":
        lines.pop(0)
    text = "\n".join(lines).strip()
    if not text or text.lstrip().startswith('{"type"'):
        return ""
    if "```json" in text or text.lstrip().startswith(("#", "{", "[")):
        return text
    return ""


def _iter_codex_json_objects(stdout: str) -> list[dict]:
    """Decode JSON objects from Codex stdout, tolerating wrapper text."""
    objects: list[dict] = []
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    if objects:
        return objects

    decoder = json.JSONDecoder()
    start = 0
    while start < len(stdout):
        brace = stdout.find("{", start)
        if brace == -1:
            break
        try:
            obj, end = decoder.raw_decode(stdout[brace:])
        except json.JSONDecodeError:
            start = brace + 1
            continue
        if isinstance(obj, dict):
            objects.append(obj)
        start = brace + max(end, 1)
    return objects


def _extract_codex_event_text(obj: dict) -> str:
    typ = obj.get("type")
    item = obj.get("item")
    if typ == "item.completed" and isinstance(item, dict):
        return _extract_codex_item_text(item)
    if typ in ("agent_message", "assistant_message"):
        text = obj.get("text") or obj.get("message")
        return text if isinstance(text, str) else ""
    if typ == "message" and obj.get("role") == "assistant":
        return _extract_codex_content_text(obj.get("content"))
    return ""


def _extract_codex_item_text(item: dict) -> str:
    itype = item.get("item_type") or item.get("type")
    if itype in ("assistant_message", "agent_message"):
        text = item.get("text")
        return text if isinstance(text, str) else ""
    if itype != "message" or item.get("role") != "assistant":
        return ""
    return _extract_codex_content_text(item.get("content"))


def _extract_codex_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for entry in content:
        if isinstance(entry, str):
            if entry.strip():
                parts.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        entry_type = str(entry.get("type") or "")
        if entry_type in ("", "text", "output_text", "assistant_text"):
            parts.append(text)
    return "".join(parts)


def _extract_codex_agent_message_text(stdout: str) -> str:
    """Raw-scan assistant text when line-level JSONL decoding misses it."""
    if not stdout:
        return ""

    markers = (
        '"type":"agent_message"',
        '"type": "agent_message"',
        '"item_type":"agent_message"',
        '"item_type": "agent_message"',
        '"type":"assistant_message"',
        '"type": "assistant_message"',
        '"item_type":"assistant_message"',
        '"item_type": "assistant_message"',
        '"role":"assistant"',
        '"role": "assistant"',
    )
    decoder = json.JSONDecoder()
    text_values: list[str] = []

    for marker in markers:
        start = 0
        while True:
            marker_pos = stdout.find(marker, start)
            if marker_pos == -1:
                break
            text_key_pos = stdout.find('"text"', marker_pos)
            if text_key_pos == -1:
                start = marker_pos + len(marker)
                continue
            colon_pos = stdout.find(":", text_key_pos + len('"text"'))
            if colon_pos == -1:
                start = marker_pos + len(marker)
                continue
            value_start = colon_pos + 1
            while value_start < len(stdout) and stdout[value_start].isspace():
                value_start += 1
            try:
                value, _end = decoder.raw_decode(stdout[value_start:])
            except json.JSONDecodeError:
                start = marker_pos + len(marker)
                continue
            if isinstance(value, str) and value.strip():
                text_values.append(value)
            start = marker_pos + len(marker)

    if not text_values:
        text_values.extend(_extract_codex_assistant_content_values(stdout, decoder))

    return text_values[-1] if text_values else ""


def _extract_codex_assistant_content_values(stdout: str, decoder: json.JSONDecoder) -> list[str]:
    values: list[str] = []
    for marker in ('"role":"assistant"', '"role": "assistant"'):
        start = 0
        while True:
            marker_pos = stdout.find(marker, start)
            if marker_pos == -1:
                break
            content_pos = stdout.find('"content"', marker_pos)
            if content_pos == -1:
                start = marker_pos + len(marker)
                continue
            colon_pos = stdout.find(":", content_pos + len('"content"'))
            if colon_pos == -1:
                start = marker_pos + len(marker)
                continue
            value_start = colon_pos + 1
            while value_start < len(stdout) and stdout[value_start].isspace():
                value_start += 1
            try:
                value, _end = decoder.raw_decode(stdout[value_start:])
            except json.JSONDecodeError:
                start = marker_pos + len(marker)
                continue
            text = _extract_codex_content_text(value)
            if text.strip():
                values.append(text)
            start = marker_pos + len(marker)
    return values


def _codex_stdout_has_completed_turn(stdout: str) -> bool:
    if not stdout:
        return False
    if '"type":"turn.completed"' in stdout or '"type": "turn.completed"' in stdout:
        return True
    return any(obj.get("type") == "turn.completed" for obj in _iter_codex_json_objects(stdout))
