"""Claude CLI provider adapter.

Wraps the existing ClaudeCLI execution logic as a ProviderAdapter.
Maintains full backward compatibility with the original ClaudeCLI class.
"""

from pathlib import Path
import logging

from .base import ProviderAdapter, HealthResult, HealthStatus
from ..claude_cli import ClaudeCLI, ClaudeCLIError


class ClaudeCLIAdapter(ProviderAdapter):
    """Provider adapter that delegates to the Claude CLI binary."""

    PROVIDER_ID = "claude"

    # Maps AIDLC phase names to config keys for per-phase model selection
    _PHASE_MODEL_KEYS = {
        "planning": "claude_model_planning",
        "research": "claude_model_research",
        "implementation": "claude_model_implementation",
        "implementation_complex": "claude_model_implementation_complex",
        "finalization": "claude_model_finalization",
        "audit": "claude_model_planning",  # audit uses planning-tier model
    }

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
    ) -> dict:
        try:
            result = self._cli.execute_prompt(
                prompt=prompt,
                working_dir=working_dir,
                allow_edits=allow_edits,
                model_override=model_override,
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

        cli_cmd = self.config.get("claude_cli_command", "claude")

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
        if phase and phase in self._PHASE_MODEL_KEYS:
            key = self._PHASE_MODEL_KEYS[phase]
            return self.config.get(key, self.config.get("claude_model", "sonnet"))
        return self.config.get("claude_model", "sonnet")
