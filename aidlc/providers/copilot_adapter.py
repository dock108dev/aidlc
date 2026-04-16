"""GitHub Copilot CLI provider adapter.

Shells out to the standalone `copilot` CLI by default.
Install: brew install copilot-cli
Auth: copilot login

Model selection is optional. If no model is configured, the adapter omits
`--model` and lets Copilot use its own current default. This avoids breaking
when vendor model IDs change.
"""

import subprocess
import time
from pathlib import Path
import logging

from .base import ProviderAdapter, HealthResult, HealthStatus

# Default model for Copilot provider.
# Empty string means "let the Copilot CLI choose its default model".
_DEFAULT_COPILOT_MODEL = ""


class CopilotAdapter(ProviderAdapter):
    """Provider adapter for GitHub Copilot CLI."""

    PROVIDER_ID = "copilot"

    def __init__(self, config: dict, logger: logging.Logger):
        super().__init__(config, logger)
        provider_cfg = self._provider_config()
        self.cli_command = provider_cfg.get("cli_command", "copilot")
        self.default_model = str(provider_cfg.get("default_model", _DEFAULT_COPILOT_MODEL) or "")
        self.dry_run = config.get("dry_run", False)
        self.hard_timeout = int(config.get("claude_hard_timeout_seconds", 1800))
        self.warn_interval = int(config.get("claude_long_run_warn_seconds", 300))

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

        # Build command: copilot -p <prompt> --allow-all -s --model <model>
        # -p / --prompt: execute prompt programmatically (exits after completion)
        # --allow-all: grant all tool permissions (required for autonomous code gen)
        # -s / --silent: output only agent response (no usage stats), useful for scripting
        # --model: specify the AI model
        cmd = self._build_command(model, allow_edits, prompt)

        start = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(working_dir),
            )
            try:
                stdout, stderr = proc.communicate(timeout=self.hard_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                duration = time.time() - start
                return self._failure_result(
                    model, account_id, duration,
                    error="Copilot CLI timed out",
                    failure_type="timeout",
                )

            duration = time.time() - start
            if proc.returncode == 0:
                return {
                    "success": True,
                    "output": stdout,
                    "error": None,
                    "failure_type": None,
                    "duration_seconds": duration,
                    "retries": 0,
                    "usage": {},
                    "total_cost_usd": None,
                    "model_used": model or "default",
                    "usage_source": "copilot_cli",
                    "provider_id": self.PROVIDER_ID,
                    "account_id": account_id,
                }
            else:
                return self._failure_result(
                    model, account_id, duration,
                    error=stderr.strip() or "Copilot CLI returned non-zero exit code",
                    failure_type="issue",
                )

        except FileNotFoundError:
            return self._failure_result(
                model or "default", account_id, 0.0,
                error=f"Copilot CLI not found at '{self.cli_command}'. Install with: brew install copilot-cli",
                failure_type="provider_error",
            )

    def _build_command(self, model: str, allow_edits: bool, prompt: str) -> list[str]:
        """Build the copilot CLI programmatic command.

        Uses: copilot -p <prompt> --allow-all -s [--model <model>]
        --allow-all grants all tool permissions for autonomous execution.
        -s (--silent) suppresses usage stats, outputs only the agent response.
        """
        cmd = [self.cli_command, "-p", prompt, "--allow-all", "-s"]
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
        try:
            subprocess.run(
                [self.cli_command, "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            pass

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
