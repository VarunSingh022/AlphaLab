"""Immutable subscription models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Subscription:
    """Immutable representation of a data stream subscription."""

    symbol: str
    feed_type: str
    active: bool
    timestamp: float
