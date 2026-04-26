"""Provider routing engine for AIDLC.

ProviderRouter is a drop-in replacement for ClaudeCLI throughout the codebase.
It selects the right provider, account, and model for each call based on:
  - routing_strategy (balanced | cheapest | best_quality | custom)
  - current lifecycle phase (planning, implementation, audit, etc.)
  - complexity signal (normal | complex)
  - account health and tier
  - current usage pressure (calls/tokens used so far in this run)

Usage:
    router = ProviderRouter(config, logger)
    router.set_phase("planning")
    result = router.execute_prompt(prompt, working_dir)

    # Or as a drop-in for ClaudeCLI (same interface):
    result = router.execute_prompt(prompt, working_dir, allow_edits=True, model_override="opus")
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..providers.base import ProviderAdapter
from . import context, cooldown, helpers, result_signals, strategy_resolution
from .adapter_registry import build_provider_adapters
from .types import RouteDecision, RoutingStrategy, UsagePressure


class ProviderRouter:
    """Drop-in replacement for ClaudeCLI that routes calls across providers/accounts.

    Implements the same execute_prompt / check_available interface so all
    existing phase classes (Planner, Implementer, Finalizer, Validator) work
    without modification.
    """

    def __init__(self, config: dict, logger: logging.Logger):
        self.config = config
        self.logger = logger
        raw_strategy = config.get("routing_strategy", "balanced")
        try:
            self._strategy = RoutingStrategy(raw_strategy)
        except ValueError:
            self.logger.warning(
                f"Unknown routing_strategy '{raw_strategy}' in config; falling back to 'balanced'"
            )
            self._strategy = RoutingStrategy.BALANCED
        self._current_phase: str = "default"
        self._complexity: str = "normal"  # "normal" | "complex"
        self._usage = UsagePressure()
        self._provider_cooldowns: dict[str, float] = {}
        self._model_cooldowns: dict[tuple[str, str], float] = {}
        self._rate_limit_cooldown_seconds = max(
            1, int(config.get("routing_rate_limit_cooldown_seconds", 300) or 300)
        )
        # Exponential buffer on rate limits: base * min(2^step, 8) added to reported restore time.
        _buf = config.get("routing_rate_limit_buffer_base_seconds")
        if _buf is None:
            self._rate_limit_buffer_base_seconds = 3600
        else:
            self._rate_limit_buffer_base_seconds = max(0, int(_buf))
        self._rate_limit_backoff_step: dict[tuple[str, str], int] = {}

        self._adapters: dict[str, ProviderAdapter] = build_provider_adapters(config, logger)

        # Import account manager lazily to avoid circular deps
        self._account_manager = None

        # dry_run passthrough
        self.dry_run = config.get("dry_run", False)

        # Session-level budget provider selection.
        # Randomly pick copilot or openai at router init so a full run uses one
        # provider consistently, but across runs they alternate to equalize
        # monthly token usage.  Uses a random seed derived from current minute
        # so the choice is stable within a single run but varies across runs.
        import random as _random
        import time as _time

        _rng = _random.Random(int(_time.time() / 60))  # changes each minute
        enabled_budget = [p for p in helpers.get_budget_providers() if p in self._adapters]
        self._session_budget_provider: str | None = (
            _rng.choice(enabled_budget) if enabled_budget else None
        )

    # ------------------------------------------------------------------
    # ClaudeCLI-compatible interface
    # ------------------------------------------------------------------

    def execute_prompt(
        self,
        prompt: str,
        working_dir: Path,
        allow_edits: bool = False,
        model_override: str | None = None,
        phase: str | None = None,
        complexity: str | None = None,
    ) -> dict:
        """Execute a prompt via the resolved provider/account/model.

        Accepts all ClaudeCLI.execute_prompt() parameters plus optional
        `phase` and `complexity` for more precise routing.
        """
        effective_phase = phase or self._current_phase
        effective_complexity = complexity or self._complexity
        while True:
            now = time.time()
            excluded_providers: set[str] = {
                provider_id
                for provider_id in self._adapters.keys()
                if self._provider_is_on_cooldown(provider_id, now)
            }
            # Snapshot keys: _model_is_on_cooldown may pop expired entries and must not
            # mutate _model_cooldowns while iterating its key view.
            excluded_models: set[tuple[str, str]] = {
                key
                for key in tuple(self._model_cooldowns.keys())
                if self._model_is_on_cooldown(key[0], key[1], now)
            }

            exhausted_providers: list[str] = []
            rate_limited_models: list[tuple[str, str]] = list(excluded_models)
            attempts_remaining = max(1, len(self._adapters) * 2)

            while attempts_remaining > 0:
                attempts_remaining -= 1
                decision = self.resolve(
                    phase=effective_phase,
                    complexity_level=effective_complexity,
                    model_override=model_override,
                    excluded_providers=excluded_providers,
                    excluded_models=excluded_models,
                    now=now,
                )

                if decision.provider_id in excluded_providers:
                    break

                if self._model_is_on_cooldown(decision.provider_id, decision.model, now):
                    excluded_models.add((decision.provider_id, decision.model))
                    excluded_providers.add(decision.provider_id)
                    rate_limited_models.append((decision.provider_id, decision.model))
                    continue

                self.logger.debug(
                    f"Router: phase={effective_phase} complexity={effective_complexity} "
                    f"→ provider={decision.provider_id} account={decision.account_id} "
                    f"model={decision.model} [{decision.reasoning}]"
                )
                self._log_routing_note(decision, effective_phase)

                result = decision.adapter.execute_prompt(
                    prompt=prompt,
                    working_dir=working_dir,
                    allow_edits=allow_edits,
                    model_override=decision.model,
                    account_id=decision.account_id,
                )
                result = result_signals.reclassify_quota_chatter_success(result)

                # Enrich result with routing metadata
                result.setdefault("provider_id", decision.provider_id)
                result.setdefault("account_id", decision.account_id)
                result["routing_decision"] = {
                    "provider_id": decision.provider_id,
                    "account_id": decision.account_id,
                    "model": decision.model,
                    "reasoning": decision.reasoning,
                    "strategy": decision.strategy_used,
                    "phase": effective_phase,
                    "fallback": decision.fallback,
                    "tier": decision.tier,
                    "quality_note": decision.quality_note,
                }

                # Track usage pressure for Balanced mode
                tokens_used = sum(result.get("usage", {}).values()) if result.get("usage") else 0
                self._usage.record(decision.provider_id, decision.account_id, tokens_used)

                # Update account last_used
                if decision.account_id and self._account_manager:
                    try:
                        self._account_manager.mark_used(decision.account_id)
                    except Exception as mark_err:  # noqa: BLE001 — 3rd-party AccountManager may raise anything; usage tracking is non-fatal
                        self.logger.debug(f"mark_used failed for {decision.account_id}: {mark_err}")

                if result.get("success"):
                    self._provider_cooldowns.pop(decision.provider_id, None)
                    self._model_cooldowns.pop((decision.provider_id, decision.model), None)
                    self._rate_limit_backoff_step.pop((decision.provider_id, decision.model), None)
                    return result

                if result_signals.is_token_exhaustion_result(result):
                    # ISSUE-004: try the next model in this provider's chain
                    # before excluding the provider entirely. Without this,
                    # single-provider users (only Claude enabled) get no
                    # fallback at all when sonnet hits its quota — even if
                    # they have opus/haiku capacity remaining.
                    next_model = self._next_chain_model(
                        decision.provider_id,
                        decision.model,
                        excluded_models,
                    )
                    excluded_models.add((decision.provider_id, decision.model))
                    if next_model:
                        self.logger.warning(
                            f"[routing] {effective_phase}: token exhaustion on "
                            f"{decision.provider_id}/{decision.model}; "
                            f"trying next model in chain: {next_model}"
                        )
                        # Hand the next model to resolve() so the same provider
                        # is reused with the new model.
                        model_override = next_model
                        attempts_remaining += 1  # this attempt isn't "spent"
                        continue
                    excluded_providers.add(decision.provider_id)
                    exhausted_providers.append(decision.provider_id)
                    self.logger.warning(
                        f"[routing] {effective_phase}: token exhaustion on "
                        f"{decision.provider_id}/{decision.model}; "
                        f"chain exhausted, trying alternate provider"
                    )
                    continue

                if result_signals.is_rate_limited_result(result):
                    excluded_providers.add(decision.provider_id)
                    excluded_models.add((decision.provider_id, decision.model))
                    rate_limited_models.append((decision.provider_id, decision.model))
                    cooldown.record_rate_limit(
                        router=self,
                        decision=decision,
                        result=result,
                        now=now,
                        effective_phase=effective_phase,
                    )
                    continue

                return result

            if exhausted_providers:
                providers = ", ".join(dict.fromkeys(exhausted_providers))
                attempted_chain = self._format_attempted_chain(excluded_models)
                chain_suffix = f" (attempted: {attempted_chain})" if attempted_chain else ""
                self.logger.warning(
                    f"[routing] {effective_phase}: stopping — all providers exhausted: "
                    f"{providers}{chain_suffix}"
                )
                return {
                    "success": False,
                    "output": None,
                    "error": (
                        "All available providers/models appear out of tokens or quota "
                        f"for phase '{effective_phase}': {providers}{chain_suffix}"
                    ),
                    "failure_type": "token_exhausted_all_models",
                    "duration_seconds": 0.0,
                    "retries": 0,
                    "usage": {},
                    "total_cost_usd": None,
                    "model_used": model_override or "unknown",
                    "usage_source": "none",
                    "provider_id": exhausted_providers[-1],
                    "account_id": None,
                    "routing_decision": {
                        "provider_id": exhausted_providers[-1],
                        "account_id": None,
                        "model": model_override or "unknown",
                        "reasoning": "all providers exhausted by token/quota limits",
                        "strategy": self._strategy.value,
                        "phase": effective_phase,
                        "fallback": True,
                        "tier": "fallback",
                        "quality_note": "all models token exhausted",
                    },
                }

            if rate_limited_models:
                unique_models = list(dict.fromkeys(rate_limited_models))
                providers = ", ".join(
                    f"{provider}/{model or 'default'}" for provider, model in unique_models
                )
                next_restore = self._next_model_restore_time()
                if next_restore is not None:
                    wait_seconds = max(0.0, next_restore - time.time())
                    if wait_seconds > 0:
                        self.logger.warning(
                            f"[routing] {effective_phase}: all models limited, waiting {wait_seconds:.1f}s for next availability"
                        )
                        time.sleep(wait_seconds)
                    continue

                return {
                    "success": False,
                    "output": None,
                    "error": (
                        "No models currently available because all known models are rate limited "
                        "and no restore time was provided; limits exhausted "
                        f"for phase '{effective_phase}': {providers}"
                    ),
                    "failure_type": "rate_limited_all_models",
                    "duration_seconds": 0.0,
                    "retries": 0,
                    "usage": {},
                    "total_cost_usd": None,
                    "model_used": model_override or "unknown",
                    "usage_source": "none",
                    "provider_id": unique_models[-1][0],
                    "account_id": None,
                    "routing_decision": {
                        "provider_id": unique_models[-1][0],
                        "account_id": None,
                        "model": unique_models[-1][1] or model_override or "unknown",
                        "reasoning": "all models temporarily rate limited with unknown restore times",
                        "strategy": self._strategy.value,
                        "phase": effective_phase,
                        "fallback": True,
                        "tier": "fallback",
                        "quality_note": "all models rate limited",
                    },
                }

            # No successful call and no explicit exhaustion classification: last resolve failed hard.
            return {
                "success": False,
                "output": None,
                "error": "No available provider could execute the prompt.",
                "failure_type": "provider_unavailable",
                "duration_seconds": 0.0,
                "retries": 0,
                "usage": {},
                "total_cost_usd": None,
                "model_used": model_override or "unknown",
                "usage_source": "none",
                "provider_id": None,
                "account_id": None,
            }

    def check_available(self) -> bool:
        """Return True if at least one configured provider is available."""
        for adapter in self._adapters.values():
            if adapter.check_available():
                return True
        return False

    # ------------------------------------------------------------------
    # Phase/complexity context setters
    # ------------------------------------------------------------------

    def set_phase(self, phase: str) -> None:
        """Set the current lifecycle phase for routing decisions."""
        self._current_phase = phase

    def set_complexity(self, complexity: str) -> None:
        """Set complexity signal: 'normal' or 'complex'."""
        self._complexity = complexity

    # ------------------------------------------------------------------
    # Route resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        phase: str = "default",
        complexity_level: str = "normal",
        model_override: str | None = None,
        excluded_providers: set[str] | None = None,
        excluded_models: set[tuple[str, str]] | None = None,
        now: float | None = None,
    ) -> RouteDecision:
        """Resolve the best (adapter, account, model) for the given context."""
        strategy = self._strategy
        excluded = excluded_providers or set()
        excluded_model_keys = excluded_models or set()

        if strategy == RoutingStrategy.CHEAPEST:
            return strategy_resolution.resolve_cheapest(
                self,
                phase,
                complexity_level,
                model_override,
                excluded,
                excluded_model_keys,
                now,
            )
        if strategy == RoutingStrategy.BEST_QUALITY:
            return strategy_resolution.resolve_best_quality(
                self,
                phase,
                complexity_level,
                model_override,
                excluded,
                excluded_model_keys,
                now,
            )
        if strategy == RoutingStrategy.CUSTOM:
            return strategy_resolution.resolve_custom(
                self,
                phase,
                complexity_level,
                model_override,
                excluded,
                excluded_model_keys,
                now,
            )
        return strategy_resolution.resolve_balanced(
            self,
            phase,
            complexity_level,
            model_override,
            excluded,
            excluded_model_keys,
            now,
        )

    def resolve_preview(self) -> dict[str, RouteDecision]:
        """Return a per-phase preview of what the router would do right now.

        Used by `aidlc config show --effective`.
        """
        phases = [
            "audit",
            "planning",
            "research",
            "implementation",
            "implementation_complex",
            "finalization",
        ]
        return {phase: self.resolve(phase=phase) for phase in phases}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tier_aware_provider_order(self, phase: str, complexity_level: str) -> list[str]:
        """Return provider IDs: implementation defaults to max-capacity first, with a
        configurable random chance to try budget CLIs first; other phases use weighted-fair.
        """
        return context.tier_aware_provider_order(
            self.config,
            set(self._adapters.keys()),
            self._usage,
            self._session_budget_provider,
            phase,
            complexity_level,
        )

    def _budget_provider_order(self, enabled: set[str]) -> list[str]:
        """Order budget providers by within-run usage pressure."""
        return context.budget_provider_order(self._usage, self._session_budget_provider, enabled)

    def _get_accounts_for_provider(self, provider_id: str) -> list:
        """Return Account objects for a provider if AccountManager is available."""
        return context.get_accounts_for_provider(
            self._account_manager, provider_id, logger=self.logger
        )

    def _select_account(
        self,
        accounts: list,
        provider_id: str,
        is_quality_phase: bool,
    ) -> tuple[str | None, str]:
        """Select the best account for a provider given current conditions."""
        return context.select_account(self._usage, accounts, provider_id, is_quality_phase)

    def _resolve_model_for_phase(
        self,
        adapter: ProviderAdapter,
        phase: str,
        complexity_level: str,
    ) -> str:
        """Resolve model string for a given phase and complexity."""
        return context.resolve_model_for_phase(self.config, adapter, phase, complexity_level)

    def _fallback_decision(
        self,
        phase: str,
        complexity_level: str,
        model_override: str | None,
        excluded_providers: set[str] | None = None,
        excluded_models: set[tuple[str, str]] | None = None,
        now: float | None = None,
    ) -> RouteDecision:
        """Emergency fallback: use first adapter that is available."""
        return context.fallback_decision(
            adapters=self._adapters,
            config=self.config,
            logger=self.logger,
            phase=phase,
            complexity_level=complexity_level,
            model_override=model_override,
            excluded_providers=excluded_providers,
            excluded_models=excluded_models,
            now=now,
            model_on_cooldown=self._model_is_on_cooldown,
        )

    def _compute_rate_limit_cooldown_until(
        self,
        provider_id: str,
        model: str,
        result: dict,
        now: float,
    ) -> float | None:
        """Delegate to ``cooldown.compute_rate_limit_cooldown_until`` (kept as
        a method so tests can call ``router._compute_rate_limit_cooldown_until``)."""
        return cooldown.compute_rate_limit_cooldown_until(
            provider_id=provider_id,
            model=model,
            result=result,
            now=now,
            backoff_step=self._rate_limit_backoff_step,
            buffer_base_seconds=self._rate_limit_buffer_base_seconds,
            fallback_cooldown_seconds=self._rate_limit_cooldown_seconds,
        )

    def _log_routing_note(self, decision: RouteDecision, phase: str) -> None:
        """Emit a user-facing log line when routing affects quality or cost."""
        if decision.fallback:
            self.logger.warning(
                f"[routing] {phase}: FALLBACK — preferred provider unavailable, "
                f"using {decision.provider_id}/{decision.model}"
            )
        elif decision.quality_note:
            if "reduced" in decision.quality_note or "unavailable" in decision.quality_note:
                self.logger.warning(f"[routing] {decision.quality_note}")
            else:
                self.logger.info(f"[routing] {decision.quality_note}")

    def _next_chain_model(
        self,
        provider_id: str,
        current_model: str,
        excluded_models: set[tuple[str, str]],
    ) -> str | None:
        """Return the next model in this provider's fallback chain, or None.

        ISSUE-004: walked when a model returns "out of tokens" so the engine
        tries the next entry on the same provider before excluding the
        provider. Skips entries already in ``excluded_models`` for this
        provider. Returns None when the chain is empty, missing, or fully
        consumed — at which point the caller falls through to the existing
        provider-exclusion branch.
        """
        providers_cfg = self.config.get("providers", {})
        provider_cfg = providers_cfg.get(provider_id) if isinstance(providers_cfg, dict) else None
        if not isinstance(provider_cfg, dict):
            return None
        chain = provider_cfg.get("model_fallback_chain") or []
        if not isinstance(chain, list) or not chain:
            return None

        # Find the current model in the chain. If it's not present (e.g., the
        # phase resolved to a model outside the chain), start from the top.
        try:
            start_idx = chain.index(current_model) + 1
        except ValueError:
            start_idx = 0

        for candidate in chain[start_idx:]:
            if not isinstance(candidate, str) or not candidate:
                continue
            if candidate == current_model:
                continue
            if (provider_id, candidate) in excluded_models:
                continue
            return candidate
        return None

    def _format_attempted_chain(
        self,
        excluded_models: set[tuple[str, str]],
    ) -> str:
        """Format ``excluded_models`` grouped by provider for stop-reason logs.

        Output looks like ``claude=[sonnet, opus, haiku]; openai=[gpt-5.4]``,
        used in the all-providers-exhausted log line so users can see what was
        tried before giving up (ISSUE-004).
        """
        by_provider: dict[str, list[str]] = {}
        for provider_id, model in sorted(excluded_models):
            by_provider.setdefault(provider_id, []).append(model)
        return "; ".join(f"{pid}=[{', '.join(models)}]" for pid, models in by_provider.items())

    def _provider_is_on_cooldown(self, provider_id: str, now: float | None = None) -> bool:
        """Return True when a provider is temporarily excluded after rate limiting."""
        return cooldown.is_on_cooldown(self._provider_cooldowns, provider_id, now)

    def _model_is_on_cooldown(self, provider_id: str, model: str, now: float | None = None) -> bool:
        """Return True when a model is temporarily excluded after rate limiting."""
        return cooldown.is_on_cooldown(self._model_cooldowns, (provider_id, model), now)

    def _next_model_restore_time(self, now: float | None = None) -> float | None:
        """Return the earliest future cooldown expiry across models, if any."""
        return cooldown.next_model_restore_time(self._model_cooldowns, now)

    def set_account_manager(self, manager) -> None:
        """Inject an AccountManager for per-account routing decisions."""
        self._account_manager = manager

    @property
    def strategy(self) -> RoutingStrategy:
        return self._strategy

    @strategy.setter
    def strategy(self, value: RoutingStrategy) -> None:
        self._strategy = value

    def get_usage_summary(self) -> dict:
        """Return current usage pressure summary for reporting."""
        return {
            "total_calls": self._usage.total_calls,
            "total_tokens": self._usage.total_tokens,
            "calls_by_provider": dict(self._usage.calls_by_provider),
            "tokens_by_provider": dict(self._usage.tokens_by_provider),
            "calls_by_account": dict(self._usage.calls_by_account),
            "tokens_by_account": dict(self._usage.tokens_by_account),
        }
