"""Cover routing.helpers branch logic (premium phase detection)."""

from aidlc.routing import helpers


def test_is_premium_phase_implementation_complex():
    assert helpers.is_premium_phase("implementation_complex", "normal") is True


def test_is_premium_phase_implementation_when_complex():
    assert helpers.is_premium_phase("implementation", "complex") is True


def test_is_premium_phase_quality_sensitive_when_complex():
    assert helpers.is_premium_phase("planning", "complex") is True


def test_is_premium_phase_not_premium_cases():
    assert helpers.is_premium_phase("implementation", "normal") is False
    assert helpers.is_premium_phase("planning", "normal") is False
