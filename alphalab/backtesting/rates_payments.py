"""A rates instrument's payments on a run's schedule, from its declared terms (ADR-0065 s. 5).

A rates position books its cash flows on their payment dates, in its currency,
by its terms. :func:`rates_payments` turns one record's terms into the
schedule's own entries, which the run books as it books any other:

* **Coupons** are a :class:`~alphalab.backtesting.schedule.ScheduledDistribution`
  per unit held, ``INTEREST``, at the payment instant: received on a long, paid
  on a short.
  - a swap's fixed coupon less its floating coupon, netted where both legs
    pay on one date, as a swap's payments are, so a receiver pays when the
    floating coupon is larger;
  - a floater's fixing plus its spread;
  - a linker's real coupon scaled by its index ratio.
* **The final payment** is a :class:`~alphalab.backtesting.schedule.ScheduledSettlement`
  closing every position at the price its terms state:
  - an FRA at its settlement, on its start;
  - a repo at its repayment, on its end;
  - a floater at its redemption and a linker at its indexed principal, at
    maturity;
  - a swap at nothing, once its last coupon is paid.

  The price is per unit of the record's quoted price, so a stated amount per
  unit held is divided by the record's multiplier.

Fixings and prints arrive as published observations
---------------------------------------------------
Each :class:`PublishedFixing` and :class:`PublishedPrint` carries the instant it
was published. A payment reads only what was published by its own instant, the
latest when a value was republished. A fixing or a print a payment needs that
is not supplied, or was published only after the payment, is refused when the
schedule is built: never assumed, and never read early.

When a payment is booked
------------------------
A payment's instant is its date at a stated time of day on a stated zone's
clock (:class:`PaymentTime`). Only payments from ``start`` to ``end`` are
placed, so a run over part of an instrument's life needs only that part's
fixings. A record that still trades after its final payment is refused, since
its settlement would close a position that could still be traded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from alphalab.backtesting.schedule import (
    ScheduledDistribution,
    ScheduledSettlement,
    ScheduleValidationError,
)
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.conventions.business_days import subtract_business_days
from alphalab.conventions.exceptions import ConventionError
from alphalab.conventions.settlement import TradingDayCalendar
from alphalab.data.exceptions import DataValidationError
from alphalab.data.time import resolve_zone
from alphalab.instrument.economics import economics_for
from alphalab.instrument.record import InstrumentRecord
from alphalab.portfolio.corporate_actions import CashFlowKind, ContractSettlement
from alphalab.rates import (
    CouponPeriod,
    FloaterTerms,
    FraTerms,
    LinkerTerms,
    RatesError,
    RepoTerms,
    SwapTerms,
    fra_settlement,
    leg_periods,
    linker_principal,
    reference_index,
    repo_repayment,
    resolve_calendars,
)

__all__ = [
    "PaymentTime",
    "PublishedFixing",
    "PublishedPrint",
    "RatesPayments",
    "rates_payments",
]


@dataclass(frozen=True, slots=True)
class PublishedFixing:
    """A rate fixing as it was published.

    Attributes:
        index: The index's name, as a floating leg names it.
        fixed_on: The day the rate is fixed for.
        value: The rate, a fraction a year.
        at: The instant it was published: a payment reads it from then on.
    """

    index: str
    fixed_on: date
    value: Decimal
    at: float


@dataclass(frozen=True, slots=True)
class PublishedPrint:
    """An index's monthly print as it was published.

    Attributes:
        index: The index's name, as a linker names it.
        month: The first day of the month it measures.
        value: The index level.
        at: The instant it was published.
    """

    index: str
    month: date
    value: Decimal
    at: float


@dataclass(frozen=True, slots=True)
class PaymentTime:
    """When on its date a payment is booked: a time of day on an IANA zone's clock, stated.

    Raises:
        ScheduleValidationError: If the time carries a zone of its own, or the
            zone is not one.
    """

    zone: str
    at: time

    def __post_init__(self) -> None:
        if not isinstance(self.at, time) or self.at.tzinfo is not None:
            raise ScheduleValidationError(
                f"A payment time is a naive time of day on its zone's clock, got {self.at!r}."
            )
        try:
            resolve_zone(self.zone)
        except DataValidationError as exc:
            raise ScheduleValidationError(f"A payment time's zone: {exc}") from exc

    def instant(self, day: date) -> float:
        """The instant a payment on ``day`` is booked at."""

        return datetime.combine(day, self.at, tzinfo=resolve_zone(self.zone)).timestamp()


@dataclass(frozen=True, slots=True)
class RatesPayments:
    """A record's payments, as the schedule takes them.

    Attributes:
        distributions: Each coupon, per unit held, for
            ``RunSchedule.of(distributions=...)``.
        settlements: The final payment, if it falls in the window, for
            ``RunSchedule.of(settlements=...)``.
    """

    distributions: tuple[ScheduledDistribution, ...]
    settlements: tuple[ScheduledSettlement, ...]


def _published(
    fixings: Sequence[PublishedFixing], index: str, day: date, instant: float, named: str
) -> Decimal:
    stated = [f for f in fixings if f.index == index and f.fixed_on == day]
    if not stated:
        raise ScheduleValidationError(
            f"{named}'s payment at {instant!r} needs {index}'s fixing on {day}, and none is "
            "supplied: a payment's fixing is never assumed."
        )
    known = [f for f in stated if f.at <= instant]
    if not known:
        first = min(f.at for f in stated)
        raise ScheduleValidationError(
            f"{named}'s payment at {instant!r} needs {index}'s fixing on {day}, first published "
            f"at {first!r}, after it: a payment reads only what was published by its instant."
        )
    latest = max(f.at for f in known)
    values = {f.value for f in known if f.at == latest}
    if len(values) > 1:
        raise ScheduleValidationError(
            f"{index}'s fixing on {day} is published twice at {latest!r}, as {sorted(values)}: "
            "two answers to one fixing."
        )
    (value,) = values
    return value


def _prints(
    prints: Sequence[PublishedPrint], index: str, instant: float
) -> dict[tuple[str, date], Decimal]:
    """The prints of ``index`` published by ``instant``, the latest of each month."""

    latest: dict[date, PublishedPrint] = {}
    for item in prints:
        if item.index != index or item.at > instant:
            continue
        held = latest.get(item.month)
        if held is None or item.at > held.at:
            latest[item.month] = item
    return {(index, month): item.value for month, item in latest.items()}


def _net(legs: Sequence[tuple[str, Decimal]]) -> Decimal:
    """One date's coupons, netted under the accounting context."""

    total = Decimal(0)
    for _, amount in legs:
        total = ACCOUNTING_CONTEXT.add(total, amount)
    return total


def _tau(period: CouponPeriod) -> Decimal:
    return Decimal(repr(period.accrual))


def _settlement_price(record: InstrumentRecord, per_unit: Decimal, named: str) -> Decimal:
    multiplier = economics_for(record.asset_type, record.economics, named).multiplier
    return ACCOUNTING_CONTEXT.divide(per_unit, multiplier)


def rates_payments(
    record: InstrumentRecord,
    calendars: Mapping[str, TradingDayCalendar],
    paid: PaymentTime,
    *,
    start: float,
    end: float,
    fixings: Sequence[PublishedFixing] = (),
    prints: Sequence[PublishedPrint] = (),
) -> RatesPayments:
    """``record``'s payments from ``start`` to ``end``, from its declared terms.

    Args:
        record: A swap, an FRA, a repo, or a bond declaring floater or linker
            terms.
        calendars: Each calendar its terms name, by id.
        paid: When on its date a payment is booked.
        start: The first instant a payment is placed at.
        end: The last.
        fixings: The fixings its floating periods read, as published.
        prints: The index prints a linker reads, as published.

    Raises:
        ScheduleValidationError: If the record declares no rates terms, a
            calendar is missing, a fixing or a print a payment needs is not
            supplied or was published after it, or the record trades after its
            final payment.
    """

    if not isinstance(paid, PaymentTime):
        raise ScheduleValidationError(f"paid is a PaymentTime, got {paid!r}.")
    if end < start:
        raise ScheduleValidationError(f"A window runs forward: {start!r} to {end!r} does not.")
    terms = record.terms
    named = f"{record.symbol} on {record.exchange}"
    if not isinstance(terms, SwapTerms | FraTerms | FloaterTerms | LinkerTerms | RepoTerms):
        raise ScheduleValidationError(
            f"{named} declares no rates terms, so nothing states what it pays."
        )
    try:
        return _payments(record, terms, calendars, paid, start, end, fixings, prints, named)
    except (RatesError, ConventionError) as exc:
        raise ScheduleValidationError(f"{named}: {exc}") from exc


def _payments(
    record: InstrumentRecord,
    terms: SwapTerms | FraTerms | FloaterTerms | LinkerTerms | RepoTerms,
    calendars: Mapping[str, TradingDayCalendar],
    paid: PaymentTime,
    start: float,
    end: float,
    fixings: Sequence[PublishedFixing],
    prints: Sequence[PublishedPrint],
    named: str,
) -> RatesPayments:
    ctx = ACCOUNTING_CONTEXT
    coupons: dict[date, list[tuple[str, Decimal]]] = {}

    def add(day: date, leg: str, amount: Decimal) -> None:
        coupons.setdefault(day, []).append((leg, amount))

    final: date
    final_price: Decimal
    if isinstance(terms, SwapTerms | FloaterTerms):
        floating = terms.floating if isinstance(terms, SwapTerms) else terms.leg
        for period in leg_periods(floating, terms.start, terms.length, calendars):
            at = paid.instant(period.end)
            if not start <= at <= end:
                continue
            assert period.fixing is not None
            rate = _published(fixings, floating.index, period.fixing, at, named)
            amount = ctx.multiply(
                ctx.multiply(floating.notional, ctx.add(rate, floating.spread)), _tau(period)
            )
            add(period.end, "floating", -amount if isinstance(terms, SwapTerms) else amount)
        if isinstance(terms, SwapTerms):
            fixed = leg_periods(terms.fixed, terms.start, terms.length, calendars)
            for period in fixed:
                at = paid.instant(period.end)
                if start <= at <= end:
                    add(
                        period.end,
                        "fixed",
                        ctx.multiply(
                            ctx.multiply(terms.fixed.notional, terms.fixed.rate), _tau(period)
                        ),
                    )
            final, final_price = fixed[-1].end, Decimal(0)
        else:
            final = leg_periods(floating, terms.start, terms.length, calendars)[-1].end
            final_price = terms.redemption
    elif isinstance(terms, LinkerTerms):
        periods = leg_periods(terms.leg, terms.start, terms.length, calendars)
        for period in periods:
            at = paid.instant(period.end)
            if not start <= at <= end:
                continue
            ratio = ctx.divide(
                reference_index(terms, period.end, _prints(prints, terms.index, at)), terms.base
            )
            real = ctx.multiply(ctx.multiply(terms.leg.notional, terms.leg.rate), _tau(period))
            add(period.end, "indexed", ctx.multiply(real, ratio))
        final = periods[-1].end
        final_price = linker_principal(
            terms, _prints(prints, terms.index, paid.instant(final)), calendars
        )
    elif isinstance(terms, FraTerms):
        final, final_price = terms.start, Decimal(0)
        at = paid.instant(final)
        if start <= at <= end:
            found = resolve_calendars(terms.calendars, calendars, "An FRA")
            fixed_on = subtract_business_days(terms.start, terms.fixing_lag, found)
            final_price = fra_settlement(
                terms, _published(fixings, terms.index, fixed_on, at, named)
            )
    else:
        final, final_price = terms.end, repo_repayment(terms)
    distributions = [
        ScheduledDistribution(
            record.asset_id,
            _net(legs),
            record.currency,
            paid.instant(day),
            CashFlowKind.INTEREST,
            f"{record.symbol}:{day.isoformat()}:{'+'.join(leg for leg, _ in legs)}",
        )
        for day, legs in sorted(coupons.items())
    ]
    settlements = []
    closes = paid.instant(final)
    if start <= closes <= end:
        if record.last_trade_at is None or record.last_trade_at > closes:
            raise ScheduleValidationError(
                f"{named} pays its last on {final}, at {closes!r}, and "
                + (
                    "declares no last trading instant"
                    if record.last_trade_at is None
                    else f"trades until {record.last_trade_at!r}"
                )
                + ": its settlement closes every position, so it stops trading by then. "
                "Declare InstrumentRecord.last_trade_at at or before it."
            )
        price = _settlement_price(record, final_price, named)
        settlements.append(
            ScheduledSettlement(
                ContractSettlement(record.asset_id, price, f"{record.symbol}:final"), closes
            )
        )
    return RatesPayments(tuple(distributions), tuple(settlements))
