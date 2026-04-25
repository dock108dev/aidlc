"""Unit tests for aidlc.routing.strategy_resolution with a lightweight fake router."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from aidlc.routing import strategy_resolution as sr
from aidlc.routing.types import RouteDecision


class _FakeAdapter:
    """Minimal adapter surface used by strategy_resolution."""

    def __init__(self, provider_id: str, default_model: str = "default-m", available: bool = True):
        self.PROVIDER_ID = provider_id
        self._default_model = default_model
        self._available = available

    def check_available(self) -> bool:
        return self._available

    def get_default_model(self, phase: str | None = None) -> str:
        return self._default_model


class _FakeRouter:
    def __init__(
        self,
        *,
        adapters: dict[str, _FakeAdapter],
        tier_order: list[str] | None = None,
        config: dict | None = None,
        account_pick: tuple[str | None, str] = ("acc-1", "test account"),
        resolve_model: str = "resolved-model",
        cooldown: set[tuple[str, str]] | None = None,
    ):
        self._adapters = adapters
        self.config = config or {}
        self.logger = MagicMock()
        self._tier_order = tier_order or list(adapters.keys())
        self._account_pick = account_pick
        self._resolve_model = resolve_model
        self._cooldown = cooldown or set()

    def _tier_aware_provider_order(self, phase: str, complexity_level: str) -> list[str]:
        return list(self._tier_order)

    def _get_accounts_for_provider(self, provider_id: str) -> list:
        cheap = SimpleNamespace(
            account_id="cheap-1",
            role_tags=["cheap"],
            tier_weight=1,
        )
        hi = SimpleNamespace(
            account_id="hi-tier",
            role_tags=["primary"],
            tier_weight=5,
        )
        low = SimpleNamespace(
            account_id="low-tier",
            role_tags=["primary"],
            tier_weight=1,
        )
        if provider_id == "openai":
            return [cheap, low]
        if provider_id == "claude":
            return [cheap, low, hi]
        return [SimpleNamespace(account_id=f"{provider_id}-a", role_tags=[], tier_weight=1)]

    def _select_account(self, accounts, provider_id: str, is_quality_phase: bool):
        return self._account_pick

    def _resolve_model_for_phase(self, adapter, phase: str, complexity_level: str) -> str:
        return self._resolve_model

    def _model_is_on_cooldown(self, provider_id: str, model: str, now: float | None) -> bool:
        return (provider_id, model) in self._cooldown

    def _fallback_decision(
        self,
        phase: str,
        complexity_level: str,
        model_override: str | None,
        excluded_providers: set[str] | None = None,
        excluded_models: set[tuple[str, str]] | None = None,
        now: float | None = None,
    ) -> RouteDecision:
        pid, ad = next(iter(self._adapters.items()))
        return RouteDecision(
            provider_id=pid,
            account_id=None,
            adapter=ad,
            model="fallback-m",
            reasoning="fallback",
            strategy_used="balanced",
            fallback=True,
        )


def _router_all_budget() -> _FakeRouter:
    return _FakeRouter(
        adapters={
            "claude": _FakeAdapter("claude", "opus"),
            "copilot": _FakeAdapter("copilot", ""),
            "openai": _FakeAdapter("openai", "gpt-5.4-mini"),
        },
        tier_order=["claude", "copilot", "openai"],
    )


def test_resolve_balanced_basic():
    r = _router_all_budget()
    d = sr.resolve_balanced(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.provider_id == "claude"
    assert d.strategy_used == "balanced"
    assert "balanced" in d.reasoning


def test_resolve_balanced_skips_excluded_then_openai():
    r = _router_all_budget()
    d = sr.resolve_balanced(r, "planning", "normal", None, {"claude"}, set(), 0.0)
    assert d.provider_id == "copilot"


def test_resolve_balanced_skips_missing_adapter_key():
    r = _FakeRouter(
        adapters={"openai": _FakeAdapter("openai", "gpt-x")},
        tier_order=["ghost", "openai"],
    )
    d = sr.resolve_balanced(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.provider_id == "openai"


def test_resolve_balanced_skips_unavailable_adapter():
    r = _FakeRouter(
        adapters={
            "claude": _FakeAdapter("claude", "opus", available=False),
            "openai": _FakeAdapter("openai", "gpt-x"),
        },
        tier_order=["claude", "openai"],
    )
    d = sr.resolve_balanced(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.provider_id == "openai"


def test_resolve_balanced_discards_claude_alias_for_openai():
    r = _FakeRouter(
        adapters={"openai": _FakeAdapter("openai", "gpt-nano")},
        tier_order=["openai"],
        resolve_model="from-phase",
    )
    d = sr.resolve_balanced(r, "planning", "normal", "opus", set(), set(), 0.0)
    assert d.model == "from-phase"
    assert "model_override" not in d.reasoning or "explicit" not in d.reasoning


def test_resolve_balanced_explicit_override_claude():
    r = _FakeRouter(
        adapters={"claude": _FakeAdapter("claude", "opus")},
        tier_order=["claude"],
    )
    d = sr.resolve_balanced(r, "planning", "normal", "opus", set(), set(), 0.0)
    assert d.model == "opus"
    assert "explicit model_override" in d.reasoning


def test_resolve_balanced_skips_excluded_model_then_fallback():
    r = _FakeRouter(
        adapters={"openai": _FakeAdapter("openai", "only")},
        tier_order=["openai"],
    )
    d = sr.resolve_balanced(
        r,
        "planning",
        "normal",
        None,
        set(),
        {("openai", "resolved-model")},
        0.0,
    )
    assert d.fallback is True


def test_resolve_balanced_premium_claude_quality_note():
    r = _FakeRouter(
        adapters={"claude": _FakeAdapter("claude", "opus")},
        tier_order=["claude"],
        config={
            "providers": {
                "claude": {
                    "enabled": True,
                    "max_capacity": True,
                    "max_capacity_weight": 20,
                },
            }
        },
    )
    d = sr.resolve_balanced(r, "implementation_complex", "normal", None, set(), set(), 0.0)
    assert d.provider_id == "claude"
    assert d.quality_note and "implementation" in d.quality_note.lower()


def test_resolve_balanced_premium_non_claude_quality_note():
    r = _FakeRouter(
        adapters={"openai": _FakeAdapter("openai", "gpt-5.4")},
        tier_order=["openai"],
        resolve_model="gpt-5.4",
    )
    d = sr.resolve_balanced(r, "implementation_complex", "normal", None, set(), set(), 0.0)
    assert d.provider_id == "openai"
    note = (d.quality_note or "").lower()
    assert "implementation" in note and "budget" in note


def test_resolve_balanced_budget_quality_note_non_nano():
    r = _FakeRouter(
        adapters={"copilot": _FakeAdapter("copilot", "")},
        tier_order=["copilot"],
        resolve_model="gpt-5.4-custom",
    )
    d = sr.resolve_balanced(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.provider_id == "copilot"
    assert d.quality_note and "upgraded" in d.quality_note


def test_resolve_cheapest_prefers_cheap_tagged_account():
    r = _FakeRouter(
        adapters={
            "claude": _FakeAdapter("claude", "haiku"),
            "openai": _FakeAdapter("openai", "gpt-5.4-nano"),
        },
    )
    d = sr.resolve_cheapest(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.strategy_used == "cheapest"
    assert d.account_id == "cheap-1"
    assert d.provider_id == "claude"


def test_resolve_cheapest_model_override_non_claude_alias():
    r = _FakeRouter(adapters={"openai": _FakeAdapter("openai", "nano")})
    d = sr.resolve_cheapest(r, "planning", "normal", "gpt-custom", set(), set(), 0.0)
    assert d.model == "gpt-custom"


def test_resolve_cheapest_skips_cooldown_model():
    r = _FakeRouter(
        adapters={
            "claude": _FakeAdapter("claude", "haiku"),
            "openai": _FakeAdapter("openai", "gpt-5.4-nano"),
        },
        cooldown={("claude", "haiku")},
    )
    d = sr.resolve_cheapest(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.provider_id == "openai"


def test_resolve_cheapest_all_blocked_fallback():
    r = _FakeRouter(
        adapters={"openai": _FakeAdapter("openai", "m")},
        tier_order=["openai"],
    )
    d = sr.resolve_cheapest(
        r,
        "planning",
        "normal",
        None,
        {"claude", "copilot", "openai"},
        set(),
        0.0,
    )
    assert d.fallback is True


def test_resolve_best_quality_picks_highest_tier_account():
    r = _FakeRouter(
        adapters={
            "claude": _FakeAdapter("claude", "opus"),
            "openai": _FakeAdapter("openai", "gpt-5.4"),
        },
    )
    d = sr.resolve_best_quality(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.strategy_used == "best_quality"
    assert d.provider_id == "claude"
    assert d.account_id == "hi-tier"
    assert d.model == "opus"


def test_resolve_best_quality_skips_excluded_providers_in_scan():
    r = _FakeRouter(adapters={"openai": _FakeAdapter("openai", "gpt-5.4")})
    d = sr.resolve_best_quality(r, "planning", "normal", None, {"claude", "copilot"}, set(), 0.0)
    assert d.provider_id == "openai"


def test_resolve_best_quality_respects_explicit_model_override():
    r = _FakeRouter(adapters={"openai": _FakeAdapter("openai", "gpt-5.4")})
    d = sr.resolve_best_quality(
        r,
        "planning",
        "normal",
        "custom-model-override",
        {"claude", "copilot"},
        set(),
        0.0,
    )
    assert d.model == "custom-model-override"


def test_resolve_best_quality_cooldown_triggers_fallback():
    r = _FakeRouter(
        adapters={"claude": _FakeAdapter("claude", "opus")},
        cooldown={("claude", "opus")},
    )
    d = sr.resolve_best_quality(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.fallback is True


def test_resolve_best_quality_no_accounts_fallback():
    empty_router = _FakeRouter(adapters={"claude": _FakeAdapter("claude", "opus")})

    def _no_accounts(_pid):
        return []

    empty_router._get_accounts_for_provider = _no_accounts  # type: ignore[method-assign]

    d = sr.resolve_best_quality(empty_router, "planning", "normal", None, set(), set(), 0.0)
    assert d.fallback is True


def test_resolve_custom_uses_routing_block():
    r = _FakeRouter(
        adapters={"openai": _FakeAdapter("openai", "gpt-x")},
        config={
            "routing": {
                "planning": {
                    "provider": "openai",
                    "account": "acc-x",
                    "model": "gpt-99",
                },
            }
        },
    )
    d = sr.resolve_custom(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.strategy_used == "custom"
    assert d.provider_id == "openai"
    assert d.account_id == "acc-x"
    assert d.model == "gpt-99"


def test_resolve_custom_default_phase_key():
    r = _FakeRouter(
        adapters={"copilot": _FakeAdapter("copilot", "")},
        config={"routing": {"default": {"provider": "copilot", "model": ""}}},
    )
    d = sr.resolve_custom(r, "unknown_phase", "normal", None, set(), set(), 0.0)
    assert d.provider_id == "copilot"


def test_resolve_custom_unavailable_provider_falls_back_to_balanced():
    r = _FakeRouter(
        adapters={
            "claude": _FakeAdapter("claude", "opus"),
            "openai": _FakeAdapter("openai", "gpt-x"),
        },
        tier_order=["claude", "openai"],
        config={"routing": {"planning": {"provider": "missing_provider"}}},
    )
    d = sr.resolve_custom(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.strategy_used == "balanced"
    r.logger.warning.assert_called()


def test_resolve_custom_model_cooldown_falls_back_to_balanced():
    r = _FakeRouter(
        adapters={
            "openai": _FakeAdapter("openai", "gpt-x"),
            "claude": _FakeAdapter("claude", "opus"),
        },
        tier_order=["claude", "openai"],
        config={"routing": {"planning": {"provider": "openai", "model": "bad"}}},
        cooldown={("openai", "bad")},
    )
    d = sr.resolve_custom(r, "planning", "normal", None, set(), set(), 0.0)
    assert d.strategy_used == "balanced"
