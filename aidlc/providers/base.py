"""Provider adapter abstract base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import logging


class HealthStatus(Enum):
    HEALTHY = "healthy"
    LIMITED = "limited"           # authenticated but rate-limited or degraded
    RATE_LIMITED = "rate_limited"
    EXPIRED = "expired"           # credentials expired
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
        return self.config.get("claude_model", "sonnet")

    def supports_edit_permissions(self) -> bool:
        """Return True if this provider supports the allow_edits workflow."""
        return True
