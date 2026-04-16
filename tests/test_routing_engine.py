"""Tests for token exhaustion fallback behavior in ProviderRouter."""

import logging
from pathlib import Path

from aidlc.routing.engine import ProviderRouter


class FakeAdapter:
    def __init__(self, provider_id: str, results: list[dict], default_model: str):
        self.PROVIDER_ID = provider_id
        self._results = list(results)
        self._default_model = default_model

    def check_available(self) -> bool:
        return True

    def execute_prompt(
        self,
        prompt: str,
        working_dir: Path,
        allow_edits: bool = False,
        model_override: str | None = None,
        account_id: str | None = None,
    ) -> dict:
        if self._results:
            return self._results.pop(0)
        return {
            "success": False,
            "output": None,
            "error": "no fake result configured",
            "failure_type": "issue",
            "duration_seconds": 0.0,
            "retries": 0,
            "usage": {},
            "total_cost_usd": None,
            "model_used": model_override or self._default_model,
            "usage_source": "none",
        }

    def get_default_model(self, phase: str | None = None) -> str:
        return self._default_model


def _config() -> dict:
    return {
        "routing_strategy": "balanced",
        "providers": {
            "claude": {"enabled": False},
            "openai": {
                "enabled": True,
                "default_model": "gpt-5.4-mini",
                "phase_models": {"default": "gpt-5.4-mini"},
            },
            "copilot": {
                "enabled": True,
                "default_model": "",
                "phase_models": {"default": ""},
            },
        },
    }


def test_token_exhaustion_falls_back_to_other_provider(tmp_path):
    router = ProviderRouter(_config(), logging.getLogger("test.router.fallback"))
    router._adapters = {
        "openai": FakeAdapter(
            "openai",
            [
                {
                    "success": False,
                    "output": None,
                    "error": "insufficient quota",
                    "failure_type": "quota_exceeded",
                    "duration_seconds": 0.0,
                    "retries": 0,
                    "usage": {},
                    "total_cost_usd": None,
                    "model_used": "gpt-5.4-mini",
                    "usage_source": "none",
                }
            ],
            default_model="gpt-5.4-mini",
        ),
        "copilot": FakeAdapter(
            "copilot",
            [
                {
                    "success": True,
                    "output": "ok",
                    "error": None,
                    "failure_type": None,
                    "duration_seconds": 0.0,
                    "retries": 0,
                    "usage": {},
                    "total_cost_usd": None,
                    "model_used": "default",
                    "usage_source": "none",
                }
            ],
            default_model="",
        ),
    }
    router._session_budget_provider = "openai"

    result = router.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    assert result["provider_id"] == "copilot"


def test_all_token_exhausted_returns_terminal_failure(tmp_path):
    router = ProviderRouter(_config(), logging.getLogger("test.router.exhausted"))
    exhausted = {
        "success": False,
        "output": None,
        "error": "out of tokens",
        "failure_type": "token_exhausted",
        "duration_seconds": 0.0,
        "retries": 0,
        "usage": {},
        "total_cost_usd": None,
        "model_used": "default",
        "usage_source": "none",
    }
    router._adapters = {
        "openai": FakeAdapter("openai", [dict(exhausted)], default_model="gpt-5.4-mini"),
        "copilot": FakeAdapter("copilot", [dict(exhausted)], default_model=""),
    }
    router._session_budget_provider = "openai"

    result = router.execute_prompt("hello", tmp_path)

    assert result["success"] is False
    assert result["failure_type"] == "token_exhausted_all_models"
    assert "out of tokens" in result["error"].lower() or "quota" in result["error"].lower()


def test_rate_limit_falls_back_to_other_provider(tmp_path):
    router = ProviderRouter(_config(), logging.getLogger("test.router.rate_limit_fallback"))
    router._adapters = {
        "openai": FakeAdapter(
            "openai",
            [
                {
                    "success": False,
                    "output": None,
                    "error": "429 too many requests",
                    "failure_type": "transient",
                    "duration_seconds": 0.0,
                    "retries": 0,
                    "usage": {},
                    "total_cost_usd": None,
                    "model_used": "gpt-5.4-mini",
                    "usage_source": "none",
                }
            ],
            default_model="gpt-5.4-mini",
        ),
        "copilot": FakeAdapter(
            "copilot",
            [
                {
                    "success": True,
                    "output": "ok",
                    "error": None,
                    "failure_type": None,
                    "duration_seconds": 0.0,
                    "retries": 0,
                    "usage": {},
                    "total_cost_usd": None,
                    "model_used": "default",
                    "usage_source": "none",
                }
            ],
            default_model="",
        ),
    }
    router._session_budget_provider = "openai"

    result = router.execute_prompt("hello", tmp_path)

    assert result["success"] is True
    assert result["provider_id"] == "copilot"


def test_all_rate_limited_returns_terminal_failure(tmp_path):
    router = ProviderRouter(_config(), logging.getLogger("test.router.rate_limited_all"))
    limited = {
        "success": False,
        "output": None,
        "error": "rate limit exceeded (429)",
        "failure_type": "transient",
        "duration_seconds": 0.0,
        "retries": 0,
        "usage": {},
        "total_cost_usd": None,
        "model_used": "default",
        "usage_source": "none",
    }
    router._adapters = {
        "openai": FakeAdapter("openai", [dict(limited)], default_model="gpt-5.4-mini"),
        "copilot": FakeAdapter("copilot", [dict(limited)], default_model=""),
    }
    router._session_budget_provider = "openai"

    result = router.execute_prompt("hello", tmp_path)

    assert result["success"] is False
    assert result["failure_type"] == "rate_limited_all_models"
    assert "rate limited" in result["error"].lower()


def test_balanced_budget_routing_uses_pressure_not_single_provider(tmp_path):
    router = ProviderRouter(_config(), logging.getLogger("test.router.pressure_balance"))
    router._adapters = {
        "openai": FakeAdapter(
            "openai",
            [
                {
                    "success": True,
                    "output": "ok-openai-1",
                    "error": None,
                    "failure_type": None,
                    "duration_seconds": 0.0,
                    "retries": 0,
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                    "total_cost_usd": None,
                    "model_used": "gpt-5.4-mini",
                    "usage_source": "none",
                }
            ],
            default_model="gpt-5.4-mini",
        ),
        "copilot": FakeAdapter(
            "copilot",
            [
                {
                    "success": True,
                    "output": "ok-copilot-1",
                    "error": None,
                    "failure_type": None,
                    "duration_seconds": 0.0,
                    "retries": 0,
                    "usage": {"input_tokens": 90, "output_tokens": 10},
                    "total_cost_usd": None,
                    "model_used": "default",
                    "usage_source": "none",
                }
            ],
            default_model="",
        ),
    }
    router._session_budget_provider = "openai"
    router.set_phase("implementation")
    router.set_complexity("normal")

    first = router.execute_prompt("first", tmp_path)
    second = router.execute_prompt("second", tmp_path)

    assert first["success"] is True
    assert second["success"] is True
    assert first["provider_id"] == "openai"
    assert second["provider_id"] == "copilot"


def test_usage_limit_phrase_is_treated_as_rate_limited():
    result = {
        "success": False,
        "error": (
            "You've hit your usage limit. Upgrade to Pro, visit settings/usage "
            "to purchase more credits or try again at 8:55 PM."
        ),
        "output": None,
        "failure_type": "issue",
    }

    assert ProviderRouter._is_rate_limited_result(result) is True


def test_restore_time_parses_try_again_at_clock_time():
    result = {
        "success": False,
        "error": "You've hit your usage limit. Please try again at 8:55 PM.",
        "output": None,
        "failure_type": "issue",
    }

    restore = ProviderRouter._extract_restore_time_epoch(result)

    assert restore is not None
    assert restore > 0
