"""Routing types shared by the provider router and strategy resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..providers.base import ProviderAdapter


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
    fallback: bool = False
    tier: str = "budget"
    quality_note: Optional[str] = None


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
        if self.total_calls == 0:
            return 0.0
        return self.calls_by_account.get(account_id, 0) / self.total_calls
