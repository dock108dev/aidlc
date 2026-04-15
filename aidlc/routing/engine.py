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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from ..providers.base import ProviderAdapter
from ..providers.claude_adapter import ClaudeCLIAdapter
from ..providers.copilot_adapter import CopilotAdapter
from ..providers.openai_adapter import OpenAIAdapter


class RoutingStrategy(Enum):
    BALANCED = "balanced"
    CHEAPEST = "cheapest"
    BEST_QUALITY = "best_quality"
    CUSTOM = "custom"


@dataclass
class RouteDecision:
    """The resolved routing decision for a single call."""
    provider_id: str
    account_id: Optional[str]
    adapter: ProviderAdapter
    model: str
    reasoning: str
    strategy_used: str = "balanced"
    fallback: bool = False           # True if this is a fallback from the preferred route


@dataclass
class UsagePressure:
    """Tracks within-run usage pressure to inform Balanced mode decisions."""
    calls_by_account: dict[str, int] = field(default_factory=dict)
    tokens_by_account: dict[str, int] = field(default_factory=dict)
    calls_by_provider: dict[str, int] = field(default_factory=dict)
    tokens_by_provider: dict[str, int] = field(default_factory=dict)
    total_calls: int = 0
    total_tokens: int = 0

    def record(self, provider_id: str, account_id: str | None, tokens: int) -> None:
        self.total_calls += 1
        self.total_tokens += tokens
        self.calls_by_provider[provider_id] = self.calls_by_provider.get(provider_id, 0) + 1
        self.tokens_by_provider[provider_id] = self.tokens_by_provider.get(provider_id, 0) + tokens
        if account_id:
            self.calls_by_account[account_id] = self.calls_by_account.get(account_id, 0) + 1
            self.tokens_by_account[account_id] = self.tokens_by_account.get(account_id, 0) + tokens

    def account_call_share(self, account_id: str) -> float:
        """Fraction of total calls made by this account (0.0 – 1.0)."""
        if self.total_calls == 0:
            return 0.0
        return self.calls_by_account.get(account_id, 0) / self.total_calls


# Maps phase names to the legacy config keys for backward compatibility
_PHASE_MODEL_CONFIG_KEYS: dict[str, str] = {
    "planning": "claude_model_planning",
    "research": "claude_model_research",
    "implementation": "claude_model_implementation",
    "implementation_complex": "claude_model_implementation_complex",
    "finalization": "claude_model_finalization",
    "audit": "claude_model_planning",
}

# Provider priority in BALANCED mode (index = preferred order, lower = better for normal work)
_BALANCED_PROVIDER_ORDER = ["claude", "copilot", "openai"]

# Phases where we prefer higher-quality models in BALANCED mode
_QUALITY_SENSITIVE_PHASES = {"planning", "implementation_complex", "finalization", "audit"}


class ProviderRouter:
    """Drop-in replacement for ClaudeCLI that routes calls across providers/accounts.

    Implements the same execute_prompt / check_available interface so all
    existing phase classes (Planner, Implementer, Finalizer, Validator) work
    without modification.
    """

    def __init__(self, config: dict, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self._strategy = RoutingStrategy(
            config.get("routing_strategy", "balanced")
        )
        self._current_phase: str = "default"
        self._complexity: str = "normal"  # "normal" | "complex"
        self._usage = UsagePressure()

        # Build adapter registry
        self._adapters: dict[str, ProviderAdapter] = self._build_adapters()

        # Import account manager lazily to avoid circular deps
        self._account_manager = None

        # dry_run passthrough
        self.dry_run = config.get("dry_run", False)

        # Legacy compat: expose .model like ClaudeCLI does
        self.model = config.get("claude_model", "sonnet")

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

        decision = self.resolve(
            phase=effective_phase,
            complexity_level=effective_complexity,
            model_override=model_override,
        )

        self.logger.debug(
            f"Router: phase={effective_phase} complexity={effective_complexity} "
            f"→ provider={decision.provider_id} account={decision.account_id} "
            f"model={decision.model} [{decision.reasoning}]"
        )

        result = decision.adapter.execute_prompt(
            prompt=prompt,
            working_dir=working_dir,
            allow_edits=allow_edits,
            model_override=decision.model,
            account_id=decision.account_id,
        )

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
        }

        # Track usage pressure for Balanced mode
        tokens_used = sum(result.get("usage", {}).values()) if result.get("usage") else 0
        self._usage.record(decision.provider_id, decision.account_id, tokens_used)

        # Update account last_used
        if decision.account_id and self._account_manager:
            try:
                self._account_manager.mark_used(decision.account_id)
            except Exception:
                pass

        return result

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
    ) -> RouteDecision:
        """Resolve the best (adapter, account, model) for the given context."""
        strategy = self._strategy

        if strategy == RoutingStrategy.CHEAPEST:
            return self._resolve_cheapest(phase, complexity_level, model_override)
        elif strategy == RoutingStrategy.BEST_QUALITY:
            return self._resolve_best_quality(phase, complexity_level, model_override)
        elif strategy == RoutingStrategy.CUSTOM:
            return self._resolve_custom(phase, complexity_level, model_override)
        else:
            return self._resolve_balanced(phase, complexity_level, model_override)

    def resolve_preview(self) -> dict[str, RouteDecision]:
        """Return a per-phase preview of what the router would do right now.

        Used by `aidlc config show --effective`.
        """
        phases = [
            "audit", "planning", "research",
            "implementation", "implementation_complex",
            "finalization",
        ]
        return {phase: self.resolve(phase=phase) for phase in phases}

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _resolve_balanced(
        self,
        phase: str,
        complexity_level: str,
        model_override: str | None,
    ) -> RouteDecision:
        """Balanced strategy: intelligent cost/quality/pressure balance.

        Decision logic:
        1. Try preferred provider for this phase (from providers config or legacy keys)
        2. Within that provider, pick the account with lowest usage pressure
           that is healthy and has sufficient tier for the task
        3. For complex/quality-sensitive phases, allow higher-tier accounts
        4. For routine phases, protect premium/reserve accounts
        5. Fall back to the next available provider if preferred is unhealthy
        """
        is_complex = complexity_level == "complex"
        is_quality_phase = phase in _QUALITY_SENSITIVE_PHASES or is_complex

        # Determine preferred provider order for this phase
        provider_order = self._preferred_provider_order(phase)

        for provider_id in provider_order:
            adapter = self._adapters.get(provider_id)
            if adapter is None or not adapter.check_available():
                continue

            # Get accounts for this provider (from AccountManager if available)
            accounts = self._get_accounts_for_provider(provider_id)

            # Select best account
            account_id, account_reasoning = self._select_account(
                accounts=accounts,
                provider_id=provider_id,
                is_quality_phase=is_quality_phase,
            )

            # Resolve model
            if model_override:
                model = model_override
                model_reason = f"explicit model_override={model_override}"
            else:
                model = self._resolve_model_for_phase(
                    adapter=adapter,
                    phase=phase,
                    complexity_level=complexity_level,
                )
                model_reason = f"phase={phase} complexity={complexity_level}"

            reasoning = (
                f"balanced: provider={provider_id}, {account_reasoning}, "
                f"model={model} ({model_reason})"
            )

            return RouteDecision(
                provider_id=provider_id,
                account_id=account_id,
                adapter=adapter,
                model=model,
                reasoning=reasoning,
                strategy_used="balanced",
            )

        # Last resort: use first available adapter with no account
        return self._fallback_decision(phase, complexity_level, model_override)

    def _resolve_cheapest(
        self,
        phase: str,
        complexity_level: str,
        model_override: str | None,
    ) -> RouteDecision:
        """Cheapest strategy: prefer lowest-cost provider/model."""
        # For cheapest: prefer haiku/cheap models, non-premium accounts
        for provider_id in _BALANCED_PROVIDER_ORDER:
            adapter = self._adapters.get(provider_id)
            if adapter is None or not adapter.check_available():
                continue

            accounts = self._get_accounts_for_provider(provider_id)
            # Prefer cheap-tagged accounts
            cheap_accounts = [a for a in accounts if "cheap" in getattr(a, "role_tags", [])]
            account_id = cheap_accounts[0].account_id if cheap_accounts else (
                accounts[0].account_id if accounts else None
            )

            if model_override:
                model = model_override
            else:
                # Use cheapest available model for this provider
                cheapest_models = {"claude": "haiku", "copilot": "claude-haiku-3-5", "openai": "gpt-4o-mini"}
                model = cheapest_models.get(provider_id, adapter.get_default_model(phase))

            return RouteDecision(
                provider_id=provider_id,
                account_id=account_id,
                adapter=adapter,
                model=model,
                reasoning=f"cheapest: using lowest-cost model={model}",
                strategy_used="cheapest",
            )

        return self._fallback_decision(phase, complexity_level, model_override)

    def _resolve_best_quality(
        self,
        phase: str,
        complexity_level: str,
        model_override: str | None,
    ) -> RouteDecision:
        """Best quality strategy: prefer highest-tier provider/account/model."""
        # Prefer premium accounts + opus-class models
        best_account = None
        best_provider = None
        best_tier = -1

        for provider_id in _BALANCED_PROVIDER_ORDER:
            adapter = self._adapters.get(provider_id)
            if adapter is None or not adapter.check_available():
                continue
            accounts = self._get_accounts_for_provider(provider_id)
            for acc in accounts:
                tw = getattr(acc, "tier_weight", 1)
                if tw > best_tier:
                    best_tier = tw
                    best_account = acc
                    best_provider = provider_id

        if best_provider:
            adapter = self._adapters[best_provider]
            account_id = best_account.account_id if best_account else None

            if model_override:
                model = model_override
            else:
                quality_models = {"claude": "opus", "copilot": "claude-sonnet-4-6", "openai": "gpt-4o"}
                model = quality_models.get(best_provider, adapter.get_default_model(phase))

            return RouteDecision(
                provider_id=best_provider,
                account_id=account_id,
                adapter=adapter,
                model=model,
                reasoning=f"best_quality: highest-tier account tier={best_tier}, model={model}",
                strategy_used="best_quality",
            )

        return self._fallback_decision(phase, complexity_level, model_override)

    def _resolve_custom(
        self,
        phase: str,
        complexity_level: str,
        model_override: str | None,
    ) -> RouteDecision:
        """Custom strategy: read per-phase routing from providers.*.routing config."""
        routing_cfg = self.config.get("routing", {})
        phase_cfg = routing_cfg.get(phase, routing_cfg.get("default", {}))

        provider_id = phase_cfg.get("provider", "claude")
        account_id = phase_cfg.get("account")
        custom_model = phase_cfg.get("model")

        adapter = self._adapters.get(provider_id)
        if adapter is None or not adapter.check_available():
            # Fall back to balanced
            self.logger.warning(
                f"Custom routing: provider '{provider_id}' for phase '{phase}' "
                "unavailable, falling back to balanced."
            )
            return self._resolve_balanced(phase, complexity_level, model_override)

        model = model_override or custom_model or adapter.get_default_model(phase)

        return RouteDecision(
            provider_id=provider_id,
            account_id=account_id,
            adapter=adapter,
            model=model,
            reasoning=f"custom: phase={phase} → provider={provider_id} model={model}",
            strategy_used="custom",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _preferred_provider_order(self, phase: str) -> list[str]:
        """Return provider IDs in preference order for a phase."""
        providers_cfg = self.config.get("providers", {})
        if isinstance(providers_cfg, dict):
            # Respect explicit enable/disable flags
            enabled = [
                pid for pid, pcfg in providers_cfg.items()
                if isinstance(pcfg, dict) and pcfg.get("enabled", True)
            ]
            if enabled:
                # Put any explicitly preferred provider for this phase first
                ordered = list(_BALANCED_PROVIDER_ORDER)
                for pid in enabled:
                    if pid not in ordered:
                        ordered.append(pid)
                # Filter to only enabled providers
                return [p for p in ordered if p in enabled]

        return list(_BALANCED_PROVIDER_ORDER)

    def _get_accounts_for_provider(self, provider_id: str) -> list:
        """Return Account objects for a provider if AccountManager is available."""
        if self._account_manager:
            try:
                return self._account_manager.by_provider(provider_id)
            except Exception:
                pass
        # Fall back to a synthetic "default" account entry
        from ..accounts.models import Account, AuthState
        return [Account(
            account_id=f"{provider_id}-default",
            provider_id=provider_id,
            display_name=f"{provider_id} (default)",
            auth_state=AuthState.UNKNOWN,
        )]

    def _select_account(
        self,
        accounts: list,
        provider_id: str,
        is_quality_phase: bool,
    ) -> tuple[str | None, str]:
        """Select the best account for a provider given current conditions.

        Returns (account_id, reasoning_string).
        """
        if not accounts:
            return None, "no accounts configured, using default auth"

        usable = [a for a in accounts if getattr(a, "is_usable", True)]
        if not usable:
            return None, "no usable accounts, using default auth"

        # For non-quality phases: avoid premium/reserve accounts
        if not is_quality_phase:
            non_premium = [
                a for a in usable
                if not getattr(a, "is_premium", False)
            ]
            if non_premium:
                # Pick least-used non-premium account
                selected = min(
                    non_premium,
                    key=lambda a: self._usage.calls_by_account.get(a.account_id, 0),
                )
                return selected.account_id, (
                    f"avoiding premium accounts for routine phase, "
                    f"account={selected.account_id} (calls={self._usage.calls_by_account.get(selected.account_id, 0)})"
                )

        # For quality phases: allow premium, prefer least-used among appropriate tier
        selected = min(
            usable,
            key=lambda a: self._usage.calls_by_account.get(a.account_id, 0),
        )
        return selected.account_id, (
            f"quality phase: account={selected.account_id} tier={getattr(selected.membership_tier, 'value', 'unknown')}"
        )

    def _resolve_model_for_phase(
        self,
        adapter: ProviderAdapter,
        phase: str,
        complexity_level: str,
    ) -> str:
        """Resolve model string for a given phase and complexity."""
        # For complex implementation, use the complex model key
        effective_phase = phase
        if phase == "implementation" and complexity_level == "complex":
            effective_phase = "implementation_complex"

        # Check per-provider phase_models config first
        provider_id = adapter.PROVIDER_ID
        providers_cfg = self.config.get("providers", {})
        if isinstance(providers_cfg, dict) and provider_id in providers_cfg:
            phase_models = providers_cfg[provider_id].get("phase_models", {})
            if isinstance(phase_models, dict):
                model = phase_models.get(effective_phase) or phase_models.get("default")
                if model:
                    return model

        # Fall back to adapter's phase model (reads legacy claude_model_* keys)
        return adapter.get_default_model(effective_phase)

    def _fallback_decision(
        self,
        phase: str,
        complexity_level: str,
        model_override: str | None,
    ) -> RouteDecision:
        """Emergency fallback: use first adapter that is available."""
        for provider_id, adapter in self._adapters.items():
            if adapter.check_available():
                model = model_override or adapter.get_default_model(phase)
                return RouteDecision(
                    provider_id=provider_id,
                    account_id=None,
                    adapter=adapter,
                    model=model,
                    reasoning="fallback: no preferred provider available",
                    strategy_used="fallback",
                    fallback=True,
                )

        # Absolute last resort: ClaudeCLI even if not available (will fail gracefully)
        adapter = self._adapters.get("claude") or ClaudeCLIAdapter(self.config, self.logger)
        model = model_override or adapter.get_default_model(phase)
        return RouteDecision(
            provider_id="claude",
            account_id=None,
            adapter=adapter,
            model=model,
            reasoning="emergency fallback: all providers unavailable",
            strategy_used="fallback",
            fallback=True,
        )

    def _build_adapters(self) -> dict[str, ProviderAdapter]:
        """Instantiate all configured provider adapters."""
        providers_cfg = self.config.get("providers", {})
        adapters: dict[str, ProviderAdapter] = {}

        # Always register Claude (primary/legacy provider)
        adapters["claude"] = ClaudeCLIAdapter(self.config, self.logger)

        # Register additional providers if enabled in config
        if isinstance(providers_cfg, dict):
            if providers_cfg.get("copilot", {}).get("enabled", False):
                adapters["copilot"] = CopilotAdapter(self.config, self.logger)
            if providers_cfg.get("openai", {}).get("enabled", False):
                adapters["openai"] = OpenAIAdapter(self.config, self.logger)

        return adapters

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
