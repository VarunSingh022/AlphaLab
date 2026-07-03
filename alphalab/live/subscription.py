"""Immutable models defining data stream subscriptions."""

from dataclasses import dataclass

from alphalab.live.provider import AssetClass


@dataclass(frozen=True, slots=True)
class Subscription:
    """Immutable record of an active data request to a provider."""

    provider_id: str
    symbol: str
    asset_class: AssetClass
    active: bool = True
