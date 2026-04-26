"""Provider ordering, account selection, model resolution, and fallback routing.

Extracted from ``ProviderRouter`` to keep ``engine.py`` focused on the execute/resolve loop.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from ..providers.base import ProviderAdapter
from ..providers.claude_adapter import ClaudeCLIAdapter
from . import helpers
from .types import RouteDecision, UsagePressure


def _provider_max_capacity_flag(cfg: dict) -> bool:
    return bool(cfg.get("max_capacity"))


def provider_max_capacity_tagged(config: dict, provider_id: str) -> bool:
    """True when this provider is marked high token-capacity (``max_capacity: true``) in config."""
    p = config.get("providers", {})
    if not isinstance(p, dict):
        return False
    cfg = p.get(provider_id)
    return isinstance(cfg, dict) and _provider_max_capacity_flag(cfg)


def provider_max_capacity_weight(config: dict, provider_id: str) -> float:
    """Relative token-budget weight for routing fairness (higher = more calls before rotating)."""
    p = config.get("providers", {})
    if not isinstance(p, dict):
        return 1.0
    cfg = p.get(provider_id)
    if not isinstance(cfg, dict):
        return 1.0
    raw = cfg.get("max_capacity_weight")
    if raw is not None:
        return max(float(raw), 1e-9)
    if _provider_max_capacity_flag(cfg):
        return 20.0
    return 1.0


def _reference_ordered_subset(ids: set[str], reference: list[str]) -> list[str]:
    """Stable order: follow ``reference``, then any remaining ids alphabetically."""
    ordered = [p for p in reference if p in ids]
    tail = sorted(ids - set(ordered))
    return ordered + tail


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
    phase: str,
    complexity_level: str,
) -> list[str]:
    """Order providers for balanced routing.

    * **Implementation** phases: by default all ``max_capacity`` providers first (stable
      reference order), then the rest. With probability ``routing_impl_budget_explore_probability``
      (default 5%), enabled Copilot/OpenAI are tried first (fair order), then max_capacity,
      then any remainder — so budget CLIs still receive occasional implementation traffic.
    * **Other phases**: weighted-fair order — minimize ``calls / max_capacity_weight`` so a
      provider with weight 20 is chosen roughly 20× as often per unit of usage before rotating.
    """
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

    ref = helpers.get_balanced_provider_order()
    is_impl = phase in helpers.implementation_phases()

    max_cap_ids = {p for p in enabled if provider_max_capacity_tagged(config, p)}

    if is_impl:
        if max_cap_ids:
            p_raw = config.get("routing_impl_budget_explore_probability", 0.05)
            try:
                explore_p = float(p_raw)
            except (TypeError, ValueError):
                explore_p = 0.05
            explore_p = max(0.0, min(1.0, explore_p))
            budget_enabled = [x for x in helpers.get_budget_providers() if x in enabled]
            if budget_enabled and explore_p > 0.0 and random.random() < explore_p:
                budget_set = set(budget_enabled)
                budget_ordered = budget_provider_order(usage, session_budget_provider, budget_set)
                used = set(budget_ordered)
                max_cap_rest = [
                    p for p in _reference_ordered_subset(max_cap_ids, ref) if p not in used
                ]
                tail = enabled - used - set(max_cap_rest)
                return list(budget_ordered) + max_cap_rest + _reference_ordered_subset(tail, ref)
            rest = enabled - max_cap_ids
            return _reference_ordered_subset(max_cap_ids, ref) + _reference_ordered_subset(
                rest, ref
            )
        # No max_capacity providers — same fairness as other phases
        return _weighted_fair_provider_order(config, enabled, usage, session_budget_provider)

    # Legacy: complex implementation previously mapped to implementation_complex phase only.
    legacy_premium_first = phase in helpers.get_premium_phases() or (
        phase == "implementation" and complexity_level == "complex"
    )
    if legacy_premium_first and "claude" in enabled:
        rest = enabled - {"claude"}
        return ["claude"] + _reference_ordered_subset(rest, ref)

    return _weighted_fair_provider_order(config, enabled, usage, session_budget_provider)


def _weighted_fair_provider_order(
    config: dict,
    enabled: set[str],
    usage: UsagePressure,
    session_budget_provider: str | None,
) -> list[str]:
    """Lower ``calls/weight`` first — higher weight tolerates more calls before rotation.

    Among Copilot/OpenAI with the same ratio and weight, prefer ``session_budget_provider``
    (same seed as budget round-robin) for the first pick.
    """

    budget_ids = set(helpers.get_budget_providers())

    def sort_key(pid: str) -> tuple[float, float, int, str]:
        w = provider_max_capacity_weight(config, pid)
        ratio = usage.calls_by_provider.get(pid, 0) / w
        if (
            pid in budget_ids
            and session_budget_provider
            and session_budget_provider in enabled
            and session_budget_provider in budget_ids
        ):
            flip = 0 if pid == session_budget_provider else 1
        else:
            flip = 0
        return (ratio, -w, flip, pid)

    return sorted(enabled, key=sort_key)


def get_accounts_for_provider(
    account_manager: Any,
    provider_id: str,
    logger: Any = None,
) -> list:
    if account_manager:
        try:
            return account_manager.by_provider(provider_id)
        except Exception as exc:  # noqa: BLE001 — 3rd-party AccountManager may raise anything; falling back to a synthetic default keeps the run going
            if logger is not None:
                logger.warning(
                    f"AccountManager.by_provider({provider_id}) failed ({exc}); "
                    "using synthetic default account"
                )
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
        standard_tier = [a for a in usable if not getattr(a, "is_premium", False)]
        if standard_tier:
            selected = min(
                standard_tier,
                key=lambda a: usage.calls_by_account.get(a.account_id, 0),
            )
            return selected.account_id, (
                "using standard-tier account for routine phase, "
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
    """Pick the model for a (provider, phase) pair using user-aware precedence.

    Order — first non-empty wins:

    1. user ``providers.<id>.phase_models[phase]``
    2. user ``providers.<id>.default_model``
    3. DEFAULT ``providers.<id>.phase_models[phase]``
    4. DEFAULT ``providers.<id>.default_model``
    5. adapter default

    Step 2 is what makes a single ``default_model: "opus"`` in the user's
    config take effect across all phases without forcing them to override
    every entry in ``phase_models``. Without it, a DEFAULT
    ``phase_models.planning: "sonnet"`` would always win and the user's
    ``default_model`` would be silently dead config (ISSUE-003).
    """
    effective_phase = phase
    if phase == "implementation" and complexity_level == "complex":
        effective_phase = "implementation_complex"

    provider_id = adapter.PROVIDER_ID
    providers_cfg = config.get("providers", {})
    user_overrides = config.get("_user_provider_overrides", {})
    user_provider_overrides = (
        user_overrides.get(provider_id) if isinstance(user_overrides, dict) else None
    )

    # 1. user phase_models[phase]
    if isinstance(user_provider_overrides, dict):
        user_phase_models = user_provider_overrides.get("phase_models") or {}
        if isinstance(user_phase_models, dict):
            user_phase_model = user_phase_models.get(effective_phase) or user_phase_models.get(
                "default"
            )
            if user_phase_model:
                return str(user_phase_model)

        # 2. user default_model
        user_default = user_provider_overrides.get("default_model")
        if user_default:
            return str(user_default)

    # 3. DEFAULT phase_models[phase]
    if isinstance(providers_cfg, dict) and provider_id in providers_cfg:
        phase_models = providers_cfg[provider_id].get("phase_models", {})
        if isinstance(phase_models, dict):
            model = phase_models.get(effective_phase) or phase_models.get("default")
            if model:
                return str(model)

        # 4. DEFAULT default_model
        merged_default = providers_cfg[provider_id].get("default_model")
        if merged_default:
            return str(merged_default)

    # 5. adapter default
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
