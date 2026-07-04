"""Immutable models defining data streams."""

from dataclasses import dataclass
from enum import Enum, auto

from alphalab.marketdata.timeframe import Timeframe


class SubscriptionStatus(Enum):
    ACTIVE = auto()
    PAUSED = auto()
    REMOVED = auto()

@dataclass(frozen=True, slots=True)
class Subscription:
    subscription_id: str
    provider_id: str
    symbol: str
    timeframe: Timeframe
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE