"""Tests for aidlc.routing.context helpers not covered via ProviderRouter alone."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from aidlc.accounts.models import MembershipTier
from aidlc.routing import context
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
