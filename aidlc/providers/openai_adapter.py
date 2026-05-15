"""OpenAI / Codex CLI provider adapter.

Shells out to the `codex` CLI or `openai` CLI binary.
Supports GPT-4o and other OpenAI models as first-class citizens.
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from .base import HealthResult, HealthStatus, ProviderAdapter

_DEFAULT_OPENAI_MODEL = "gpt-5.5"


def extract_codex_thread_id(stdout: str) -> str | None:
    """Return Codex ``thread_id`` from JSONL ``thread.started`` events, if any."""
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "thread.started":
            continue
        tid = obj.get("thread_id")
        if isinstance(tid, str) and tid.strip():
            return tid.strip()
    return None


def _parse_codex_jsonl(stdout: str) -> tuple[str, dict]:
    """Parse `codex exec --json` JSONL: assistant text + normalized usage from last turn.completed."""
    output_text = ""
    last_usage: dict = {}
    for obj in _iter_codex_json_objects(stdout or ""):
        if not isinstance(obj, dict):
            continue
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


def _extract_codex_plain_final_text(stdout: str) -> str:
    """Extract raw assistant text when Codex mixes plain output with JSON events."""
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
    while lines and lines[0].strip() in {
        "Reading additional input from stdin...",
    }:
        lines.pop(0)
    text = "\n".join(lines).strip()
    if not text:
        return ""
    if text.lstrip().startswith('{"type"'):
        return ""

    # Plain final messages should look like model output, not just a stream
    # of Codex events. The first two cases cover discovery/planning artifacts;
    # the last handles short final answers from implementation helpers.
    if "```json" in text or text.lstrip().startswith(("#", "{", "[")):
        return text
    return ""


def _iter_codex_json_objects(stdout: str) -> list[dict]:
    """Decode JSON objects from Codex stdout.

    Codex normally emits one JSON object per line, but CLI wrapper text can
    appear before JSONL. This mirrors Claude's stream tolerance: consume valid
    events wherever they appear instead of treating console framing as fatal.
    """
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
    """Return assistant text from a top-level Codex event object."""
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
    """Return assistant text from known Codex JSONL item shapes."""
    itype = item.get("item_type") or item.get("type")
    if itype in ("assistant_message", "agent_message"):
        text = item.get("text")
        return text if isinstance(text, str) else ""

    # Newer Codex JSONL emits assistant messages as:
    # {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "..."}]}
    if itype != "message" or item.get("role") != "assistant":
        return ""

    return _extract_codex_content_text(item.get("content"))


def _extract_codex_content_text(content: object) -> str:
    """Extract assistant text from Codex message ``content`` payloads."""
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
    """Extract the last agent/assistant message text from raw Codex JSON-ish stdout.

    Codex occasionally exits non-zero after emitting a completed turn. In the
    observed failure, the useful answer was present in a very large
    ``item.completed`` JSONL event, but normal line-level parsing did not yield
    it and the adapter fell back to logging a truncated raw stdout tail as an
    error. This scanner is deliberately narrow: only events that identify an
    ``agent_message`` or ``assistant_message`` are considered, and the value is
    decoded with Python's JSON decoder rather than ad hoc unescaping.
    """
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

    return text_values[-1] if text_values else ""


def _codex_stdout_has_completed_turn(stdout: str) -> bool:
    """Return True when Codex stdout contains a completed turn event."""
    if not stdout:
        return False
    if '"type":"turn.completed"' in stdout or '"type": "turn.completed"' in stdout:
        return True
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "turn.completed":
            return True
    return False


def _codex_nonzero_output_is_usable(
    *,
    stdout: str,
    stderr: str,
    output_text: str,
    diagnostic: str,
) -> bool:
    """True when Codex produced a completed answer despite a non-zero exit.

    We still honor concrete provider failures (quota/rate limits, transient
    outages). A generic non-zero exit with a completed turn and usable answer is
    treated as success because downstream phases can parse and use the answer.
    """
    if not (output_text or "").strip():
        return False

    failure_type = _classify_openai_cli_failure(diagnostic or stderr or "")
    if failure_type in {"rate_limited", "token_exhausted", "transient"}:
        return False

    if _codex_stdout_has_completed_turn(stdout):
        return True

    return False


def _extract_codex_failure_diagnostics(
    stderr: str, stdout: str, *, include_stdout_tail: bool = True
) -> str:
    """Best-effort: stderr first; then JSONL error payloads from codex --json stdout."""
    parts: list[str] = []
    err = (stderr or "").strip()
    if err:
        parts.append(err)

    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

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

    # Codex TUI: usage limits are often plain text (bullets / box chars), not JSONL.
    _plain_hints = (
        "usage limit",
        "rate limit",
        "try again at",
        "try again in",
        "again at",  # line-wrap: "… or try" / "again at 5:41 PM"
        "too many requests",
        "purchase more credit",
        "hit your usage",
    )
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()
        if any(h in low for h in _plain_hints):
            parts.append(line)

    combined = "\n".join(dict.fromkeys(parts))
    if include_stdout_tail and not combined.strip():
        tail = (stdout or "").strip()
        if tail:
            combined = tail[-12000:] if len(tail) > 12000 else tail
    return combined.strip()


def _read_text_if_present(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _codex_exit_zero_is_quota_blocker(
    stdout: str, stderr: str, parsed_out: str
) -> tuple[bool, str]:
    """Codex may exit 0 while printing usage limits / interactive TUI (no real completion)."""
    from ..routing import result_signals as rs

    diagnostic = _extract_codex_failure_diagnostics(stderr or "", stdout or "")
    merged = "\n".join(
        [
            diagnostic,
            parsed_out.strip(),
            (stdout or "").strip(),
        ]
    )
    probe = {"error": diagnostic or merged, "output": merged}
    if not merged.strip():
        return False, ""
    if rs.is_rate_limited_result(probe) or rs.is_token_exhaustion_result(probe):
        return True, (diagnostic or merged).strip()[:20000]
    return False, ""


def _classify_openai_cli_failure(diagnostic: str) -> str:
    """Map combined stderr/stdout diagnostic to a normalized failure_type."""
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


class OpenAIAdapter(ProviderAdapter):
    """Provider adapter for OpenAI / Codex CLI."""

    PROVIDER_ID = "openai"

    def __init__(self, config: dict, logger: logging.Logger):
        super().__init__(config, logger)
        provider_cfg = self._provider_config()
        self.cli_command = provider_cfg.get("cli_command", "codex")
        self.default_model = provider_cfg.get("default_model", _DEFAULT_OPENAI_MODEL)
        self.model_reasoning_effort = provider_cfg.get("model_reasoning_effort")
        self.dry_run = config.get("dry_run", False)
        self._dangerous_mode_warned = False
        # Non-streaming provider — wall-clock timeout is appropriate here
        # (unlike Claude CLI streaming, where we removed wall-clock kills).
        self.call_timeout = int(config.get("provider_call_timeout_seconds", 1800))
        self.warn_interval = int(config.get("claude_long_run_warn_seconds", 300))

    def _provider_config(self) -> dict:
        providers = self.config.get("providers", {})
        return providers.get("openai", {}) if isinstance(providers, dict) else {}

    def execute_prompt(
        self,
        prompt: str,
        working_dir: Path,
        allow_edits: bool = False,
        model_override: str | None = None,
        account_id: str | None = None,
        continuation_session_id: str | None = None,
        resume_session_id: str | None = None,
    ) -> dict:
        # Codex emits a thread id in JSONL. Any later call with that id should
        # use ``codex exec resume``; an explicit resume id wins when both are set.
        effective_session_id = resume_session_id or continuation_session_id
        if self.dry_run:
            self.logger.info(f"[DRY RUN] OpenAI prompt ({len(prompt)} chars) in {working_dir}")
            return self._dry_run_result(model_override or self.default_model, account_id)

        model = model_override or self.default_model
        if allow_edits and not self._dangerous_mode_warned:
            self.logger.warning(
                "Codex edit runs use --dangerously-bypass-approvals-and-sandbox: "
                "no approval prompts and no sandbox. EXTREMELY DANGEROUS. Use only "
                "in externally sandboxed environments."
            )
            self._dangerous_mode_warned = True

        final_message_path: Path | None = None
        with tempfile.NamedTemporaryFile(
            prefix="aidlc-codex-last-", suffix=".md", delete=False
        ) as f:
            final_message_path = Path(f.name)

        cmd = self._build_command(
            model,
            allow_edits,
            prompt,
            effective_session_id,
            reasoning_effort=self.model_reasoning_effort,
            output_last_message_path=final_message_path,
        )

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(working_dir),
            )
            stdout, stderr, duration, timed_out = self._communicate_with_heartbeat(
                proc,
                provider_label="OpenAI CLI",
                model=model,
                timeout_seconds=self.call_timeout,
                warn_interval=self.warn_interval,
                account_id=account_id,
            )
            last_message = _read_text_if_present(final_message_path).strip()
            if timed_out:
                return self._failure_result(
                    model,
                    account_id,
                    duration,
                    error="OpenAI CLI timed out",
                    failure_type="timeout",
                )
            if proc.returncode == 0:
                parsed_out, usage = _parse_codex_jsonl(stdout or "")
                out_text = last_message
                if not out_text:
                    out_text = parsed_out if parsed_out.strip() else (stdout or "")
                blocked, diag = _codex_exit_zero_is_quota_blocker(
                    stdout or "", stderr or "", parsed_out
                )
                if blocked:
                    failure_type = _classify_openai_cli_failure(diag)
                    out_tail = (stdout or "")[-16000:] if stdout else None
                    failure = self._failure_result(
                        model,
                        account_id,
                        duration,
                        error=diag,
                        failure_type=failure_type,
                        output=out_tail,
                    )
                    failure["raw_stdout"] = stdout or ""
                    failure["raw_stderr"] = stderr or ""
                    return failure
                if last_message:
                    usage_source = "codex_last_message"
                else:
                    usage_source = "codex_jsonl" if usage else "openai_cli"
                tid = extract_codex_thread_id(stdout or "") or effective_session_id
                payload = {
                    "success": True,
                    "output": out_text,
                    "error": None,
                    "failure_type": None,
                    "duration_seconds": duration,
                    "retries": 0,
                    "usage": usage,
                    "total_cost_usd": None,
                    "model_used": model,
                    "usage_source": usage_source,
                    "provider_id": self.PROVIDER_ID,
                    "account_id": account_id,
                }
                if tid:
                    payload["continuation_session_id"] = tid
                payload["raw_stdout"] = stdout or ""
                payload["raw_stderr"] = stderr or ""
                return payload
            else:
                parsed_out, usage = _parse_codex_jsonl(stdout or "")
                explicit_diagnostic = _extract_codex_failure_diagnostics(
                    stderr or "", stdout or "", include_stdout_tail=False
                )
                usable_output = last_message or parsed_out
                if (last_message and not explicit_diagnostic) or _codex_nonzero_output_is_usable(
                    stdout=stdout or "",
                    stderr=stderr or "",
                    output_text=usable_output,
                    diagnostic=explicit_diagnostic,
                ):
                    tid = extract_codex_thread_id(stdout or "") or effective_session_id
                    payload = {
                        "success": True,
                        "output": usable_output,
                        "error": None,
                        "failure_type": None,
                        "duration_seconds": duration,
                        "retries": 0,
                        "usage": usage,
                        "total_cost_usd": None,
                        "model_used": model,
                        "usage_source": "codex_last_message" if last_message else "codex_jsonl",
                        "provider_id": self.PROVIDER_ID,
                        "account_id": account_id,
                    }
                    if tid:
                        payload["continuation_session_id"] = tid
                    payload["raw_stdout"] = stdout or ""
                    payload["raw_stderr"] = stderr or ""
                    return payload

                diagnostic = explicit_diagnostic
                if not diagnostic:
                    diagnostic = "OpenAI CLI returned non-zero exit code"
                failure_type = _classify_openai_cli_failure(diagnostic)
                out_tail = (
                    last_message or parsed_out or ((stdout or "")[-16000:] if stdout else None)
                )
                failure = self._failure_result(
                    model,
                    account_id,
                    duration,
                    error=diagnostic,
                    failure_type=failure_type,
                    output=out_tail,
                )
                failure["raw_stdout"] = stdout or ""
                failure["raw_stderr"] = stderr or ""
                return failure

        except FileNotFoundError:
            return self._failure_result(
                model,
                account_id,
                0.0,
                error=f"OpenAI CLI not found at '{self.cli_command}'. Install with: npm install -g @openai/codex",
                failure_type="provider_error",
            )
        finally:
            if final_message_path is not None:
                try:
                    final_message_path.unlink()
                except OSError:
                    pass

    def _build_command(
        self,
        model: str,
        allow_edits: bool,
        prompt: str,
        continuation_session_id: str | None = None,
        reasoning_effort: str | None = None,
        output_last_message_path: Path | None = None,
    ) -> list[str]:
        """Build ``codex exec`` or ``codex exec resume`` (--json JSONL)."""
        reasoning_effort = str(reasoning_effort or "").strip()
        if continuation_session_id:
            cmd = [
                self.cli_command,
                "exec",
                "resume",
                "--json",
                "--model",
                model,
                "--skip-git-repo-check",
            ]
            if reasoning_effort:
                cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
            if allow_edits:
                cmd.append("--dangerously-bypass-approvals-and-sandbox")
            if output_last_message_path is not None:
                cmd.extend(["--output-last-message", str(output_last_message_path)])
            cmd.extend([continuation_session_id, prompt])
            return cmd
        cmd = [
            self.cli_command,
            "exec",
            "--json",
            "--model",
            model,
            "--skip-git-repo-check",
        ]
        if reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        if allow_edits:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        if output_last_message_path is not None:
            cmd.extend(["--output-last-message", str(output_last_message_path)])
        cmd.append(prompt)
        return cmd

    def check_available(self) -> bool:
        if self.dry_run:
            return True
        try:
            result = subprocess.run(
                [self.cli_command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def validate_health(self, account_id: str | None = None) -> HealthResult:
        """Check OpenAI CLI installation and API key."""
        try:
            result = subprocess.run(
                [self.cli_command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return HealthResult(
                    status=HealthStatus.NOT_AUTHENTICATED,
                    message="OpenAI CLI found but returned non-zero exit.",
                )
        except FileNotFoundError:
            return HealthResult(
                status=HealthStatus.NOT_INSTALLED,
                message=f"OpenAI CLI not found at '{self.cli_command}'. Install with: npm install -g @openai/codex",
            )
        except subprocess.TimeoutExpired:
            return HealthResult(
                status=HealthStatus.UNREACHABLE,
                message="OpenAI CLI check timed out.",
            )

        version = result.stdout.strip().splitlines()[0] if result.stdout else ""
        return HealthResult(
            status=HealthStatus.HEALTHY,
            message=f"Codex CLI available ({version}) — run 'codex login' if not authenticated",
            details={"version": version},
        )

    def get_default_model(self, phase: str | None = None) -> str:
        provider_cfg = self._provider_config()
        phase_models = provider_cfg.get("phase_models", {})
        if phase and phase in phase_models:
            return phase_models[phase]
        return provider_cfg.get("default_model", _DEFAULT_OPENAI_MODEL)

    @staticmethod
    def _dry_run_result(model: str, account_id: str | None) -> dict:
        return {
            "success": True,
            "output": "[DRY RUN] No execution",
            "error": None,
            "failure_type": None,
            "duration_seconds": 0.0,
            "retries": 0,
            "usage": {},
            "total_cost_usd": None,
            "model_used": model,
            "usage_source": "dry_run",
            "provider_id": "openai",
            "account_id": account_id,
        }

    @staticmethod
    def _failure_result(
        model: str,
        account_id: str | None,
        duration: float,
        error: str,
        failure_type: str,
        output: str | None = None,
    ) -> dict:
        return {
            "success": False,
            "output": output,
            "error": error,
            "failure_type": failure_type,
            "duration_seconds": duration,
            "retries": 0,
            "usage": {},
            "total_cost_usd": None,
            "model_used": model,
            "usage_source": "none",
            "provider_id": "openai",
            "account_id": account_id,
        }
