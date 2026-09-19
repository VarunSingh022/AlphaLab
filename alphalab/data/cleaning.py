"""Deterministic cleaning, under a policy, with every change recorded.

The rule this module exists to enforce: **AlphaLab never silently alters a
user's data.** Every transformation applied is returned as a
:class:`TransformationRecord` saying what was done, to how many rows, and why,
and those records are carried into the dataset's provenance. A cleaned dataset
can always answer "what was changed to get here?".

The policy is the caller's
--------------------------

:class:`CleaningPolicy` has no defaults. Whether a duplicate timestamp is a
fatal problem or a routine artefact of a vendor's export depends entirely on
the desk and the dataset, and a default either way would be an invented policy
presented as an architectural one -- ADR-0033 decision 10's rule, applied here.
:data:`REFUSE_EVERYTHING` is the named starting point, in the spirit of
``NO_RATES``: it is not a lenient default, it is the position that nothing may
be altered at all.

What is deliberately not offered
--------------------------------

**Filling a missing price.** Not forward-fill, not interpolation, not
last-known-value. Every one of them invents a print that never happened, and
the invention is invisible by the time it reaches a backtest: the equity curve
is smooth, the drawdown is understated, and nothing in the output says a number
was manufactured. A gap in a price series is a fact about the market -- a halt,
a holiday, a delisting -- and the honest representations of it are to keep the
gap or to drop the row. Both are offered. Manufacturing a price is not, at any
policy setting, which is why :class:`MissingValuePolicy` has no ``FILL`` member
rather than a ``FILL`` member that raises.

**Repairing an impossible bar.** A bar with ``high < low`` is not a bar with a
small error in it; it is a row whose meaning is unknown. Clamping it to a
consistent range would produce a plausible-looking bar that the source never
reported.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from alphalab.data.exceptions import DataQualityError
from alphalab.data.feed import Bar, CanonicalRecord
from alphalab.data.validation import FindingKind, Severity, ValidationFinding

__all__ = [
    "REFUSE_EVERYTHING",
    "CleaningOutcome",
    "CleaningPolicy",
    "DuplicatePolicy",
    "InvalidRecordPolicy",
    "MissingValuePolicy",
    "OrderingPolicy",
    "TransformationRecord",
    "clean_records",
    "is_internally_consistent",
    "remove_duplicates",
    "remove_invalid_ohlc",
]


class DuplicatePolicy(Enum):
    """What to do with two records for one instrument at one instant."""

    #: Stop. Nothing in the data says which of the two is correct.
    REFUSE = auto()

    #: Keep the earliest occurrence in source order. The right reading when a
    #: vendor appends corrections *after* the original and the original is
    #: authoritative -- rare, and stated rather than assumed.
    KEEP_FIRST = auto()

    #: Keep the latest occurrence in source order. The right reading when a
    #: later row is a correction of an earlier one, which is the common shape
    #: of a re-published vendor file.
    KEEP_LAST = auto()


class OrderingPolicy(Enum):
    """What to do with records that are not in chronological order."""

    #: Stop. A source that claimed to be chronological and is not may have
    #: other problems, and sorting hides the evidence.
    REFUSE = auto()

    #: Sort by instrument and instant. Recorded as a transformation, because a
    #: reader comparing the dataset to the file will otherwise find the rows in
    #: a different order and have no explanation.
    SORT = auto()


class InvalidRecordPolicy(Enum):
    """What to do with a record that failed a set-level check."""

    #: Stop.
    REFUSE = auto()

    #: Drop it, recording how many and why.
    DROP = auto()

    #: Keep it, so that the dataset reflects the source exactly and the
    #: quality report is the only place the problem is noted. Legitimate when
    #: the point of the dataset is to study the defects.
    KEEP = auto()


class MissingValuePolicy(Enum):
    """What to do with a row missing a value a record requires.

    There is no ``FILL``. See this module's docstring.
    """

    #: Stop.
    REFUSE = auto()

    #: Drop the row, recording it.
    DROP_ROW = auto()


@dataclass(frozen=True, slots=True)
class CleaningPolicy:
    """The four decisions cleaning is not entitled to make on its own."""

    duplicates: DuplicatePolicy
    ordering: OrderingPolicy
    invalid_records: InvalidRecordPolicy
    missing_values: MissingValuePolicy


#: The position that nothing may be altered: every defect is a refusal.
#:
#: Named, like ``NO_RATES``, so that "no cleaning was configured" is expressible
#: without a default that quietly permits something.
REFUSE_EVERYTHING: Final = CleaningPolicy(
    duplicates=DuplicatePolicy.REFUSE,
    ordering=OrderingPolicy.REFUSE,
    invalid_records=InvalidRecordPolicy.REFUSE,
    missing_values=MissingValuePolicy.REFUSE,
)


@dataclass(frozen=True, slots=True)
class TransformationRecord:
    """One change applied to the data, and the reason for it."""

    operation: str
    rows_affected: int
    reason: str


@dataclass(frozen=True, slots=True)
class CleaningOutcome:
    """The cleaned records, and the complete list of what was done to get them."""

    records: tuple[CanonicalRecord, ...]
    transformations: tuple[TransformationRecord, ...]


def clean_records(
    records: Sequence[CanonicalRecord],
    policy: CleaningPolicy,
    findings: Sequence[ValidationFinding],
) -> CleaningOutcome:
    """Apply ``policy`` to ``records``, recording every change.

    The order of operations is fixed and part of the contract: drop invalid
    records, then resolve duplicates, then order. Dropping first means a
    duplicate of an invalid record does not survive by being the copy that was
    kept, and ordering last means the result is chronological whatever the
    earlier steps removed.

    Args:
        records: Records built from the source, in source order.
        policy: What may be changed.
        findings: The set-level findings from
            :func:`~alphalab.data.validation.validate_records`, which say which
            records are invalid and whether the series is ordered.

    Raises:
        DataQualityError: When the policy refuses a defect that is present. The
            message names the defect and the policy that refused it.
    """

    transformations: list[TransformationRecord] = []
    working = list(records)

    invalid_kinds = {
        FindingKind.INVALID_OHLC,
        FindingKind.NON_POSITIVE_PRICE,
        FindingKind.NEGATIVE_VOLUME,
    }
    has_invalid = any(
        finding.kind in invalid_kinds and finding.severity is Severity.ERROR for finding in findings
    )
    if has_invalid:
        if policy.invalid_records is InvalidRecordPolicy.REFUSE:
            raise DataQualityError(
                "The source contains records that are not internally consistent -- an "
                "impossible OHLC relationship, a non-positive price or a negative volume -- "
                "and InvalidRecordPolicy.REFUSE does not permit altering them. Choose DROP to "
                "remove them or KEEP to carry them with the defect recorded."
            )
        if policy.invalid_records is InvalidRecordPolicy.DROP:
            kept = [record for record in working if is_internally_consistent(record)]
            removed = len(working) - len(kept)
            if removed:
                transformations.append(
                    TransformationRecord(
                        operation="drop_invalid_record",
                        rows_affected=removed,
                        reason=(
                            "impossible OHLC relationship, non-positive price or negative "
                            "volume; the row's true values are unknown and cannot be repaired"
                        ),
                    )
                )
            working = kept

    duplicate_keys = _duplicate_keys(working)
    if duplicate_keys:
        if policy.duplicates is DuplicatePolicy.REFUSE:
            example = next(iter(sorted(duplicate_keys)))
            raise DataQualityError(
                f"{len(duplicate_keys)} instrument/instant pairs occur more than once (for "
                f"example {example[0]} at {example[1]!r}), and DuplicatePolicy.REFUSE does "
                "not permit choosing between them. Choose KEEP_FIRST or KEEP_LAST to say "
                "which copy is authoritative."
            )
        keep_last = policy.duplicates is DuplicatePolicy.KEEP_LAST
        deduplicated = _deduplicate(working, keep_last=keep_last)
        removed = len(working) - len(deduplicated)
        if removed:
            transformations.append(
                TransformationRecord(
                    operation="drop_duplicate",
                    rows_affected=removed,
                    reason=(
                        f"duplicate symbol+timestamp; {policy.duplicates.name} kept the "
                        f"{'last' if keep_last else 'first'} occurrence in source order"
                    ),
                )
            )
        working = deduplicated

    if not _is_ordered(working):
        if policy.ordering is OrderingPolicy.REFUSE:
            raise DataQualityError(
                "Records are not in chronological order per instrument, and "
                "OrderingPolicy.REFUSE does not permit reordering them. Choose SORT to order "
                "them, which will be recorded as a transformation."
            )
        moved = _count_out_of_place(working)
        working.sort(key=lambda record: (record.symbol, record.timestamp))
        transformations.append(
            TransformationRecord(
                operation="sort_chronologically",
                rows_affected=moved,
                reason=(
                    "the source was not ordered by instrument and instant; every downstream "
                    "consumer assumes non-decreasing timestamps"
                ),
            )
        )

    return CleaningOutcome(records=tuple(working), transformations=tuple(transformations))


def is_internally_consistent(record: CanonicalRecord) -> bool:
    """Whether a record's own fields contradict each other.

    The single definition of "invalid record" in this package.
    :mod:`alphalab.data.validation` raises findings from the same rules and
    :mod:`alphalab.data.quality` counts with this predicate, so a record can
    never be reported invalid by one and dropped as valid by another.

    A bar is consistent when its low is the lowest of its four prices, its
    high the highest, every price is strictly positive, and its volume is not
    negative. Records that are not bars carry no cross-field relationship to
    check and are consistent by construction.
    """

    if not isinstance(record, Bar):
        return True
    return (
        record.low <= record.high
        and record.low <= record.open <= record.high
        and record.low <= record.close <= record.high
        and min(record.open, record.high, record.low, record.close) > 0.0
        and record.volume >= 0.0
    )


def _duplicate_keys(records: Sequence[CanonicalRecord]) -> set[tuple[str, float]]:
    seen: set[tuple[str, float]] = set()
    duplicates: set[tuple[str, float]] = set()
    for record in records:
        key = (record.symbol, record.timestamp)
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return duplicates


def _deduplicate(records: Sequence[CanonicalRecord], keep_last: bool) -> list[CanonicalRecord]:
    """Keep one record per instrument/instant, preserving source order."""

    chosen: dict[tuple[str, float], CanonicalRecord] = {}
    for record in records:
        key = (record.symbol, record.timestamp)
        if keep_last or key not in chosen:
            chosen[key] = record
    return list(chosen.values())


def _is_ordered(records: Sequence[CanonicalRecord]) -> bool:
    previous: dict[str, float] = {}
    for record in records:
        last = previous.get(record.symbol)
        if last is not None and record.timestamp < last:
            return False
        previous[record.symbol] = record.timestamp
    return True


def _count_out_of_place(records: Sequence[CanonicalRecord]) -> int:
    """How many records a sort moves, so the record says something measured."""

    ordered = sorted(records, key=lambda record: (record.symbol, record.timestamp))
    return sum(
        1
        for before, after in zip(records, ordered, strict=True)
        if (before.symbol, before.timestamp) != (after.symbol, after.timestamp)
    )


def remove_duplicates(bars: tuple[Bar, ...]) -> tuple[Bar, ...]:
    """Preserve the first bar for each instrument and instant.

    Keyed on symbol *and* timestamp. Keying on timestamp alone -- which this
    did before v3.1 -- silently discarded every instrument but the first in any
    multi-symbol series, because two instruments printing in the same minute
    are not duplicates of one another.

    This is the unreported primitive. :func:`clean_records` is the surface that
    applies it under a policy and records what it removed.
    """

    seen: set[tuple[str, float]] = set()
    cleaned: list[Bar] = []
    for bar in bars:
        key = (bar.symbol, bar.timestamp)
        if key not in seen:
            cleaned.append(bar)
            seen.add(key)
    return tuple(cleaned)


def remove_invalid_ohlc(bars: tuple[Bar, ...]) -> tuple[Bar, ...]:
    """Drop bars whose own fields contradict each other.

    The unreported primitive behind :func:`clean_records`'
    ``InvalidRecordPolicy.DROP``.
    """

    return tuple(bar for bar in bars if is_internally_consistent(bar))
