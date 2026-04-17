"""Provider ordering, account selection, model resolution, and fallback routing.

Extracted from ``ProviderRouter`` to keep ``engine.py`` focused on the execute/resolve loop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..providers.base import ProviderAdapter
from ..providers.claude_adapter import ClaudeCLIAdapter
from . import helpers
from .types import RouteDecision, UsagePressure


def budget_provider_order(
    usage: UsagePressure,
    session_budget_provider: str | None,
    enabled: set[str],
) -> list[str]:
    budget = [p for p in helpers.get_budget_providers() if p in enabled]
    if not budget:
        return []

    def key(provider_id: str) -> tuple[int, int, int, str]:
        return (
            usage.calls_by_provider.get(provider_id, 0),
            usage.tokens_by_provider.get(provider_id, 0),
            0 if provider_id == session_budget_provider else 1,
            provider_id,
        )

    return sorted(budget, key=key)


def tier_aware_provider_order(
    config: dict,
    adapter_ids: set[str],
    usage: UsagePressure,
    session_budget_provider: str | None,
    is_premium_phase: bool,
) -> list[str]:
    providers_cfg = config.get("providers", {})
    enabled: set[str] = set()
    if isinstance(providers_cfg, dict):
        enabled = {
            pid
            for pid, pcfg in providers_cfg.items()
            if isinstance(pcfg, dict) and pcfg.get("enabled", True)
        }
    if not enabled:
        enabled = set(adapter_ids)

    budget_order = budget_provider_order(usage, session_budget_provider, enabled)

    if is_premium_phase:
        candidates = ["claude"]
        candidates.extend(budget_order)
        for p in helpers.get_balanced_provider_order():
            if p not in candidates and p in enabled:
                candidates.append(p)
    else:
        candidates = list(budget_order)
        if "claude" in enabled:
            candidates.append("claude")
        for p in helpers.get_balanced_provider_order():
            if p not in candidates and p in enabled:
                candidates.append(p)

    return candidates


def get_accounts_for_provider(account_manager: Any, provider_id: str) -> list:
    if account_manager:
        try:
            return account_manager.by_provider(provider_id)
        except Exception:
            pass
    from ..accounts.models import Account, AuthState

    return [
        Account(
            account_id=f"{provider_id}-default",
            provider_id=provider_id,
            display_name=f"{provider_id} (default)",
            auth_state=AuthState.UNKNOWN,
        )
    ]


def select_account(
    usage: UsagePressure,
    accounts: list,
    provider_id: str,
    is_quality_phase: bool,
) -> tuple[str | None, str]:
    if not accounts:
        return None, "no accounts configured, using default auth"

    usable = [a for a in accounts if getattr(a, "is_usable", True)]
    if not usable:
        return None, "no usable accounts, using default auth"

    if not is_quality_phase:
        non_premium = [a for a in usable if not getattr(a, "is_premium", False)]
        if non_premium:
            selected = min(
                non_premium,
                key=lambda a: usage.calls_by_account.get(a.account_id, 0),
            )
            return selected.account_id, (
                f"avoiding premium accounts for routine phase, "
                f"account={selected.account_id} (calls={usage.calls_by_account.get(selected.account_id, 0)})"
            )

    selected = min(
        usable,
        key=lambda a: usage.calls_by_account.get(a.account_id, 0),
    )
    return selected.account_id, (
        f"quality phase: account={selected.account_id} tier={getattr(selected.membership_tier, 'value', 'unknown')}"
    )


def resolve_model_for_phase(
    config: dict,
    adapter: ProviderAdapter,
    phase: str,
    complexity_level: str,
) -> str:
    effective_phase = phase
    if phase == "implementation" and complexity_level == "complex":
        effective_phase = "implementation_complex"

    provider_id = adapter.PROVIDER_ID
    providers_cfg = config.get("providers", {})
    if isinstance(providers_cfg, dict) and provider_id in providers_cfg:
        phase_models = providers_cfg[provider_id].get("phase_models", {})
        if isinstance(phase_models, dict):
            model = phase_models.get(effective_phase) or phase_models.get("default")
            if model:
                return str(model)

    return adapter.get_default_model(effective_phase)


def fallback_decision(
    *,
    adapters: dict[str, ProviderAdapter],
    config: dict,
    logger,
    phase: str,
    complexity_level: str,
    model_override: str | None,
    excluded_providers: set[str] | None,
    excluded_models: set[tuple[str, str]] | None,
    now: float | None,
    model_on_cooldown: Callable[[str, str, float | None], bool],
) -> RouteDecision:
    excluded = excluded_providers or set()
    excluded_model_keys = excluded_models or set()
    for provider_id, adapter in adapters.items():
        if provider_id in excluded:
            continue
        if adapter.check_available():
            model = model_override or adapter.get_default_model(phase)
            if (provider_id, model) in excluded_model_keys or model_on_cooldown(
                provider_id, model, now
            ):
                continue
            return RouteDecision(
                provider_id=provider_id,
                account_id=None,
                adapter=adapter,
                model=model,
                reasoning="fallback: no preferred provider available",
                strategy_used="fallback",
                fallback=True,
            )

    if adapters:
        provider_id, adapter = next(
            ((pid, ad) for pid, ad in adapters.items() if pid not in excluded),
            next(iter(adapters.items())),
        )
        model = model_override or adapter.get_default_model(phase)
        if (provider_id, model) in excluded_model_keys or model_on_cooldown(
            provider_id, model, now
        ):
            provider_id, adapter, model = None, None, None
            for pid, ad in adapters.items():
                candidate_model = model_override or ad.get_default_model(phase)
                if pid in excluded:
                    continue
                if (pid, candidate_model) in excluded_model_keys:
                    continue
                if model_on_cooldown(pid, candidate_model, now):
                    continue
                provider_id, adapter, model = pid, ad, candidate_model
                break
            if provider_id is None or adapter is None or model is None:
                provider_id, adapter = next(iter(adapters.items()))
                model = model_override or adapter.get_default_model(phase)
        return RouteDecision(
            provider_id=provider_id,
            account_id=None,
            adapter=adapter,
            model=model,
            reasoning="emergency fallback: all providers unavailable",
            strategy_used="fallback",
            fallback=True,
        )
    adapter = ClaudeCLIAdapter(config, logger)
    model = model_override or adapter.get_default_model(phase)
    return RouteDecision(
        provider_id="claude",
        account_id=None,
        adapter=adapter,
        model=model,
        reasoning="emergency fallback: no adapters registered",
        strategy_used="fallback",
        fallback=True,
    )
