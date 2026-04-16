"""Provider adapter layer for AIDLC.

Each provider (Claude CLI, Copilot, OpenAI) implements the ProviderAdapter
ABC so the rest of the codebase can work with any provider interchangeably.
"""

from .base import HealthStatus, ProviderAdapter, ProviderError
from .claude_adapter import ClaudeCLIAdapter
from .copilot_adapter import CopilotAdapter
from .openai_adapter import OpenAIAdapter

__all__ = [
    "ProviderAdapter",
    "ProviderError",
    "HealthStatus",
    "ClaudeCLIAdapter",
    "CopilotAdapter",
    "OpenAIAdapter",
]
