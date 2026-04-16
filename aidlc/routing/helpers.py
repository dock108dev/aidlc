"""Routing helpers and utilities for provider routing engine."""

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import ProviderRouter, RouteDecision


def get_claude_only_aliases() -> frozenset[str]:
    """Return Claude-specific short-form model aliases.
    
    These are meaningless to other providers and should not be passed
    as model_override when routing to non-Claude providers.
    """
    return frozenset({
        "sonnet", "opus", "haiku",
        "sonnet-4", "opus-4", "haiku-4",
        "claude-sonnet", "claude-opus", "claude-haiku",
    })


def should_discard_model_override(provider_id: str, model_override: Optional[str]) -> bool:
    """Check if model_override should be discarded for this provider.
    
    Claude-specific aliases like 'sonnet' should not be passed to copilot/openai.
    """
    if not model_override or provider_id == "claude":
        return False
    return model_override in get_claude_only_aliases()


def get_phase_model_config_keys() -> dict[str, str]:
    """Maps phase names to legacy config keys for backward compatibility."""
    return {
        "planning": "claude_model_planning",
        "research": "claude_model_research",
        "implementation": "claude_model_implementation",
        "implementation_complex": "claude_model_implementation_complex",
        "finalization": "claude_model_finalization",
        "audit": "claude_model_planning",
    }


def get_quality_sensitive_phases() -> frozenset[str]:
    """Return phases where higher-quality models are preferred."""
    return frozenset({"planning", "implementation_complex", "finalization", "audit"})


def get_premium_phases() -> frozenset[str]:
    """Return phases routed to premium tier (Claude) when available."""
    return frozenset({"implementation_complex"})


def get_balanced_provider_order() -> list[str]:
    """Return provider priority order for balanced mode."""
    return ["claude", "copilot", "openai"]


def get_budget_providers() -> list[str]:
    """Return budget provider pair for round-robin distribution."""
    return ["copilot", "openai"]


def is_premium_phase(phase: str, complexity: str = "normal") -> bool:
    """Check if a phase should use premium tier (Claude)."""
    premium = get_premium_phases()
    quality_sensitive = get_quality_sensitive_phases()
    is_complex = complexity == "complex"
    return phase in premium or (phase == "implementation" and is_complex) or (phase in quality_sensitive and is_complex)
