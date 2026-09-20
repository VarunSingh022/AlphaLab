"""Funding rate mechanics for perpetual instruments.

Perpetuals have no expiry, so instead of convergence at expiry, exchanges use
periodic funding payments between longs and shorts to keep the perpetual's price
anchored to the underlying index. By convention: a positive funding_rate means
longs pay shorts; a negative funding_rate means shorts pay longs.
"""

from collections.abc import Mapping
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
        interval_hours: Hours between funding payments. **Required** as of
            v3.4, having defaulted to ``8``. Eight hours is the most common
            convention and it is a *venue's* convention, not a universal: some
            venues fund hourly, some every four hours, and a venue may change
            its interval for one contract. The interval is what
            :func:`annualized_funding_rate` scales by, so a wrong one is wrong
            by that ratio and looks like a rate rather than an error.
    """

    instrument_symbol: str
    rate: Decimal
    timestamp: float
    interval_hours: int

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
    contract_size: Decimal,
) -> Decimal:
    """Computes the cash flow to a position holder for one funding interval.

    Returns a signed value: negative means the position holder pays, positive means
    they receive. A long position (positive quantity) with a positive funding_rate
    pays -- matching the standard convention that longs pay shorts when funding is
    positive.

    ``contract_size`` is **required** as of v3.4, having defaulted to ``1``. It
    is :attr:`CryptoInstrument.contract_size` and it scales the notional the
    payment is computed on: an inverse contract sized in quote currency and a
    linear one sized in base currency give answers that differ by the mark
    price. The default was right for spot and silently wrong for every sized
    contract.
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


@dataclass(frozen=True, slots=True)
class FundingAccrual:
    """One funding payment a position actually incurred.

    Attributes:
        timestamp: When it applied.
        rate: The rate charged for that interval.
        mark_price: The mark the notional was computed on, supplied rather than
            derived -- a funding payment is charged on the venue's mark, not on
            the last trade, and `alphalab.crypto.perpetual` exists to make that
            hard to get wrong.
        notional: ``quantity * mark_price * contract_size``, signed.
        payment: Cash flow to the holder, signed: negative means they paid.
    """

    timestamp: float
    rate: Decimal
    mark_price: Decimal
    notional: Decimal
    payment: Decimal


@dataclass(frozen=True, slots=True)
class FundingSummary:
    """Every accrual over a period, and what they came to.

    Attributes:
        instrument_symbol: The instrument.
        accruals: One per funding instant, in order.
        total: Sum of the payments, signed. In the quote asset -- the same
            currency the marks are in, which is the only currency these can be
            added in.
        intervals: How many funding instants were charged.
    """

    instrument_symbol: str
    accruals: tuple[FundingAccrual, ...]
    total: Decimal
    intervals: int


def funding_instants(
    start: float, end: float, interval_hours: int, anchor: float
) -> tuple[float, ...]:
    """When funding applies in ``[start, end)``, given a venue's schedule.

    ``anchor`` is one instant the venue is known to have funded at, and every
    other instant is that one plus a whole number of intervals. It is required
    and has no default: eight-hourly funding at 00:00/08:00/16:00 UTC is
    an eight-hourly venue schedule, and a venue funding at 01:00/09:00/17:00 on the same
    interval produces an entirely different set of instants. Assuming an anchor
    would charge funding at times the venue did not.

    The window is half-open, matching every other interval in AlphaLab.

    Raises:
        CryptoInputError: If the interval is not positive, or ``end`` precedes
            ``start``.
    """

    if interval_hours <= 0:
        raise CryptoInputError(f"interval_hours must be positive, got {interval_hours}.")
    if end < start:
        raise CryptoInputError(f"end {end} precedes start {start}; the window runs forwards.")

    step = interval_hours * 3600.0
    # Whole steps from the anchor to the first instant at or after ``start``.
    steps = -(-(start - anchor) // step)
    instants: list[float] = []
    cursor = anchor + steps * step
    while cursor < end:
        instants.append(cursor)
        cursor += step
    return tuple(instants)


def accrued_funding(
    history: FundingRateHistory,
    position_quantity: Decimal,
    marks: Mapping[float, Decimal],
    contract_size: Decimal,
) -> FundingSummary:
    """What a constant position paid or received over a funding history.

    Args:
        history: The observed rates. Each one's ``timestamp`` is a funding
            instant.
        position_quantity: Signed contract count, held constant across the
            period. A position that changed size is several calls, one per
            constant stretch -- pretending otherwise would charge the wrong
            notional at every instant after the change.
        marks: Mark price at each funding instant, keyed by timestamp. Every
            instant in ``history`` must appear.
        contract_size: Units of the base asset per contract, from
            :attr:`alphalab.crypto.instrument.CryptoInstrument.contract_size`.
            Required for the reason that field is.

    Raises:
        CryptoInputError: If a funding instant has no mark. Carrying the
            previous mark forward would compute a real cash flow from a price
            the venue never marked at, and the error would be invisible in the
            total.
    """

    accruals: list[FundingAccrual] = []
    for observation in history.rates:
        mark = marks.get(observation.timestamp)
        if mark is None:
            raise CryptoInputError(
                f"{history.instrument_symbol} funded at {observation.timestamp} and no mark "
                "price was supplied for that instant. Funding is charged on the venue's "
                "mark, so carrying an earlier one forward would charge a real payment "
                "against a price that was never marked."
            )
        notional = position_quantity * mark * contract_size
        accruals.append(
            FundingAccrual(
                timestamp=observation.timestamp,
                rate=observation.rate,
                mark_price=mark,
                notional=notional,
                payment=-(notional * observation.rate),
            )
        )
    return FundingSummary(
        instrument_symbol=history.instrument_symbol,
        accruals=tuple(accruals),
        total=sum((accrual.payment for accrual in accruals), Decimal("0")),
        intervals=len(accruals),
    )
