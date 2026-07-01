"""Immutable margin tracking models."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MarginStatus:
    """Immutable snapshot of margin requirements and utilization."""

    initial_margin: Decimal = Decimal("0.00")
    maintenance_margin: Decimal = Decimal("0.00")
    available_margin: Decimal = Decimal("0.00")
    margin_used: Decimal = Decimal("0.00")

    @property
    def margin_remaining(self) -> Decimal:
        """Calculates remaining usable margin before violation."""
        return max(Decimal("0.00"), self.available_margin - self.margin_used)

    @property
    def utilization_pct(self) -> Decimal:
        """Calculates percentage of available margin currently used."""
        if self.available_margin == Decimal("0.00"):
            return Decimal("0.00")
        return (self.margin_used / self.available_margin).quantize(Decimal("0.0001"))
