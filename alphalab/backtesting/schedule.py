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

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum, StrEnum, unique
from itertools import pairwise
from typing import Any, Final

from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.backtesting.exceptions import BacktestError
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.common.constants import DEFAULT_ENCODING
from alphalab.persistence.serializer import serialize
from alphalab.portfolio.corporate_actions import (
    Assignment,
    CashFlow,
    CashFlowKind,
    ContractSettlement,
    Exercise,
    OptionExpiry,
    Split,
)
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.runtime.execution_pipeline import ContextFactory
from alphalab.runtime.run import RunEngine, RunState
from alphalab.strategy.events import TimerEvent

__all__ = [
    "EMPTY_SCHEDULE",
    "Funding",
    "RunSchedule",
    "ScheduleCursor",
    "ScheduleEntry",
    "ScheduleKind",
    "SchedulePlace",
    "ScheduleValidationError",
    "ScheduledAssignment",
    "ScheduledCashFlow",
    "ScheduledDistribution",
    "ScheduledExercise",
    "ScheduledExpiry",
    "ScheduledFundingRate",
    "ScheduledSettlement",
    "ScheduledSplit",
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
class ScheduledSplit:
    """A split, reverse split or stock dividend, effective at ``at``."""

    split: Split
    at: float


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
    ) -> RunSchedule:
        """A schedule of the inputs given, placed and ordered.

        Raises:
            ScheduleValidationError: If two entries of one kind name the same
                thing at the same instant -- one observation delivered twice, two
                rates for one pair as of one instant, one timer fired twice, a
                contract settled twice. Each is two answers to one question,
                and picking one would be arbitrary.
        """

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
