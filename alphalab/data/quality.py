"""What the data is like, in detail and in summary -- from one source of truth.

Two report types live here, and the relationship between them is the point:

* :class:`DataQualityReport` is the **authority**. It carries the counts *and*
  every individual :class:`~alphalab.data.validation.ValidationFinding`, so a
  reader can go from "17 invalid records" to the seventeen rows.
* :class:`QualityReport` is a **projection** of it -- the scored summary that
  :class:`~alphalab.data.state.UniversalDataState` stores and
  :class:`~alphalab.data.catalog.CatalogRecord` carries. It is produced by
  :meth:`DataQualityReport.summarize` and never assembled independently.

That is deliberately the same shape as ``BacktestResult.dataset_id``, which is
a property over ``RunState`` rather than a second copy of the identity: one
fact, one home, two views. Two independently-computed quality reports could
come to disagree about the same dataset, and the one stored in state would win
while the detailed one was the one people read.

A score is a summary, never the whole story
-------------------------------------------

:attr:`QualityReport.quality_score` exists because operators want one number to
sort a catalogue by. It is not sufficient to decide whether a dataset is usable:
a file can score 97 and be unusable because the 3% missing is the day of the
event being studied. The findings are mandatory; the score is convenience.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from alphalab.data.cleaning import is_internally_consistent
from alphalab.data.feed import Bar, CanonicalRecord
from alphalab.data.validation import (
    FindingKind,
    RowRejection,
    Severity,
    ValidationFinding,
    validate_records,
)

__all__ = ["DataQualityReport", "QualityReport", "evaluate_bar_quality", "evaluate_quality"]

#: Weights the summary score is composed from. Unchanged from v1 so that a
#: score recorded by an earlier release means the same thing today.
_COMPLETENESS_WEIGHT = 0.4
_CONSISTENCY_WEIGHT = 0.6


@dataclass(frozen=True, slots=True)
class QualityReport:
    """The scored summary of a dataset's quality.

    Attributes:
        dataset_id: The dataset this describes.
        completeness: Percentage of source rows that became records.
        consistency: Percentage of records carrying no error-level finding.
        missing_count: Rows rejected for a missing required value.
        duplicate_count: Instrument/instant collisions found.
        invalid_count: Records whose own fields contradict each other.
        out_of_order_count: Records preceding the one before them.
        quality_score: Weighted combination of completeness and consistency.
    """

    dataset_id: str
    completeness: float
    consistency: float
    missing_count: int
    duplicate_count: int
    invalid_count: int
    out_of_order_count: int
    quality_score: float


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """Everything found in the data, with the evidence attached.

    Attributes:
        dataset_id: The dataset this describes.
        row_count: Rows read from the source, excluding the header.
        valid_rows: Rows that became records.
        rejected_rows: Rows that could not, each with its reasons.
        duplicate_count: Instrument/instant collisions.
        missing_count: Rows rejected for a missing required value.
        invalid_count: Records whose own fields contradict each other.
        out_of_order_count: Records preceding the one before them.
        warnings: Findings worth knowing that do not invalidate a record.
        errors: Findings that do.
    """

    dataset_id: str
    row_count: int
    valid_rows: int
    rejected_rows: tuple[RowRejection, ...] = ()
    duplicate_count: int = 0
    missing_count: int = 0
    invalid_count: int = 0
    out_of_order_count: int = 0
    warnings: tuple[ValidationFinding, ...] = ()
    errors: tuple[ValidationFinding, ...] = field(default_factory=tuple)

    @property
    def rejected_count(self) -> int:
        """How many rows did not become records."""

        return len(self.rejected_rows)

    @property
    def completeness(self) -> float:
        """Percentage of source rows that became records."""

        if self.row_count <= 0:
            return 0.0
        return round(100.0 * self.valid_rows / self.row_count, 2)

    @property
    def consistency(self) -> float:
        """Percentage of records carrying no error-level finding.

        Capped at zero rather than going negative: a series can carry more
        error findings than it has records -- one bar can be both out of order
        and impossibly shaped -- and a negative percentage would read as a
        defect in the report rather than in the data.
        """

        if self.valid_rows <= 0:
            return 0.0
        flawed = self.duplicate_count + self.invalid_count + self.out_of_order_count
        return round(100.0 * max(0.0, 1.0 - flawed / self.valid_rows), 2)

    @property
    def quality_score(self) -> float:
        """One number for sorting a catalogue by. Never a substitute for the findings."""

        return round(
            self.completeness * _COMPLETENESS_WEIGHT + self.consistency * _CONSISTENCY_WEIGHT, 2
        )

    def summarize(self) -> QualityReport:
        """Project this report onto the scored summary stored in state."""

        return QualityReport(
            dataset_id=self.dataset_id,
            completeness=self.completeness,
            consistency=self.consistency,
            missing_count=self.missing_count,
            duplicate_count=self.duplicate_count,
            invalid_count=self.invalid_count,
            out_of_order_count=self.out_of_order_count,
            quality_score=self.quality_score,
        )


def evaluate_quality(
    dataset_id: str,
    records: Sequence[CanonicalRecord],
    row_count: int,
    rejected: Sequence[RowRejection],
    findings: Sequence[ValidationFinding],
) -> DataQualityReport:
    """Assemble the detailed report from an ingestion's own measurements.

    Args:
        dataset_id: What the dataset is called.
        records: The records that were built.
        row_count: How many rows the source held, header excluded.
        rejected: Rows that did not become records.
        findings: Every finding raised at row and set level.
    """

    errors = tuple(finding for finding in findings if finding.severity is Severity.ERROR)
    warnings = tuple(finding for finding in findings if finding.severity is Severity.WARNING)

    missing = sum(
        1
        for rejection in rejected
        if any(finding.kind is FindingKind.MISSING_VALUE for finding in rejection.findings)
    )

    return DataQualityReport(
        dataset_id=dataset_id,
        row_count=row_count,
        valid_rows=len(records),
        rejected_rows=tuple(rejected),
        duplicate_count=_count(findings, FindingKind.DUPLICATE_TIMESTAMP),
        missing_count=missing,
        invalid_count=_invalid_records(records),
        out_of_order_count=_count(findings, FindingKind.OUT_OF_ORDER),
        warnings=warnings,
        errors=errors,
    )


def _count(findings: Sequence[ValidationFinding], kind: FindingKind) -> int:
    return sum(1 for finding in findings if finding.kind is kind)


def _invalid_records(records: Sequence[CanonicalRecord]) -> int:
    """How many records contradict themselves.

    Counted over records rather than findings because one bar can raise several
    findings -- a negative open is both non-positive and outside its own range
    -- and the count people mean by "invalid" is of rows, not complaints.
    """

    return sum(1 for record in records if not is_internally_consistent(record))


def evaluate_bar_quality(dataset_id: str, bars: tuple[Bar, ...]) -> QualityReport:
    """Score a tuple of bars that are already records.

    This is the surface the engine facade has always had, and it measures what
    a tuple of bars can show. ``missing_count`` is necessarily zero here: a
    missing value is a property of a *row*, and a row with one never becomes a
    bar. :func:`evaluate_quality` is the ingestion-time surface that can count
    them, because it still has the rows.
    """

    if not bars:
        return QualityReport(dataset_id, 0.0, 0.0, 0, 0, 0, 0, 0.0)

    findings = validate_records(bars, expected_frequency=None)
    return evaluate_quality(
        dataset_id=dataset_id,
        records=bars,
        row_count=len(bars),
        rejected=(),
        findings=findings,
    ).summarize()
