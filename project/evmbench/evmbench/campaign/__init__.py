"""Campaign configuration, durable state, coordinator, and CLI."""

from .state import (
    BudgetSpec,
    CampaignConfig,
    CampaignStatus,
    CampaignStore,
    ConfigError,
    StageKey,
    new_campaign_id,
)

__all__ = [
    "BudgetSpec",
    "CampaignConfig",
    "CampaignStatus",
    "CampaignStore",
    "ConfigError",
    "StageKey",
    "new_campaign_id",
]
