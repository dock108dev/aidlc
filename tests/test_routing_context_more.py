"""Tests for aidlc.routing.context helpers not covered via ProviderRouter alone."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from aidlc.accounts.models import MembershipTier
from aidlc.routing import context, helpers
from aidlc.routing.types import UsagePressure

from tests.test_routing_engine import FakeAdapter


def _fake(provider_id: str, default_model: str) -> FakeAdapter:
    return FakeAdapter(provider_id, [], default_model)


def test_select_account_empty_list():
    aid, reason = context.select_account(UsagePressure(), [], "openai", False)
    assert aid is None
    assert "no accounts" in reason


def test_select_account_no_usable():
    bad = SimpleNamespace(account_id="x", is_usable=False)
    aid, reason = context.select_account(UsagePressure(), [bad], "openai", True)
    assert aid is None
    assert "no usable" in reason


def test_select_account_quality_phase_includes_tier_in_reason():
    acc = SimpleNamespace(
        account_id="a1",
        is_usable=True,
        is_premium=False,
        membership_tier=MembershipTier.PRO,
    )
    aid, reason = context.select_account(UsagePressure(), [acc], "openai", True)
    assert aid == "a1"
    assert "pro" in reason


def test_resolve_model_for_phase_maps_implementation_complex():
    cfg = {
        "providers": {
            "openai": {
                "phase_models": {
                    "implementation_complex": "gpt-99",
                    "default": "gpt-1",
                }
            }
        }
    }
    ad = _fake("openai", "fallback")
    m = context.resolve_model_for_phase(cfg, ad, "implementation", "complex")
    assert m == "gpt-99"


def test_fallback_decision_no_adapters_instantiates_claude():
    logger = logging.getLogger("test.ctx.fallback")
    d = context.fallback_decision(
        adapters={},
        config={"providers": {"claude": {"enabled": True}}},
        logger=logger,
        phase="planning",
        complexity_level="normal",
        model_override=None,
        excluded_providers=set(),
        excluded_models=set(),
        now=0.0,
        model_on_cooldown=lambda *_a, **_k: False,
    )
    assert d.provider_id == "claude"
    assert d.fallback is True


def test_tier_aware_provider_order_implementation_prefers_premium_tag():
    cfg = {
        "routing_impl_budget_explore_probability": 0.0,
        "providers": {
            "claude": {"enabled": True, "max_capacity": True, "max_capacity_weight": 20},
            "openai": {"enabled": True, "max_capacity": False},
        },
    }
    order = context.tier_aware_provider_order(
        cfg,
        {"claude", "openai"},
        UsagePressure(),
        None,
        "implementation",
        "normal",
    )
    assert order[0] == "claude"
    assert "openai" in order


def test_tier_aware_impl_budget_explore_reorders_when_probability_one():
    cfg = {
        "routing_impl_budget_explore_probability": 1.0,
        "providers": {
            "claude": {"enabled": True, "max_capacity": True},
            "openai": {"enabled": True},
            "copilot": {"enabled": True},
        },
    }
    order = context.tier_aware_provider_order(
        cfg,
        {"claude", "openai", "copilot"},
        UsagePressure(),
        None,
        "implementation",
        "normal",
    )
    assert order[0] in helpers.get_budget_providers()
    assert order.index("claude") > order.index(order[0])


def test_tier_aware_impl_explore_random(monkeypatch):
    cfg = {
        "routing_impl_budget_explore_probability": 0.05,
        "providers": {
            "claude": {"enabled": True, "max_capacity": True},
            "openai": {"enabled": True},
        },
    }
    adapters = {"claude", "openai"}

    monkeypatch.setattr("aidlc.routing.context.random.random", lambda: 0.001)
    order_hit = context.tier_aware_provider_order(
        cfg, adapters, UsagePressure(), None, "implementation", "normal"
    )
    assert order_hit[0] == "openai"

    monkeypatch.setattr("aidlc.routing.context.random.random", lambda: 0.99)
    order_miss = context.tier_aware_provider_order(
        cfg, adapters, UsagePressure(), None, "implementation", "normal"
    )
    assert order_miss[0] == "claude"


def test_tier_aware_provider_order_non_impl_uses_weighted_fairness():
    cfg = {
        "providers": {
            "claude": {"enabled": True, "max_capacity": True, "max_capacity_weight": 20},
            "openai": {"enabled": True},
        }
    }
    usage = UsagePressure()
    usage.calls_by_provider["claude"] = 19
    usage.calls_by_provider["openai"] = 1
    order = context.tier_aware_provider_order(
        cfg,
        {"claude", "openai"},
        usage,
        None,
        "planning",
        "normal",
    )
    # 19/20 < 1/1 → claude still preferred first
    assert order[0] == "claude"

    usage2 = UsagePressure()
    usage2.calls_by_provider["claude"] = 20
    usage2.calls_by_provider["openai"] = 0
    order2 = context.tier_aware_provider_order(
        cfg,
        {"claude", "openai"},
        usage2,
        "openai",
        "planning",
        "normal",
    )
    assert order2[0] == "openai"


def test_old_premium_provider_keys_no_longer_affect_routing():
    """SSOT: only ``max_capacity`` / ``max_capacity_weight`` apply (``premium`` is ignored)."""
    cfg = {"providers": {"openai": {"premium": True, "premium_capacity_weight": 99}}}
    assert context.provider_max_capacity_tagged(cfg, "openai") is False
    assert context.provider_max_capacity_weight(cfg, "openai") == 1.0


def test_provider_max_capacity_weight_explicit():
    cfg = {"providers": {"openai": {"max_capacity": True, "max_capacity_weight": 10}}}
    assert context.provider_max_capacity_tagged(cfg, "openai") is True
    assert context.provider_max_capacity_weight(cfg, "openai") == 10.0


def test_provider_max_capacity_weight_defaults():
    assert context.provider_max_capacity_weight({"providers": {"openai": {}}}, "openai") == 1.0
    assert (
        context.provider_max_capacity_weight(
            {"providers": {"openai": {"max_capacity": True}}},
            "openai",
        )
        == 20.0
    )


def test_fallback_decision_skips_excluded_in_first_pass():
    a1 = _fake("openai", "m1")
    a2 = _fake("copilot", "")
    d = context.fallback_decision(
        adapters={"openai": a1, "copilot": a2},
        config={},
        logger=logging.getLogger("t"),
        phase="planning",
        complexity_level="normal",
        model_override=None,
        excluded_providers={"openai"},
        excluded_models=set(),
        now=0.0,
        model_on_cooldown=lambda *_a, **_k: False,
    )
    assert d.provider_id == "copilot"
