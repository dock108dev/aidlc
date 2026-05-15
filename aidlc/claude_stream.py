"""Claude stream-json parsing and reader helpers."""

from __future__ import annotations

import json
import time


def extract_session_id_from_stream_json(stdout: str) -> str | None:
    """Return ``session_id`` from the first ``system/init`` stream-json line, if any."""
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            sid = event.get("session_id")
            if isinstance(sid, str) and sid.strip():
                return sid.strip()
    return None


def compact_text(value: str | None, max_len: int = 240) -> str:
    """Compact multiline text for concise log output."""
    if not value:
        return ""
    compact = " ".join(value.split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


def summarize_stream_event(line: str) -> str:
    """Return a one-line description of a stream-json event for heartbeat logs."""
    raw = (line or "").strip()
    if not raw or not raw.startswith("{"):
        return ""
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(event, dict):
        return ""
    kind = event.get("type")
    if kind == "system":
        sub = event.get("subtype", "")
        return f"system {sub}".strip()
    if kind == "result":
        sub = event.get("subtype", "")
        if event.get("is_error"):
            return f"result error ({sub})".strip()
        return f"result {sub}".strip()
    if kind in ("assistant", "user"):
        return _summarize_message_event(kind, event)
    return str(kind or "event")


def pick_last_nonempty_summary(lines: list[str]) -> str:
    """Walk backward through collected stream lines for a meaningful summary."""
    for line in reversed(lines):
        summary = summarize_stream_event(line)
        if summary:
            return summary
    return "no events yet"


def stream_reader(stream, sink: list[str], last_activity_at: list[float]) -> None:
    """Read stream lines into ``sink`` and stamp activity for stall detection."""
    try:
        while True:
            try:
                raw = stream.readline()
            except (ValueError, OSError):
                break
            if not isinstance(raw, str) or raw == "":
                break
            sink.append(raw)
            last_activity_at[0] = time.time()
    finally:
        try:
            stream.close()
        except (OSError, ValueError, AttributeError):
            pass


def _summarize_message_event(kind: str, event: dict) -> str:
    msg = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = msg.get("content") if isinstance(msg.get("content"), list) else []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_use":
            name = block.get("name") or "?"
            inp = block.get("input") or {}
            hint = ""
            for key in ("file_path", "path", "command", "url", "query", "pattern"):
                val = inp.get(key) if isinstance(inp, dict) else None
                if isinstance(val, str) and val:
                    hint = val if len(val) <= 60 else val[:57] + "..."
                    break
            return f"tool_use {name}({hint})" if hint else f"tool_use {name}"
        if btype == "tool_result":
            return f"tool_result ({kind})"
        if btype == "text":
            text = block.get("text") or ""
            return f"assistant_text {len(text)} chars"
        if btype == "thinking":
            text = block.get("thinking") or ""
            return f"thinking {len(text)} chars"
    return kind
