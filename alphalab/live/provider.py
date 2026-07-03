"""Immutable models defining external market data vendors."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto


class ProviderStatus(Enum):
    """Lifecycle states for a market data provider configuration."""

    ACTIVE = auto()
    INACTIVE = auto()


class AssetClass(Enum):
    """Standardized asset classifications."""

    EQUITY = auto()
    ETF = auto()
    FUTURE = auto()
    OPTION = auto()
    FOREX = auto()
    CRYPTO = auto()


@dataclass(frozen=True, slots=True)
class Provider:
    """Immutable representation of a market data provider configuration."""

    provider_id: str
    name: str
    vendor: str
    asset_classes: frozenset[AssetClass]
    status: ProviderStatus = ProviderStatus.ACTIVE
    metadata: Mapping[str, str] = field(default_factory=dict)
