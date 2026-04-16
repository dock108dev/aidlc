"""Routing strategy selectors for provider routing engine.

Extracts routing strategy implementation (_resolve_balanced, _resolve_cheapest,
_resolve_best_quality, _resolve_custom) and helper methods into a separate module
for improved maintainability.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import RouteDecision, UsagePressure, ProviderRouter


def resolve_balanced(
    router: "ProviderRouter",
    phase: str,
    complexity_level: str,
    model_override: str | None,
) -> "RouteDecision":
    """Balanced routing: Prefer high-quality providers, but balance load."""
    is_premium_phase = phase in {"implementation_complex"}
    provider_order = router._tier_aware_provider_order(phase, is_premium_phase)

    for provider_id in provider_order:
        if provider_id not in router._adapters:
            continue

        accounts = router._get_accounts_for_provider(provider_id)
        if not accounts:
            continue

        # Pick the account with the least usage share so far
        account = router._select_account(
            provider_id=provider_id,
            accounts=accounts,
            strategy="balanced",
        )
        if not account:
            continue

        model = router._resolve_model_for_phase(
            provider_id=provider_id,
            phase=phase,
            complexity_level=complexity_level,
            model_override=model_override,
        )
        if not model:
            continue

        adapter = router._adapters[provider_id]
        return type("RouteDecision", (), {
            "provider_id": provider_id,
            "account_id": account.account_id if account else None,
            "adapter": adapter,
            "model": model,
            "reasoning": (
                f"Balanced mode: {provider_id} (account load balanced)"
                if account else f"Balanced mode: {provider_id}"
            ),
            "strategy_used": "balanced",
            "tier": "premium" if is_premium_phase else "budget",
        })()

    # Fallback to any working provider
    return router._fallback_decision(phase, complexity_level, model_override)


def resolve_cheapest(
    router: "ProviderRouter",
    phase: str,
    complexity_level: str,
    model_override: str | None,
) -> "RouteDecision":
    """Cheapest mode: Use lowest-cost provider available."""
    # Sorted by price (cheapest first in config)
    provider_order = ["openai", "copilot", "claude"]

    for provider_id in provider_order:
        if provider_id not in router._adapters:
            continue

        accounts = router._get_accounts_for_provider(provider_id)
        if not accounts:
            continue

        account = router._select_account(
            provider_id=provider_id,
            accounts=accounts,
            strategy="cheapest",
        )
        if not account:
            continue

        model = router._resolve_model_for_phase(
            provider_id=provider_id,
            phase=phase,
            complexity_level=complexity_level,
            model_override=model_override,
        )
        if not model:
            continue

        adapter = router._adapters[provider_id]
        return type("RouteDecision", (), {
            "provider_id": provider_id,
            "account_id": account.account_id if account else None,
            "adapter": adapter,
            "model": model,
            "reasoning": f"Cheapest mode: {provider_id} (lowest cost)",
            "strategy_used": "cheapest",
            "tier": "budget",
        })()

    return router._fallback_decision(phase, complexity_level, model_override)


def resolve_best_quality(
    router: "ProviderRouter",
    phase: str,
    complexity_level: str,
    model_override: str | None,
) -> "RouteDecision":
    """Best quality mode: Prefer highest-capability models."""
    # Claude is generally highest quality
    provider_order = ["claude", "copilot", "openai"]

    for provider_id in provider_order:
        if provider_id not in router._adapters:
            continue

        accounts = router._get_accounts_for_provider(provider_id)
        if not accounts:
            continue

        account = router._select_account(
            provider_id=provider_id,
            accounts=accounts,
            strategy="best_quality",
        )
        if not account:
            continue

        model = router._resolve_model_for_phase(
            provider_id=provider_id,
            phase=phase,
            complexity_level=complexity_level,
            model_override=model_override,
        )
        if not model:
            continue

        adapter = router._adapters[provider_id]
        return type("RouteDecision", (), {
            "provider_id": provider_id,
            "account_id": account.account_id if account else None,
            "adapter": adapter,
            "model": model,
            "reasoning": f"Best quality mode: {provider_id} (highest capability)",
            "strategy_used": "best_quality",
            "tier": "premium",
        })()

    return router._fallback_decision(phase, complexity_level, model_override)


def resolve_custom(
    router: "ProviderRouter",
    phase: str,
    complexity_level: str,
    model_override: str | None,
) -> "RouteDecision":
    """Custom mode: Use explicit routing rules from config."""
    # This is a placeholder for custom routing logic.
    # In a real implementation, this would read custom rules from config
    # and apply them. For now, fall back to balanced.
    return resolve_balanced(router, phase, complexity_level, model_override)
