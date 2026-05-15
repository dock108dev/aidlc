"""OpenAI / Codex CLI provider adapter.

Shells out to the `codex` CLI or `openai` CLI binary.
Supports GPT-4o and other OpenAI models as first-class citizens.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

from .base import HealthResult, HealthStatus, ProviderAdapter
from .codex_output import (
    classify_openai_cli_failure,
    codex_exit_zero_is_quota_blocker,
    codex_nonzero_output_is_usable,
    extract_codex_failure_diagnostics,
    extract_codex_thread_id,
    parse_codex_jsonl,
)

_DEFAULT_OPENAI_MODEL = "gpt-5.5"


def _read_text_if_present(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


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
                parsed_out, usage = parse_codex_jsonl(stdout or "")
                out_text = last_message
                if not out_text:
                    out_text = parsed_out if parsed_out.strip() else (stdout or "")
                blocked, diag = codex_exit_zero_is_quota_blocker(
                    stdout or "", stderr or "", parsed_out
                )
                if blocked:
                    failure_type = classify_openai_cli_failure(diag)
                    failure = self._failure_result(
                        model,
                        account_id,
                        duration,
                        error=diag,
                        failure_type=failure_type,
                        output=last_message or parsed_out or None,
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
                parsed_out, usage = parse_codex_jsonl(stdout or "")
                explicit_diagnostic = extract_codex_failure_diagnostics(stderr or "", stdout or "")
                usable_output = last_message or parsed_out
                if (last_message and not explicit_diagnostic) or codex_nonzero_output_is_usable(
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
                failure_type = classify_openai_cli_failure(diagnostic)
                failure = self._failure_result(
                    model,
                    account_id,
                    duration,
                    error=diagnostic,
                    failure_type=failure_type,
                    output=last_message or parsed_out or None,
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
