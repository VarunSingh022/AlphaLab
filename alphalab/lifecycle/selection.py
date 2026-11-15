"""Selection statistics over captured backtests: the bridge (v4.3, ADR-0055 section 6, plan K.2).

ADR-0054's statistics take a :class:`~alphalab.research.selection.TrialMatrix`;
ADR-0055's captures produce one series per trial. This module joins them, so the
statistics are never disconnected from AlphaLab's own backtests and never wait
for ADR-0072's trial ledger:

* :func:`trial_matrix_from_captures` builds the matrix from captures that share
  one extractor and one grid, and refuses any that do not -- two series compare
  only when every choice that made them is the same (AC-12), and only over the
  same instants;
* :func:`assess_captures` runs the probability of backtest overfitting over it,
  and keeps beside the result the identity of everything it read: the
  extractor, and each trial's ``series_id``.

The matrix is built from the captured returns at full precision, unchanged, so a
statistic over captures equals the same statistic over the same runs' full
histories (M3's exit gate holds exactly that).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.backtesting.capture import CaptureRecord
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.research.selection import (
    PBOResult,
    SelectionMeasure,
    TrialMatrix,
    Trim,
    probability_of_backtest_overfitting,
)

__all__ = ["CaptureAssessment", "assess_captures", "trial_matrix_from_captures"]


def trial_matrix_from_captures(
    captures: Sequence[CaptureRecord], *, labels: Sequence[str]
) -> TrialMatrix:
    """ADR-0054's matrix of ``captures``: one column per trial, one row per period.

    Each row is a period of the captures' common grid, at the instant it ends;
    each column is one capture's returns, unchanged, under its label.

    Args:
        captures: One record per trial, in column order.
        labels: Each trial's label, in the same order; distinct.

    Raises:
        LifecycleInputError: If ``captures`` is empty or not capture records,
            the labels are not one per capture, or the captures do not share one
            extractor and one grid.
    """

    records = tuple(captures)
    names = tuple(labels)
    if not records:
        raise LifecycleInputError("A trial matrix needs at least one capture.")
    for record in records:
        if not isinstance(record, CaptureRecord):
            raise LifecycleInputError(f"Each capture must be a CaptureRecord, got {record!r}.")
    if len(names) != len(records):
        raise LifecycleInputError(
            f"{len(records)} captures were given {len(names)} labels: each trial needs one."
        )
    first = records[0]
    for name, record in zip(names, records, strict=True):
        if record.extractor_id != first.extractor_id:
            raise LifecycleInputError(
                f"Trial {name!r} was captured under extractor {record.extractor_id}, and "
                f"{names[0]!r} under {first.extractor_id}. Two series compare only under one "
                "extractor: every choice that made them -- grid, costs, cash flows, currency, "
                "insolvency -- must be the same (ADR-0055, AC-12)."
            )
        if record.instants != first.instants:
            raise LifecycleInputError(
                f"Trial {name!r} was read over {len(record.instants)} grid instants and "
                f"{names[0]!r} over {len(first.instants)}, or over other ones: a trial matrix "
                "is over one common set of instants. A series ended at insolvency is shorter "
                "than its peers."
            )
    try:
        return TrialMatrix(
            labels=names,
            instants=first.period_ends,
            columns=tuple(record.returns for record in records),
            measure=f"captured period returns, extractor {first.extractor_id}",
        )
    except Exception as error:
        raise LifecycleInputError(f"The captures form no trial matrix: {error}") from error


@dataclass(frozen=True, slots=True)
class CaptureAssessment:
    """The probability of backtest overfitting of a search, beside what it read.

    Attributes:
        matrix: The trial matrix the statistic was computed over.
        extractor_id: The extractor every trial was captured under.
        series_ids: Each trial's ``series_id``, in column order.
        overfitting: The probability of backtest overfitting (ADR-0054).
    """

    matrix: TrialMatrix
    extractor_id: str
    series_ids: tuple[str, ...]
    overfitting: PBOResult


def assess_captures(
    captures: Sequence[CaptureRecord],
    *,
    labels: Sequence[str],
    blocks: int,
    measure: SelectionMeasure,
    trim: Trim,
) -> CaptureAssessment:
    """The probability of backtest overfitting of the search ``captures`` record.

    Every argument the statistic takes is required, as ADR-0054 requires it;
    see :func:`~alphalab.research.selection.probability_of_backtest_overfitting`.
    The Reality Check and SPA take a matrix of performance relative to a
    benchmark, which the caller forms from :func:`trial_matrix_from_captures`.

    Raises:
        LifecycleInputError: If the captures form no trial matrix.
        ResearchValidationError: If the statistic refuses its arguments.
    """

    records = tuple(captures)
    matrix = trial_matrix_from_captures(records, labels=labels)
    return CaptureAssessment(
        matrix=matrix,
        extractor_id=records[0].extractor_id,
        series_ids=tuple(record.series_id for record in records),
        overfitting=probability_of_backtest_overfitting(
            matrix, blocks=blocks, measure=measure, trim=trim
        ),
    )
