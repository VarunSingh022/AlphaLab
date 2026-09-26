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

When something became knowable, stated rather than assumed (v3.7)
-----------------------------------------------------------------

`known_as_of` trusts its records: a ``release_date`` is taken to be the instant a
value became knowable, and every record is assumed to have one. v3.7's research
layers -- events, alternative data, fundamentals, and the adaptive updates driven
by them -- need three things that protocol cannot say:

* **Whether the availability instant is known at all.** A vendor file that
  carries only the period a figure describes says nothing about when the figure
  was published. :class:`AvailabilityBasis` records how the instant was
  established -- stated by the source, derived by a named rule, or not
  established -- and a record whose availability is ``UNKNOWN`` is never visible
  to research. It is not assumed to be available at its observation time, which
  is the assumption that turns period-end-stamped fundamentals into look-ahead.
* **Whose clock decides visibility.** :class:`VisibilityRule` separates what the
  world could have known (``PUBLICATION``) from what *this system* could have
  known (``INGESTION``). A record backfilled months after it was published is
  visible under the first from its publication and under the second only from
  when it arrived.
* **When a known fact takes effect.** A split announced in January and
  effective in February is known in January and in effect in February, and an
  effective date earlier than the announcement -- a retroactive change -- never
  makes the fact visible before it was announced.

:class:`PointInTimeStamp` carries the four instants and the basis, and refuses
the combinations that cannot be true. :class:`PointInTimeIndex` answers "what was
visible at this instant?" by bisection over the knowledge instants, so a study
sampling thousands of instants does not rescan every record for each one -- the
rescan `known_as_of` performs, and which stays correct and unchanged for the
callers that use it.

Nothing here reads a clock. Every instant is supplied, which is what lets two
processes replay the same history and see the same thing at every step.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from itertools import pairwise
from typing import Protocol

from alphalab.common.exceptions import AlphaLabValidationError

__all__ = [
    "AvailabilityBasis",
    "PointInTimeIndex",
    "PointInTimeRecord",
    "PointInTimeStamp",
    "StampedRecord",
    "VisibilityRule",
    "known_as_of",
]


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

    Two records with the same reference period *and* the same release date are
    an exact tie, and the first of them in input order is returned. That is
    deterministic for a given input and dependent on its order; v3.7's
    :class:`PointInTimeIndex` and the selections built on it order ties by a
    content-derived record identity instead, and refuse two vintages that claim
    the same revision.
    """
    known = tuple(r for r in records if r.release_date <= as_of)
    if not known:
        return None
    return max(known, key=lambda r: (r.reference_period, r.release_date))


class AvailabilityBasis(Enum):
    """How a record's availability instant was established.

    Three members, because "when did this become knowable?" has three honest
    answers, and only two of them are instants.
    """

    #: The source stated the instant: a publication timestamp, a filing's
    #: acceptance time, a feed's delivery stamp.
    DECLARED = auto()

    #: The instant was derived from declared facts by a rule the stamp names --
    #: a date-only release read as the next session's open, a documented
    #: processing lag. The rule travels with the stamp so the derivation can be
    #: checked.
    DERIVED = auto()

    #: Nobody stated when the record became knowable. It cannot be shown to be
    #: point-in-time safe, so it is never visible to research, and nothing here
    #: substitutes its observation time for the missing instant.
    UNKNOWN = auto()


class VisibilityRule(Enum):
    """Whose clock decides when a record becomes visible to research."""

    #: Visible once its availability instant has passed: what the world could
    #: have known.
    PUBLICATION = auto()

    #: Visible once its availability instant *and* the instant this system
    #: ingested it have both passed: what this system could have known. A record
    #: with no recorded ingestion instant is never visible under this rule --
    #: its arrival cannot be placed, so it is not assumed to have been on time.
    INGESTION = auto()


def _require_instant(value: float, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AlphaLabValidationError(f"{field} is {value!r}; an instant is a number of seconds.")
    if not math.isfinite(value):
        raise AlphaLabValidationError(
            f"{field} is {value!r}. A non-finite instant cannot be ordered against any other."
        )


@dataclass(frozen=True, slots=True)
class PointInTimeStamp:
    """When a record describes, when it became knowable, and when it takes effect.

    Attributes:
        observed_at: The instant the fact describes or was measured at: an
            event's occurrence, the end of the period a figure covers, a
            satellite pass. Unix seconds.
        available_at: The instant the fact became knowable, or ``None`` when
            nobody stated it -- in which case ``basis`` is ``UNKNOWN``. Never
            earlier than ``observed_at``: nothing is known before it happens.
        basis: How ``available_at`` was established.
        effective_at: The instant the fact takes effect, where that differs
            from when it was observed -- a split's ex-date, a rate decision's
            effective date, a restatement effective for an earlier period.
            ``None`` means the fact takes effect as soon as it is known. It may
            precede ``available_at`` (a retroactive change); that never makes
            the fact visible early.
        ingested_at: The instant this system received the record, or ``None``
            when not recorded. Read only by :attr:`VisibilityRule.INGESTION`.
        rule: The derivation, in words, when ``basis`` is ``DERIVED``; empty
            otherwise. A derived instant whose derivation is not stated cannot
            be checked, and one that states a rule for a declared instant would
            suggest a derivation that never happened.

    Raises:
        AlphaLabValidationError: If an instant is not a finite number; if the
            basis and the presence of ``available_at`` disagree; if the record
            is available or ingested before it was observed; or if ``rule`` is
            missing for a derived instant, present for any other, or spans a
            line.
    """

    observed_at: float
    available_at: float | None
    basis: AvailabilityBasis
    effective_at: float | None = None
    ingested_at: float | None = None
    rule: str = ""

    def __post_init__(self) -> None:
        _require_instant(self.observed_at, "observed_at")
        for field, value in (
            ("available_at", self.available_at),
            ("effective_at", self.effective_at),
            ("ingested_at", self.ingested_at),
        ):
            if value is not None:
                _require_instant(value, field)

        if self.basis is AvailabilityBasis.UNKNOWN and self.available_at is not None:
            raise AlphaLabValidationError(
                f"The stamp declares an availability instant ({self.available_at!r}) and calls "
                "its basis UNKNOWN. A stated instant is DECLARED or DERIVED."
            )
        if self.basis is not AvailabilityBasis.UNKNOWN and self.available_at is None:
            raise AlphaLabValidationError(
                f"The stamp's basis is {self.basis.name} and it carries no availability "
                "instant. A record whose availability nobody stated is UNKNOWN, and is never "
                "visible to research."
            )
        if self.available_at is not None and self.available_at < self.observed_at:
            raise AlphaLabValidationError(
                f"available_at {self.available_at!r} precedes observed_at {self.observed_at!r}. "
                "Nothing is knowable before it happens."
            )
        if self.ingested_at is not None and self.ingested_at < self.observed_at:
            raise AlphaLabValidationError(
                f"ingested_at {self.ingested_at!r} precedes observed_at {self.observed_at!r}. A "
                "record cannot arrive before the fact it records."
            )
        if "\n" in self.rule or "\r" in self.rule:
            raise AlphaLabValidationError("A derivation rule is one line of text.")
        if self.basis is AvailabilityBasis.DERIVED and not self.rule.strip():
            raise AlphaLabValidationError(
                "A DERIVED availability instant must state the rule it was derived by; an "
                "unstated derivation cannot be checked."
            )
        if self.basis is not AvailabilityBasis.DERIVED and self.rule:
            raise AlphaLabValidationError(
                f"The stamp's basis is {self.basis.name} and it states a derivation rule "
                f"({self.rule!r}). Only a DERIVED instant has one."
            )

    @classmethod
    def declared(
        cls,
        observed_at: float,
        available_at: float,
        *,
        effective_at: float | None = None,
        ingested_at: float | None = None,
    ) -> PointInTimeStamp:
        """A stamp whose availability instant the source stated."""

        return cls(
            observed_at=observed_at,
            available_at=available_at,
            basis=AvailabilityBasis.DECLARED,
            effective_at=effective_at,
            ingested_at=ingested_at,
        )

    @classmethod
    def derived(
        cls,
        observed_at: float,
        available_at: float,
        rule: str,
        *,
        effective_at: float | None = None,
        ingested_at: float | None = None,
    ) -> PointInTimeStamp:
        """A stamp whose availability instant was derived by ``rule``."""

        return cls(
            observed_at=observed_at,
            available_at=available_at,
            basis=AvailabilityBasis.DERIVED,
            effective_at=effective_at,
            ingested_at=ingested_at,
            rule=rule,
        )

    @classmethod
    def unknown(
        cls,
        observed_at: float,
        *,
        effective_at: float | None = None,
        ingested_at: float | None = None,
    ) -> PointInTimeStamp:
        """A stamp for a record whose availability nobody stated."""

        return cls(
            observed_at=observed_at,
            available_at=None,
            basis=AvailabilityBasis.UNKNOWN,
            effective_at=effective_at,
            ingested_at=ingested_at,
        )

    @property
    def verifiable(self) -> bool:
        """Whether the availability instant was established at all."""

        return self.basis is not AvailabilityBasis.UNKNOWN

    def known_at(self, visibility: VisibilityRule) -> float | None:
        """The instant this record becomes visible under ``visibility``, or ``None``.

        ``None`` means *never*: an ``UNKNOWN`` availability under either rule,
        or a missing ingestion instant under ``INGESTION``. A caller that needs
        a verdict rather than an absence uses :meth:`is_visible`.
        """

        if self.available_at is None:
            return None
        if visibility is VisibilityRule.PUBLICATION:
            return self.available_at
        if self.ingested_at is None:
            return None
        return max(self.available_at, self.ingested_at)

    def is_visible(self, as_of: float, visibility: VisibilityRule) -> bool:
        """Whether the record is knowable at ``as_of`` under ``visibility``.

        Inclusive: a record that became knowable exactly at ``as_of`` is visible
        at ``as_of``, the convention ``known_as_of`` and a bar stamped at its
        close already follow.
        """

        _require_instant(as_of, "as_of")
        known = self.known_at(visibility)
        return known is not None and known <= as_of

    def in_effect_at(self, as_of: float, visibility: VisibilityRule) -> bool:
        """Whether the record is both knowable and in effect at ``as_of``.

        A record with no effective instant takes effect as soon as it is known.
        One whose effective instant precedes its availability is in effect from
        the moment it becomes known, never earlier.
        """

        if not self.is_visible(as_of, visibility):
            return False
        return self.effective_at is None or self.effective_at <= as_of


class StampedRecord(Protocol):
    """Anything a :class:`PointInTimeIndex` can order.

    ``record_id`` is a content-derived identity. It is the final tie-break
    between records known at the same instant and observed at the same instant,
    which is what makes the order independent of the order records were handed
    over in.
    """

    @property
    def stamp(self) -> PointInTimeStamp: ...

    @property
    def record_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class PointInTimeIndex[T: StampedRecord]:
    """Records ordered by when they became knowable, for bisection by instant.

    Built by :meth:`build`, which sorts once. ``visible_as_of`` is then a
    bisection and a slice: ``O(log n + k)`` for ``k`` visible records, against
    the ``O(n)`` rescan per query a filter would perform.

    Attributes:
        visibility: The rule the knowledge instants were computed under.
        records: Every record with a knowledge instant, ordered by
            ``(knowledge instant, observed_at, record_id)``.
        known_instants: Each record's knowledge instant, in the same order.
        unverified: Records with no knowledge instant under ``visibility`` --
            an unknown availability, or no ingestion instant under
            ``INGESTION`` -- ordered by ``record_id``. They are never visible,
            and are kept so a caller can count and report them rather than
            lose them.

    Raises:
        AlphaLabValidationError: If the records and their instants cannot be
            paired, or the instants are out of order -- which a hand-built index
            could get wrong and :meth:`build` cannot.
    """

    visibility: VisibilityRule
    records: tuple[T, ...]
    known_instants: tuple[float, ...]
    unverified: tuple[T, ...]

    def __post_init__(self) -> None:
        if len(self.records) != len(self.known_instants):
            raise AlphaLabValidationError(
                f"{len(self.records)} records and {len(self.known_instants)} knowledge instants "
                "cannot be paired."
            )
        if any(later < earlier for earlier, later in pairwise(self.known_instants)):
            raise AlphaLabValidationError(
                "The knowledge instants are not in order, so bisecting them would answer a "
                "different question. Build the index with PointInTimeIndex.build."
            )

    @classmethod
    def build(cls, records: Iterable[T], visibility: VisibilityRule) -> PointInTimeIndex[T]:
        """Order ``records`` by when they became knowable under ``visibility``.

        Raises:
            AlphaLabValidationError: If two records share a ``record_id``. The
                same fact handed over twice would be counted twice by every
                selection built on the index.
        """

        knowable: list[tuple[float, float, str, T]] = []
        unverified: list[tuple[str, T]] = []
        seen: set[str] = set()
        for record in records:
            identity = record.record_id
            if identity in seen:
                raise AlphaLabValidationError(
                    f"Record {identity} appears more than once. One fact handed over twice "
                    "would be counted twice at every instant it is visible."
                )
            seen.add(identity)
            known = record.stamp.known_at(visibility)
            if known is None:
                unverified.append((identity, record))
            else:
                knowable.append((known, record.stamp.observed_at, identity, record))

        knowable.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
        unverified.sort(key=lambda entry: entry[0])
        return cls(
            visibility=visibility,
            records=tuple(entry[3] for entry in knowable),
            known_instants=tuple(entry[0] for entry in knowable),
            unverified=tuple(entry[1] for entry in unverified),
        )

    def __len__(self) -> int:
        return len(self.records) + len(self.unverified)

    def count_visible(self, as_of: float) -> int:
        """How many records are knowable at ``as_of`` (inclusive)."""

        _require_instant(as_of, "as_of")
        return bisect_right(self.known_instants, as_of)

    def visible_as_of(self, as_of: float) -> tuple[T, ...]:
        """Every record knowable at ``as_of``, in knowledge order."""

        return self.records[: self.count_visible(as_of)]

    def pending_at(self, as_of: float) -> int:
        """How many records have a knowledge instant still ahead of ``as_of``."""

        return len(self.records) - self.count_visible(as_of)

    def known_between(self, after: float, through: float) -> tuple[T, ...]:
        """Records that became knowable in ``(after, through]``.

        What an incremental consumer reads at each step: the records that
        arrived since the last instant it looked, and nothing it has seen.

        Raises:
            AlphaLabValidationError: If ``through`` precedes ``after``.
        """

        _require_instant(after, "after")
        _require_instant(through, "through")
        if through < after:
            raise AlphaLabValidationError(
                f"The interval ({after!r}, {through!r}] runs backwards in time."
            )
        start = bisect_right(self.known_instants, after)
        stop = bisect_right(self.known_instants, through)
        return self.records[start:stop]

    def next_known_instant(self, as_of: float) -> float | None:
        """The first knowledge instant strictly after ``as_of``, or ``None``."""

        position = self.count_visible(as_of)
        if position >= len(self.known_instants):
            return None
        return self.known_instants[position]
