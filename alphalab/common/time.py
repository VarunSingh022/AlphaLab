"""Shared time helpers, and the precision of an instant.

What an instant is, and how finely it can be told apart
--------------------------------------------------------
Every instant on the execution path -- a record's timestamp, an order's, a fill's
-- is a ``float`` of Unix seconds. That is fundamental and frozen: it is what
every state, snapshot and identity since v1 carries. Its resolution is the
spacing of IEEE 754 doubles at the instant, which :func:`instant_resolution`
reports: about 0.24 microseconds at current epochs (``2**-22`` seconds from
``2**30`` to ``2**31`` Unix seconds, 10 January 2004 to 19 January 2038), fine
enough for a microsecond feed and far too coarse for a nanosecond one.

So two events a nanosecond feed tells apart can carry the same instant, and the
library never orders same-instant events by their timestamps. Market records
are processed in the order the dataset or source delivers them -- that order is
the sequence -- and a venue's reports are ordered by the venue's own sequence
numbers where it numbers them (:class:`~alphalab.broker.lifecycle.VenueEvent`,
v3.11). A caller who needs nanosecond identity carries it in the record, as a
sequence or an identifier, not in the instant. Data quality reports two rows
that land on one instant as a duplicate instant rather than guessing which came
first. (Ledger DAT-008: a limitation kept, and stated.)
"""

import math
from datetime import UTC, datetime

from alphalab.common.exceptions import AlphaLabValidationError


def instant_resolution(instant: float) -> float:
    """The smallest step an instant can take at ``instant``, in seconds.

    The spacing of doubles there (:func:`math.ulp`): ``2**-22`` seconds, about
    0.24 microseconds, for any instant from 10 January 2004 to 19 January 2038.
    Two moments closer than this are the same instant. See the module docstring.
    """

    return math.ulp(instant)


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(UTC)


def ensure_timezone_aware(value: datetime, field_name: str = "timestamp") -> datetime:
    """Validate and return a timezone-aware datetime."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise AlphaLabValidationError(f"{field_name} must be timezone-aware")
    return value


def to_utc(value: datetime) -> datetime:
    """Convert a timezone-aware datetime to UTC."""

    return ensure_timezone_aware(value).astimezone(UTC)
