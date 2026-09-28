"""Immutable margin tracking models."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT


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
    def utilization_pct(self) -> Decimal | None:
        """The fraction of available margin in use, or ``None`` when none is available.

        Undefined -- not zero -- with no positive margin base: until v3.10 it
        read ``0.00`` there, and a gate reading it passed every order.
        """
        if self.available_margin <= Decimal("0"):
            return None
        return ACCOUNTING_CONTEXT.divide(self.margin_used, self.available_margin)
