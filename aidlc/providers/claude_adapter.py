"""Claude CLI provider adapter."""

import logging
from pathlib import Path

from ..claude_cli import ClaudeCLI, ClaudeCLIError
from .base import HealthResult, HealthStatus, ProviderAdapter


class ClaudeCLIAdapter(ProviderAdapter):
    """Provider adapter that delegates to the Claude CLI binary."""

    PROVIDER_ID = "claude"

    def __init__(self, config: dict, logger: logging.Logger):
        super().__init__(config, logger)
        self._cli = ClaudeCLI(config, logger)

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
        try:
            result = self._cli.execute_prompt(
                prompt=prompt,
                working_dir=working_dir,
                allow_edits=allow_edits,
                model_override=model_override,
                continuation_session_id=continuation_session_id,
                resume_session_id=resume_session_id,
            )
        except ClaudeCLIError as e:
            return {
                "success": False,
                "output": None,
                "error": str(e),
                "failure_type": "provider_error",
                "duration_seconds": 0.0,
                "retries": 0,
                "usage": {},
                "total_cost_usd": None,
                "model_used": model_override or self._cli.model,
                "usage_source": "none",
                "provider_id": self.PROVIDER_ID,
                "account_id": account_id,
            }
        result["provider_id"] = self.PROVIDER_ID
        result["account_id"] = account_id
        return result

    def check_available(self) -> bool:
        return self._cli.check_available()

    def validate_health(self, account_id: str | None = None) -> HealthResult:
        """Check Claude CLI installation and authentication."""
        import subprocess

        providers_cfg = self.config.get("providers", {})
        if not isinstance(providers_cfg, dict):
            providers_cfg = {}
        claude_cfg = providers_cfg.get(self.PROVIDER_ID, {})
        if not isinstance(claude_cfg, dict):
            claude_cfg = {}
        cli_cmd = str(claude_cfg.get("cli_command", "claude"))

        # Check if binary exists
        try:
            result = subprocess.run(
                [cli_cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return HealthResult(
                    status=HealthStatus.NOT_AUTHENTICATED,
                    message=f"Claude CLI found but returned non-zero: {result.stderr.strip()}",
                )
        except FileNotFoundError:
            return HealthResult(
                status=HealthStatus.NOT_INSTALLED,
                message=f"Claude CLI binary '{cli_cmd}' not found in PATH.",
            )
        except subprocess.TimeoutExpired:
            return HealthResult(
                status=HealthStatus.UNREACHABLE,
                message="Claude CLI version check timed out.",
            )

        version = result.stdout.strip() or result.stderr.strip()
        return HealthResult(
            status=HealthStatus.HEALTHY,
            message=f"Claude CLI available ({version})",
            details={"version": version, "cli_command": cli_cmd},
        )

    def get_default_model(self, phase: str | None = None) -> str:
        providers_cfg = self.config.get("providers", {})
        if not isinstance(providers_cfg, dict):
            providers_cfg = {}
        claude_cfg = providers_cfg.get(self.PROVIDER_ID, {})
        if not isinstance(claude_cfg, dict):
            claude_cfg = {}

        if phase:
            phase_models = claude_cfg.get("phase_models", {})
            if isinstance(phase_models, dict):
                model = phase_models.get(phase)
                if model:
                    return str(model)

        return str(claude_cfg.get("default_model", "unknown"))
