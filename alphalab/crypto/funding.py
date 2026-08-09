"""Funding rate mechanics for perpetual instruments.

Perpetuals have no expiry, so instead of convergence at expiry, exchanges use
periodic funding payments between longs and shorts to keep the perpetual's price
anchored to the underlying index. By convention: a positive funding_rate means
longs pay shorts; a negative funding_rate means shorts pay longs.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.crypto.exceptions import CryptoInputError

_HOURS_PER_YEAR = 24 * 365


@dataclass(frozen=True, slots=True)
class FundingRate:
    """A single funding rate observation for one instrument.

    Attributes:
        instrument_symbol: The instrument this observation applies to, typically
            `crypto_symbol(instrument)`.
        rate: The funding rate for this interval, e.g. 0.0001 for 0.01%.
        timestamp: Unix timestamp this rate was observed/applied at.
        interval_hours: Hours between funding payments, 8 is the most common
            convention (00:00, 08:00, 16:00 UTC).
    """

    instrument_symbol: str
    rate: Decimal
    timestamp: float
    interval_hours: int = 8

    def __post_init__(self) -> None:
        if self.interval_hours <= 0:
            raise CryptoInputError(f"interval_hours must be positive, got {self.interval_hours}.")


@dataclass(frozen=True, slots=True)
class FundingRateHistory:
    """An immutable ordered series of funding rate observations for one instrument."""

    instrument_symbol: str
    rates: tuple[FundingRate, ...]


def compute_funding_payment(
    position_quantity: Decimal,
    mark_price: Decimal,
    funding_rate: Decimal,
    contract_size: Decimal = Decimal("1"),
) -> Decimal:
    """Computes the cash flow to a position holder for one funding interval.

    Returns a signed value: negative means the position holder pays, positive means
    they receive. A long position (positive quantity) with a positive funding_rate
    pays -- matching the standard convention that longs pay shorts when funding is
    positive.
    """
    notional = position_quantity * mark_price * contract_size
    return -(notional * funding_rate)


def average_funding_rate(history: FundingRateHistory) -> Decimal:
    """Computes the simple mean funding rate across all observations in a history.

    Raises:
        CryptoInputError: If history contains no observations.
    """
    if not history.rates:
        raise CryptoInputError("Cannot compute average funding rate from empty history.")
    return sum((r.rate for r in history.rates), Decimal("0")) / len(history.rates)


def annualized_funding_rate(history: FundingRateHistory) -> Decimal:
    """Annualizes the average funding rate based on each observation's interval.

    Assumes a uniform interval_hours across the history (the first observation's
    interval is used); raises if observations disagree, since mixing intervals
    would silently misrepresent the annualization.

    Raises:
        CryptoInputError: If history contains no observations, or observations use
            inconsistent interval_hours.
    """
    if not history.rates:
        raise CryptoInputError("Cannot annualize funding rate from empty history.")

    interval_hours = history.rates[0].interval_hours
    if any(r.interval_hours != interval_hours for r in history.rates):
        raise CryptoInputError(
            "annualized_funding_rate requires a uniform interval_hours across all observations."
        )

    payments_per_year = Decimal(_HOURS_PER_YEAR) / Decimal(interval_hours)
    return average_funding_rate(history) * payments_per_year
