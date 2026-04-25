"""Construct provider adapter instances from resolved config."""

from __future__ import annotations

import logging

from ..providers.base import ProviderAdapter
from ..providers.claude_adapter import ClaudeCLIAdapter
from ..providers.copilot_adapter import CopilotAdapter
from ..providers.openai_adapter import OpenAIAdapter


def build_provider_adapters(
    config: dict, logger: logging.Logger
) -> dict[str, ProviderAdapter]:
    """Instantiate all configured provider adapters (respects ``enabled`` flags)."""
    providers_cfg = config.get("providers", {})
    adapters: dict[str, ProviderAdapter] = {}

    if isinstance(providers_cfg, dict):
        if providers_cfg.get("claude", {}).get("enabled", True):
            adapters["claude"] = ClaudeCLIAdapter(config, logger)
        if providers_cfg.get("copilot", {}).get("enabled", False):
            adapters["copilot"] = CopilotAdapter(config, logger)
        if providers_cfg.get("openai", {}).get("enabled", False):
            adapters["openai"] = OpenAIAdapter(config, logger)
    else:
        adapters["claude"] = ClaudeCLIAdapter(config, logger)

    return adapters
