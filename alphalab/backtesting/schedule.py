"""Every timed input a historical run takes besides its records, and when each is applied.

A run's market records come from its dataset. Everything else a run is told --
external information, exchange rates, cash put into a second currency, cash a
holding earns or costs, a corporate action, a timer, a contract's final
settlement -- arrives at an instant of its own, and the canonical accounting
needs each at that instant: a rate converts nothing before it was quoted, a
dividend is booked when it is paid, a contract is settled when the exchange
says so. Until v4.1.1 :meth:`BacktestEngine.run
<alphalab.backtesting.engine.BacktestEngine.run>` took observations and nothing
else, so a two-currency book, an ``on_timer`` strategy or a perpetual's funding
had to be stepped by hand through :class:`~alphalab.runtime.run.RunEngine`
(ledger FUT-004).

A :class:`RunSchedule` holds them all, in one order, and a
:class:`ScheduleCursor` applies them between the records -- the same object in
:meth:`BacktestEngine.run <alphalab.backtesting.engine.BacktestEngine.run>`,
:meth:`ReplayBacktest.run <alphalab.backtesting.replay.ReplayBacktest.run>` and
a loop a caller writes by hand, so all three apply every input at the same step.

When each input is applied
--------------------------
Every entry has an instant, and one of two places at it:

* **before the records of its instant** -- a rate (at its ``as_of``), a
  funding, a cash flow, a split. These say what the world is *at* the instant,
  so the records of the instant are marked and traded against them: a split's
  ratio applies to the instant's prices, a rate quoted at ``t`` converts at ``t``;
* **after the records of its instant, before its slice closes** -- an
  observation (at its knowledge instant, the rule of ledger OFE-009), a timer, an
  option's exercise, assignment or expiry (FUT-013), a contract settlement. These
  are things that *happen* at the instant, so what a strategy asks for in
  response rests until each asset's next event -- information known at ``t``
  never trades at a price printed at ``t``.

At one instant, within one place, entries apply in the order their kinds are
listed in :class:`ScheduleKind`. For an option that order carries economics: an
exercise instructed for the expiry instant applies before the expiry resolves
what is left, and the holder's exercise before the writer's assignment.

Entries are ordered by ``(instant, place, kind, identity)``, so ties are broken
the same way on every host. The slice of an instant is closed before anything
later than it. An entry after the data's last record is not applied: the run
could not have acted on it.

What has been applied is a function of the last record a run was handed
---------------------------------------------------------------------------
Every entry ordered before the records of instant ``t`` is applied before the
first record at ``t``, and nothing else is. So a run continued after the record
at ``t`` -- from a snapshot, from a pause -- has applied exactly the entries
ordered before ``(t, records)``, whatever it did with the record itself, and
:meth:`ScheduleCursor.start` with ``after=t`` resumes there, the rate table it
had included. That is why a paused replay's payload need carry only the
schedule's :attr:`RunSchedule.digest`, and a run snapshot nothing at all.

Nothing here reads a clock or contacts anything.
"""

from __future__ import annotations

import decimal
import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum, auto, unique
from itertools import pairwise
from typing import Any, Final

from alphalab.allocation.engine import AllocationEngine
from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.backtesting.exceptions import BacktestError
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.common.constants import DEFAULT_ENCODING
from alphalab.conventions.daycount import DayCount, day_count_days, days_in_year
from alphalab.data.exceptions import DataValidationError
from alphalab.data.time import resolve_zone
from alphalab.persistence.serializer import serialize
from alphalab.portfolio.corporate_actions import (
    Assignment,
    CashFlow,
    CashFlowKind,
    ContractSettlement,
    Exercise,
    HoldingExchange,
    OptionExpiry,
    Split,
)
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.financing import (
    ACCRUES,
    BalanceBasis,
    FinancingTier,
    accrued_interest,
    day_fraction,
    require_tiers,
)
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.settlement import DeliveryConvention, PaymentChange
from alphalab.portfolio.types import TransactionType
from alphalab.risk.surfaces import ObservedSurface
from alphalab.runtime.execution_pipeline import ContextFactory
from alphalab.runtime.run import RunEngine, RunState
from alphalab.strategy.events import ElectionMade, HoldersRecorded, TimerEvent, UnitsElected

__all__ = [
    "EMPTY_SCHEDULE",
    "DailyAccrual",
    "DailyBorrowFee",
    "DailyInterest",
    "DeliveredExchange",
    "DeliveryAmendment",
    "EarlierElections",
    "ElectiveExchange",
    "ElectiveTerms",
    "Funding",
    "PaidExchange",
    "PaymentAmendment",
    "RateChange",
    "RunSchedule",
    "ScheduleCursor",
    "ScheduleEntry",
    "ScheduleKind",
    "SchedulePlace",
    "ScheduleValidationError",
    "ScheduledAssignment",
    "ScheduledBorrowFee",
    "ScheduledCashFlow",
    "ScheduledDistribution",
    "ScheduledExchange",
    "ScheduledExercise",
    "ScheduledExpiry",
    "ScheduledFundingRate",
    "ScheduledInterest",
    "ScheduledSettlement",
    "ScheduledSplit",
    "TermsCorrection",
]


class ScheduleValidationError(BacktestError):
    """A schedule that cannot be applied as given -- two answers to one question."""


@unique
class SchedulePlace(IntEnum):
    """Where at its instant an entry is applied, relative to the instant's records."""

    BEFORE_RECORDS = 0
    #: The records of the instant themselves; no entry is placed here.
    RECORDS = 1
    AFTER_RECORDS = 2


@unique
class ScheduleKind(StrEnum):
    """What an entry is. Its order breaks ties within one place at one instant."""

    RATE = "rate"
    FUNDING = "funding"
    CASH_FLOW = "cash_flow"
    DISTRIBUTION = "distribution"
    FUNDING_RATE = "funding_rate"
    SPLIT = "split"
    OBSERVATION = "observation"
    TIMER = "timer"
    EXERCISE = "exercise"
    ASSIGNMENT = "assignment"
    EXPIRY = "expiry"
    SETTLEMENT = "settlement"
    #: Phase 3 (FUT-023): one day of a daily-accrued period -- after the
    #: instant's records, on the balance and the shorts the day's cutoff left,
    #: and before a period posting at the same instant.
    ACCRUAL = "accrual"
    #: v4.2.0 (FUT-018): a period's interest on a cash balance, and its borrow
    #: fees -- after the instant's records, on the balance and the shorts the
    #: instant left.
    INTEREST = "interest"
    BORROW_FEE = "borrow_fee"
    #: v4.2.0 (FUT-021): a holding exchanged by its issuer -- before the
    #: instant's records, as a split is.
    EXCHANGE = "exchange"
    #: Phase 4 (FUT-034): the issuer's later word on an exchange's payment --
    #: before the instant's records, and after an exchange at the same instant.
    PAYMENT = "payment"
    #: Phase 4 (FUT-030): an elective action's record instant -- after the
    #: instant's records, on the holders they left.
    RECORD = "record"
    #: Phase 5 (FUT-039): a volatility surface delivered as data -- before the
    #: instant's records, so a check at the instant reads it.
    SURFACE = "surface"


_PLACE: Final = {
    ScheduleKind.RATE: SchedulePlace.BEFORE_RECORDS,
    ScheduleKind.FUNDING: SchedulePlace.BEFORE_RECORDS,
    ScheduleKind.CASH_FLOW: SchedulePlace.BEFORE_RECORDS,
    ScheduleKind.DISTRIBUTION: SchedulePlace.BEFORE_RECORDS,
    ScheduleKind.FUNDING_RATE: SchedulePlace.BEFORE_RECORDS,
    ScheduleKind.SPLIT: SchedulePlace.BEFORE_RECORDS,
    ScheduleKind.OBSERVATION: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.TIMER: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.EXERCISE: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.ASSIGNMENT: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.EXPIRY: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.SETTLEMENT: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.ACCRUAL: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.INTEREST: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.BORROW_FEE: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.EXCHANGE: SchedulePlace.BEFORE_RECORDS,
    ScheduleKind.PAYMENT: SchedulePlace.BEFORE_RECORDS,
    ScheduleKind.RECORD: SchedulePlace.AFTER_RECORDS,
    ScheduleKind.SURFACE: SchedulePlace.BEFORE_RECORDS,
}
_RANK: Final = {kind: rank for rank, kind in enumerate(ScheduleKind)}


@dataclass(frozen=True, slots=True)
class Funding:
    """Cash deposited in a currency the run settles, at ``at`` (ExecutionPipeline.fund).

    How a book that settles a second currency gets cash in it: no fill converts
    cash to cover itself (ADR-0035).
    """

    amount: Decimal
    currency: str
    at: float


@dataclass(frozen=True, slots=True)
class ScheduledCashFlow:
    """A dividend, interest, a fee or a funding payment, booked at ``at``."""

    flow: CashFlow
    at: float


@dataclass(frozen=True, slots=True)
class ScheduledDistribution:
    """A dividend or coupon declared per unit held, paid to whoever holds at ``at``.

    A schedule is written before the run, so it cannot know a position's size;
    this states the per-unit figure an issuer declares, and the run books
    ``per_unit * quantity held`` as a :class:`~alphalab.portfolio.corporate_actions.CashFlow`
    when it applies -- received on a long, paid on a short, nothing on no
    position. ``at`` is the instant entitlement is fixed (the ex-date's open).
    """

    asset_id: str
    per_unit: Decimal
    currency: str
    at: float
    kind: CashFlowKind = CashFlowKind.DIVIDEND
    reference: str = ""


@dataclass(frozen=True, slots=True)
class ScheduledFundingRate:
    """A perpetual's funding rate for one interval, settled at ``at``.

    The payment is ``-(quantity * multiplier * mark * rate)``: a long pays a
    positive rate, a short receives it -- booked as a ``FUNDING`` cash flow in
    the position's currency at the price the run last marked the contract at.
    """

    asset_id: str
    rate: Decimal
    at: float
    reference: str = ""


@dataclass(frozen=True, slots=True)
class ScheduledInterest:
    """A period's interest on one currency's cash balance, booked at ``at`` (ledger FUT-018).

    The broker's terms for the period, declared: a debit is charged band by band
    by ``debit_tiers``; a credit earns ``credit_rate`` when one is stated and
    nothing otherwise. The period runs from ``start`` to ``end`` -- dates, so its
    length is the day count's, not the clock's -- and the balance is the one at
    ``at``, by ``basis``: trade-date cash, or settled cash (FUT-015). Booked as an
    ``INTEREST`` cash flow in ``currency``; nothing for a zero.
    """

    currency: str
    start: date
    end: date
    at: float
    day_count: DayCount
    debit_tiers: tuple[FinancingTier, ...]
    credit_rate: Decimal | None = None
    basis: BalanceBasis = BalanceBasis.TRADE_DATE
    reference: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "debit_tiers", require_tiers(self.debit_tiers))
        day_fraction(self.start, self.end, self.day_count)  # refuses a backward period
        if self.credit_rate is not None and (
            not isinstance(self.credit_rate, Decimal)
            or not self.credit_rate.is_finite()
            or self.credit_rate < 0
        ):
            raise ScheduleValidationError(
                f"A credit rate is a non-negative Decimal or None, got {self.credit_rate!r}."
            )


@dataclass(frozen=True, slots=True)
class ScheduledBorrowFee:
    """A period's borrow fees on every short position, booked at ``at`` (ledger FUT-018).

    Each short position in an instrument declaring
    :class:`~alphalab.instrument.borrow.BorrowTerms` pays, for the period from
    ``start`` to ``end``, the fee in effect at ``at`` on its value at the run's
    last mark, by its terms' day count -- a ``FEE`` cash flow in the position's
    currency. A short in an instrument declaring no terms pays nothing.
    """

    start: date
    end: date
    at: float
    reference: str = ""


@dataclass(frozen=True, slots=True)
class DailyAccrual:
    """When each day of a period accrues: at ``cutoff`` on ``zone``'s wall clock (FUT-023).

    A broker accrues interest on each day's closing balance and posts the sum
    once a period; which instant closes a day is its own, so it is declared --
    an IANA zone and a time of day -- never assumed. Every calendar day of the
    period accrues, a weekend or a holiday carrying its balance as it stood.
    Where the zone's clock skips or repeats the cutoff, it is the instant the
    zone database resolves it to.
    """

    zone: str
    cutoff: time

    def __post_init__(self) -> None:
        if not isinstance(self.cutoff, time) or self.cutoff.tzinfo is not None:
            raise ScheduleValidationError(
                f"A daily cutoff is a naive time of day on the declared zone, got {self.cutoff!r}."
            )
        if not isinstance(self.zone, str):
            raise ScheduleValidationError(
                f"A daily accrual's zone is an IANA zone's name, got {self.zone!r}."
            )
        try:
            resolve_zone(self.zone)
        except DataValidationError as exc:
            raise ScheduleValidationError(f"A daily accrual's zone: {exc}") from exc

    def instant(self, day: date) -> float:
        """The instant ``day``'s cutoff falls at."""

        return datetime.combine(day, self.cutoff, tzinfo=resolve_zone(self.zone)).timestamp()


@dataclass(frozen=True, slots=True)
class RateChange:
    """A broker's rates from ``effective`` on, within a daily-accrued period (FUT-023).

    Attributes:
        effective: The first day the rates apply to.
        debit_tiers: The tiered debit rates, as :class:`ScheduledInterest` states them.
        credit_rate: The credit rate, or ``None`` for none.
    """

    effective: date
    debit_tiers: tuple[FinancingTier, ...]
    credit_rate: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "debit_tiers", require_tiers(self.debit_tiers))
        _require_credit_rate(self.credit_rate)


@dataclass(frozen=True, slots=True)
class DailyInterest:
    """``interest`` accrued day by day and posted at its ``at`` (FUT-023).

    Each calendar day of the period, at its cutoff, accrues the day's interest on
    the currency's balance then -- trade-date or settled, by the period's basis --
    at the rates in effect that day: the period's own, or the latest
    :class:`RateChange` effective by then. Each day is weighted by what its day
    count counts it as, over that count's year; the exact sum posts once, at
    ``interest.at``, rounded once, as an ``INTEREST`` cash flow. Every cutoff is
    at or before the posting.
    """

    interest: ScheduledInterest
    accrual: DailyAccrual
    rates: tuple[RateChange, ...] = ()

    def __post_init__(self) -> None:
        rates = tuple(self.rates)
        if any(b.effective <= a.effective for a, b in pairwise(rates)):
            raise ScheduleValidationError(
                "Rate changes take effect on strictly increasing days: two on one day would be "
                "two answers to what the rate is."
            )
        object.__setattr__(self, "rates", rates)
        _require_cutoffs(self.interest.start, self.interest.end, self.interest.at, self.accrual)

    @property
    def key(self) -> str:
        """The ledger's name for this period's accrual."""

        item = self.interest
        return f"interest:{item.currency}:{item.start}:{item.end}:{item.reference}"

    def rates_on(self, day: date) -> tuple[tuple[FinancingTier, ...], Decimal | None]:
        """The debit tiers and credit rate in effect on ``day``."""

        tiers, credit = self.interest.debit_tiers, self.interest.credit_rate
        for change in self.rates:
            if change.effective > day:
                break
            tiers, credit = change.debit_tiers, change.credit_rate
        return tiers, credit


@dataclass(frozen=True, slots=True)
class DailyBorrowFee:
    """``fee`` accrued day by day on each short and posted at its ``at`` (FUT-023).

    Each calendar day of the period, at its cutoff, every short in an instrument
    declaring borrow terms accrues the day's fee: the fee in effect then, on the
    short's value at the run's mark then, weighted by its terms' day count. A
    short held for part of the period pays for those days; the exact sum per
    instrument posts once, at ``fee.at``, as a ``FEE`` cash flow.
    """

    fee: ScheduledBorrowFee
    accrual: DailyAccrual

    def __post_init__(self) -> None:
        _require_cutoffs(self.fee.start, self.fee.end, self.fee.at, self.accrual)

    def key(self, asset_id: str) -> str:
        """The ledger's name for ``asset_id``'s accrual in this period."""

        item = self.fee
        return f"borrow:{asset_id}:{item.start}:{item.end}:{item.reference}"


@dataclass(frozen=True, slots=True)
class _AccrualDay:
    """One day of a daily-accrued period: what the schedule applies at its cutoff."""

    of: DailyInterest | DailyBorrowFee
    day: date


def _days(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=n) for n in range((end - start).days))


def _require_cutoffs(start: date, end: date, at: float, accrual: DailyAccrual) -> None:
    last = end - timedelta(days=1)
    if accrual.instant(last) > at:
        raise ScheduleValidationError(
            f"A daily accrual posts at {at!r}, before its last day's cutoff "
            f"{accrual.instant(last)!r}: a period posts what its days accrued, after they did."
        )


def _require_credit_rate(rate: Decimal | None) -> None:
    if rate is not None and (not isinstance(rate, Decimal) or not rate.is_finite() or rate < 0):
        raise ScheduleValidationError(
            f"A credit rate is a non-negative Decimal or None, got {rate!r}."
        )


@dataclass(frozen=True, slots=True)
class ScheduledSplit:
    """A split, reverse split or stock dividend, effective at ``at``."""

    split: Split
    at: float


@dataclass(frozen=True, slots=True)
class ScheduledExchange:
    """A holding exchanged by its issuer, effective at ``at`` (ledger FUT-021)."""

    exchange: HoldingExchange
    at: float


@dataclass(frozen=True, slots=True)
class PaidExchange:
    """A holding exchanged at ``at`` whose cash is paid at ``payment_at`` (FUT-025, phase 3).

    The issuer's terms set the effective instant -- when the holding becomes the
    legs and the entitlement to the cash -- and the payment instant, when the
    cash moves: until then it is unsettled, kept out of buying power and of
    settled cash, as a sale's proceeds are before they settle.
    """

    exchange: HoldingExchange
    at: float
    payment_at: float

    def __post_init__(self) -> None:
        if self.payment_at < self.at:
            raise ScheduleValidationError(
                f"An exchange effective at {self.at!r} pays at {self.payment_at!r}, before it."
            )


@dataclass(frozen=True, slots=True)
class DeliveredExchange:
    """A holding exchanged at ``at`` whose legs are delivered later (FUT-037, phase 5).

    The issuer's three instants, kept apart: at ``at`` -- effective -- the
    holding is exchanged and each holder's entitlement to the legs recorded,
    its basis and its strategies' parts; at each leg's delivery instant the
    units arrive; the cash part is paid at ``payment_at``, when stated
    (FUT-025). Until delivered, units are owed -- valued, and attributed --
    but, under the declared ``convention``, by default neither sold nor counted
    as margin collateral. Markets deliver on their own schedules, so each leg's
    instant is declared, never assumed: a leg named in none is delivered at
    ``at``.

    Attributes:
        exchange: The terms.
        at: The effective instant.
        deliveries: ``(asset_id, instant)`` for each leg delivered after ``at``.
        payment_at: When the cash part is paid, or ``None`` for at ``at``.
        convention: What undelivered units may do before they arrive.
    """

    exchange: HoldingExchange
    at: float
    deliveries: tuple[tuple[str, float], ...]
    payment_at: float | None = None
    convention: DeliveryConvention = field(default_factory=DeliveryConvention)

    def __post_init__(self) -> None:
        legs = {leg.asset_id for leg in self.exchange.legs}
        named = [asset for asset, _ in self.deliveries]
        if not named or len(set(named)) != len(named):
            raise ScheduleValidationError(
                "A delivered exchange names each leg delivered later once, and at least one."
            )
        stray = sorted(set(named) - legs)
        if stray:
            raise ScheduleValidationError(
                f"A delivered exchange delivers {stray}, which its terms do not exchange for."
            )
        for asset, when in self.deliveries:
            if when <= self.at:
                raise ScheduleValidationError(
                    f"{asset} is delivered at {when!r}, not after the exchange's {self.at!r}: "
                    "a leg delivered at its instant is no delivery to declare."
                )
        if self.payment_at is not None and self.payment_at < self.at:
            raise ScheduleValidationError(
                f"An exchange effective at {self.at!r} pays at {self.payment_at!r}, before it."
            )
        if not isinstance(self.convention, DeliveryConvention):
            raise ScheduleValidationError("A delivery's convention is a DeliveryConvention.")
        object.__setattr__(self, "deliveries", tuple(sorted(self.deliveries)))


class EarlierElections(Enum):
    """What a correction of an elective action's terms does to elections already made (FUT-038)."""

    #: They stand: an election of alternative ``k`` is of the corrected ``k``.
    KEPT = auto()
    #: They are void: a holder elects again under the corrected terms, or
    #: receives the default.
    VOIDED = auto()


@dataclass(frozen=True, slots=True)
class TermsCorrection:
    """The issuer's correction of an elective action's terms, known at ``announced_at`` (FUT-038).

    A version of its own, beside the terms it corrects: a field left ``None``
    keeps what the earlier version said. Announced at or before the action's
    effective instant -- a correction after it is a payment's or a delivery's
    (:class:`PaymentAmendment`, :class:`DeliveryAmendment`), never a rewrite of
    an exchange already applied.

    Attributes:
        announced_at: When the correction is known, and in effect from.
        alternatives: The corrected alternatives, as many as before or more, all
            of the one holding; ``None`` keeps them.
        default: The corrected default; ``None`` keeps it.
        deadline: The corrected deadline -- extended, or reopening a window
            that had closed; ``None`` keeps it.
        proration: The corrected prorations; ``None`` keeps them.
        elections: What becomes of elections made before it.
    """

    announced_at: float
    alternatives: tuple[HoldingExchange, ...] | None = None
    default: int | None = None
    deadline: float | None = None
    proration: tuple[tuple[int, Decimal], ...] | None = None
    elections: EarlierElections = EarlierElections.KEPT

    def __post_init__(self) -> None:
        if not isinstance(self.elections, EarlierElections):
            raise ScheduleValidationError(
                f"A correction states what becomes of earlier elections, got {self.elections!r}."
            )
        if self.alternatives is not None:
            object.__setattr__(self, "alternatives", tuple(self.alternatives))
        if self.proration is not None:
            object.__setattr__(self, "proration", tuple(sorted(self.proration)))


@dataclass(frozen=True, slots=True)
class ElectiveTerms:
    """One version of an elective action's terms, as in effect from its announcement (FUT-038)."""

    version: int
    announced_at: float
    alternatives: tuple[HoldingExchange, ...]
    default: int
    deadline: float
    proration: tuple[tuple[int, Decimal], ...]
    voids_earlier: bool


@dataclass(frozen=True, slots=True)
class ElectiveExchange:
    """A corporate action offering its holders alternatives, by election (FUT-024, phase 3).

    The issuer's terms: each alternative a complete :class:`HoldingExchange` of
    the same holding -- cash, stock, a mix -- and ``default``, the one applied to
    a holder that does not elect. Holders elect with
    :attr:`~alphalab.strategy.events.IntentKind.ELECT` between the announcement
    and the deadline; the announcement itself reaches a strategy as the caller's
    data delivers it, at its knowledge instant, and an election before
    ``announced_at`` or after ``deadline`` is not counted. At ``at`` each
    holder's share of the holding is exchanged by its own election (FUT-030,
    phase 4) -- the whole of it, or the units each election names
    (``metadata['units']``) -- and what it did not elect, or elected for more
    units than it may, by the default. A ``record_at`` instant records who
    holds it then, and each may elect for no more than it held then and holds
    still; what it holds beyond is exchanged by the default. An alternative the
    issuer prorates accepts its ``proration`` factor of each election, in whole
    units where the shares are whole, and the default takes the rest. One
    holder, or holders agreeing, exchange the holding whole, as before. The
    cash part is paid at ``payment_at`` when stated (FUT-025).

    Attributes:
        alternatives: At least two, all of one holding.
        default: The index of the alternative a holder not electing receives.
        announced_at: When the action was announced: no election counts before it.
        deadline: The last instant an election counts, at or before ``at``.
        at: The effective instant.
        reference: The issuer's reference; holders' elections name it.
        payment_at: When the cash part is paid, or ``None`` for at ``at``.
        proration: ``(alternative, factor)`` for each alternative the issuer
            prorates: the fraction of each election it accepts, above zero and
            at most one; never the default (FUT-030).
        record_at: The record instant, at or before ``at``: who holds the
            holding then, and how much, bounds each holder's election; ``None``
            bounds it by the holding at ``at`` alone (FUT-030).
        corrections: The issuer's corrections of these terms, each announced
            at its own instant, strictly after the last and at or before ``at``
            (FUT-038). Each election is read against the version in effect when
            it was made, within that version's window, and voided by a later
            correction voiding earlier elections; the holding is exchanged by
            the version in effect at ``at``.
        deliveries: ``(asset_id, instant)`` for each leg delivered after ``at``,
            under ``convention`` (FUT-037).
        convention: What undelivered units may do before they arrive.
    """

    alternatives: tuple[HoldingExchange, ...]
    default: int
    announced_at: float
    deadline: float
    at: float
    reference: str
    payment_at: float | None = None
    proration: tuple[tuple[int, Decimal], ...] = ()
    record_at: float | None = None
    corrections: tuple[TermsCorrection, ...] = ()
    deliveries: tuple[tuple[str, float], ...] = ()
    convention: DeliveryConvention = field(default_factory=DeliveryConvention)

    def __post_init__(self) -> None:
        alternatives = tuple(self.alternatives)
        if len(alternatives) < 2 or not all(isinstance(a, HoldingExchange) for a in alternatives):
            raise ScheduleValidationError(
                "An elective exchange offers at least two HoldingExchange alternatives."
            )
        if len({a.asset_id for a in alternatives}) != 1:
            raise ScheduleValidationError("An elective exchange's alternatives are of one holding.")
        if isinstance(self.default, bool) or not 0 <= self.default < len(alternatives):
            raise ScheduleValidationError(
                f"An elective exchange's default is one of its {len(alternatives)} alternatives, "
                f"got {self.default!r}."
            )
        if not self.announced_at <= self.deadline <= self.at:
            raise ScheduleValidationError(
                "An elective exchange is announced, then closes its elections, then takes "
                f"effect: got {self.announced_at!r}, {self.deadline!r}, {self.at!r}."
            )
        if self.payment_at is not None and self.payment_at < self.at:
            raise ScheduleValidationError(
                f"An exchange effective at {self.at!r} pays at {self.payment_at!r}, before it."
            )
        if not isinstance(self.reference, str) or not self.reference:
            raise ScheduleValidationError(
                "An elective exchange has the reference holders elect by."
            )
        prorated = tuple(sorted(self.proration))
        if len({index for index, _ in prorated}) != len(prorated):
            raise ScheduleValidationError("An alternative is prorated by one factor.")
        for index, factor in prorated:
            if isinstance(index, bool) or not 0 <= index < len(alternatives):
                raise ScheduleValidationError(
                    f"A proration names one of the {len(alternatives)} alternatives, got {index!r}."
                )
            if index == self.default:
                raise ScheduleValidationError(
                    "The default takes what a proration does not accept, and is not prorated."
                )
            if not isinstance(factor, Decimal) or not factor.is_finite() or not 0 < factor <= 1:
                raise ScheduleValidationError(
                    f"A proration factor is above zero and at most one, got {factor!r}."
                )
        if self.record_at is not None and self.record_at > self.at:
            raise ScheduleValidationError(
                f"An elective exchange's record instant {self.record_at!r} is after it takes "
                f"effect at {self.at!r}."
            )
        object.__setattr__(self, "alternatives", alternatives)
        object.__setattr__(self, "proration", prorated)
        corrections = tuple(self.corrections)
        if not all(isinstance(c, TermsCorrection) for c in corrections):
            raise ScheduleValidationError("An elective action's corrections are TermsCorrections.")
        last = self.announced_at
        for correction in corrections:
            if not last < correction.announced_at <= self.at:
                raise ScheduleValidationError(
                    f"A correction announced at {correction.announced_at!r} follows the version "
                    f"announced at {last!r} and comes at or before the action's {self.at!r}; a "
                    "correction after it amends a payment or a delivery instead."
                )
            last = correction.announced_at
        object.__setattr__(self, "corrections", corrections)
        if corrections:
            for terms in self.versions[1:]:
                _require_terms(terms, self.at, self.asset_id)
        if not isinstance(self.convention, DeliveryConvention):
            raise ScheduleValidationError("A delivery's convention is a DeliveryConvention.")
        legs = {leg.asset_id for alt in self.final.alternatives for leg in alt.legs}
        for asset, when in self.deliveries:
            if asset not in legs or when <= self.at:
                raise ScheduleValidationError(
                    f"{asset} is delivered at {when!r}: a leg of an alternative, after {self.at!r}."
                )
        object.__setattr__(self, "deliveries", tuple(sorted(self.deliveries)))

    @property
    def versions(self) -> tuple[ElectiveTerms, ...]:
        """Every version of the terms, the original first, each in effect from its announcement."""

        current = ElectiveTerms(
            0, self.announced_at, self.alternatives, self.default, self.deadline,
            self.proration, False,
        )  # fmt: skip
        found = [current]
        for number, c in enumerate(self.corrections, start=1):
            current = ElectiveTerms(
                number,
                c.announced_at,
                current.alternatives if c.alternatives is None else c.alternatives,
                current.default if c.default is None else c.default,
                current.deadline if c.deadline is None else c.deadline,
                current.proration if c.proration is None else c.proration,
                c.elections is EarlierElections.VOIDED,
            )
            found.append(current)
        return tuple(found)

    def terms_at(self, at: float) -> ElectiveTerms | None:
        """The version in effect at ``at`` -- the latest announced by then -- or ``None``."""

        found = None
        for terms in self.versions:
            if terms.announced_at > at:
                break
            found = terms
        return found

    @property
    def final(self) -> ElectiveTerms:
        """The version in effect at the action's effective instant: what it is applied by."""

        terms = self.terms_at(self.at)
        assert terms is not None  # announced before it takes effect
        return terms

    @property
    def asset_id(self) -> str:
        """The holding the action is on."""

        return self.alternatives[0].asset_id

    def chosen(self, elections: Mapping[str, int | None]) -> HoldingExchange:
        """The alternative ``elections`` -- each holder's, or ``None`` -- give the book."""

        made = set(elections.values())
        if len(made) == 1:
            (only,) = made
            if only is not None:
                return self.alternatives[only]
        return self.alternatives[self.default]


@dataclass(frozen=True, slots=True)
class DeliveryAmendment:
    """A later word on a leg an exchange delivers after its instant, at ``at`` (FUT-037).

    Names ``asset_id``'s delivery of the exchange of ``holding`` effective at
    ``effective_at`` -- a :class:`DeliveredExchange` the schedule declares --
    and what became of it: ``DELAYED`` to ``delivers_at``; ``CORRECTED`` to
    ``units`` delivered, fewer than owed; ``FAILED``, none delivered. ``at``
    is when the run learns of it.
    """

    holding: str
    effective_at: float
    asset_id: str
    at: float
    change: PaymentChange
    delivers_at: float | None = None
    units: Decimal | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        if self.change not in (
            PaymentChange.DELAYED,
            PaymentChange.CORRECTED,
            PaymentChange.FAILED,
        ):
            raise ScheduleValidationError(
                f"A delivery is delayed, corrected or failed, got {self.change!r}."
            )
        if self.at < self.effective_at:
            raise ScheduleValidationError(
                f"A delivery of an action effective at {self.effective_at!r} is amended at "
                f"{self.at!r}, before it."
            )
        delayed = self.change is PaymentChange.DELAYED
        if delayed != (self.delivers_at is not None) or (
            delayed and self.delivers_at is not None and self.delivers_at <= self.at
        ):
            raise ScheduleValidationError(
                "A delayed delivery, and only one, names its new instant, after the word."
            )
        if (self.change is PaymentChange.CORRECTED) != (self.units is not None):
            raise ScheduleValidationError(
                "A corrected delivery, and only one, names the units delivered."
            )

    @property
    def key(self) -> str:
        """The delivery it names: holding, effective instant and leg."""

        return f"delivery:{self.holding}|{self.effective_at!r}|{self.asset_id}"


@dataclass(frozen=True, slots=True)
class PaymentAmendment:
    """The issuer's later word on a corporate action's payment, at ``at`` (FUT-034, phase 4).

    Names one currency's payment of the exchange of ``holding`` effective at
    ``effective_at`` -- an exchange the same schedule declares -- and what
    became of it, by :class:`~alphalab.portfolio.settlement.PaymentChange`:

    * ``DELAYED``: paid at ``payment_at`` instead, after ``at``.
    * ``FAILED``: will not be paid; ``REVERSED``: paid, and clawed back.
    * ``CORRECTED``: paid ``per_unit`` for each unit exchanged, in lieu cash
      included -- at ``payment_at`` if the rest is paid already and it is later.
    * ``REPLACED``: paid ``per_unit`` in ``replacement_currency`` instead, at
      ``payment_at``.

    ``at`` is when the run learns of it and it takes effect: nothing is known
    of it before.
    """

    holding: str
    effective_at: float
    currency: str
    at: float
    change: PaymentChange
    payment_at: float | None = None
    per_unit: Decimal | None = None
    replacement_currency: str | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.change, PaymentChange):
            raise ScheduleValidationError(
                f"A payment amendment states its change as a PaymentChange, got {self.change!r}."
            )
        if self.at < self.effective_at:
            raise ScheduleValidationError(
                f"A payment of an action effective at {self.effective_at!r} is amended at "
                f"{self.at!r}, before it."
            )
        change = self.change
        priced = change in (PaymentChange.CORRECTED, PaymentChange.REPLACED)
        if priced != (self.per_unit is not None):
            raise ScheduleValidationError(
                f"A {change.name.lower()} payment "
                + ("states" if priced else "states no")
                + " amount per unit."
            )
        if self.per_unit is not None and (not self.per_unit.is_finite() or self.per_unit < 0):
            raise ScheduleValidationError(
                f"A payment per unit is zero or more, got {self.per_unit}."
            )
        if (change is PaymentChange.REPLACED) != (self.replacement_currency is not None):
            raise ScheduleValidationError(
                "A replaced payment, and only one, names the currency it is paid in instead."
            )
        if self.replacement_currency == self.currency:
            raise ScheduleValidationError("A replacement is paid in another currency.")
        if change is PaymentChange.DELAYED and (
            self.payment_at is None or self.payment_at <= self.at
        ):
            raise ScheduleValidationError(
                f"A delayed payment is paid after the delay is known ({self.at!r}), got "
                f"{self.payment_at!r}."
            )
        if change in (PaymentChange.FAILED, PaymentChange.REVERSED) and self.payment_at is not None:
            raise ScheduleValidationError(f"A {change.name.lower()} payment is paid at no instant.")
        if self.payment_at is not None and self.payment_at < self.at:
            raise ScheduleValidationError(
                f"A payment amended at {self.at!r} is paid at {self.payment_at!r}, before it."
            )

    @property
    def key(self) -> str:
        """The payment it names: holding, effective instant and currency."""

        return f"{self.holding}|{self.effective_at!r}|{self.currency}"


def _require_terms(terms: ElectiveTerms, at: float, asset_id: str) -> None:
    """A corrected version holds as the original must (FUT-038)."""

    count = len(terms.alternatives)
    if count < 2 or not all(isinstance(a, HoldingExchange) for a in terms.alternatives):
        raise ScheduleValidationError("A corrected action offers at least two alternatives.")
    if {a.asset_id for a in terms.alternatives} != {asset_id}:
        raise ScheduleValidationError("A corrected action's alternatives are of its one holding.")
    if isinstance(terms.default, bool) or not 0 <= terms.default < count:
        raise ScheduleValidationError(
            f"A corrected default is one of the {count} alternatives, got {terms.default!r}."
        )
    if terms.deadline > at:
        raise ScheduleValidationError(
            f"A corrected deadline {terms.deadline!r} is after the action's {at!r}."
        )
    for index, factor in terms.proration:
        if not 0 <= index < count or index == terms.default or not 0 < factor <= 1:
            raise ScheduleValidationError(
                f"A corrected proration names an alternative other than the default, by a factor "
                f"above zero and at most one, got {(index, factor)!r}."
            )


def _require_payments(
    payments: tuple[PaymentAmendment | DeliveryAmendment, ...],
    exchanges: tuple[ScheduledExchange | PaidExchange | ElectiveExchange | DeliveredExchange, ...],
) -> None:
    """Each amendment names an exchange the schedule declares, none after a terminal one."""

    declared = {
        (x.asset_id if isinstance(x, ElectiveExchange) else x.exchange.asset_id, x.at)
        for x in exchanges
    }
    # An elective action's legs delivered later (FUT-038) are amended as a
    # delivered exchange's are (FUT-037).
    delivered = {
        (x.asset_id if isinstance(x, ElectiveExchange) else x.exchange.asset_id, x.at, asset)
        for x in exchanges
        if isinstance(x, DeliveredExchange | ElectiveExchange)
        for asset, _ in x.deliveries
    }
    by_payment: dict[str, list[PaymentAmendment | DeliveryAmendment]] = {}
    for amendment in payments:
        if isinstance(amendment, DeliveryAmendment) and (
            (amendment.holding, amendment.effective_at, amendment.asset_id) not in delivered
        ):
            raise ScheduleValidationError(
                f"A delivery amendment names {amendment.asset_id}'s delivery for the exchange "
                f"of {amendment.holding} at {amendment.effective_at!r}, which the schedule "
                "does not declare."
            )
        if (amendment.holding, amendment.effective_at) not in declared:
            raise ScheduleValidationError(
                f"A payment amendment names the exchange of {amendment.holding} at "
                f"{amendment.effective_at!r}, which the schedule does not declare."
            )
        by_payment.setdefault(amendment.key, []).append(amendment)
    for amendments in by_payment.values():
        ordered = sorted(amendments, key=lambda a: a.at)
        for earlier, later in pairwise(ordered):
            if earlier.change in (PaymentChange.FAILED, PaymentChange.REVERSED):
                raise ScheduleValidationError(
                    f"The payment {earlier.key} {earlier.change.name.lower()} at "
                    f"{earlier.at!r}; it is amended again at {later.at!r}, which cannot hold."
                )


@dataclass(frozen=True, slots=True)
class ScheduledSettlement:
    """A contract's final settlement, published at ``at`` (ledger FUT-012)."""

    settlement: ContractSettlement
    at: float


@dataclass(frozen=True, slots=True)
class ScheduledExercise:
    """A holder's instruction to exercise, taking effect at ``at`` (ledger FUT-013)."""

    exercise: Exercise
    at: float


@dataclass(frozen=True, slots=True)
class ScheduledAssignment:
    """Written contracts assigned at ``at``: an input, never drawn (ledger FUT-013)."""

    assignment: Assignment
    at: float


@dataclass(frozen=True, slots=True)
class ScheduledExpiry:
    """An option's expiry, resolved at ``at`` against its settlement value (ledger FUT-013)."""

    expiry: OptionExpiry
    at: float


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    """One timed input, placed.

    Attributes:
        at: Its instant.
        place: Before or after the records of that instant.
        kind: What it is.
        identity: What tells it apart from another entry of its kind at its
            instant: a delivery id, a currency pair, a timer id, an asset.
        item: The input itself.
    """

    at: float
    place: SchedulePlace
    kind: ScheduleKind
    identity: str
    item: Any

    @property
    def key(self) -> tuple[float, int, int, str]:
        """What entries are ordered by."""

        return (self.at, int(self.place), _RANK[self.kind], self.identity)


def _entry(kind: ScheduleKind, at: float, identity: str, item: object) -> ScheduleEntry:
    return ScheduleEntry(float(at), _PLACE[kind], kind, identity, item)


def _daily_entries(
    interest: Iterable[ScheduledInterest | DailyInterest],
    borrow_fees: Iterable[ScheduledBorrowFee | DailyBorrowFee],
) -> list[ScheduleEntry]:
    """Each daily-accrued period's days, at their cutoffs, and its posting (FUT-023)."""

    entries: list[ScheduleEntry] = []
    for item in (*interest, *borrow_fees):
        period: ScheduledInterest | ScheduledBorrowFee
        if isinstance(item, DailyInterest):
            period, identity = item.interest, f"{item.interest.currency}:{item.interest.reference}"
            entries.append(_entry(ScheduleKind.INTEREST, period.at, identity, item))
        elif isinstance(item, DailyBorrowFee):
            period, identity = item.fee, item.fee.reference
            entries.append(_entry(ScheduleKind.BORROW_FEE, period.at, identity, item))
        else:
            continue
        for day in _days(period.start, period.end):
            entries.append(
                _entry(
                    ScheduleKind.ACCRUAL,
                    item.accrual.instant(day),
                    f"{identity}:{day.isoformat()}",
                    _AccrualDay(item, day),
                )
            )
    return entries


def _instruction(instruction: Exercise | Assignment) -> str:
    """One exercise or assignment's identity: two at one instant are two only by reference."""

    return f"{instruction.asset_id}:{instruction.reference}"


def _boundary(after: float | None) -> tuple[float, int] | None:
    return None if after is None else (after, int(SchedulePlace.RECORDS))


@dataclass(frozen=True, slots=True)
class RunSchedule:
    """Every timed input of one run, in the order they are applied.

    Build one with :meth:`of`. Immutable, and identified by :attr:`digest`, so
    a paused replay can hold its continuation to the schedule it was paused on.

    Attributes:
        entries: Every entry, ordered by :attr:`ScheduleEntry.key`.
        rate_max_age_seconds: How stale a rate may be when it is used, carried
            onto the rate table; ``None`` checks nothing -- the tolerance is a
            desk's, as :meth:`~alphalab.portfolio.fx_feed.FxFeed.initialize`
            states.
    """

    entries: tuple[ScheduleEntry, ...] = ()
    rate_max_age_seconds: float | None = None

    @classmethod
    def of(
        cls,
        *,
        observations: Iterable[ObservationDelivery[Any]] = (),
        rates: Iterable[FxRate] = (),
        fundings: Iterable[Funding] = (),
        cash_flows: Iterable[ScheduledCashFlow] = (),
        distributions: Iterable[ScheduledDistribution] = (),
        funding_rates: Iterable[ScheduledFundingRate] = (),
        splits: Iterable[ScheduledSplit] = (),
        timers: Iterable[TimerEvent] = (),
        settlements: Iterable[ScheduledSettlement] = (),
        rate_max_age_seconds: float | None = None,
        exercises: Iterable[ScheduledExercise] = (),
        assignments: Iterable[ScheduledAssignment] = (),
        expiries: Iterable[ScheduledExpiry] = (),
        interest: Iterable[ScheduledInterest | DailyInterest] = (),
        borrow_fees: Iterable[ScheduledBorrowFee | DailyBorrowFee] = (),
        exchanges: Iterable[
            ScheduledExchange | PaidExchange | ElectiveExchange | DeliveredExchange
        ] = (),
        payments: Iterable[PaymentAmendment | DeliveryAmendment] = (),
        surfaces: Iterable[ObservedSurface] = (),
    ) -> RunSchedule:
        """A schedule of the inputs given, placed and ordered.

        Raises:
            ScheduleValidationError: If two entries of one kind name the same
                thing at the same instant -- one observation delivered twice, two
                rates for one pair as of one instant, one timer fired twice, a
                contract settled twice. Each is two answers to one question,
                and picking one would be arbitrary. Or a payment amendment
                names an exchange the schedule does not declare, or follows a
                failure or reversal of the same payment (FUT-034).
        """

        exchanges = tuple(exchanges)
        payments = tuple(payments)
        _require_payments(payments, exchanges)
        entries = [
            *(_entry(ScheduleKind.OBSERVATION, d.known_at, d.delivery_id, d) for d in observations),
            *(_entry(ScheduleKind.RATE, r.as_of, f"{r.base}/{r.quote}", r) for r in rates),
            *(_entry(ScheduleKind.FUNDING, f.at, f"{f.currency}:{f.amount}", f) for f in fundings),
            *(
                _entry(
                    ScheduleKind.CASH_FLOW,
                    c.at,
                    f"{c.flow.asset_id}:{c.flow.kind.value}:{c.flow.reference}:{c.flow.amount}",
                    c,
                )
                for c in cash_flows
            ),
            *(
                _entry(ScheduleKind.DISTRIBUTION, d.at, f"{d.asset_id}:{d.kind.value}", d)
                for d in distributions
            ),
            *(_entry(ScheduleKind.FUNDING_RATE, f.at, f.asset_id, f) for f in funding_rates),
            *(_entry(ScheduleKind.SPLIT, s.at, s.split.asset_id, s) for s in splits),
            *(_entry(ScheduleKind.TIMER, t.timestamp, t.timer_id, t) for t in timers),
            *(_entry(ScheduleKind.SETTLEMENT, s.at, s.settlement.asset_id, s) for s in settlements),
            *(_entry(ScheduleKind.EXERCISE, e.at, _instruction(e.exercise), e) for e in exercises),
            *(
                _entry(ScheduleKind.ASSIGNMENT, a.at, _instruction(a.assignment), a)
                for a in assignments
            ),
            *(_entry(ScheduleKind.EXPIRY, x.at, x.expiry.asset_id, x) for x in expiries),
            *(
                _entry(ScheduleKind.INTEREST, i.at, f"{i.currency}:{i.reference}", i)
                for i in interest
                if isinstance(i, ScheduledInterest)
            ),
            *(
                _entry(ScheduleKind.BORROW_FEE, b.at, b.reference, b)
                for b in borrow_fees
                if isinstance(b, ScheduledBorrowFee)
            ),
            *_daily_entries(interest, borrow_fees),
            # One exchange of an instrument at an instant: a second, conflicting
            # declaration of it is refused below, as two answers to one question.
            *(
                _entry(
                    ScheduleKind.EXCHANGE,
                    x.at,
                    x.asset_id if isinstance(x, ElectiveExchange) else x.exchange.asset_id,
                    x,
                )
                for x in exchanges
            ),
            # A surface delivered as data at its knowledge instant (FUT-039): one
            # source's one version once.
            *(
                _entry(
                    ScheduleKind.SURFACE,
                    o.known_at,
                    f"{o.source}|{o.version}|{o.surface.underlying_asset_id}",
                    o,
                )
                for o in surfaces
            ),
            # One word on one payment at an instant: two are two answers.
            *(_entry(ScheduleKind.PAYMENT, p.at, p.key, p) for p in payments),
            # An elective action's record instant (FUT-030), when it declares one.
            *(
                _entry(ScheduleKind.RECORD, x.record_at, x.reference, _RecordInstant(x))
                for x in exchanges
                if isinstance(x, ElectiveExchange) and x.record_at is not None
            ),
            # Each correction of an elective action's terms, recorded when
            # announced (FUT-038).
            *(
                _entry(
                    ScheduleKind.RECORD,
                    v.announced_at,
                    f"{x.reference}#{v.version}",
                    _Announcement(x, v.version),
                )
                for x in exchanges
                if isinstance(x, ElectiveExchange)
                for v in x.versions[1:]
            ),
        ]
        entries.sort(key=lambda entry: entry.key)
        for previous, current in pairwise(entries):
            if previous.key == current.key:
                raise ScheduleValidationError(
                    f"Two {current.kind.value} entries name {current.identity!r} at "
                    f"{current.at!r}. One input cannot be applied twice, and two values "
                    "for it cannot both be right; supply one."
                )
        return cls(tuple(entries), rate_max_age_seconds)

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def with_observations(self, observations: Iterable[ObservationDelivery[Any]]) -> RunSchedule:
        """This schedule with ``observations`` added, placed by the same rule."""

        added = tuple(observations)
        if not added:
            return self
        extra = RunSchedule.of(observations=added)
        merged = sorted((*self.entries, *extra.entries), key=lambda entry: entry.key)
        for previous, current in pairwise(merged):
            if previous.key == current.key:
                raise ScheduleValidationError(
                    f"Observation {current.identity!r} at {current.at!r} is given twice."
                )
        return RunSchedule(tuple(merged), self.rate_max_age_seconds)

    @property
    def digest(self) -> str:
        """SHA-256 over every entry's kind, instant and canonical JSON, in order.

        What a paused replay's payload holds its schedule to. Built from the
        encoder every payload uses, so it is the same on every host.
        """

        digest = hashlib.sha256()
        digest.update(repr(self.rate_max_age_seconds).encode(DEFAULT_ENCODING))
        for entry in self.entries:
            line = f"{entry.kind.value}|{entry.at!r}|{serialize(entry.item)}\n"
            digest.update(line.encode(DEFAULT_ENCODING))
        return digest.hexdigest()

    def position_after(self, after: float | None) -> int:
        """How many entries a run handed records up to ``after`` has applied.

        Every entry ordered before the records of ``after``'s instant; none for
        a run handed no record (``after`` is ``None``).
        """

        boundary = _boundary(after)
        if boundary is None:
            return 0
        low, high = 0, len(self.entries)
        while low < high:
            middle = (low + high) // 2
            if self.entries[middle].key[:2] < boundary:
                low = middle + 1
            else:
                high = middle
        return low

    def rates_through(self, position: int) -> FxRates:
        """The rate table the first ``position`` entries make."""

        table = FxRates(max_age_seconds=self.rate_max_age_seconds)
        for entry in self.entries[:position]:
            if entry.kind is ScheduleKind.RATE:
                table = table.with_rate(entry.item)
        return table


#: A schedule with nothing in it: a run of records alone, as before v4.1.1.
EMPTY_SCHEDULE: Final = RunSchedule()


@dataclass(frozen=True, slots=True)
class ScheduleCursor:
    """Where a run stands in its schedule, and the rate table it has built (v4.1.1).

    Immutable: each call returns the cursor after it with the run state it
    produced. Every historical driver, and a loop written by hand, takes the
    same three steps -- :meth:`start`, :meth:`before` each record, :meth:`finish`
    at the end of the data::

        cursor = ScheduleCursor.start(schedule)
        for record in records:
            cursor, state = cursor.before(state, record.timestamp, contexts)
            state = RunEngine.close_slice(state, contexts, cursor.rates, before=record.timestamp)
            state, _ = RunEngine.advance(state, record, contexts, rates=cursor.rates)
        cursor, state = cursor.finish(state, contexts)

    Attributes:
        schedule: The schedule.
        position: How many of its entries have been applied.
        rates: The rate table those entries made.
    """

    schedule: RunSchedule
    position: int
    rates: FxRates

    @classmethod
    def start(cls, schedule: RunSchedule, after: float | None = None) -> ScheduleCursor:
        """A cursor for a run handed records up to ``after`` -- none, by default.

        ``after`` is the timestamp of the last record the run was handed,
        processed or skipped: to continue a run from a snapshot or a pause, pass
        the timestamp of the record it stopped after.
        """

        position = schedule.position_after(after)
        return cls(schedule, position, schedule.rates_through(position))

    @property
    def pending(self) -> Sequence[ScheduleEntry]:
        """The entries not yet applied, in order."""

        return self.schedule.entries[self.position :]

    def before(
        self, state: RunState, timestamp: float, context_factory: ContextFactory
    ) -> tuple[ScheduleCursor, RunState]:
        """Apply, in order, every entry ordered before the records of ``timestamp``.

        Called with the timestamp of the record about to be handed to the run.
        """

        boundary = (timestamp, int(SchedulePlace.RECORDS))
        return self._apply_while(state, context_factory, lambda entry: entry.key[:2] < boundary)

    def finish(
        self, state: RunState, context_factory: ContextFactory, last: float | None = None
    ) -> tuple[ScheduleCursor, RunState]:
        """At the end of the data: apply what is at or before its last record's instant.

        ``last`` is the timestamp of the last record the run was handed --
        processed or skipped, as :meth:`start`'s ``after`` is; the drivers pass
        it. Not given, it is ``state.last_record_timestamp``, the last record
        processed, which is the same unless the data ends in skipped records.
        An entry after it is beyond the data, and is not applied.
        """

        end = state.last_record_timestamp if last is None else last
        if end is None:
            return self, state
        return self._apply_while(state, context_factory, lambda entry: entry.at <= end)

    def _apply_while(
        self,
        state: RunState,
        context_factory: ContextFactory,
        due: Callable[[ScheduleEntry], bool],
    ) -> tuple[ScheduleCursor, RunState]:
        entries = self.schedule.entries
        position, rates = self.position, self.rates
        while position < len(entries) and due(entries[position]):
            entry = entries[position]
            # The slice of an instant closes before anything later than it.
            state = RunEngine.close_slice(state, context_factory, rates, before=entry.at)
            rates, state = _apply(entry, state, rates, context_factory)
            position += 1
        if position == self.position:
            return self, state
        return ScheduleCursor(self.schedule, position, rates), state


def _apply(
    entry: ScheduleEntry, state: RunState, rates: FxRates, context_factory: ContextFactory
) -> tuple[FxRates, RunState]:
    """Apply one entry through the run's own operation for it."""

    item = entry.item
    kind = entry.kind
    if kind is ScheduleKind.RATE:
        return rates.with_rate(item), state
    if kind is ScheduleKind.FUNDING:
        return rates, RunEngine.fund(state, item.amount, item.currency, item.at, rates)
    if kind is ScheduleKind.CASH_FLOW:
        return rates, RunEngine.apply_cash_flow(state, item.flow, item.at, rates)
    if kind is ScheduleKind.DISTRIBUTION:
        flow = _distribution(state, item)
        if flow is None:
            return rates, state
        return rates, RunEngine.apply_cash_flow(state, flow, item.at, rates)
    if kind is ScheduleKind.FUNDING_RATE:
        flow = _funding(state, item)
        if flow is None:
            return rates, state
        return rates, RunEngine.apply_cash_flow(state, flow, item.at, rates)
    if kind is ScheduleKind.SPLIT:
        return rates, RunEngine.apply_split(state, item.split, item.at, rates)
    if kind is ScheduleKind.EXCHANGE and isinstance(item, ElectiveExchange):
        parts = _election_parts(state, item)
        final = item.final
        delivers = dict(item.deliveries) or None
        convention = item.convention if item.deliveries else None
        if len(parts) <= 1:
            # One holder, or holders agreeing: the holding exchanged whole.
            chosen = final.alternatives[parts[0][0] if parts else final.default]
            return rates, RunEngine.apply_exchange(
                state, chosen, item.at, rates, payment_at=item.payment_at,
                delivers_at=delivers, delivery=convention,
            )  # fmt: skip
        for index, part in parts:
            state = RunEngine.apply_exchange(
                state, final.alternatives[index], item.at, rates,
                payment_at=item.payment_at, part=part, delivers_at=delivers, delivery=convention,
            )  # fmt: skip
        return rates, state
    if kind is ScheduleKind.RECORD and isinstance(item, _Announcement):
        return rates, RunEngine.record_terms(
            state, item.action.asset_id, item.action.reference, item.version, entry.at
        )
    if kind is ScheduleKind.RECORD:
        action = item.action
        return rates, RunEngine.record_holders(state, action.asset_id, action.reference, entry.at)
    if kind is ScheduleKind.EXCHANGE and isinstance(item, DeliveredExchange):
        return rates, RunEngine.apply_exchange(
            state, item.exchange, item.at, rates, payment_at=item.payment_at,
            delivers_at=dict(item.deliveries), delivery=item.convention,
        )  # fmt: skip
    if kind is ScheduleKind.EXCHANGE:
        paid = item.payment_at if isinstance(item, PaidExchange) else None
        return rates, RunEngine.apply_exchange(
            state, item.exchange, item.at, rates, payment_at=paid
        )
    if kind is ScheduleKind.SURFACE:
        return rates, RunEngine.deliver_surface(state, item)
    if kind is ScheduleKind.PAYMENT and isinstance(item, DeliveryAmendment):
        return rates, RunEngine.amend_delivery(
            state, item.holding, item.effective_at, item.asset_id, item.change, item.at, rates,
            delivers_at=item.delivers_at, units=item.units, reference=item.reference,
        )  # fmt: skip
    if kind is ScheduleKind.PAYMENT:
        return rates, RunEngine.amend_payment(
            state, item.holding, item.effective_at, item.currency, item.change, item.at, rates,
            per_unit=item.per_unit, replacement_currency=item.replacement_currency,
            payment_at=item.payment_at, reference=item.reference,
        )  # fmt: skip
    if kind is ScheduleKind.OBSERVATION:
        return rates, RunEngine.deliver_observation(state, item, context_factory, rates)
    if kind is ScheduleKind.TIMER:
        return rates, RunEngine.fire_timer(state, item, context_factory, rates)
    if kind is ScheduleKind.EXERCISE:
        return rates, RunEngine.exercise(state, item.exercise, item.at, rates)
    if kind is ScheduleKind.ASSIGNMENT:
        return rates, RunEngine.assign(state, item.assignment, item.at, rates)
    if kind is ScheduleKind.EXPIRY:
        return rates, RunEngine.expire_option(state, item.expiry, item.at, rates)
    if kind is ScheduleKind.ACCRUAL:
        return rates, _accrue_day(state, item, entry.at)
    if kind is ScheduleKind.INTEREST and isinstance(item, DailyInterest):
        period = item.interest
        return rates, RunEngine.post_accrual(
            state,
            item.key,
            CashFlowKind.INTEREST,
            period.currency,
            "",
            period.reference,
            period.at,
            rates,
        )
    if kind is ScheduleKind.BORROW_FEE and isinstance(item, DailyBorrowFee):
        return rates, _post_borrow_fees(state, item, rates)
    if kind is ScheduleKind.INTEREST:
        flow = _interest(state, item)
        if flow is None:
            return rates, state
        return rates, RunEngine.apply_cash_flow(state, flow, item.at, rates)
    if kind is ScheduleKind.BORROW_FEE:
        for flow in _borrow_fees(state, item):
            state = RunEngine.apply_cash_flow(state, flow, item.at, rates)
        return rates, state
    return rates, RunEngine.settle_contract(state, item.settlement, item.at, rates)


def _held(state: RunState, asset_id: str) -> Any:
    position = state.pipeline.portfolio.positions.get(asset_id)
    return None if position is None or position.quantity == 0 else position


def _distribution(state: RunState, item: ScheduledDistribution) -> CashFlow | None:
    """The cash a per-unit distribution pays the position held now, or ``None`` for none."""

    position = _held(state, item.asset_id)
    if position is None:
        return None
    amount = ACCOUNTING_CONTEXT.multiply(item.per_unit, position.quantity)
    if amount == 0:
        return None
    return CashFlow(item.kind, amount, item.currency, item.asset_id, item.reference)


def _funding(state: RunState, item: ScheduledFundingRate) -> CashFlow | None:
    """What one funding interval costs the position held now, at the run's last mark.

    Raises:
        ScheduleValidationError: If the run holds the contract but has never
            priced it -- a funding payment needs a mark.
    """

    position = _held(state, item.asset_id)
    if position is None:
        return None
    mark = state.pipeline.market_prices.get(item.asset_id)
    if mark is None:
        raise ScheduleValidationError(
            f"A funding rate for {item.asset_id} at {item.at!r} needs the contract's mark, "
            "and the run has none."
        )
    ctx = ACCOUNTING_CONTEXT
    notional = ctx.multiply(ctx.multiply(position.quantity, position.multiplier), mark)
    amount = -ctx.multiply(notional, item.rate)
    if amount == 0:
        return None
    return CashFlow(CashFlowKind.FUNDING, amount, position.currency, item.asset_id, item.reference)


def _interest(state: RunState, item: ScheduledInterest) -> CashFlow | None:
    """What a period's declared terms charge or pay on the currency's balance at its instant."""

    portfolio = state.pipeline.portfolio
    if item.basis is BalanceBasis.SETTLED:
        balance = PortfolioEngine.cash_position(portfolio, item.currency, item.at).settled
    else:
        balance = portfolio.cash.balance(item.currency)
    fraction = day_fraction(item.start, item.end, item.day_count)
    amount = accrued_interest(balance, item.debit_tiers, item.credit_rate, fraction)
    if amount == 0:
        return None
    return CashFlow(CashFlowKind.INTEREST, amount, item.currency, "", item.reference)


@dataclass(frozen=True, slots=True)
class _Announcement:
    """A correction of an action's terms, as a schedule entry at its announcement (FUT-038)."""

    action: ElectiveExchange
    version: int


@dataclass(frozen=True, slots=True)
class _RecordInstant:
    """An elective action's record instant, as a schedule entry (FUT-030)."""

    action: ElectiveExchange


def _election_parts(
    state: RunState, action: ElectiveExchange
) -> list[tuple[int, dict[str, Decimal]]]:
    """Each alternative the holding's holders elected, and each holder's units of it (FUT-030).

    A holder is a strategy with a share as the action takes effect. Its counted
    elections -- between the announcement and the deadline, naming one of the
    alternatives -- are folded in order: a whole-holding election replaces
    every earlier one, a part election replaces an earlier one for its
    alternative. It may elect for no more than it holds, and no more than the
    record instant recorded, when the action declares one; elections for more
    are not counted, and the default takes the whole. An alternative prorated
    accepts its factor of each election, rounded down to whole units where the
    share is whole; the default takes what is not elected or not accepted.
    Ordered by alternative, each part signed as its holder's share.
    """

    ctx = ACCOUNTING_CONTEXT
    shares = {
        sid: share
        for sid, share in AllocationEngine.shares_of(state.pipeline.allocation, action.asset_id)
        if share
    }
    recorded: Mapping[str, Decimal] | None = None
    events = state.pipeline.strategy.events
    if action.record_at is not None:
        recorded = {}
        for event in events:
            if (
                isinstance(event, HoldersRecorded)
                and event.asset_id == action.asset_id
                and event.action == action.reference
            ):
                recorded = event.holders
    final = action.final
    offered = range(len(final.alternatives))
    voided_until = max(
        (v.announced_at for v in action.versions if v.voids_earlier),
        default=None,
    )
    elected: dict[str, dict[int, Decimal | None]] = {sid: {} for sid in shares}
    for event in events:
        if not (
            isinstance(event, ElectionMade | UnitsElected)
            and event.strategy_id in shares
            and event.asset_id == action.asset_id
            and event.action == action.reference
            and event.alternative in offered
        ):
            continue
        # Read against the version in effect when it was made (FUT-038): after
        # its announcement, by its deadline, and not voided by a later one.
        made_under = action.terms_at(event.timestamp)
        if made_under is None or event.timestamp > made_under.deadline:
            continue
        if voided_until is not None and event.timestamp < voided_until:
            continue
        if isinstance(event, UnitsElected):
            choices = elected[event.strategy_id]
            choices.pop(next((k for k, v in choices.items() if v is None), -1), None)
            choices[event.alternative] = event.units
        else:
            elected[event.strategy_id] = {event.alternative: None}
    factors = dict(final.proration)
    parts: dict[int, dict[str, Decimal]] = {}
    for sid, share in sorted(shares.items()):
        sign = Decimal("1") if share > 0 else Decimal("-1")
        held = share.copy_abs()
        eligible = held
        if recorded is not None:
            then = recorded.get(sid, Decimal("0"))
            eligible = min(held, then.copy_abs()) if (then > 0) == (share > 0) else Decimal("0")
        choices = elected[sid]
        asked = {k: eligible if v is None else v for k, v in choices.items()}
        if sum(asked.values(), Decimal("0")) > eligible:
            asked = {}  # elected for more than it may: not counted
        whole = held == held.to_integral_value()
        accepted: dict[int, Decimal] = {}
        for index, units in sorted(asked.items()):
            if index == final.default:
                continue
            taken = ctx.multiply(units, factors.get(index, Decimal("1")))
            if whole:
                taken = taken.to_integral_value(rounding=decimal.ROUND_FLOOR)
            if taken > 0:
                accepted[index] = taken
        rest = ctx.subtract(held, sum(accepted.values(), Decimal("0")))
        if rest > 0:
            accepted[final.default] = rest
        for index, units in accepted.items():
            parts.setdefault(index, {})[sid] = ctx.multiply(sign, units)
    return sorted(parts.items())


def _elections(state: RunState, action: ElectiveExchange) -> dict[str, int | None]:
    """Each holder's latest counted election in ``action``, or ``None`` for none (FUT-024).

    A holder is a strategy with a share of the holding as the action takes
    effect; an election counts when it names the action and one of its
    alternatives, between its announcement and its deadline.
    """

    holders: dict[str, int | None] = {
        sid: None
        for sid, share in AllocationEngine.shares_of(state.pipeline.allocation, action.asset_id)
        if share
    }
    offered = range(len(action.alternatives))
    for event in state.pipeline.strategy.events:
        if (
            isinstance(event, ElectionMade)
            and event.strategy_id in holders
            and event.asset_id == action.asset_id
            and event.action == action.reference
            and action.announced_at <= event.timestamp <= action.deadline
            and event.alternative in offered
        ):
            holders[event.strategy_id] = event.alternative
    return holders


def _accrue_day(state: RunState, item: _AccrualDay, at: float) -> RunState:
    """Accrue one day of a daily period on the book as its cutoff leaves it (FUT-023)."""

    period = item.of
    if isinstance(period, DailyInterest):
        interest = period.interest
        portfolio = state.pipeline.portfolio
        if interest.basis is BalanceBasis.SETTLED:
            balance = PortfolioEngine.cash_position(portfolio, interest.currency, at).settled
        else:
            balance = portfolio.cash.balance(interest.currency)
        tiers, credit = period.rates_on(item.day)
        amount = accrued_interest(balance, tiers, credit, _day_weight(item.day, interest.day_count))
        if amount == 0:
            return state
        return RunEngine.record_accrual(state, period.key, amount, interest.currency, at)
    registry = state.config.pipeline.instruments
    if registry is None:
        return state
    ctx = ACCOUNTING_CONTEXT
    for asset_id, position in state.pipeline.portfolio.positions.items():
        if position.quantity >= 0:
            continue
        record = registry.record_for(asset_id)
        terms = None if record is None else record.borrow
        if terms is None:
            continue
        entry = terms.in_effect(at)
        mark = state.pipeline.market_prices.get(asset_id)
        if entry is None or mark is None:
            raise ScheduleValidationError(
                f"{asset_id} is held short at {at!r} with no borrow entry in effect or no mark "
                "to value its day's fee by."
            )
        value = ctx.multiply(ctx.multiply(position.quantity.copy_abs(), position.multiplier), mark)
        weight = _day_weight(item.day, terms.day_count)
        amount = -ctx.multiply(ctx.multiply(value, entry.annual_rate), weight)
        if amount != 0:
            state = RunEngine.record_accrual(
                state, period.key(asset_id), amount, position.currency, at
            )
    return state


def _day_weight(day: date, basis: DayCount) -> Decimal:
    """One day as a fraction of its day count's year: what the count counts it as."""

    counted = day_count_days(day, day + timedelta(days=1), basis)
    return ACCOUNTING_CONTEXT.divide(Decimal(counted), Decimal(days_in_year(basis)))


def _post_borrow_fees(state: RunState, item: DailyBorrowFee, rates: FxRates) -> RunState:
    """Post every instrument's fee the period accrued, once each (FUT-023)."""

    # Every key of this period ends in what ``key("")`` does after its asset.
    head, suffix = "borrow:", item.key("")[len("borrow:") :]
    accrued: dict[str, str] = {}
    for tx in state.pipeline.portfolio.ledger.transactions:
        key = tx.metadata.get(ACCRUES) if tx.type is TransactionType.ACCRUAL else None
        if isinstance(key, str) and key.startswith(head) and key.endswith(suffix):
            accrued.setdefault(key, tx.currency)
    for key, currency in accrued.items():
        asset_id = key[len(head) : len(key) - len(suffix)]
        state = RunEngine.post_accrual(
            state,
            key,
            CashFlowKind.FEE,
            currency,
            asset_id,
            item.fee.reference,
            item.fee.at,
            rates,
        )
    return state


def _borrow_fees(state: RunState, item: ScheduledBorrowFee) -> tuple[CashFlow, ...]:
    """Each short's fee for the period, by its instrument's borrow terms in effect at ``at``.

    Raises:
        ScheduleValidationError: If a short in an instrument declaring borrow
            terms has no mark, or no borrow was in effect at ``at`` -- a short
            the run holds (from a delivery, say) with nothing declared to cost it.
    """

    registry = state.config.pipeline.instruments
    if registry is None:
        return ()
    ctx = ACCOUNTING_CONTEXT
    flows = []
    for asset_id, position in state.pipeline.portfolio.positions.items():
        if position.quantity >= 0:
            continue
        record = registry.record_for(asset_id)
        terms = None if record is None else record.borrow
        if terms is None:
            continue
        entry = terms.in_effect(item.at)
        if entry is None:
            raise ScheduleValidationError(
                f"{asset_id} is held short at {item.at!r}, and its borrow terms have no entry "
                "in effect then: declare what the borrow cost from the instant it was held."
            )
        mark = state.pipeline.market_prices.get(asset_id)
        if mark is None:
            raise ScheduleValidationError(
                f"A borrow fee on {asset_id} at {item.at!r} needs its mark, and the run has none."
            )
        value = ctx.multiply(ctx.multiply(position.quantity.copy_abs(), position.multiplier), mark)
        fraction = day_fraction(item.start, item.end, terms.day_count)
        amount = -ctx.multiply(ctx.multiply(value, entry.annual_rate), fraction)
        if amount != 0:
            flows.append(
                CashFlow(CashFlowKind.FEE, amount, position.currency, asset_id, item.reference)
            )
    return tuple(flows)
