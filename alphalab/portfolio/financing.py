"""Interest on a cash balance, by declared tiers and day count (ledger FUT-018).

A broker charges interest on a debit balance -- what a financed delivery, a
covered short or a margin purchase leaves the account owing -- and may pay it on
a credit balance. The rate is the broker's: tiered by the size of the balance,
by currency, changing over time. Nothing here supplies one. A
:class:`~alphalab.backtesting.schedule.ScheduledInterest` states, for one
currency and one period, the tiers and day count in force, and the run books
what they give on the balance at the period's end as an ``INTEREST`` cash flow:
a debit pays, a credit receives, nothing for a balance of zero.

The period's balance is the account's trade-date cash or, by declaration, its
settled cash (ledger FUT-015) -- a broker that finances settled balances charges
nothing on a sale's unreceived proceeds until they arrive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum, auto
from itertools import pairwise

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.conventions.daycount import DayCount, day_count_days
from alphalab.portfolio.exceptions import InvalidTransactionError

__all__ = ["BalanceBasis", "FinancingTier", "accrued_interest", "day_fraction", "require_tiers"]

_ZERO = Decimal("0")


class BalanceBasis(Enum):
    """Which balance a period's interest is charged on."""

    #: The cash ledger's balance: every fill counted at its trade.
    TRADE_DATE = auto()
    #: What has settled: trade-date cash undone by what has not (FUT-015).
    SETTLED = auto()


@dataclass(frozen=True, slots=True)
class FinancingTier:
    """One band of a tiered debit rate.

    Attributes:
        up_to: The debit, in the period's currency, up to which this rate
            applies -- the band runs from the previous tier's ``up_to`` -- or
            ``None`` for the last band, which has no ceiling.
        annual_rate: The rate charged on the debit within the band, a fraction
            a year; at least zero.
    """

    up_to: Decimal | None
    annual_rate: Decimal

    def __post_init__(self) -> None:
        if self.up_to is not None and (
            not isinstance(self.up_to, Decimal) or not self.up_to.is_finite() or self.up_to <= 0
        ):
            raise InvalidTransactionError(
                f"A tier's ceiling is a positive Decimal or None, got {self.up_to!r}."
            )
        if (
            not isinstance(self.annual_rate, Decimal)
            or not self.annual_rate.is_finite()
            or self.annual_rate < 0
        ):
            raise InvalidTransactionError(
                f"A financing rate is a non-negative Decimal, got {self.annual_rate!r}."
            )


def require_tiers(tiers: tuple[FinancingTier, ...]) -> tuple[FinancingTier, ...]:
    """``tiers`` checked: at least one, ceilings increasing, only the last unbounded."""

    tiers = tuple(tiers)
    if not tiers or not all(isinstance(t, FinancingTier) for t in tiers):
        raise InvalidTransactionError("A financing schedule declares at least one FinancingTier.")
    if tiers[-1].up_to is not None:
        raise InvalidTransactionError(
            "The last tier has no ceiling (up_to=None): a debit above every declared ceiling "
            "would otherwise be charged at a rate nobody stated."
        )
    ceilings = [t.up_to for t in tiers[:-1] if t.up_to is not None]
    if len(ceilings) != len(tiers) - 1 or any(b <= a for a, b in pairwise(ceilings)):
        raise InvalidTransactionError("Tier ceilings are declared and strictly increasing.")
    return tiers


def day_fraction(start: date, end: date, basis: DayCount) -> Decimal:
    """``end - start`` as an exact fraction of a year under ``basis``.

    Raises:
        InvalidTransactionError: If ``end`` is not after ``start``.
    """

    days = day_count_days(start, end, basis)
    if days <= 0:
        raise InvalidTransactionError(
            f"An accrual period runs forward: {start} to {end} counts {days} days."
        )
    denominator = (
        Decimal(360) if basis in (DayCount.ACT_360, DayCount.THIRTY_360_US) else Decimal(365)
    )
    return ACCOUNTING_CONTEXT.divide(Decimal(days), denominator)


def accrued_interest(
    balance: Decimal,
    tiers: tuple[FinancingTier, ...],
    credit_rate: Decimal | None,
    fraction: Decimal,
) -> Decimal:
    """Interest on ``balance`` for ``fraction`` of a year: negative paid, positive received.

    A debit (a negative balance) is charged band by band; a credit earns
    ``credit_rate`` when one is declared, and nothing otherwise.
    """

    ctx = ACCOUNTING_CONTEXT
    if balance > 0:
        if credit_rate is None:
            return _ZERO
        return ctx.multiply(ctx.multiply(balance, credit_rate), fraction)
    debit = balance.copy_abs()
    charged = _ZERO
    floor = _ZERO
    for tier in tiers:
        ceiling = debit if tier.up_to is None else min(debit, tier.up_to)
        if ceiling > floor:
            charged = ctx.add(charged, ctx.multiply(ctx.subtract(ceiling, floor), tier.annual_rate))
        if tier.up_to is None or debit <= tier.up_to:
            break
        floor = tier.up_to
    return -ctx.multiply(charged, fraction)
