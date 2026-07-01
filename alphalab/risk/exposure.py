"""Immutable exposure tracking models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ExposureStatus:
    """Immutable snapshot of portfolio exposure distributions."""

    gross_exposure: Decimal = Decimal("0.00")
    net_exposure: Decimal = Decimal("0.00")
    long_exposure: Decimal = Decimal("0.00")
    short_exposure: Decimal = Decimal("0.00")
    asset_exposure: Mapping[str, Decimal] = field(default_factory=dict)
    sector_exposure: Mapping[str, Decimal] = field(default_factory=dict)

    @property
    def total_notional(self) -> Decimal:
        return self.gross_exposure
