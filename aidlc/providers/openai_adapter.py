"""OpenAI / Codex CLI provider adapter.

Shells out to the `codex` CLI or `openai` CLI binary.
Supports GPT-4o and other OpenAI models as first-class citizens.
"""

import json
import logging
import subprocess
from pathlib import Path

from .base import HealthResult, HealthStatus, ProviderAdapter

_DEFAULT_OPENAI_MODEL = "gpt-4o"


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
        if obj.get("type") == "turn.completed" and isinstance(obj.get("usage"), dict):
            last_usage = obj["usage"]
        if obj.get("type") != "item.completed":
            continue
        item = obj.get("item")
        if not isinstance(item, dict):
            continue
        itype = item.get("item_type") or item.get("type")
        if itype not in ("assistant_message", "agent_message"):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            output_text = text

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


def _extract_codex_failure_diagnostics(stderr: str, stdout: str) -> str:
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
        if (
            isinstance(msg, str)
            and msg.strip()
            and typ
            not in (
                "turn.completed",
                "item.completed",
                "thread.started",
            )
        ):
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
    if not combined.strip():
        tail = (stdout or "").strip()
        if tail:
            combined = tail[-12000:] if len(tail) > 12000 else tail
    return combined.strip()


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
            "unavailable",
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
        self.dry_run = config.get("dry_run", False)
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
    ) -> dict:
        if self.dry_run:
            self.logger.info(f"[DRY RUN] OpenAI prompt ({len(prompt)} chars) in {working_dir}")
            return self._dry_run_result(model_override or self.default_model, account_id)

        model = model_override or self.default_model

        cmd = self._build_command(model, allow_edits, prompt, continuation_session_id)

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
                out_text = parsed_out if parsed_out.strip() else (stdout or "")
                blocked, diag = _codex_exit_zero_is_quota_blocker(
                    stdout or "", stderr or "", parsed_out
                )
                if blocked:
                    failure_type = _classify_openai_cli_failure(diag)
                    out_tail = (stdout or "")[-16000:] if stdout else None
                    return self._failure_result(
                        model,
                        account_id,
                        duration,
                        error=diag,
                        failure_type=failure_type,
                        output=out_tail,
                    )
                usage_source = "codex_jsonl" if usage else "openai_cli"
                tid = extract_codex_thread_id(stdout or "") or continuation_session_id
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
                return payload
            else:
                diagnostic = _extract_codex_failure_diagnostics(stderr or "", stdout or "")
                if not diagnostic:
                    diagnostic = "OpenAI CLI returned non-zero exit code"
                failure_type = _classify_openai_cli_failure(diagnostic)
                out_tail = (stdout or "")[-16000:] if stdout else None
                return self._failure_result(
                    model,
                    account_id,
                    duration,
                    error=diagnostic,
                    failure_type=failure_type,
                    output=out_tail,
                )

        except FileNotFoundError:
            return self._failure_result(
                model,
                account_id,
                0.0,
                error=f"OpenAI CLI not found at '{self.cli_command}'. Install with: npm install -g @openai/codex",
                failure_type="provider_error",
            )

    def _build_command(
        self,
        model: str,
        allow_edits: bool,
        prompt: str,
        continuation_session_id: str | None = None,
    ) -> list[str]:
        """Build ``codex exec`` or ``codex exec resume`` (--json JSONL)."""
        if continuation_session_id:
            cmd = [
                self.cli_command,
                "exec",
                "resume",
                "--json",
                "--model",
                model,
            ]
            if allow_edits:
                cmd.append("--full-auto")
            cmd.extend([continuation_session_id, prompt])
            return cmd
        cmd = [self.cli_command, "exec", "--json", "--model", model]
        if allow_edits:
            cmd.append("--full-auto")
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
