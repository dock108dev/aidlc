"""Routing strategy resolution (balanced, cheapest, best_quality, custom)."""

from __future__ import annotations

from . import helpers
from .context import provider_max_capacity_tagged, provider_max_capacity_weight
from .types import RouteDecision


def resolve_balanced(
    router,
    phase: str,
    complexity_level: str,
    model_override: str | None,
    excluded_providers: set[str],
    excluded_models: set[tuple[str, str]],
    now: float | None,
) -> RouteDecision:
    """Balanced strategy: intelligent 3-tier cost/quality routing."""
    is_complex = complexity_level == "complex"
    is_quality_phase = phase in helpers.get_quality_sensitive_phases() or is_complex
    is_impl = phase in helpers.implementation_phases()
    is_legacy_premium = phase in helpers.get_premium_phases() or (
        phase == "implementation" and is_complex
    )

    provider_order = router._tier_aware_provider_order(phase, complexity_level)

    for provider_id in provider_order:
        if provider_id in excluded_providers:
            continue
        adapter = router._adapters.get(provider_id)
        if adapter is None or not adapter.check_available():
            continue

        accounts = router._get_accounts_for_provider(provider_id)
        account_id, account_reasoning = router._select_account(
            accounts=accounts,
            provider_id=provider_id,
            is_quality_phase=is_quality_phase,
        )

        effective_override = (
            None
            if helpers.should_discard_model_override(provider_id, model_override)
            else model_override
        )
        if effective_override:
            model = effective_override
            model_reason = f"explicit model_override={effective_override}"
        else:
            model = router._resolve_model_for_phase(
                adapter=adapter,
                phase=phase,
                complexity_level=complexity_level,
            )
            model_reason = f"phase={phase} complexity={complexity_level}"

        if (provider_id, model) in excluded_models or router._model_is_on_cooldown(
            provider_id, model, now
        ):
            continue

        max_cap = provider_max_capacity_tagged(router.config, provider_id)
        tier_label = "max_capacity" if max_cap else "standard"
        reasoning = (
            f"balanced/{tier_label}: provider={provider_id}, {account_reasoning}, "
            f"model={model} ({model_reason})"
        )

        explore_p = 0.05
        try:
            explore_p = float(
                router.config.get("routing_impl_budget_explore_probability", 0.05)
                or 0.0
            )
        except (TypeError, ValueError):
            explore_p = 0.05
        explore_p = max(0.0, min(1.0, explore_p))

        quality_note: str | None = None
        if is_impl:
            if provider_id in helpers.get_budget_providers():
                quality_note = (
                    f"implementation → budget CLI ({provider_id}/{model}); "
                    f"~{explore_p:.0%} of resolves try budget CLIs first when enabled"
                )
            elif max_cap:
                quality_note = (
                    f"implementation → max-capacity backend ({provider_id}/{model}); "
                    f"~{explore_p:.0%} of resolves try budget CLIs first; "
                    "others when excluded or unavailable"
                )
            else:
                quality_note = (
                    f"implementation: {provider_id}/{model} — no max_capacity provider; "
                    "set providers.<id>.max_capacity for premium-first ordering"
                )
        elif is_legacy_premium and provider_id == "claude":
            quality_note = (
                f"legacy Claude-first phase ({phase}) → {provider_id}/{model}"
            )
        elif is_legacy_premium and provider_id != "claude":
            quality_note = f"Claude unavailable for {phase} (legacy phase), fallback {provider_id}/{model}"
        elif max_cap and not is_impl:
            w = provider_max_capacity_weight(router.config, provider_id)
            quality_note = f"capacity-weighted routing ({provider_id}, weight≈{w:.0f}×)"
        elif not is_impl and provider_id in helpers.get_budget_providers():
            if model and model not in ("gpt-5.4-mini", "gpt-5.4-nano"):
                quality_note = f"model upgraded to {model} for {phase} (complexity)"

        return RouteDecision(
            provider_id=provider_id,
            account_id=account_id,
            adapter=adapter,
            model=model,
            reasoning=reasoning,
            strategy_used="balanced",
            tier=tier_label,
            quality_note=quality_note,
        )

    return router._fallback_decision(
        phase,
        complexity_level,
        model_override,
        excluded_providers=excluded_providers,
        excluded_models=excluded_models,
        now=now,
    )


def resolve_cheapest(
    router,
    phase: str,
    complexity_level: str,
    model_override: str | None,
    excluded_providers: set[str],
    excluded_models: set[tuple[str, str]],
    now: float | None,
) -> RouteDecision:
    """Cheapest strategy: prefer lowest-cost provider/model."""
    for provider_id in helpers.get_balanced_provider_order():
        if provider_id in excluded_providers:
            continue
        adapter = router._adapters.get(provider_id)
        if adapter is None or not adapter.check_available():
            continue

        accounts = router._get_accounts_for_provider(provider_id)
        cheap_accounts = [a for a in accounts if "cheap" in getattr(a, "role_tags", [])]
        account_id = (
            cheap_accounts[0].account_id
            if cheap_accounts
            else (accounts[0].account_id if accounts else None)
        )

        effective_override = (
            None
            if helpers.should_discard_model_override(provider_id, model_override)
            else model_override
        )
        if effective_override:
            model = effective_override
        else:
            cheapest_models = {
                "claude": "haiku",
                "copilot": "",
                "openai": "gpt-5.4-nano",
            }
            model = cheapest_models.get(provider_id, adapter.get_default_model(phase))

        if (provider_id, model) in excluded_models or router._model_is_on_cooldown(
            provider_id, model, now
        ):
            continue

        return RouteDecision(
            provider_id=provider_id,
            account_id=account_id,
            adapter=adapter,
            model=model,
            reasoning=f"cheapest: using lowest-cost model={model}",
            strategy_used="cheapest",
        )

    return router._fallback_decision(
        phase,
        complexity_level,
        model_override,
        excluded_providers=excluded_providers,
        excluded_models=excluded_models,
        now=now,
    )


def resolve_best_quality(
    router,
    phase: str,
    complexity_level: str,
    model_override: str | None,
    excluded_providers: set[str],
    excluded_models: set[tuple[str, str]],
    now: float | None,
) -> RouteDecision:
    """Best quality strategy: prefer highest-tier provider/account/model."""
    best_account = None
    best_provider = None
    best_tier = -1

    for provider_id in helpers.get_balanced_provider_order():
        if provider_id in excluded_providers:
            continue
        adapter = router._adapters.get(provider_id)
        if adapter is None or not adapter.check_available():
            continue
        accounts = router._get_accounts_for_provider(provider_id)
        for acc in accounts:
            tw = getattr(acc, "tier_weight", 1)
            if tw > best_tier:
                best_tier = tw
                best_account = acc
                best_provider = provider_id

    if best_provider:
        adapter = router._adapters[best_provider]
        account_id = best_account.account_id if best_account else None

        effective_override = (
            None
            if helpers.should_discard_model_override(best_provider, model_override)
            else model_override
        )
        if effective_override:
            model = effective_override
        else:
            quality_models = {"claude": "opus", "copilot": "", "openai": "gpt-5.4"}
            model = quality_models.get(best_provider, adapter.get_default_model(phase))

        if (best_provider, model) in excluded_models or router._model_is_on_cooldown(
            best_provider, model, now
        ):
            return router._fallback_decision(
                phase,
                complexity_level,
                model_override,
                excluded_providers=excluded_providers,
                excluded_models=excluded_models,
                now=now,
            )

        return RouteDecision(
            provider_id=best_provider,
            account_id=account_id,
            adapter=adapter,
            model=model,
            reasoning=f"best_quality: highest-tier account tier={best_tier}, model={model}",
            strategy_used="best_quality",
        )

    return router._fallback_decision(
        phase,
        complexity_level,
        model_override,
        excluded_providers=excluded_providers,
        excluded_models=excluded_models,
        now=now,
    )


def resolve_custom(
    router,
    phase: str,
    complexity_level: str,
    model_override: str | None,
    excluded_providers: set[str],
    excluded_models: set[tuple[str, str]],
    now: float | None,
) -> RouteDecision:
    """Custom strategy: read per-phase routing from ``routing`` config."""
    routing_cfg = router.config.get("routing", {})
    phase_cfg = routing_cfg.get(phase, routing_cfg.get("default", {}))

    provider_id = phase_cfg.get("provider", "claude")
    account_id = phase_cfg.get("account")
    custom_model = phase_cfg.get("model")

    adapter = router._adapters.get(provider_id)
    if (
        provider_id in excluded_providers
        or adapter is None
        or not adapter.check_available()
    ):
        router.logger.warning(
            f"Custom routing: provider '{provider_id}' for phase '{phase}' "
            "unavailable, falling back to balanced."
        )
        return resolve_balanced(
            router,
            phase,
            complexity_level,
            model_override,
            excluded_providers,
            excluded_models,
            now,
        )

    clean_override = (
        None
        if helpers.should_discard_model_override(provider_id, model_override)
        else model_override
    )
    model = clean_override or custom_model or adapter.get_default_model(phase)

    if (provider_id, model) in excluded_models or router._model_is_on_cooldown(
        provider_id, model, now
    ):
        return resolve_balanced(
            router,
            phase,
            complexity_level,
            model_override,
            excluded_providers,
            excluded_models,
            now,
        )

    return RouteDecision(
        provider_id=provider_id,
        account_id=account_id,
        adapter=adapter,
        model=model,
        reasoning=f"custom: phase={phase} → provider={provider_id} model={model}",
        strategy_used="custom",
    )
