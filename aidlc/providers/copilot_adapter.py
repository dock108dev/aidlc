"""GitHub Copilot CLI provider adapter.

Shells out to the standalone `copilot` CLI by default.
Install: brew install copilot-cli
Auth: copilot login

Model selection is optional. If no model is configured, the adapter omits
`--model` and lets Copilot use its own current default. This avoids breaking
when vendor model IDs change.
"""

import logging
import re
import subprocess
from pathlib import Path

from .base import HealthResult, HealthStatus, ProviderAdapter

# Default model for Copilot provider.
# Empty string means "let the Copilot CLI choose its default model".
_DEFAULT_COPILOT_MODEL = ""


def _parse_int_loose(s: str) -> int:
    return int(s.replace(",", "").replace("_", "") or 0)


def _parse_copilot_usage_blob(blob: str) -> dict:
    """Best-effort token counts from combined Copilot CLI stdout/stderr.

    Formats differ by version (plain text, tables, box-drawing). Prefer **separate**
    regexes for input vs output so we do not require both on one line.
    """
    if not blob or not blob.strip():
        return {}
    inp = out = 0

    # 1) Independent labels (works when input/output are on different lines or table rows)
    inp_m = re.search(r"(?i)(?:input|prompt)\s*tokens?\s*[:=]\s*([\d,_]+)", blob)
    out_m = re.search(r"(?i)(?:output|completion)\s*tokens?\s*[:=]\s*([\d,_]+)", blob)
    if inp_m:
        inp = _parse_int_loose(inp_m.group(1))
    if out_m:
        out = _parse_int_loose(out_m.group(1))

    # 2) Single-line "input ... output" (original behavior)
    if inp == 0 and out == 0:
        m = re.search(
            r"(?i)(?:input|prompt)\s*tokens?\s*[:=]\s*([\d,_]+).*?(?:output|completion)\s*tokens?\s*[:=]\s*([\d,_]+)",
            blob,
            re.DOTALL,
        )
        if m:
            inp = _parse_int_loose(m.group(1))
            out = _parse_int_loose(m.group(2))

    # 3) Slash form: 1,000 in / 2,000 out
    if inp == 0 and out == 0:
        m2 = re.search(r"(?i)(\d[\d,_]*)\s*(?:input|in)\s*/\s*(\d[\d,_]*)\s*(?:output|out)", blob)
        if m2:
            inp = _parse_int_loose(m2.group(1))
            out = _parse_int_loose(m2.group(2))

    # 4) "N tokens in | M tokens out" or similar
    if inp == 0 and out == 0:
        m3 = re.search(
            r"(?i)([\d,_]+)\s*tokens?\s*(?:in|input)\D{0,40}([\d,_]+)\s*tokens?\s*(?:out|output)",
            blob,
        )
        if m3:
            inp = _parse_int_loose(m3.group(1))
            out = _parse_int_loose(m3.group(2))

    # 5) Fallback: lines with "input/output/total" token labels
    if inp == 0 and out == 0:
        nums = re.findall(r"(?i)(?:input|output|total)\s*tokens?\s*[:=]\s*([\d,_]+)", blob)
        if len(nums) >= 2:
            inp = _parse_int_loose(nums[0])
            out = _parse_int_loose(nums[1])
        elif len(nums) == 1:
            tot = _parse_int_loose(nums[0])
            if tot:
                inp = tot

    if inp == 0 and out == 0:
        return {}
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def _strip_copilot_trailing_stats(stdout: str) -> str:
    """Remove trailing decoration / stats lines; keep agent body when possible."""
    text = stdout or ""
    lines = text.splitlines()
    meta_re = re.compile(
        r"(?i)^(tokens?|usage|session|model\b|cost|usd|\$|────|━|═|╭|╰|│\s*(input|output|token)|\d+\s*→).*$"
    )
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        if meta_re.match(last) or re.match(r"^[\s│╭╮╰╯─━═]+$", last):
            lines.pop()
            continue
        if re.search(r"(?i)token", last) and re.search(r"\d", last):
            lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def _extract_response_text(stdout: str, stderr: str, silent: bool) -> str:
    """Prefer stdout, but fall back to stderr when the CLI emits the answer there."""
    primary = stdout or ""
    fallback = stderr or ""
    if not silent:
        primary = _strip_copilot_trailing_stats(primary)
        fallback = _strip_copilot_trailing_stats(fallback)
    primary = primary.strip()
    fallback = fallback.strip()
    return primary or fallback


class CopilotAdapter(ProviderAdapter):
    """Provider adapter for GitHub Copilot CLI."""

    PROVIDER_ID = "copilot"

    def __init__(self, config: dict, logger: logging.Logger):
        super().__init__(config, logger)
        provider_cfg = self._provider_config()
        self.cli_command = provider_cfg.get("cli_command", "copilot")
        self.default_model = str(provider_cfg.get("default_model", _DEFAULT_COPILOT_MODEL) or "")
        self.dry_run = config.get("dry_run", False)
        # Non-streaming provider — wall-clock timeout is appropriate here
        # (unlike Claude CLI streaming, where we removed wall-clock kills).
        self.call_timeout = int(config.get("provider_call_timeout_seconds", 1800))
        self.warn_interval = int(config.get("claude_long_run_warn_seconds", 300))
        # False = include CLI stats in output so we can parse token usage (default).
        self._silent = bool(provider_cfg.get("silent", False))

    def _provider_config(self) -> dict:
        providers = self.config.get("providers", {})
        return providers.get("copilot", {}) if isinstance(providers, dict) else {}

    def execute_prompt(
        self,
        prompt: str,
        working_dir: Path,
        allow_edits: bool = False,
        model_override: str | None = None,
        account_id: str | None = None,
    ) -> dict:
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Copilot prompt ({len(prompt)} chars) in {working_dir}")
            return self._dry_run_result(model_override or self.default_model, account_id)

        model = str(model_override or self.default_model or "")

        # Build command: copilot -p <prompt> --allow-all [--no-ask-user] [-s] [--model ...]
        cmd = self._build_command(model, allow_edits, prompt)

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
                provider_label="Copilot CLI",
                model=model or "default",
                timeout_seconds=self.call_timeout,
                warn_interval=self.warn_interval,
                account_id=account_id,
            )
            if timed_out:
                return self._failure_result(
                    model,
                    account_id,
                    duration,
                    error="Copilot CLI timed out",
                    failure_type="timeout",
                )
            if proc.returncode == 0:
                combined = f"{stdout or ''}\n{stderr or ''}"
                usage = _parse_copilot_usage_blob(combined)
                out = _extract_response_text(stdout, stderr, self._silent)
                usage_source = "copilot_cli"
                return {
                    "success": True,
                    "output": out,
                    "error": None,
                    "failure_type": None,
                    "duration_seconds": duration,
                    "retries": 0,
                    "usage": usage,
                    "total_cost_usd": None,
                    "model_used": model or "default",
                    "usage_source": usage_source,
                    "provider_id": self.PROVIDER_ID,
                    "account_id": account_id,
                    "raw_stdout": stdout or "",
                    "raw_stderr": stderr or "",
                }
            else:
                return self._failure_result(
                    model,
                    account_id,
                    duration,
                    error=stderr.strip() or "Copilot CLI returned non-zero exit code",
                    failure_type="issue",
                )

        except FileNotFoundError:
            return self._failure_result(
                model or "default",
                account_id,
                0.0,
                error=f"Copilot CLI not found at '{self.cli_command}'. Install with: brew install copilot-cli",
                failure_type="provider_error",
            )

    def _build_command(self, model: str, allow_edits: bool, prompt: str) -> list[str]:
        """Build the copilot CLI programmatic command.

        By default omits -s so the CLI can print usage lines we parse into ``usage``.
        Set ``providers.copilot.silent`` to true for pipe-friendly output only (no token stats).
        """
        cmd = [self.cli_command, "-p", prompt, "--allow-all", "--no-ask-user"]
        if self._silent:
            cmd.append("-s")
        if model and model.lower() not in {"default", "auto"}:
            cmd.extend(["--model", model])
        return cmd

    def check_available(self) -> bool:
        if self.dry_run:
            return True
        try:
            result = subprocess.run(
                [self.cli_command, "version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def validate_health(self, account_id: str | None = None) -> HealthResult:
        """Check Copilot CLI installation and auth."""
        try:
            result = subprocess.run(
                [self.cli_command, "version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return HealthResult(
                    status=HealthStatus.NOT_AUTHENTICATED,
                    message="Copilot CLI found but returned non-zero exit.",
                )
        except FileNotFoundError:
            return HealthResult(
                status=HealthStatus.NOT_INSTALLED,
                message=f"Copilot CLI not found at '{self.cli_command}'. Install with: brew install copilot-cli",
            )
        except subprocess.TimeoutExpired:
            return HealthResult(
                status=HealthStatus.UNREACHABLE,
                message="Copilot CLI check timed out.",
            )

        # Auth failures surface at prompt execution time; version check is sufficient for install probe.
        version = result.stdout.strip().splitlines()[0] if result.stdout else ""
        return HealthResult(
            status=HealthStatus.HEALTHY,
            message=f"Copilot CLI available ({version})",
            details={"version": version},
        )

    def get_default_model(self, phase: str | None = None) -> str:
        provider_cfg = self._provider_config()
        phase_models = provider_cfg.get("phase_models", {})
        if phase and phase in phase_models:
            return phase_models[phase]
        return str(provider_cfg.get("default_model", _DEFAULT_COPILOT_MODEL) or "")

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
            "provider_id": "copilot",
            "account_id": account_id,
        }

    @staticmethod
    def _failure_result(
        model: str,
        account_id: str | None,
        duration: float,
        error: str,
        failure_type: str,
    ) -> dict:
        return {
            "success": False,
            "output": None,
            "error": error,
            "failure_type": failure_type,
            "duration_seconds": duration,
            "retries": 0,
            "usage": {},
            "total_cost_usd": None,
            "model_used": model,
            "usage_source": "none",
            "provider_id": "copilot",
            "account_id": account_id,
        }
