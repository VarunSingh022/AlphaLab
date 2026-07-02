"""Portfolio exposure calculations."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ExposureMetrics:
    """Immutable record of portfolio risk exposures."""

    gross: Decimal
    net: Decimal
    long: Decimal
    short: Decimal
    cash_pct: float
    leverage: float


def calculate_exposure(long_val: Decimal, short_val: Decimal, cash: Decimal) -> ExposureMetrics:
    """Computes standard exposure distributions and ratios."""
    gross = long_val + abs(short_val)
    net = long_val - abs(short_val)
    total_equity = cash + net

    if total_equity <= Decimal("0.00"):
        return ExposureMetrics(gross, net, long_val, short_val, 0.0, 0.0)

    cash_pct = float(cash / total_equity)
    leverage = float(gross / total_equity)

    return ExposureMetrics(
        gross=gross,
        net=net,
        long=long_val,
        short=short_val,
        cash_pct=cash_pct,
        leverage=leverage,
    )
