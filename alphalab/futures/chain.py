"""The contract chain, the roll rule, and what makes a continuous series reproducible.

:func:`alphalab.futures.roll.build_continuous_series` has been able to splice
segments since v1: hand it ordered :class:`~alphalab.futures.roll.RollSegment`
objects, each already carrying the pair of prices observed at its roll, and it
produces an adjusted series. What it could not say is **which** contract was
front on any given day, or **when** the roll happened, or **why**. The caller
decided all three and the series recorded none of them.

That is the gap this module closes, and it is the difference between a
continuous series and a plausible one. Two researchers handed the same contracts
and the same bars could previously produce different series, both correct
according to ``build_continuous_series``, differing only in a choice neither of
them wrote down.

Four separate things
--------------------

The v3.4 brief asks for these to stay apart, and they do:

1. **Raw contract observations** -- bars, keyed by ``futures_symbol``. Supplied.
2. **The selected active contract** -- :func:`active_contract_at`, a pure
   function of the chain, the policy and an instant.
3. **Roll events** -- :func:`roll_schedule`, which says when each handover
   happened and which rule produced it.
4. **The continuous representation** -- :func:`continuous_segments` feeding
   ``build_continuous_series``, whose
   :class:`~alphalab.futures.roll.AdjustmentMethod` is a fifth decision the
   caller still makes explicitly.

Given the same chain, policy, observations and adjustment method, the series is
byte-identical. Change any one of them and it changes, visibly.

Point in time
-------------

An expiry is known when the contract is listed, so selecting on days-to-expiry
reads nothing from the future. A volume crossover does not have that property:
deciding today's front month from a volume comparison requires volume, and
volume printed after the decision instant would be a look-ahead.
:func:`roll_schedule` therefore reads each contract's bars **strictly at or
before** the instant it is deciding, and
``tests/regression/test_v34_invariants.py`` holds that property by truncating
the observations and requiring the earlier rolls to be unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum, auto
from itertools import pairwise
from typing import Protocol

from alphalab.futures.contract import FutureContract, futures_symbol
from alphalab.futures.exceptions import FuturesInputError
from alphalab.futures.roll import RollSegment
from alphalab.market.bar import Bar

__all__ = [
    "ContractChain",
    "RollEvent",
    "RollPolicy",
    "RollTrigger",
    "SessionCalendar",
    "active_contract_at",
    "continuous_segments",
    "roll_schedule",
]


class SessionCalendar(Protocol):
    """What a trading-day roll rule needs from a calendar.

    Structural, so that :mod:`alphalab.futures` does not import
    :mod:`alphalab.data` for a single method and
    :class:`alphalab.data.calendar.MarketCalendar` stays the one calendar
    authority. The same device
    :class:`alphalab.conventions.settlement.TradingDayCalendar` uses.
    """

    def is_trading_day(self, day: date) -> bool: ...


class RollTrigger(Enum):
    """What decides that the front contract has changed.

    There is no default. Every futures research result depends on the roll rule
    -- a 5-day and a 10-day roll on the same chain produce different returns on
    the same days -- so a continuous series whose rule was assumed is a result
    nobody can reproduce.
    """

    #: Roll ``offset_days`` calendar days before the outgoing contract's expiry.
    #: Needs no calendar, and counts the weekend.
    CALENDAR_DAYS_BEFORE_EXPIRY = auto()

    #: Roll ``offset_days`` *trading* days before expiry, on a supplied
    #: calendar. Not interchangeable with the above: five trading days before a
    #: December expiry can be nine calendar days back once a holiday lands in
    #: the window.
    TRADING_DAYS_BEFORE_EXPIRY = auto()

    #: Roll on the first observation where the next contract trades more volume
    #: than the front one. The rule most desks actually use, and the only one
    #: here that needs market data -- which it therefore **requires** rather
    #: than approximating from dates when none is supplied.
    VOLUME_CROSSOVER = auto()


@dataclass(frozen=True, slots=True)
class RollPolicy:
    """A roll rule, stated completely enough to be replayed.

    Attributes:
        trigger: What decides the handover.
        offset_days: How many days before expiry, for the two date triggers.
            Required for those and refused for
            :attr:`RollTrigger.VOLUME_CROSSOVER`, where it would have no effect
            and would suggest one.

    Raises:
        FuturesInputError: If the offset is absent where it is needed, present
            where it is not, or negative.
    """

    trigger: RollTrigger
    offset_days: int | None = None

    def __post_init__(self) -> None:
        date_based = self.trigger in (
            RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY,
            RollTrigger.TRADING_DAYS_BEFORE_EXPIRY,
        )
        if date_based and self.offset_days is None:
            raise FuturesInputError(
                f"{self.trigger.name} rolls a stated number of days before expiry and has no "
                "default number. Name offset_days."
            )
        if not date_based and self.offset_days is not None:
            raise FuturesInputError(
                f"{self.trigger.name} does not read a date offset, so offset_days="
                f"{self.offset_days} would have no effect on when the roll happens."
            )
        if self.offset_days is not None and self.offset_days < 0:
            raise FuturesInputError(
                f"offset_days is {self.offset_days}; a roll happens before expiry, not after it."
            )

    @property
    def needs_calendar(self) -> bool:
        """Whether resolving this policy requires a calendar."""

        return self.trigger is RollTrigger.TRADING_DAYS_BEFORE_EXPIRY

    @property
    def needs_observations(self) -> bool:
        """Whether resolving this policy requires market data."""

        return self.trigger is RollTrigger.VOLUME_CROSSOVER

    @property
    def label(self) -> str:
        """Short rendering for a report or a refusal message."""

        if self.trigger is RollTrigger.VOLUME_CROSSOVER:
            return "volume crossover"
        unit = "calendar" if self.trigger is RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY else "trading"
        return f"{self.offset_days} {unit} day(s) before expiry"


@dataclass(frozen=True, slots=True)
class ContractChain:
    """Every listed month of one root, in expiry order.

    A chain is more than a list: it is the assertion that these contracts are
    *the same instrument at different maturities*, which is what makes splicing
    them into one series meaningful. So the terms that decide what a price
    *means* -- multiplier, tick size and currency -- must agree across the
    chain, and a chain that mixes them is refused rather than spliced into a
    series whose earlier half is denominated differently from its later half.

    Attributes:
        root: The contract root, e.g. ``"CL"``. Matches every contract's
            ``underlying_asset_id``.
        contracts: Ascending by expiry, at least one.

    Raises:
        FuturesInputError: If the chain is empty, names a root a contract does
            not carry, repeats or mis-orders an expiry, or mixes multipliers,
            tick sizes or currencies.
    """

    root: str
    contracts: tuple[FutureContract, ...]

    def __post_init__(self) -> None:
        if not self.root.strip():
            raise FuturesInputError("A contract chain names its root.")
        if not self.contracts:
            raise FuturesInputError(f"{self.root}: a chain holds at least one contract.")
        first = self.contracts[0]
        for index, contract in enumerate(self.contracts):
            if contract.underlying_asset_id != self.root:
                raise FuturesInputError(
                    f"{self.root}: contract {index} is on "
                    f"{contract.underlying_asset_id!r}. A chain splices one root."
                )
            if contract.multiplier != first.multiplier:
                raise FuturesInputError(
                    f"{self.root}: contract {index} has multiplier {contract.multiplier} and "
                    f"the first has {first.multiplier}. A series spliced across two "
                    "multipliers is not one price series -- its halves are in different units."
                )
            if contract.tick_size != first.tick_size:
                raise FuturesInputError(
                    f"{self.root}: contract {index} ticks in {contract.tick_size} and the "
                    f"first in {first.tick_size}."
                )
            if contract.currency != first.currency:
                raise FuturesInputError(
                    f"{self.root}: contract {index} settles in {contract.currency} and the "
                    f"first in {first.currency}. Splicing them would sum two currencies."
                )
        for index, (earlier, later) in enumerate(pairwise(self.contracts)):
            if later.expiry <= earlier.expiry:
                raise FuturesInputError(
                    f"{self.root}: contract {index + 1} expires at {later.expiry}, at or "
                    f"before contract {index} at {earlier.expiry}. A chain is ordered by "
                    "expiry and holds each month once."
                )

    @property
    def symbols(self) -> tuple[str, ...]:
        """``futures_symbol`` of every contract, in chain order."""

        return tuple(futures_symbol(contract) for contract in self.contracts)

    @property
    def multiplier(self) -> int:
        """The multiplier every contract in the chain shares."""

        return self.contracts[0].multiplier

    @property
    def currency(self) -> str:
        """The settlement currency every contract in the chain shares."""

        return self.contracts[0].currency


@dataclass(frozen=True, slots=True)
class RollEvent:
    """One handover from the front contract to the next.

    Attributes:
        timestamp: When the handover took effect, in Unix seconds. From this
            instant on, ``incoming`` is the front contract.
        outgoing: The contract being rolled out of.
        incoming: The contract being rolled into.
        trigger: Which rule produced this event.
        reason: A sentence a person auditing the series can read. Free-form and
            never parsed.
    """

    timestamp: float
    outgoing: FutureContract
    incoming: FutureContract
    trigger: RollTrigger
    reason: str


def _expiry_date(contract: FutureContract) -> date:
    """The contract's expiry as a UTC calendar date.

    UTC because ``FutureContract`` carries an instant and names no venue
    timezone; ``futures_symbol`` has read its ``contract_month`` in UTC since
    v1 and the two must agree. A contract whose roll must be timed in the
    venue's local day resolves the date with
    :meth:`alphalab.data.calendar.MarketCalendar.trading_day_of` and states the
    resulting instant itself.
    """

    return datetime.fromtimestamp(contract.expiry, tz=UTC).date()


def _date_roll_instant(
    contract: FutureContract, policy: RollPolicy, calendar: SessionCalendar | None
) -> float:
    """The instant a date-triggered roll takes effect for one contract."""

    offset = policy.offset_days
    assert offset is not None  # established by RollPolicy.__post_init__
    if policy.trigger is RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY:
        return contract.expiry - offset * 86400.0
    assert calendar is not None  # established by roll_schedule's guard
    cursor = _expiry_date(contract)
    remaining = offset
    for _ in range(400):
        if remaining == 0:
            break
        cursor -= timedelta(days=1)
        if calendar.is_trading_day(cursor):
            remaining -= 1
    else:
        raise FuturesInputError(
            f"{futures_symbol(contract)}: the calendar declares fewer than {offset} trading "
            "day(s) in the 400 days before expiry, so the roll date cannot be located."
        )
    days_back = (_expiry_date(contract) - cursor).days
    return contract.expiry - days_back * 86400.0


def _crossover_instant(
    outgoing: FutureContract,
    incoming: FutureContract,
    observations: Mapping[str, Sequence[Bar]],
) -> float | None:
    """The first instant the incoming contract out-trades the outgoing one.

    Compares only bars the two contracts share a timestamp on: a crossover is a
    statement about one instant, and pairing a front-month bar with the next
    month's bar from a different day would compare two different markets.
    """

    front = {bar.timestamp: bar.volume for bar in observations.get(futures_symbol(outgoing), ())}
    back = {bar.timestamp: bar.volume for bar in observations.get(futures_symbol(incoming), ())}
    shared = sorted(set(front) & set(back))
    for timestamp in shared:
        if back[timestamp] > front[timestamp]:
            return timestamp
    return None


def roll_schedule(
    chain: ContractChain,
    policy: RollPolicy,
    calendar: SessionCalendar | None = None,
    observations: Mapping[str, Sequence[Bar]] | None = None,
) -> tuple[RollEvent, ...]:
    """Every handover in a chain, under one policy, in chronological order.

    A chain of *n* contracts has *n - 1* rolls: the last contract is the one
    still front at the end and is never rolled out of.

    Args:
        chain: The contracts to roll through.
        policy: The rule that decides when.
        calendar: Required for
            :attr:`RollTrigger.TRADING_DAYS_BEFORE_EXPIRY` and refused
            otherwise, where supplying one would suggest holidays were skipped.
        observations: Bars per ``futures_symbol``. Required for
            :attr:`RollTrigger.VOLUME_CROSSOVER` and refused otherwise.

    Raises:
        FuturesInputError: If a required input is absent, an unused one is
            supplied, or a volume crossover never occurs between two adjacent
            contracts -- which is a missing observation rather than a roll at a
            date this function would have to invent.
    """

    if policy.needs_calendar and calendar is None:
        raise FuturesInputError(
            f"{policy.label} counts trading days, which needs the venue's calendar. Counting "
            "calendar days instead would roll on a day the market was shut."
        )
    if not policy.needs_calendar and calendar is not None:
        raise FuturesInputError(
            f"{policy.label} reads no calendar, so supplying one suggests holidays affect the "
            "roll date when they do not."
        )
    if policy.needs_observations and observations is None:
        raise FuturesInputError(
            "A volume crossover is decided by volume, and none was supplied. Supply the bars "
            "per contract, or name a date-based trigger -- approximating a crossover from "
            "expiry dates would be a different rule reported under this one's name."
        )
    if not policy.needs_observations and observations is not None:
        raise FuturesInputError(
            f"{policy.label} is decided by dates alone, so the observations would not affect "
            "the roll instant."
        )

    events: list[RollEvent] = []
    for outgoing, incoming in pairwise(chain.contracts):
        if policy.needs_observations:
            assert observations is not None  # established by the guard above
            instant = _crossover_instant(outgoing, incoming, observations)
            if instant is None:
                raise FuturesInputError(
                    f"{futures_symbol(incoming)} never out-trades {futures_symbol(outgoing)} "
                    "on any timestamp they both report. The crossover did not happen in the "
                    "data supplied, and choosing a date instead would be a different policy."
                )
            reason = (
                f"{futures_symbol(incoming)} first traded more volume than "
                f"{futures_symbol(outgoing)} at {instant}"
            )
        else:
            instant = _date_roll_instant(outgoing, policy, calendar)
            reason = f"{policy.label} for {futures_symbol(outgoing)}"
        events.append(
            RollEvent(
                timestamp=instant,
                outgoing=outgoing,
                incoming=incoming,
                trigger=policy.trigger,
                reason=reason,
            )
        )

    for index, (earlier, later) in enumerate(pairwise(events)):
        if later.timestamp <= earlier.timestamp:
            raise FuturesInputError(
                f"{chain.root}: roll {index + 1} at {later.timestamp} is not after roll "
                f"{index} at {earlier.timestamp}. Rolls under one policy are ordered, and a "
                "series built from these would hold two front contracts at once."
            )
    return tuple(events)


def active_contract_at(
    chain: ContractChain,
    rolls: Sequence[RollEvent],
    timestamp: float,
) -> FutureContract:
    """Which contract was front at an instant, given a schedule.

    Takes the schedule rather than the policy so that one call to
    :func:`roll_schedule` answers for a whole backtest: recomputing the rolls
    per bar would be the same answer at *n* times the cost, and a schedule
    computed once is a schedule a reader can inspect.

    Before the first roll the earliest contract is front; after the last, the
    latest. A chain of one contract is that contract at every instant.
    """

    active = chain.contracts[0]
    for event in rolls:
        if timestamp >= event.timestamp:
            active = event.incoming
        else:
            break
    return active


def continuous_segments(
    chain: ContractChain,
    rolls: Sequence[RollEvent],
    observations: Mapping[str, Sequence[Bar]],
) -> tuple[RollSegment, ...]:
    """Split observations into the non-overlapping segments a splice needs.

    Each contract contributes exactly the bars for which it was the front
    contract, and each non-final segment carries the outgoing and incoming
    prices **observed at its roll instant** -- so the gap
    ``build_continuous_series`` adjusts for is a pair of real prints rather than
    a difference between two contracts' closes on days they were not compared.

    The result feeds :func:`alphalab.futures.roll.build_continuous_series`
    unchanged. The adjustment method stays that function's argument: how to
    absorb the gap is a separate decision from when the gap happened, and
    ``BACK_ADJUSTED`` and ``RATIO_ADJUSTED`` answer different questions about
    the same rolls.

    Raises:
        FuturesInputError: If a contract that was front for some interval has no
            bars, or if either side of a roll has no bar at the roll instant.
            Interpolating one would invent the very print the adjustment is
            computed from.
    """

    boundaries: list[tuple[FutureContract, float, float]] = []
    start = float("-inf")
    for event in rolls:
        boundaries.append((event.outgoing, start, event.timestamp))
        start = event.timestamp
    boundaries.append((chain.contracts[len(rolls)], start, float("inf")))

    segments: list[RollSegment] = []
    for index, (contract, lower, upper) in enumerate(boundaries):
        symbol = futures_symbol(contract)
        bars = tuple(
            bar
            for bar in sorted(observations.get(symbol, ()), key=lambda bar: bar.timestamp)
            if lower <= bar.timestamp < upper
        )
        if not bars:
            raise FuturesInputError(
                f"{symbol} was the front contract between {lower} and {upper} and has no bars "
                "in that interval. A segment with no observations cannot contribute to a "
                "series, and skipping it would silently shorten the history."
            )
        if index == len(boundaries) - 1:
            segments.append(RollSegment(contract=contract, bars=bars))
            continue

        event = rolls[index]
        outgoing_price = _price_at(observations, futures_symbol(event.outgoing), event.timestamp)
        incoming_price = _price_at(observations, futures_symbol(event.incoming), event.timestamp)
        segments.append(
            RollSegment(
                contract=contract,
                bars=bars,
                outgoing_roll_price=outgoing_price,
                incoming_roll_price=incoming_price,
            )
        )
    return tuple(segments)


def _price_at(observations: Mapping[str, Sequence[Bar]], symbol: str, timestamp: float) -> Decimal:
    """The close printed by ``symbol`` at exactly ``timestamp``."""

    for bar in observations.get(symbol, ()):
        if bar.timestamp == timestamp:
            return bar.close
    raise FuturesInputError(
        f"{symbol} printed no bar at the roll instant {timestamp}, so the price the roll "
        "gap is measured from does not exist. Supply the bar, or roll on an instant both "
        "contracts traded -- interpolating one would invent the print the whole adjustment "
        "is computed from."
    )
