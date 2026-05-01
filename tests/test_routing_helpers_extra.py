"""SSOT enforcement: legacy 'premium phase' helpers must stay removed."""

from aidlc.routing import helpers


def test_legacy_premium_phase_helpers_are_absent():
    """SSOT: provider tier preference is driven by ``providers.<id>.max_capacity``,
    not by hard-coded 'premium phases'. Reintroducing these helpers would
    resurrect the legacy Claude-first routing branch."""
    assert not hasattr(helpers, "get_premium_phases")
    assert not hasattr(helpers, "is_premium_phase")


def test_routed_model_from_result_prefers_routing_decision():
    from aidlc.routing.helpers import routed_model_from_result

    assert (
        routed_model_from_result({"routing_decision": {"model": "opus"}, "model_used": "sonnet"})
        == "opus"
    )
    assert routed_model_from_result({"model_used": "haiku"}) == "haiku"
    assert routed_model_from_result({"model_used": "unknown"}) is None
    assert routed_model_from_result(None) is None
