"""Claude CLI JSON output parsers.

Extracted from `claude_cli.py` so the orchestrator stays focused on
subprocess + retry control flow. Two responsibilities live here:

  * `extract_cli_metadata` — parse the CLI's stream-json (and single-blob)
    output into ``(text, usage, total_cost_usd, model_used, usage_source)``.
  * `extract_text_from_message` — pull concatenated ``text`` blocks out of
    a Claude message object's ``content`` list.

The CLI emits one JSON event per line in ``stream-json`` mode, but
sometimes a single blob; the parser tolerates both. Cost / usage / model
fields may sit at the top level OR nested inside ``message`` — both are
honored. Unknown shapes degrade gracefully to "no usage data" rather
than raising.
"""

from __future__ import annotations

import json


def extract_text_from_message(message: dict) -> str:
    """Return concatenated ``text`` blocks from a Claude message's ``content`` list."""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    chunks = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            chunks.append(block["text"])
    return "".join(chunks)


def _server_tool_use_count(parsed_usage: dict, key: str) -> int:
    """Read ``server_tool_use.<key>`` if nested, else fall back to top-level ``<key>``."""
    server_tool_use = parsed_usage.get("server_tool_use")
    if isinstance(server_tool_use, dict):
        return int(server_tool_use.get(key, 0) or 0)
    return int(parsed_usage.get(key, 0) or 0)


def _coerce_usage(parsed_usage: dict) -> dict:
    """Normalize the ``usage`` block to AIDLC's canonical token-count keys."""
    return {
        "input_tokens": int(parsed_usage.get("input_tokens", 0) or 0),
        "output_tokens": int(parsed_usage.get("output_tokens", 0) or 0),
        "cache_creation_input_tokens": int(parsed_usage.get("cache_creation_input_tokens", 0) or 0),
        "cache_read_input_tokens": int(parsed_usage.get("cache_read_input_tokens", 0) or 0),
        "web_search_requests": _server_tool_use_count(parsed_usage, "web_search_requests"),
        "web_fetch_requests": _server_tool_use_count(parsed_usage, "web_fetch_requests"),
    }


def _pick_parsed_payload(text: str) -> dict | None:
    """Find the parsed JSON payload that carries usage metadata.

    The CLI's stream-json output is one JSON object per line; the terminal
    line carries ``result``/``usage``/``total_cost_usd``. The CLI may also
    emit a single JSON blob. Try blob-first, then fall back to scanning
    lines and preferring whichever line carries a ``usage`` dict.
    """
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    usage_candidate = None
    fallback_line_dict = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            cand = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(cand, dict):
            continue
        fallback_line_dict = cand
        msg = cand.get("message")
        if isinstance(cand.get("usage"), dict) or (
            isinstance(msg, dict) and isinstance(msg.get("usage"), dict)
        ):
            usage_candidate = cand
    return usage_candidate or fallback_line_dict


def extract_cli_metadata(
    stdout: str,
    fallback_model: str,
) -> tuple[str, dict, float | None, str, str]:
    """Parse Claude CLI JSON output, returning text + usage metadata.

    Returns ``(text, usage, total_cost_usd, model_used, usage_source)``.
    ``usage_source`` is ``"none"`` when no parseable JSON was found and
    ``"claude_cli_json"`` otherwise.
    """
    text = stdout or ""
    usage: dict = {}
    total_cost_usd: float | None = None
    model_used = fallback_model
    usage_source = "none"

    if not text.strip():
        return text, usage, total_cost_usd, model_used, usage_source

    parsed = _pick_parsed_payload(text)
    if not isinstance(parsed, dict):
        return text, usage, total_cost_usd, model_used, usage_source

    usage_source = "claude_cli_json"

    # Result text: top-level `result` wins; otherwise pull from a `message` block.
    result_text = parsed.get("result")
    if not isinstance(result_text, str):
        message = parsed.get("message")
        if isinstance(message, dict):
            result_text = extract_text_from_message(message)
        else:
            result_text = text

    # Usage may sit at top level or nested under `message`.
    parsed_usage = parsed.get("usage")
    if not isinstance(parsed_usage, dict):
        message = parsed.get("message")
        if isinstance(message, dict):
            parsed_usage = message.get("usage")
    if isinstance(parsed_usage, dict):
        usage = _coerce_usage(parsed_usage)

    # Cost may sit at top level or nested under `message`.
    raw_cost = parsed.get("total_cost_usd")
    if raw_cost is None and isinstance(parsed.get("message"), dict):
        raw_cost = parsed["message"].get("total_cost_usd")
    try:
        total_cost_usd = float(raw_cost) if raw_cost is not None else None
    except (TypeError, ValueError):
        total_cost_usd = None

    # Model the CLI actually used (may differ from requested model).
    raw_model = parsed.get("model")
    if raw_model is None and isinstance(parsed.get("message"), dict):
        raw_model = parsed["message"].get("model")
    if isinstance(raw_model, str) and raw_model.strip():
        model_used = raw_model

    return result_text, usage, total_cost_usd, model_used, usage_source
