"""Account data models for AIDLC multi-account management."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AuthState(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class MembershipTier(Enum):
    """Membership/subscription tier of the account.

    Used by the routing engine to decide when to use premium vs. standard accounts.
    """

    FREE = "free"
    STANDARD = "standard"  # e.g. $20/mo plan
    PRO = "pro"  # e.g. $100+/mo plan
    PREMIUM = "premium"  # e.g. $200+/mo or enterprise
    API = "api"  # pay-as-you-go API key
    UNKNOWN = "unknown"


# Role tags that can be assigned to accounts
ROLE_PRIMARY = "primary"
ROLE_BACKUP = "backup"
ROLE_OVERFLOW = "overflow"
ROLE_PREMIUM = "premium"
ROLE_RESERVE = "reserve"
ROLE_CHEAP = "cheap"
ROLE_EXPERIMENTAL = "experimental"

ALL_ROLE_TAGS = {
    ROLE_PRIMARY,
    ROLE_BACKUP,
    ROLE_OVERFLOW,
    ROLE_PREMIUM,
    ROLE_RESERVE,
    ROLE_CHEAP,
    ROLE_EXPERIMENTAL,
}


@dataclass
class Account:
    """A managed provider account with metadata for routing decisions."""

    account_id: str
    provider_id: str
    display_name: str = ""
    auth_state: AuthState = AuthState.UNKNOWN
    health_status: str = "unknown"  # from HealthStatus.value
    membership_tier: MembershipTier = MembershipTier.UNKNOWN
    role_tags: list[str] = field(default_factory=list)
    enabled: bool = True
    last_validated: Optional[str] = None
    last_used: Optional[str] = None
    notes: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def is_premium(self) -> bool:
        """True if this account is tagged as premium or reserve."""
        return any(t in self.role_tags for t in (ROLE_PREMIUM, ROLE_RESERVE))

    @property
    def tier_weight(self) -> int:
        """Numeric weight for routing comparisons. Higher = better/more expensive."""
        weights = {
            MembershipTier.FREE: 0,
            MembershipTier.STANDARD: 1,
            MembershipTier.PRO: 2,
            MembershipTier.PREMIUM: 3,
            MembershipTier.API: 2,
            MembershipTier.UNKNOWN: 1,
        }
        return weights.get(self.membership_tier, 1)

    @property
    def is_usable(self) -> bool:
        return self.enabled and self.auth_state in (
            AuthState.CONNECTED,
            AuthState.UNKNOWN,
        )

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "auth_state": self.auth_state.value,
            "health_status": self.health_status,
            "membership_tier": self.membership_tier.value,
            "role_tags": self.role_tags,
            "enabled": self.enabled,
            "last_validated": self.last_validated,
            "last_used": self.last_used,
            "notes": self.notes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        account = cls(
            account_id=data["account_id"],
            provider_id=data["provider_id"],
            display_name=data.get("display_name", ""),
        )
        try:
            account.auth_state = AuthState(data.get("auth_state", "unknown"))
        except ValueError:
            account.auth_state = AuthState.UNKNOWN
        account.health_status = data.get("health_status", "unknown")
        try:
            account.membership_tier = MembershipTier(data.get("membership_tier", "unknown"))
        except ValueError:
            account.membership_tier = MembershipTier.UNKNOWN
        account.role_tags = data.get("role_tags", [])
        account.enabled = data.get("enabled", True)
        account.last_validated = data.get("last_validated")
        account.last_used = data.get("last_used")
        account.notes = data.get("notes", "")
        account.metadata = data.get("metadata", {})
        return account
