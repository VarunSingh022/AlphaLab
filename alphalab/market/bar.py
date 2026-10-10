"""Immutable OHLCV bars, and the interval a bar covers.

The interval is a value (v3.11)
-------------------------------
Until v3.10 :class:`TimeFrame` was a closed enumeration of eight intervals --
one, five and fifteen minutes, one and four hours, a day, a week, a month --
and a thirty-minute, two-hour or ten-second bar could not be labelled at all
(ledger DAT-005). It is now a value: a positive ``count`` of an
:class:`IntervalUnit`. The eight former members are constants on the class
(``TimeFrame.M1`` ... ``TimeFrame.MN1``) and equal any interval spelled the
same way, so ``TimeFrame(1, IntervalUnit.MINUTE) == TimeFrame.M1``.

Every interval has one compact **code** -- ``"30m"``, ``"2h"``, ``"1M"`` --
which is how it is written to a snapshot and how :meth:`TimeFrame.parse`
reads it back. The former enumeration's values were already these codes.

A month has no length in seconds: it is 28 to 31 days, and a single figure for
it would be an invented average. :attr:`TimeFrame.seconds` is ``None`` for any
interval counted in months, and the operations that need a length -- moving a
start-stamped bar to the end of its interval -- refuse one.

What a wire bar does not report is absent (v3.11)
-------------------------------------------------
``vwap`` and ``trade_count`` are ``None`` when the source did not report them.
Until v3.10 a normalized wire bar carried ``0`` for both and documented the
zero as "not reported", which a consumer could not tell from a reported zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import ClassVar, Final

from alphalab.market.exceptions import MarketValidationError

__all__ = ["Bar", "IntervalUnit", "TimeFrame"]


class IntervalUnit(Enum):
    """The unit an interval is counted in. The value is its code letter."""

    SECOND = "s"
    MINUTE = "m"
    HOUR = "h"
    DAY = "d"
    WEEK = "w"
    MONTH = "M"


_UNIT_SECONDS: Final = {
    IntervalUnit.SECOND: 1,
    IntervalUnit.MINUTE: 60,
    IntervalUnit.HOUR: 3600,
    IntervalUnit.DAY: 86400,
    IntervalUnit.WEEK: 604800,
}

_CODE: Final = re.compile(r"^([1-9][0-9]*)([smhdwM])$")


@dataclass(frozen=True, slots=True)
class TimeFrame:
    """The interval one bar covers: ``count`` of a ``unit``.

    Attributes:
        count: How many units, a positive integer.
        unit: What is counted.

    Raises:
        MarketValidationError: If ``count`` is not a positive integer or
            ``unit`` is not an :class:`IntervalUnit`.
    """

    count: int
    unit: IntervalUnit

    M1: ClassVar[TimeFrame]
    M5: ClassVar[TimeFrame]
    M15: ClassVar[TimeFrame]
    H1: ClassVar[TimeFrame]
    H4: ClassVar[TimeFrame]
    D1: ClassVar[TimeFrame]
    W1: ClassVar[TimeFrame]
    MN1: ClassVar[TimeFrame]

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise MarketValidationError(
                f"An interval counts a positive whole number of units, got {self.count!r}."
            )
        if not isinstance(self.unit, IntervalUnit):
            raise MarketValidationError(
                f"An interval's unit must be an IntervalUnit, got {self.unit!r}."
            )

    @property
    def code(self) -> str:
        """The compact code: ``"30m"``, ``"2h"``, ``"1M"``."""

        return f"{self.count}{self.unit.value}"

    @property
    def seconds(self) -> float | None:
        """The interval's length in seconds, or ``None`` for one counted in months."""

        per_unit = _UNIT_SECONDS.get(self.unit)
        return None if per_unit is None else float(self.count * per_unit)

    @classmethod
    def parse(cls, code: str) -> TimeFrame:
        """Read an interval from its code.

        Raises:
            MarketValidationError: If ``code`` is not a positive count followed
                by one of ``s m h d w M``.
        """

        match = _CODE.match(code) if isinstance(code, str) else None
        if match is None:
            raise MarketValidationError(
                f"{code!r} is not an interval code: a positive count followed by one of "
                "s (second), m (minute), h (hour), d (day), w (week) or M (month)."
            )
        return cls(int(match.group(1)), IntervalUnit(match.group(2)))

    def __serializable__(self) -> str:
        """Persist as the code, which :meth:`parse` reads back."""

        return self.code

    def __str__(self) -> str:
        return self.code


TimeFrame.M1 = TimeFrame(1, IntervalUnit.MINUTE)
TimeFrame.M5 = TimeFrame(5, IntervalUnit.MINUTE)
TimeFrame.M15 = TimeFrame(15, IntervalUnit.MINUTE)
TimeFrame.H1 = TimeFrame(1, IntervalUnit.HOUR)
TimeFrame.H4 = TimeFrame(4, IntervalUnit.HOUR)
TimeFrame.D1 = TimeFrame(1, IntervalUnit.DAY)
TimeFrame.W1 = TimeFrame(1, IntervalUnit.WEEK)
TimeFrame.MN1 = TimeFrame(1, IntervalUnit.MONTH)


@dataclass(frozen=True, slots=True)
class Bar:
    """Immutable representation of an OHLCV candlestick bar.

    ``timestamp`` is the **end** of the bar's interval -- the instant its close
    became knowable, and the instant the run treats it as observed. A source
    that stamps bars at the start of their interval declares so, and ingestion
    and normalization move them (ledger DAT-001).

    ``vwap`` and ``trade_count`` are ``None`` when the source did not report
    them; see the module docstring.
    """

    asset_id: str
    timestamp: float
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal | None
    trade_count: int | None
    timeframe: TimeFrame
