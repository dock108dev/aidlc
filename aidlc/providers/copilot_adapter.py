"""GitHub Copilot CLI provider adapter.

Shells out to the `gh copilot` CLI or a compatible `copilot` binary.
Default model: claude-sonnet-4-6 (configurable via provider config).
"""

import subprocess
import time
from pathlib import Path
import logging

from .base import ProviderAdapter, HealthResult, HealthStatus

# Default model for Copilot provider
_DEFAULT_COPILOT_MODEL = "claude-sonnet-4-6"


class CopilotAdapter(ProviderAdapter):
    """Provider adapter for GitHub Copilot CLI."""

    PROVIDER_ID = "copilot"

    def __init__(self, config: dict, logger: logging.Logger):
        super().__init__(config, logger)
        provider_cfg = self._provider_config()
        self.cli_command = provider_cfg.get("cli_command", "gh")
        self.default_model = provider_cfg.get("default_model", _DEFAULT_COPILOT_MODEL)
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

        model = model_override or self.default_model

        # Build command: `gh copilot suggest -t shell` or similar
        # For code generation we use `gh copilot explain` / suggest patterns
        # The primary path is: gh copilot suggest --target shell
        # For now we support a generic prompt execution mode
        cmd = self._build_command(model, allow_edits)

        start = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(working_dir),
            )
            try:
                stdout, stderr = proc.communicate(input=prompt, timeout=self.hard_timeout)
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
                    "model_used": model,
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
                model, account_id, 0.0,
                error=f"Copilot CLI not found at '{self.cli_command}'. Install gh CLI with Copilot extension.",
                failure_type="provider_error",
            )

    def _build_command(self, model: str, allow_edits: bool) -> list[str]:
        """Build the gh copilot CLI command."""
        # gh copilot suggest accepts stdin via pipe
        # The --target flag controls output type: shell, git, gh
        return [self.cli_command, "copilot", "suggest", "--target", "shell"]

    def check_available(self) -> bool:
        if self.dry_run:
            return True
        try:
            result = subprocess.run(
                [self.cli_command, "copilot", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def validate_health(self, account_id: str | None = None) -> HealthResult:
        """Check gh CLI installation and Copilot auth."""
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
                    message="gh CLI found but returned non-zero exit.",
                )
        except FileNotFoundError:
            return HealthResult(
                status=HealthStatus.NOT_INSTALLED,
                message=f"gh CLI not found at '{self.cli_command}'. Install with: brew install gh",
            )
        except subprocess.TimeoutExpired:
            return HealthResult(
                status=HealthStatus.UNREACHABLE,
                message="gh CLI check timed out.",
            )

        # Check auth status
        try:
            auth_result = subprocess.run(
                [self.cli_command, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if auth_result.returncode != 0:
                return HealthResult(
                    status=HealthStatus.NOT_AUTHENTICATED,
                    message="gh CLI not authenticated. Run: gh auth login",
                )
        except Exception:
            return HealthResult(
                status=HealthStatus.UNKNOWN,
                message="Could not check gh auth status.",
            )

        version = result.stdout.strip().splitlines()[0] if result.stdout else ""
        return HealthResult(
            status=HealthStatus.HEALTHY,
            message=f"GitHub Copilot CLI available ({version})",
            details={"version": version},
        )

    def get_default_model(self, phase: str | None = None) -> str:
        provider_cfg = self._provider_config()
        phase_models = provider_cfg.get("phase_models", {})
        if phase and phase in phase_models:
            return phase_models[phase]
        return provider_cfg.get("default_model", _DEFAULT_COPILOT_MODEL)

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
