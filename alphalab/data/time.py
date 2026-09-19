"""Chronological dimensions, and how a raw timestamp becomes a canonical one.

AlphaLab's canonical instant is a ``float`` of Unix seconds -- the execution
path, the replay cursor and every snapshot agree on that, and
``tests/regression/test_execution_timestamp_is_float.py`` pins it. Raw data does
not arrive that way. It arrives as ISO-8601 with an offset, as ISO-8601 without
one, as a bare date, or as a number that might be seconds or might be
milliseconds.

Turning those into canonical instants is where look-ahead bias and
cross-market comparison errors are born, so every rule here is explicit and
every assumption is *returned* rather than applied quietly.

The three rules
---------------

1. **An offset-bearing timestamp is authoritative.** ``2025-01-02T16:00:00Z``
   and ``2025-01-02T11:00:00-05:00`` are the same instant and need no
   declaration from anyone.

2. **A naive timestamp is not an instant until a zone is named.** It is a wall
   clock reading, and the same reading means different instants in Mumbai and
   New York. :func:`parse_timestamp` refuses one without a zone rather than
   assuming UTC -- assuming UTC is how an India-centric or US-centric default
   gets encoded, and it produces a number rather than an error, which is the
   worst kind of wrong.

3. **A bare date is a day, not an instant.** Reading it as midnight is a
   convention, so it is applied only when the caller asks for it by passing
   :class:`DateOnlyPolicy`, and it is recorded as an assumption either way.

Milliseconds
------------

A number is read as seconds, because that is AlphaLab's declared canonical unit
and a convention stated once is not a guess. But a column whose values are all
large enough to be milliseconds is genuinely ambiguous -- both readings produce
a valid instant, centuries apart -- so :func:`detect_timestamp_format` reports
that ambiguity and refuses to pick. The caller declares which it is.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum, auto
from itertools import pairwise
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alphalab.data.exceptions import DataValidationError

__all__ = [
    "AMBIGUOUS_EPOCH_THRESHOLD",
    "DateOnlyPolicy",
    "TimeFrequency",
    "TimestampFormat",
    "detect_timestamp_format",
    "frequency_seconds",
    "infer_frequency",
    "parse_timestamp",
    "resolve_zone",
]


class TimeFrequency(Enum):
    """Immutable definitions for chronological dimensions."""

    TICK = auto()
    SECOND = auto()
    MINUTE = auto()
    HOURLY = auto()
    DAILY = auto()
    WEEKLY = auto()
    MONTHLY = auto()
    CUSTOM = auto()


#: Nominal length of one interval, for the frequencies that have a fixed one.
#:
#: ``WEEKLY`` is included because seven days is exact. ``MONTHLY`` is not: a
#: month is 28 to 31 days, and a single number for it would be an invented
#: average that silently mis-bins every calendar month. ``TICK`` has no
#: interval at all, and ``CUSTOM`` means "the caller knows, AlphaLab does not".
_FREQUENCY_SECONDS: Final[dict[TimeFrequency, float]] = {
    TimeFrequency.SECOND: 1.0,
    TimeFrequency.MINUTE: 60.0,
    TimeFrequency.HOURLY: 3600.0,
    TimeFrequency.DAILY: 86400.0,
    TimeFrequency.WEEKLY: 604800.0,
}

#: Above this, a number is as plausibly milliseconds as seconds.
#:
#: ``1e11`` seconds is the year 5138; ``1e11`` milliseconds is 1973. Any column
#: of values at or above it therefore has two readings that both produce
#: sensible instants, and nothing in the data itself distinguishes them.
AMBIGUOUS_EPOCH_THRESHOLD: Final = 1e11


class TimestampFormat(Enum):
    """How a raw timestamp value is to be read."""

    #: A number of seconds since the Unix epoch. AlphaLab's canonical unit.
    EPOCH_SECONDS = auto()

    #: A number of milliseconds since the Unix epoch. Never inferred -- only
    #: ever declared, because inferring it is the ambiguity above.
    EPOCH_MILLISECONDS = auto()

    #: An ISO-8601 string carrying a UTC offset. Self-describing.
    ISO_8601_AWARE = auto()

    #: An ISO-8601 string with no offset. A wall clock, needing a zone.
    ISO_8601_NAIVE = auto()

    #: A bare calendar date, needing both a zone and a time-of-day convention.
    DATE_ONLY = auto()


class DateOnlyPolicy(Enum):
    """Which instant within a day a bare date is taken to mean."""

    #: 00:00:00 in the named zone. The conventional reading of a daily bar's
    #: date label, and the one a caller almost always wants.
    START_OF_DAY = auto()

    #: 23:59:59.999999 in the named zone. Offered because a daily bar's *close*
    #: is the instant its value became known, and a point-in-time study that
    #: stamps it at midnight has moved the knowledge back by a day.
    END_OF_DAY = auto()


def resolve_zone(timezone_name: str) -> ZoneInfo:
    """Look up an IANA zone, refusing clearly rather than obscurely.

    ``zoneinfo`` reads the host's tz database, which a bare Windows install
    does not have. The resulting ``ZoneInfoNotFoundError`` names the zone but
    not the cause, so it is re-raised here as a domain error that says what to
    install. AlphaLab keeps zero runtime dependencies, so bundling ``tzdata``
    is the caller's decision rather than one made on their behalf.
    """

    if not timezone_name.strip():
        raise DataValidationError(
            "A timezone must be named; an empty zone is not UTC, it is unstated."
        )
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise DataValidationError(
            f"Unknown timezone {timezone_name!r}. Either the name is wrong or this host "
            "has no IANA time zone database; install the 'tzdata' package to supply one."
        ) from error
    except (ValueError, OSError) as error:
        raise DataValidationError(f"Invalid timezone {timezone_name!r}: {error}") from error


def detect_timestamp_format(values: Sequence[str]) -> tuple[TimestampFormat | None, str]:
    """Decide how a column of raw timestamp strings is to be read.

    Deterministic and total: every input produces either a format with the
    reason it was chosen, or ``None`` with the reason nothing could be.

    Values that cannot be read at all are **excluded from the decision** and
    counted in the rationale. A column of ten thousand ISO dates with one
    ``"N/A"`` in it is an ISO column with one bad row, not an undecidable
    column, and the bad row is rejected individually by
    :func:`~alphalab.data.validation.coerce_row` with its own finding. What is
    refused is a column whose *readable* values disagree about their shape,
    because there is no single way to read those without misplacing some of
    them.

    Returns:
        ``(format, rationale)``. The rationale is written to be read by a person
        auditing an ingestion, and is carried into the schema detection report.
    """

    samples = [value.strip() for value in values if value is not None and value.strip()]
    if not samples:
        return None, "no non-empty timestamp values to inspect"

    numeric = [value for value in samples if _is_numeric(value)]
    shapes: dict[_IsoShape, int] = {}
    unreadable = 0
    for value in samples:
        if _is_numeric(value):
            continue
        shape = _try_iso(value)
        if shape is None:
            unreadable += 1
        else:
            shapes[shape] = shapes.get(shape, 0) + 1

    readable = len(numeric) + sum(shapes.values())
    if readable == 0:
        return None, (
            f"none of the {len(samples)} values is numeric or an ISO-8601 date or datetime"
        )

    distinct = len(shapes) + (1 if numeric else 0)
    if distinct > 1:
        named = sorted([shape.name for shape in shapes] + (["NUMERIC"] if numeric else []))
        return None, (
            f"the column mixes {', '.join(named)} timestamp shapes; a single column read two "
            "ways would silently place some rows in the wrong zone"
        )

    note = (
        ""
        if unreadable == 0
        else f"; {unreadable} of {len(samples)} values are unreadable and are rejected row by row"
    )

    if numeric:
        if all(abs(float(value)) >= AMBIGUOUS_EPOCH_THRESHOLD for value in numeric):
            return None, (
                f"every value is >= {AMBIGUOUS_EPOCH_THRESHOLD:.0e}, which reads as a valid "
                "instant both as seconds and as milliseconds; declare which unit this column "
                "uses rather than having one guessed"
            )
        return TimestampFormat.EPOCH_SECONDS, (
            "all readable values are numeric and within the range where seconds is the only "
            f"reading that yields a plausible instant{note}"
        )

    shape = next(iter(shapes))
    if shape is _IsoShape.DATE:
        return TimestampFormat.DATE_ONLY, f"every readable value is a bare ISO-8601 date{note}"
    if shape is _IsoShape.AWARE:
        return TimestampFormat.ISO_8601_AWARE, (
            "every readable value is an ISO-8601 datetime carrying a UTC offset, so each names "
            f"an instant without needing a zone declared{note}"
        )
    return TimestampFormat.ISO_8601_NAIVE, (
        "every readable value is an ISO-8601 datetime with no offset, so each is a wall clock "
        f"reading and needs a zone to become an instant{note}"
    )


class _IsoShape(Enum):
    DATE = auto()
    NAIVE = auto()
    AWARE = auto()


def _is_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _try_iso(value: str) -> _IsoShape | None:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            date.fromisoformat(value)
        except ValueError:
            return None
        return _IsoShape.DATE
    if len(value) == 10 and parsed.time() == time(0, 0):
        return _IsoShape.DATE
    return _IsoShape.AWARE if parsed.tzinfo is not None else _IsoShape.NAIVE


def parse_timestamp(
    value: str,
    timestamp_format: TimestampFormat,
    timezone_name: str | None,
    date_policy: DateOnlyPolicy | None,
) -> float:
    """Read one raw timestamp as canonical Unix seconds.

    Every argument after ``value`` is required rather than defaulted, because
    each one is a decision whose wrong answer produces a number instead of an
    error. Pass ``None`` for the two that do not apply to the format in hand.

    Args:
        value: The raw string, as it appeared in the source.
        timestamp_format: How to read it.
        timezone_name: IANA zone the wall clock belongs to. Required for
            ``ISO_8601_NAIVE`` and ``DATE_ONLY``; refused for the others, where
            supplying one would suggest it had an effect.
        date_policy: Which instant in the day a bare date means. Required for
            ``DATE_ONLY``; refused for the others.

    Raises:
        DataValidationError: If the value cannot be read as the declared format,
            or if a required decision was not supplied.
    """

    text = value.strip()
    if not text:
        raise DataValidationError("An empty string is not a timestamp.")

    needs_zone = timestamp_format in (TimestampFormat.ISO_8601_NAIVE, TimestampFormat.DATE_ONLY)
    if needs_zone and timezone_name is None:
        raise DataValidationError(
            f"{timestamp_format.name} carries no offset, so {text!r} is a wall clock reading "
            "rather than an instant. Name the IANA timezone it was recorded in."
        )
    if not needs_zone and timezone_name is not None:
        raise DataValidationError(
            f"{timestamp_format.name} already names an instant, so a timezone would have no "
            f"effect on {text!r}; passing one suggests a conversion that will not happen."
        )
    if timestamp_format is TimestampFormat.DATE_ONLY and date_policy is None:
        raise DataValidationError(
            f"{text!r} is a calendar date, not an instant. Name a DateOnlyPolicy so the "
            "time of day it is taken to mean is a decision rather than an assumption."
        )
    if timestamp_format is not TimestampFormat.DATE_ONLY and date_policy is not None:
        raise DataValidationError(
            f"A DateOnlyPolicy has no meaning for {timestamp_format.name}; {text!r} already "
            "carries a time of day."
        )

    if timestamp_format is TimestampFormat.EPOCH_SECONDS:
        return _as_float(text)
    if timestamp_format is TimestampFormat.EPOCH_MILLISECONDS:
        return _as_float(text) / 1000.0

    if timestamp_format is TimestampFormat.DATE_ONLY:
        if timezone_name is None or date_policy is None:  # pragma: no cover - guarded above
            raise DataValidationError(f"A date needs both a zone and a policy to read {text!r}.")
        zone = resolve_zone(timezone_name)
        try:
            day = date.fromisoformat(text)
        except ValueError as error:
            raise DataValidationError(f"{text!r} is not an ISO-8601 calendar date.") from error
        if date_policy is DateOnlyPolicy.START_OF_DAY:
            moment = datetime.combine(day, time(0, 0), tzinfo=zone)
        else:
            moment = datetime.combine(day, time(0, 0), tzinfo=zone) + timedelta(
                days=1, microseconds=-1
            )
        return moment.timestamp()

    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise DataValidationError(f"{text!r} is not an ISO-8601 datetime.") from error

    if timestamp_format is TimestampFormat.ISO_8601_AWARE:
        if moment.tzinfo is None:
            raise DataValidationError(
                f"{text!r} was declared ISO_8601_AWARE but carries no UTC offset."
            )
        return moment.astimezone(UTC).timestamp()

    if moment.tzinfo is not None:
        raise DataValidationError(
            f"{text!r} was declared ISO_8601_NAIVE but carries a UTC offset; reading it in "
            f"{timezone_name} would discard the offset the source stated."
        )
    if timezone_name is None:  # pragma: no cover - guarded above
        raise DataValidationError(f"A naive timestamp needs a zone to read {text!r}.")
    return moment.replace(tzinfo=resolve_zone(timezone_name)).timestamp()


def _as_float(text: str) -> float:
    try:
        return float(text)
    except ValueError as error:
        raise DataValidationError(f"{text!r} is not a number.") from error


def frequency_seconds(frequency: TimeFrequency) -> float | None:
    """The nominal length of one interval, or ``None`` where there is no fixed one.

    ``None`` is the honest answer for ``MONTHLY`` (28-31 days), ``TICK`` (no
    interval) and ``CUSTOM`` (the caller's to define), and callers are expected
    to handle it rather than receive an invented average.
    """

    return _FREQUENCY_SECONDS.get(frequency)


def infer_frequency(timestamps: Sequence[float]) -> tuple[TimeFrequency | None, str]:
    """Read the dominant spacing of a sorted series as a frequency.

    Returns a candidate and the reason, never a silent answer: a series with
    gaps -- weekends, holidays, a halted session -- has several spacings, and
    which one is "the" frequency is a judgement. The modal spacing is reported
    with the share of intervals that agree with it so the caller can decide
    whether that share is convincing.
    """

    if len(timestamps) < 2:
        return None, "fewer than two timestamps, so there is no interval to measure"

    deltas = [
        round(later - earlier, 6) for earlier, later in pairwise(timestamps) if later > earlier
    ]
    if not deltas:
        return None, "no strictly increasing pair of timestamps, so no interval to measure"

    counts: dict[float, int] = {}
    for delta in deltas:
        counts[delta] = counts.get(delta, 0) + 1
    modal = min(counts, key=lambda delta: (-counts[delta], delta))
    share = counts[modal] / len(deltas)

    for frequency, seconds in _FREQUENCY_SECONDS.items():
        if modal == seconds:
            return frequency, (
                f"the most common interval is {modal:g}s, matching {frequency.name}, and "
                f"{share:.0%} of intervals agree with it"
            )
    return TimeFrequency.CUSTOM, (
        f"the most common interval is {modal:g}s, which matches no named frequency; "
        f"{share:.0%} of intervals agree with it"
    )
