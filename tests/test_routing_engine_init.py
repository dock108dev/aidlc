"""ProviderRouter init and small execute_prompt branches."""

import logging
from unittest.mock import MagicMock, patch

from aidlc.routing.engine import ProviderRouter


def test_invalid_routing_strategy_logs_and_falls_back_to_balanced(caplog):
    caplog.set_level(logging.WARNING)
    router = ProviderRouter(
        {
            "routing_strategy": "not-a-real-strategy",
            "providers": {"claude": {"enabled": False}},
        },
        logging.getLogger("r.init"),
    )
    assert router._strategy.name == "BALANCED"
    assert "Unknown routing_strategy" in caplog.text


@patch("aidlc.routing.engine.build_provider_adapters")
def test_mark_used_swallows_account_manager_errors(mock_build, tmp_path):
    mock_build.return_value = {
        "openai": MagicMock(
            PROVIDER_ID="openai",
            execute_prompt=MagicMock(
                return_value={
                    "success": True,
                    "output": "ok",
                    "usage": {},
                    "failure_type": None,
                    "error": None,
                    "duration_seconds": 0.0,
                    "retries": 0,
                }
            ),
            check_available=MagicMock(return_value=True),
            get_default_model=MagicMock(return_value="m"),
        )
    }
    cfg = {
        "routing_strategy": "balanced",
        "providers": {"openai": {"enabled": True, "default_model": "m"}},
    }
    router = ProviderRouter(cfg, logging.getLogger("r.am"))
    mgr = MagicMock()
    mgr.mark_used.side_effect = RuntimeError("unavailable")
    router._account_manager = mgr
    with patch.object(router, "resolve") as mock_resolve:
        mock_resolve.return_value = MagicMock(
            provider_id="openai",
            account_id="acc-1",
            model="m",
            reasoning="r",
            strategy_used="balanced",
            fallback=False,
            tier="t",
            quality_note="q",
            adapter=router._adapters["openai"],
        )
        router.execute_prompt("hi", tmp_path)
    mgr.mark_used.assert_called_once_with("acc-1")
