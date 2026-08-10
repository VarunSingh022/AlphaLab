"""Generic point-in-time correctness utilities.

Any timestamped record with a `reference_period` (the period a value describes) and
a `release_date` (when that value became publicly known) can be queried for "what
was actually known as of a given time" via `known_as_of`, without leaking later
revisions or not-yet-released values into a backtest -- the same look-ahead bias
risk that applies to economic data revisions applies equally to any lagged,
periodically-revised data source.

This was originally implemented directly inside `alphalab.macro.indicator` for
`IndicatorObservation`. Generalized here via a structural Protocol so
`alphalab.alt_data` (news, satellite, ESG, etc. -- all of which have their own
release lag) shares the same implementation rather than reinventing it, the same
lesson repeatedly learned the hard way from the Order/Side/Status enum
fragmentation across broker/brokers/oms/execution earlier in this project.
`alphalab.macro.indicator.known_as_of` now delegates here.
"""

from typing import Protocol


class PointInTimeRecord(Protocol):
    """Structural interface any point-in-time-queryable record must satisfy."""

    @property
    def reference_period(self) -> float: ...

    @property
    def release_date(self) -> float: ...


def known_as_of[T: PointInTimeRecord](records: tuple[T, ...], as_of: float) -> T | None:
    """Returns the record reflecting what was actually known at `as_of`.

    Filters to records released on or before `as_of`, then prefers the most recent
    reference_period, and within that period, the most recent release (the latest
    revision known by that date). Returns None if nothing had been released yet by
    `as_of`.
    """
    known = tuple(r for r in records if r.release_date <= as_of)
    if not known:
        return None
    return max(known, key=lambda r: (r.reference_period, r.release_date))
