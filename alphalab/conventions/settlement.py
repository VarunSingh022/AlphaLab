"""When a trade turns into money, in the market's own trading days.

A trade date is not a settlement date. Indian cash equities settle T+1, US cash
equities T+1 since May 2024 and T+2 before it, most European venues T+2, spot FX
T+2 with its own holiday rules, and a futures contract settles daily against a
variation margin call that is not a T+n date at all. None of those is *the*
convention, and a module that picked one would be right in one market and
silently wrong everywhere else.

So a settlement convention is **declared**, and the calendar it counts over is
**supplied**.

Why the calendar arrives as an argument
---------------------------------------

:class:`alphalab.data.calendar.MarketCalendar` is AlphaLab's one calendar
authority and this module does not import it. It names the shape it needs --
:class:`TradingDayCalendar`, one method -- and ``MarketCalendar`` satisfies it
structurally, exactly as :class:`alphalab.common.point_in_time.PointInTimeRecord`
lets ``macro`` and ``alt_data`` share one point-in-time query without either
importing the other.

That is not only tidiness. ``alphalab.conventions`` must be importable by
``options``, ``futures``, ``crypto``, ``portfolio`` and ``data`` alike, and
``alphalab.data`` already imports ``alphalab.options``. An edge from here into
``alphalab.data`` would close a package-level import cycle, which
``tests/regression/test_import_graph_stays_acyclic.py`` refuses. This package
imports ``alphalab.common`` and nothing else in ``alphalab``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum, auto
from typing import Final, Protocol

from alphalab.conventions.exceptions import ConventionInputError, ConventionViolationError

__all__ = [
    "MAX_SETTLEMENT_SEARCH_DAYS",
    "SettlementBasis",
    "SettlementRule",
    "TradingDayCalendar",
    "settlement_date",
]

#: How far :func:`settlement_date` will look for the n-th trading day before it
#: refuses. A calendar whose next trading day is more than this far away is
#: almost certainly mis-declared -- an empty ``weekly_sessions``, or a holiday
#: set that swallowed the year -- and walking forever would hang a backtest
#: rather than report the mistake.
MAX_SETTLEMENT_SEARCH_DAYS: Final = 400


class TradingDayCalendar(Protocol):
    """The one thing a settlement rule needs from a calendar.

    Structural on purpose: :class:`alphalab.data.calendar.MarketCalendar`
    already answers this and is the authority, and nothing here re-derives it.
    """

    def is_trading_day(self, day: date) -> bool: ...


class SettlementBasis(Enum):
    """What the offset in a settlement rule counts."""

    #: Settles on the trade date itself. Cash-and-carry markets, and the honest
    #: rendering of a daily-margined future whose cash moves the same day.
    TRADE_DATE = auto()

    #: ``offset_days`` trading days after the trade date, skipping weekends and
    #: holidays on the supplied calendar. The convention every listed cash
    #: market states as "T+n".
    TRADING_DAYS = auto()

    #: ``offset_days`` calendar days after the trade date, counting weekends and
    #: holidays. Distinct from :attr:`TRADING_DAYS` and not interchangeable with
    #: it: T+2 trading days across a long weekend is four calendar days, and a
    #: model that conflates them books cash on a day the market was shut.
    CALENDAR_DAYS = auto()


@dataclass(frozen=True, slots=True)
class SettlementRule:
    """How a trade date becomes a settlement date in one market.

    Attributes:
        basis: What ``offset_days`` counts. Required -- "T+2" alone does not say
            whether the 2 is trading days or calendar days, and the two differ
            by a long weekend.
        offset_days: How many. Zero for :attr:`SettlementBasis.TRADE_DATE`, and
            refused as non-zero there so a rule cannot say "same day, plus two".

    Raises:
        ConventionInputError: If ``offset_days`` is negative, or is non-zero
            under :attr:`SettlementBasis.TRADE_DATE`.
    """

    basis: SettlementBasis
    offset_days: int

    def __post_init__(self) -> None:
        if self.offset_days < 0:
            raise ConventionInputError(
                f"offset_days is {self.offset_days}; settlement follows a trade and cannot "
                "precede it."
            )
        if self.basis is SettlementBasis.TRADE_DATE and self.offset_days != 0:
            raise ConventionInputError(
                f"TRADE_DATE settles on the trade date, so offset_days must be 0, not "
                f"{self.offset_days}. Name TRADING_DAYS or CALENDAR_DAYS to shift it."
            )

    @property
    def label(self) -> str:
        """Short rendering for a report, e.g. ``"T+2 trading days"``."""

        if self.basis is SettlementBasis.TRADE_DATE:
            return "T+0"
        unit = "trading days" if self.basis is SettlementBasis.TRADING_DAYS else "calendar days"
        return f"T+{self.offset_days} {unit}"

    @property
    def needs_calendar(self) -> bool:
        """Whether resolving this rule requires a calendar to be supplied."""

        return self.basis is SettlementBasis.TRADING_DAYS


def settlement_date(
    rule: SettlementRule, trade_date: date, calendar: TradingDayCalendar | None
) -> date:
    """The local date a trade struck on ``trade_date`` settles.

    Args:
        rule: The market's declared convention.
        trade_date: The local trading date of the trade, in the market's own
            timezone. Deriving it from an instant is
            :meth:`alphalab.data.calendar.MarketCalendar.trading_day_of`'s job,
            not this function's -- which local day an instant belongs to is a
            calendar question and has exactly one owner.
        calendar: The market's calendar. Required for
            :attr:`SettlementBasis.TRADING_DAYS` and refused for the other two,
            where supplying one would suggest holidays were considered when they
            were not.

    Raises:
        ConventionInputError: If a calendar is required and absent, or supplied
            where it has no effect.
        ConventionViolationError: If the calendar has no ``offset_days``-th
            trading day within :data:`MAX_SETTLEMENT_SEARCH_DAYS`.
    """

    if rule.needs_calendar and calendar is None:
        raise ConventionInputError(
            f"{rule.label} counts trading days, so it needs the market's calendar to know "
            "which days those are. Counting calendar days instead would settle on a holiday."
        )
    if not rule.needs_calendar and calendar is not None:
        raise ConventionInputError(
            f"{rule.label} does not read a calendar, so supplying one suggests holidays are "
            "skipped when they are not. Pass None, or declare TRADING_DAYS."
        )

    if rule.basis is SettlementBasis.TRADE_DATE:
        return trade_date
    if rule.basis is SettlementBasis.CALENDAR_DAYS:
        return trade_date + timedelta(days=rule.offset_days)

    assert calendar is not None  # guarded above; narrows the Optional for mypy
    remaining = rule.offset_days
    cursor = trade_date
    for _ in range(MAX_SETTLEMENT_SEARCH_DAYS):
        if remaining == 0:
            return cursor
        cursor += timedelta(days=1)
        if calendar.is_trading_day(cursor):
            remaining -= 1
    raise ConventionViolationError(
        f"{rule.label} from {trade_date} found only {rule.offset_days - remaining} trading "
        f"day(s) within {MAX_SETTLEMENT_SEARCH_DAYS} days. The calendar declares no trading "
        "day in that range, which is a mis-declared calendar rather than a settlement date."
    )
