"""Cron schedules: a five-field expression, read in a stated time zone.

Until v3.13 ``ScheduleType.CRON`` was declared and refused at registration --
nothing parsed an expression -- and :class:`~alphalab.scheduler.timer.Timer`
carried a ``cron_expression`` string nobody read (ledger DAT-006). A
:class:`CronSchedule` is the expression parsed once, together with the IANA
zone whose wall clock it is read on, and it answers one question:
:meth:`CronSchedule.next_after`, the first instant strictly after a given one at
which the schedule fires.

The grammar
-----------

Five fields, separated by white space::

    minute  hour  day-of-month  month  day-of-week
    0-59    0-23  1-31          1-12   0-7 (0 and 7 are Sunday)

Each field is ``*``, a value, a range ``a-b``, either of those with a step
(``*/15``, ``8-18/2``), or a comma-separated list of them. Months and days of
the week may also be named by their first three letters (``JAN``, ``MON``), in
any case. Nothing else is accepted: no ``@daily`` shorthands, no seconds or
years field, no ``L``, ``W``, ``#`` or ``?`` -- an expression using one is
refused rather than read as something its author did not write.

Two rules every cron implementation must choose, stated
--------------------------------------------------------

**Day of month and day of week.** When both fields are restricted, a day
matches if *either* does: ``0 9 1 * MON`` fires on the first of every month and
on every Monday. When one of them is unrestricted, only the other applies. A
field is unrestricted when its text begins with ``*`` -- so ``*/2`` counts as
unrestricted -- which is Vixie cron's rule and the one most implementations
copy (POSIX leaves a stepped ``*`` unspecified).

**Daylight-saving transitions.** The expression is read on the zone's wall
clock, one local minute at a time. A local minute that does not exist that day
-- inside a spring-forward gap -- does not fire: ``30 2 * * *`` in
``America/New_York`` skips the day the clocks jump from 02:00 to 03:00. A local
minute that occurs twice -- inside a fall-back overlap -- fires once, at its
first occurrence. So a schedule fires at most once per wall-clock minute, and a
frequent one (``*/15 * * * *``) is silent for the repeated hour's second pass.
Both follow from reading the wall clock exactly; a caller who needs evenly
spaced instants across a transition wants an ``INTERVAL`` timer, which counts
seconds.

An expression that can never fire -- ``0 0 31 4 *``, the thirty-first of April
-- is refused when it is parsed, rather than producing a timer that waits
forever.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Final

from alphalab.data.exceptions import DataValidationError
from alphalab.data.time import resolve_zone
from alphalab.scheduler.exceptions import SchedulerValidationError

__all__ = ["CRON_SEARCH_DAYS", "CronSchedule"]

#: How far :meth:`CronSchedule.next_after` looks: eight years and two days, which
#: holds the longest gap any accepted expression can leave -- a 29 February
#: across a century year that is not a leap year.
CRON_SEARCH_DAYS: Final = 8 * 366 + 2

_MONTHS: Final = {
    name: number
    for number, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
        start=1,
    )
}
_WEEKDAYS: Final = {
    name: number for number, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))
}
#: The longest each month can be: a day of month above it never occurs in it.
_LONGEST_MONTH: Final = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31,
                         11: 30, 12: 31}  # fmt: skip

_ITEM: Final = re.compile(r"^(?P<range>\*|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?)(?:/(?P<step>\d+))?$")


@dataclass(frozen=True, slots=True)
class _Field:
    name: str
    low: int
    high: int
    names: dict[str, int] = field(default_factory=dict)


_FIELDS: Final = (
    _Field("minute", 0, 59),
    _Field("hour", 0, 23),
    _Field("day-of-month", 1, 31),
    _Field("month", 1, 12, _MONTHS),
    _Field("day-of-week", 0, 7, _WEEKDAYS),
)


def _value(text: str, spec: _Field, expression: str) -> int:
    upper = text.upper()
    if upper in spec.names:
        return spec.names[upper]
    if not text.isdigit():
        raise SchedulerValidationError(
            f"{expression!r}: {text!r} is not a {spec.name}; it is a number in "
            f"[{spec.low}, {spec.high}]"
            + (f" or one of {', '.join(spec.names)}" if spec.names else "")
            + "."
        )
    number = int(text)
    if not spec.low <= number <= spec.high:
        raise SchedulerValidationError(
            f"{expression!r}: the {spec.name} {number} is outside [{spec.low}, {spec.high}]."
        )
    return number


def _parse_field(text: str, spec: _Field, expression: str) -> frozenset[int]:
    values: set[int] = set()
    for item in text.split(","):
        match = _ITEM.match(item)
        if match is None:
            raise SchedulerValidationError(
                f"{expression!r}: {item!r} is not a {spec.name}: write *, a value, a range a-b, "
                "either with a /step, or a comma-separated list of those."
            )
        span, step_text = match.group("range"), match.group("step")
        if span == "*":
            start, stop = spec.low, spec.high
        elif "-" in span:
            first, last = span.split("-", 1)
            start, stop = _value(first, spec, expression), _value(last, spec, expression)
            if start > stop:
                raise SchedulerValidationError(
                    f"{expression!r}: the {spec.name} range {span} runs backwards."
                )
        else:
            start = stop = _value(span, spec, expression)
            if step_text is not None:
                # "5/15" is read by Vixie cron as 5-max/15; say so by writing it.
                raise SchedulerValidationError(
                    f"{expression!r}: {item!r} steps from a single value; write the range "
                    f"{span}-{spec.high}/{step_text} it would mean."
                )
        step = 1 if step_text is None else int(step_text)
        if step == 0:
            raise SchedulerValidationError(f"{expression!r}: a step of zero in {item!r}.")
        values.update(range(start, stop + 1, step))
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class CronSchedule:
    """A five-field cron expression read on one IANA zone's wall clock.

    See the module docstring for the grammar, how the two day fields combine,
    and what happens at a daylight-saving transition.

    Attributes:
        expression: The expression, as written.
        zone: The IANA zone whose wall clock it is read on. Required: a cron
            schedule whose zone is assumed belongs to whoever wrote the default.

    Raises:
        SchedulerValidationError: If the expression is malformed, uses syntax
            outside the grammar, can never fire, or the zone is unknown.
    """

    expression: str
    zone: str
    minutes: frozenset[int] = field(init=False, repr=False)
    hours: frozenset[int] = field(init=False, repr=False)
    days_of_month: frozenset[int] = field(init=False, repr=False)
    months: frozenset[int] = field(init=False, repr=False)
    #: 0 is Sunday; a 7 in the expression is folded into 0.
    days_of_week: frozenset[int] = field(init=False, repr=False)
    #: Whether each day field was restricted (its text did not begin with ``*``).
    day_of_month_restricted: bool = field(init=False, repr=False)
    day_of_week_restricted: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.expression, str) or not isinstance(self.zone, str):
            raise SchedulerValidationError("A cron schedule is an expression and a zone, as text.")
        try:
            resolve_zone(self.zone)
        except DataValidationError as error:
            raise SchedulerValidationError(
                f"{self.zone!r} is not an IANA time zone this host knows: {error}"
            ) from error
        parts = self.expression.split()
        if len(parts) != len(_FIELDS):
            raise SchedulerValidationError(
                f"{self.expression!r} has {len(parts)} fields; a cron expression has five: "
                "minute hour day-of-month month day-of-week."
            )
        parsed = [
            _parse_field(text, spec, self.expression)
            for text, spec in zip(parts, _FIELDS, strict=True)
        ]
        object.__setattr__(self, "minutes", parsed[0])
        object.__setattr__(self, "hours", parsed[1])
        object.__setattr__(self, "days_of_month", parsed[2])
        object.__setattr__(self, "months", parsed[3])
        object.__setattr__(self, "days_of_week", frozenset(day % 7 for day in parsed[4]))
        object.__setattr__(self, "day_of_month_restricted", not parts[2].startswith("*"))
        object.__setattr__(self, "day_of_week_restricted", not parts[4].startswith("*"))
        if self.day_of_month_restricted and not self.day_of_week_restricted:
            possible = any(
                day <= _LONGEST_MONTH[month] for month in self.months for day in self.days_of_month
            )
            if not possible:
                raise SchedulerValidationError(
                    f"{self.expression!r} names no day that occurs in any month it names, so "
                    "it would never fire."
                )

    def _fires_on(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        by_month_day = day.day in self.days_of_month
        by_weekday = (day.isoweekday() % 7) in self.days_of_week
        if self.day_of_month_restricted and self.day_of_week_restricted:
            return by_month_day or by_weekday
        if self.day_of_month_restricted:
            return by_month_day
        if self.day_of_week_restricted:
            return by_weekday
        return True

    def next_after(self, after: float) -> float | None:
        """The first instant strictly after ``after`` at which the schedule fires.

        In Unix seconds, always a whole minute. ``None`` only when nothing fires
        within :data:`CRON_SEARCH_DAYS` -- which no accepted expression allows,
        and is reported rather than looped on.
        """

        zone = resolve_zone(self.zone)
        # No later instant falls on an earlier local date: a clock that falls
        # back shows the later date only once the repeated stretch is over.
        start = datetime.fromtimestamp(after, tz=UTC).astimezone(zone).date()
        hours, minutes = sorted(self.hours), sorted(self.minutes)
        for offset in range(CRON_SEARCH_DAYS + 1):
            day = start + timedelta(days=offset)
            if not self._fires_on(day):
                continue
            for hour in hours:
                for minute in minutes:
                    instant = _first_occurrence(day, hour, minute, zone)
                    if instant is not None and instant > after:
                        return instant
        return None


def _first_occurrence(day: date, hour: int, minute: int, zone: tzinfo) -> float | None:
    """The instant a local minute first occurs on ``day``, or ``None`` if it does not."""

    local = datetime(day.year, day.month, day.day, hour, minute)
    first = local.replace(tzinfo=zone, fold=0)
    if first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local:
        return None  # inside a spring-forward gap: this minute does not exist today
    return first.timestamp()
