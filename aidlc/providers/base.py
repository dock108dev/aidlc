"""Provider adapter abstract base class."""

import logging
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class HealthStatus(Enum):
    HEALTHY = "healthy"
    LIMITED = "limited"  # authenticated but rate-limited or degraded
    RATE_LIMITED = "rate_limited"
    EXPIRED = "expired"  # credentials expired
    MISSING_MODEL = "missing_model"
    UNREACHABLE = "unreachable"
    NOT_INSTALLED = "not_installed"
    NOT_AUTHENTICATED = "not_authenticated"
    FALLBACK_ONLY = "fallback_only"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


@dataclass
class HealthResult:
    status: HealthStatus
    message: str = ""
    latency_ms: float | None = None
    details: dict = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        return self.status in (
            HealthStatus.HEALTHY,
            HealthStatus.LIMITED,
            HealthStatus.FALLBACK_ONLY,
        )


class ProviderError(Exception):
    pass


class ProviderAdapter(ABC):
    """Abstract base class for all AI provider adapters.

    Each concrete adapter implements this contract so the ProviderRouter
    can work with any provider interchangeably.
    """

    PROVIDER_ID: str = "unknown"

    def __init__(self, config: dict, logger: logging.Logger):
        self.config = config
        self.logger = logger

    @abstractmethod
    def execute_prompt(
        self,
        prompt: str,
        working_dir: Path,
        allow_edits: bool = False,
        model_override: str | None = None,
        account_id: str | None = None,
    ) -> dict:
        """Execute a prompt and return a normalized result dict.

        Returns:
            dict with keys:
                success (bool)
                output (str | None)
                error (str | None)
                failure_type (str | None)
                duration_seconds (float)
                retries (int)
                usage (dict)
                total_cost_usd (float | None)
                model_used (str)
                usage_source (str)
                provider_id (str)
                account_id (str | None)
        """

    @abstractmethod
    def check_available(self) -> bool:
        """Return True if this provider can accept calls right now."""

    @abstractmethod
    def validate_health(self, account_id: str | None = None) -> HealthResult:
        """Return a structured health/auth status for this provider/account."""

    def get_default_model(self, phase: str | None = None) -> str:
        """Return the default model name for a given phase.

        Subclasses should override this to return phase-specific defaults.
        """
        return str(self.config.get("default_model", "unknown"))

    def supports_edit_permissions(self) -> bool:
        """Return True if this provider supports the allow_edits workflow."""
        return True

    def _communicate_with_heartbeat(
        self,
        proc: subprocess.Popen,
        *,
        provider_label: str,
        model: str,
        timeout_seconds: int,
        warn_interval: int,
        account_id: str | None = None,
    ) -> tuple[str, str, float, bool]:
        """Wait for a provider process while emitting compact heartbeat logs."""
        start = time.time()
        interval = max(1, int(warn_interval))
        timeout_limit = max(0, int(timeout_seconds))

        while True:
            elapsed = time.time() - start
            remaining = max(0.0, timeout_limit - elapsed) if timeout_limit else None
            wait_timeout = interval if remaining is None else min(interval, max(0.0, remaining))

            try:
                stdout, stderr = proc.communicate(timeout=wait_timeout)
                return stdout, stderr, time.time() - start, False
            except subprocess.TimeoutExpired:
                elapsed = time.time() - start
                account_text = f", account={account_id}" if account_id else ""
                self.logger.info(
                    f"{provider_label} still running (elapsed={elapsed:.0f}s, model={model}{account_text})"
                )
                if timeout_limit and elapsed >= timeout_limit:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    return stdout, stderr, time.time() - start, True
