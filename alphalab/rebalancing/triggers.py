"""When a rebalancing strategy rebalances: its triggers (v4.3, ADR-0063 section 6).

A trigger is a frozen value that reads a :class:`RebalanceMoment` -- the
slice's instant, the book's weights now, and the weights of the last target --
and its own memory, and says whether it fires. The strategy keeps each
trigger's memory as part of its declared state, so a paused run resumes with
every trigger where it was. A strategy rebalances when any of its triggers
fires.

* :class:`CalendarTrigger` -- the first slice of each day, week or month, read
  in a declared zone or by the sessions of a declared calendar.
* :class:`ThresholdTrigger` -- a weight farther than a stated distance from its
  last target.
* :class:`DriftTrigger` -- the book's total distance from its last target
  beyond a stated amount.

Every parameter is stated; none has a default (AC-12). A researcher's own
trigger satisfies :class:`Trigger`.

``alphalab.portfolio_optimizer.rebalance`` keeps v3's standalone rules --
``check_schedule_rebalance``, whose month is thirty days, and
``check_threshold_rebalance``, whose threshold has a default -- unchanged until
5.0. These triggers supersede them on a run's path.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from alphalab.data.exceptions import DataValidationError
from alphalab.data.time import resolve_zone
from alphalab.rebalancing.exceptions import RebalancingInputError

__all__ = [
    "CalendarTrigger",
    "DriftTrigger",
    "Period",
    "RebalanceMoment",
    "SessionCalendar",
    "ThresholdTrigger",
    "Trigger",
]


class Period(Enum):
    """A calendar trigger's period: a day, an ISO week (Monday to Sunday) or a calendar month."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@runtime_checkable
class SessionCalendar(Protocol):
    """A calendar of trading sessions, taken by protocol: any calendar that states its sessions.

    :class:`alphalab.data.calendar.MarketCalendar` satisfies it: its
    :meth:`trading_day_of` an instant is the date the session it falls in
    opened on -- for an overnight session, the previous evening's -- or
    ``None`` when the market was shut.
    """

    @property
    def calendar_id(self) -> str:
        """The calendar's identity, which a trigger's rendering names."""
        ...

    def trading_day_of(self, timestamp: float) -> date | None:
        """The trading date ``timestamp`` belongs to, or ``None`` outside every session."""
        ...


@dataclass(frozen=True, slots=True)
class RebalanceMoment:
    """What a trigger reads at a slice.

    Attributes:
        instant: The slice's timestamp, in Unix seconds.
        weights: The strategy's book now, asset -> its value over the
            strategy's capital, for every asset the strategy priced.
        target: The weights of the last target the strategy set, or ``None``
            before its first rebalance.
    """

    instant: float
    weights: Mapping[str, float]
    target: Mapping[str, float] | None


@runtime_checkable
class Trigger(Protocol):
    """Whether a slice is a rebalance: a frozen value, and a memory the strategy keeps.

    :meth:`fires` is handed the memory it returned last time -- ``None`` at
    first -- and returns whether it fires and the memory to keep. The memory is
    a string so that it travels in the strategy's captured state.
    """

    def rendering(self) -> list[str]:
        """The trigger's identity lines: every parameter that decides it."""
        ...

    def fires(self, moment: RebalanceMoment, memory: str | None) -> tuple[bool, str | None]:
        """Whether ``moment`` is a rebalance, and the memory to keep."""
        ...


def _positive(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RebalancingInputError(f"{what} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise RebalancingInputError(f"{what} is {number!r}; it must be positive and finite.")
    return number


def _distances(moment: RebalanceMoment) -> list[float] | None:
    """Each asset's distance from the last target, or ``None`` before there is one."""

    target = moment.target
    if target is None:
        return None
    assets = sorted(set(moment.weights) | set(target))
    return [abs(moment.weights.get(asset, 0.0) - target.get(asset, 0.0)) for asset in assets]


@dataclass(frozen=True, slots=True)
class CalendarTrigger:
    """Fires at the first slice of each day, week or month (v4.3, ADR-0063 section 6).

    The period is read in one of two ways, stated -- no zone is assumed
    (AC-12):

    * **in a zone**, an IANA name: a slice's local date there;
    * **by a calendar's sessions**: the trading day a slice falls in, which for
      an overnight session is the day it opened. A slice outside every session
      is in no period and fires nothing.

    A week is the ISO week of that date, a month its calendar month. Its memory
    is the last period a slice was in, so the first slice of a new one fires --
    the first the strategy evaluates, which needs a price for everything it
    holds.

    Attributes:
        period: Day, week or month.
        zone: The IANA zone the local date is read in, or ``None``.
        calendar: The calendar whose sessions are read, or ``None``. Exactly
            one of ``zone`` and ``calendar`` is given.
    """

    period: Period
    zone: str | None = None
    calendar: SessionCalendar | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.period, Period):
            raise RebalancingInputError(f"period must be a Period, got {self.period!r}.")
        if (self.zone is None) == (self.calendar is None):
            raise RebalancingInputError(
                "A calendar trigger reads its period in a zone or by a calendar's sessions: state "
                "exactly one. No zone is assumed."
            )
        if self.zone is not None:
            if not isinstance(self.zone, str) or not self.zone.strip():
                raise RebalancingInputError(f"zone must be an IANA zone name, got {self.zone!r}.")
            try:
                # The one place a zone is resolved (alphalab.data.time), which says
                # whether the name is wrong or the host has no tz database.
                resolve_zone(self.zone)
            except DataValidationError as error:
                raise RebalancingInputError(
                    f"{self.zone!r} is not an IANA zone here: {error}"
                ) from error
        if self.calendar is not None and not isinstance(self.calendar, SessionCalendar):
            raise RebalancingInputError(
                f"{self.calendar!r} is not a session calendar: it states no calendar_id and no "
                "trading_day_of."
            )

    def _day(self, instant: float) -> date | None:
        if self.calendar is not None:
            return self.calendar.trading_day_of(instant)
        assert self.zone is not None  # one of the two is given
        return datetime.fromtimestamp(instant, resolve_zone(self.zone)).date()

    def _key(self, day: date) -> str:
        if self.period is Period.DAY:
            return day.isoformat()
        if self.period is Period.WEEK:
            year, week, _ = day.isocalendar()
            return f"{year:04d}-W{week:02d}"
        return f"{day.year:04d}-{day.month:02d}"

    def rendering(self) -> list[str]:
        where = (
            f"zone={self.zone!r}"
            if self.calendar is None
            else f"calendar={self.calendar.calendar_id!r}"
        )
        return ["trigger=calendar", f"period={self.period.value}", where]

    def fires(self, moment: RebalanceMoment, memory: str | None) -> tuple[bool, str | None]:
        day = self._day(moment.instant)
        if day is None:
            return False, memory
        key = self._key(day)
        return key != memory, key


@dataclass(frozen=True, slots=True)
class ThresholdTrigger:
    """Fires when any weight is farther than ``distance`` from its last target (v4.3).

    Before the strategy's first rebalance there is no target to measure from,
    and it fires: the strategy constructs its first book.

    Attributes:
        distance: The largest ``|w_i - target_i|`` that does not fire: positive.
    """

    distance: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "distance", _positive(self.distance, "ThresholdTrigger distance"))

    def rendering(self) -> list[str]:
        return ["trigger=threshold", f"distance={self.distance!r}"]

    def fires(self, moment: RebalanceMoment, memory: str | None) -> tuple[bool, str | None]:
        distances = _distances(moment)
        if distances is None:
            return True, memory
        return any(distance > self.distance for distance in distances), memory


@dataclass(frozen=True, slots=True)
class DriftTrigger:
    """Fires when the book's total distance from its last target exceeds ``amount`` (v4.3).

    ``sum_i |w_i - target_i|``, over every asset either holds. Before the first
    rebalance it fires, as :class:`ThresholdTrigger` does.

    Attributes:
        amount: The largest total distance that does not fire: positive.
    """

    amount: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _positive(self.amount, "DriftTrigger amount"))

    def rendering(self) -> list[str]:
        return ["trigger=drift", f"amount={self.amount!r}"]

    def fires(self, moment: RebalanceMoment, memory: str | None) -> tuple[bool, str | None]:
        distances = _distances(moment)
        if distances is None:
            return True, memory
        return math.fsum(distances) > self.amount, memory
