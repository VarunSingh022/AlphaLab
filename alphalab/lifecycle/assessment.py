"""The statistics over a ledgered experiment (v4.3, ADR-0073 section 7, WS-06).

ADR-0054's statistics measure a search given its population, and the trial
ledger records the population (ADR-0072). This module reads one from the other.

* :func:`trial_matrix_of` is an experiment's **family**: one column per
  candidate that completed everywhere it was scored, one row per period.
  - A full-sample experiment's family is its completed trials' series.
  - A walk-forward or cross-validated experiment's is each candidate's fold
    series, joined, when the folds' windows tile the sample.
  - A CPCV experiment's is each candidate's series on one of its paths, which
    the caller names.
* :func:`assess_experiment` states every choice each statistic takes:
  - PBO by CSCV over the family;
  - the deflated Sharpe ratio of the family's best, ``N`` the members with a
    Sharpe ratio, or a smaller ``N`` the caller states with its reason
    (:class:`EffectiveTrials`);
  - White's Reality Check and Hansen's SPA of the family against a declared
    benchmark.

  It returns them with the family count -- every trial the ledger recorded,
  failures included -- that selection-aware evidence carries (section 8).

Two statistics divide by a member's dispersion, and a member can lack one: a
candidate that never trades has none, and a benchmark that is a candidate's
own returns leaves that candidate none relative to it. Research refuses both
(``per_period_sharpe``, ``superior_predictive_ability``), and the assessment
does not invent a value for either:

* the deflation's trial Sharpe ratios leave out the members whose returns have
  no dispersion, and the assessment names them (``flat``);
* SPA tests the members whose relative performance varies, and the assessment
  names the others (``constant``). Each one's constant is its verdict: it beats
  the benchmark by that much in every period, or by nothing. The Reality
  Check, which divides by nothing, takes the whole family.

No threshold decides anything (ADR-0054). An :class:`ExperimentAssessment`
has an identity over its experiment's record, every choice made -- a stated
``N`` and its reason among them -- and the exact results: PBO's fraction, the
p-values, and the members each statistic left out. The deflated Sharpe ratio
is correctly rounded at best, so it enters no identity (ADR-0058).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

from alphalab.backtesting.capture import CaptureRecord
from alphalab.common.statistics import sample_variance
from alphalab.lifecycle.designs import (
    CombinatorialPurged,
    CrossValidated,
    ExperimentError,
    FullSample,
    WalkForward,
)
from alphalab.lifecycle.experiments import (
    ExperimentRecord,
    ExperimentSpec,
    TrialLedger,
    TrialOutcome,
    _capture_of,
    _digest_text,
    _joined,
    _matrix_identity,
    _score,
)
from alphalab.lifecycle.selection import trial_matrix_from_captures
from alphalab.research.data_snooping import (
    RealityCheckResult,
    SPAResult,
    reality_check,
    superior_predictive_ability,
)
from alphalab.research.selection import (
    PBOResult,
    SelectionMeasure,
    TrialMatrix,
    Trim,
    probability_of_backtest_overfitting,
)
from alphalab.research.sharpe_inference import (
    DeflatedSharpe,
    deflated_sharpe_ratio,
    per_period_sharpe,
)

__all__ = [
    "ASSESSMENT_SCHEME",
    "EffectiveTrials",
    "ExperimentAssessment",
    "assess_experiment",
    "trial_matrix_of",
]

#: The scheme an assessment's identity is written under.
ASSESSMENT_SCHEME: Final = "alphalab.experiment_assessment.v1"


@dataclass(frozen=True, slots=True)
class EffectiveTrials:
    """A smaller ``N`` for the deflation, and why (ADR-0073 section 7).

    The deflation's ``N`` is the effective number of *independent* trials. By
    default it is the family's members with a Sharpe ratio, which bounds it
    from above when the trials are correlated. A researcher who has measured
    the search's effective size -- by clustering the trials' returns, say --
    states it here with the reason, and the assessment records both.

    Attributes:
        count: ``N``: at least 2, and at most the members with a Sharpe ratio.
        reason: Why the search is that size: the method, and what it found.

    Raises:
        ExperimentError: If ``count`` is not an integer of at least 2, or the
            reason is not stated.
    """

    count: int
    reason: str

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 2:
            raise ExperimentError(
                f"EffectiveTrials.count must be an integer of at least 2, got {self.count!r}: "
                "a deflation compares the selection with the best of N trials."
            )
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ExperimentError(
                "EffectiveTrials.reason must be stated: a smaller N is recorded with why."
            )


def _family(
    spec: ExperimentSpec, ledger: TrialLedger, record: ExperimentRecord, path: int | None
) -> tuple[list[CaptureRecord], list[str]]:
    design = spec.validation
    if record.experiment_id != spec.experiment_id:
        raise ExperimentError(
            f"The record is of experiment {record.experiment_id}, not {spec.experiment_id}."
        )
    if isinstance(design, CombinatorialPurged):
        if path is None:
            raise ExperimentError(
                "A CPCV experiment's family is its candidates' series on one path: name it."
            )
    elif path is not None:
        raise ExperimentError(f"Only a CPCV experiment has paths; {spec.name}'s design does not.")
    extractor = spec.capture.extractor.extractor_id
    captures: list[CaptureRecord] = []
    labels: list[str] = []
    if isinstance(design, FullSample):
        for trial in spec.trials():
            held = ledger.find(trial.trial_id)
            if held is not None and held.outcome is TrialOutcome.COMPLETED:
                captures.append(_capture_of(ledger, held))
                labels.append(trial.label)
        return captures, labels
    if isinstance(design, CombinatorialPurged):
        assert path is not None
        for label, ids in record.paths:
            if not 0 <= path < len(ids):
                raise ExperimentError(
                    f"The experiment has {len(ids)} paths; there is no path {path}."
                )
            if ids[path] is not None:
                joined = _joined(ledger, [ids[path]], extractor)
                assert joined is not None
                captures.append(joined)
                labels.append(label)
        return captures, labels
    assert isinstance(design, WalkForward | CrossValidated)
    by_label: dict[str, list[str | None]] = {}
    for trial in spec.trials():
        held = ledger.find(trial.trial_id)
        complete = held is not None and held.outcome is TrialOutcome.COMPLETED
        by_label.setdefault(trial.label, []).append(
            held.series_id if complete and held is not None else None
        )
    for label, fold_ids in by_label.items():
        if any(each is None for each in fold_ids):
            continue
        joined = _joined(ledger, fold_ids, extractor)
        if joined is None:
            raise ExperimentError(
                "The folds' windows do not tile the sample, so a candidate's fold series "
                "join into no one history, and the experiment has no family matrix."
            )
        captures.append(joined)
        labels.append(label)
    return captures, labels


def trial_matrix_of(
    spec: ExperimentSpec, ledger: TrialLedger, record: ExperimentRecord, *, path: int | None
) -> TrialMatrix:
    """``record``'s family: one column per candidate that completed everywhere it was scored.

    Args:
        spec: The experiment.
        ledger: The ledger its trials are recorded in.
        record: What :func:`~alphalab.lifecycle.experiments.run_experiment` returned.
        path: For a CPCV experiment, which path's histories form the family;
            ``None`` for any other design.

    Raises:
        ExperimentError: If ``record`` is another experiment's, ``path`` is
            missing for a CPCV experiment or given for another, the folds do
            not tile the sample, or fewer than two candidates completed
            everywhere.
    """

    captures, labels = _family(spec, ledger, record, path)
    if len(captures) < 2:
        raise ExperimentError(
            f"{len(captures)} candidate(s) of {spec.name} completed everywhere they were scored: "
            "a family to select among is at least two."
        )
    return trial_matrix_from_captures(captures, labels=labels)


def _fraction(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


@dataclass(frozen=True, slots=True)
class ExperimentAssessment:
    """ADR-0054's statistics over a ledgered experiment's family, with every choice they took.

    Attributes:
        experiment_record_id: The record assessed.
        family: The trials the ledger counted, failures included: the size of
            the search behind the selection.
        path: The CPCV path the family is read on, or ``None``.
        matrix_id: The family matrix's identity.
        selected: The family's best candidate by the measure, the first of equals.
        overfitting: PBO by CSCV.
        deflated: The selected candidate's deflated Sharpe ratio, ``N`` the
            members with a Sharpe ratio or the stated ``effective_trials``;
            ``None`` if the selected candidate has none, or fewer than two
            members have one.
        effective_trials: The smaller ``N`` the caller stated, with its reason,
            or ``None``.
        flat: The members whose returns have no dispersion, so no Sharpe ratio:
            the deflation leaves them out.
        reality_check: White's Reality Check against the benchmark, over the
            whole family.
        superior: Hansen's SPA against the benchmark, over the members whose
            relative performance varies; ``None`` if fewer than two do.
        constant: The members whose performance relative to the benchmark is
            the same in every period: SPA, which studentizes, leaves them out.
        benchmark_id: The benchmark's identity: its per-period returns' digest.
    """

    experiment_record_id: str
    family: int
    path: int | None
    matrix_id: str
    selected: str
    overfitting: PBOResult
    deflated: DeflatedSharpe | None
    effective_trials: EffectiveTrials | None
    flat: tuple[str, ...]
    reality_check: RealityCheckResult
    superior: SPAResult | None
    constant: tuple[str, ...]
    benchmark_id: str

    @property
    def assessment_id(self) -> str:
        """SHA-256 of the record, every choice and the exact results (:data:`ASSESSMENT_SCHEME`)."""

        superior = self.superior
        effective = self.effective_trials
        return _digest_text(
            {
                "benchmark_id": self.benchmark_id,
                "blocks": self.overfitting.blocks,
                "constant": list(self.constant),
                "effective_trials": None
                if effective is None
                else {"count": effective.count, "reason": effective.reason},
                "experiment_record_id": self.experiment_record_id,
                "family": self.family,
                "flat": list(self.flat),
                "matrix_id": self.matrix_id,
                "mean_block_length": self.reality_check.mean_block_length,
                "measure": self.overfitting.measure.name,
                "path": self.path,
                "pbo": _fraction(self.overfitting.probability),
                "reality_check": _fraction(self.reality_check.p_value),
                "replications": self.reality_check.replications,
                "scheme": ASSESSMENT_SCHEME,
                "seed": self.reality_check.seed,
                "selected": self.selected,
                "spa": None
                if superior is None
                else [
                    _fraction(superior.p_lower),
                    _fraction(superior.p_consistent),
                    _fraction(superior.p_upper),
                ],
                "trim": self.overfitting.trim.name,
            }
        )


def _deflated(
    matrix: TrialMatrix, best: int, effective: EffectiveTrials | None
) -> tuple[DeflatedSharpe | None, tuple[str, ...]]:
    """The best column's deflated Sharpe ratio over the members that have a Sharpe ratio.

    A member whose returns have no dispersion -- research's sample variance of
    them is zero -- has none (``per_period_sharpe``), so it is left out of the
    trial Sharpe ratios and of ``N``, and named. ``N`` is the members with a
    Sharpe ratio, or the smaller one ``effective`` states.
    """

    flat = {k for k, column in enumerate(matrix.columns) if sample_variance(column) == 0.0}
    sharpes = [
        per_period_sharpe(column) for k, column in enumerate(matrix.columns) if k not in flat
    ]
    names = tuple(matrix.labels[k] for k in sorted(flat))
    if best in flat or len(sharpes) < 2:
        return None, names
    trials = len(sharpes) if effective is None else effective.count
    if trials > len(sharpes):
        raise ExperimentError(
            f"An effective N of {trials} exceeds the {len(sharpes)} members with a Sharpe "
            "ratio: the effective number of trials is at most the number run."
        )
    return (
        deflated_sharpe_ratio(matrix.columns[best], sharpes, independent_trials=trials),
        names,
    )


def _superior(
    relative: TrialMatrix, *, replications: int, mean_block_length: float, seed: int
) -> tuple[SPAResult | None, tuple[str, ...]]:
    """SPA over the members whose relative performance varies; the others, named.

    A member whose relative performance is the same in every period has the
    same mean in every bootstrap sample, so no deviation to studentize by,
    which research refuses. Its constant is its verdict, and SPA tests the rest
    -- if at least two remain, research's least matrix.
    """

    varying = [
        k
        for k, column in enumerate(relative.columns)
        if any(value != column[0] for value in column)
    ]
    constant = tuple(label for k, label in enumerate(relative.labels) if k not in varying)
    if len(varying) < 2:
        return None, constant
    tested = TrialMatrix(
        labels=tuple(relative.labels[k] for k in varying),
        instants=relative.instants,
        columns=tuple(relative.columns[k] for k in varying),
        measure=relative.measure,
    )
    return (
        superior_predictive_ability(
            tested, replications=replications, mean_block_length=mean_block_length, seed=seed
        ),
        constant,
    )


def assess_experiment(
    spec: ExperimentSpec,
    ledger: TrialLedger,
    record: ExperimentRecord,
    *,
    path: int | None,
    blocks: int,
    measure: SelectionMeasure,
    trim: Trim,
    benchmark: Sequence[float],
    replications: int,
    mean_block_length: float,
    seed: int,
    effective_trials: EffectiveTrials | None,
) -> ExperimentAssessment:
    """PBO, the deflated Sharpe ratio, the Reality Check and SPA over ``record``'s family.

    Every argument a statistic takes is required (ADR-0054, AC-12); see
    :func:`~alphalab.research.selection.probability_of_backtest_overfitting`,
    :func:`~alphalab.research.sharpe_inference.deflated_sharpe_ratio` and
    :func:`~alphalab.research.data_snooping.reality_check`. ``benchmark`` is
    the per-period return each candidate is measured against -- zero for
    cash, or a candidate's own -- one for each of the family's periods. The
    Reality Check and SPA share the bootstrap's ``replications``,
    ``mean_block_length`` and ``seed``. ``trim`` is PBO's alone: CSCV's blocks
    divide the periods, and the other statistics take every period.
    ``effective_trials`` states a smaller ``N`` for the deflation with its
    reason; ``None`` takes the members with a Sharpe ratio.

    Raises:
        ExperimentError: As :func:`trial_matrix_of` does, if the benchmark is
            not one finite return a period, or if ``effective_trials`` exceeds
            the members with a Sharpe ratio.
        ResearchValidationError: If a statistic refuses its arguments.
    """

    matrix = trial_matrix_of(spec, ledger, record, path=path)
    held = tuple(float(value) for value in benchmark)
    periods = len(matrix.instants)
    if len(held) != periods:
        raise ExperimentError(
            f"The benchmark has {len(held)} returns and the family {periods} periods: it is "
            "one return a period."
        )
    for index, value in enumerate(held):
        if not math.isfinite(value):
            raise ExperimentError(f"The benchmark's return {index} is {value!r}: it is finite.")
    overfitting = probability_of_backtest_overfitting(
        matrix, blocks=blocks, measure=measure, trim=trim
    )
    scores = [_score(column, measure) for column in matrix.columns]
    best = scores.index(max(scores))
    deflated, flat = _deflated(matrix, best, effective_trials)
    relative = TrialMatrix(
        labels=matrix.labels,
        instants=matrix.instants,
        columns=tuple(
            tuple(value - base for value, base in zip(column, held, strict=True))
            for column in matrix.columns
        ),
        measure=f"{matrix.measure}, less the benchmark's",
    )
    superior, constant = _superior(
        relative, replications=replications, mean_block_length=mean_block_length, seed=seed
    )
    return ExperimentAssessment(
        experiment_record_id=record.experiment_record_id,
        family=record.counts.recorded,
        path=path,
        matrix_id=_matrix_identity(matrix),
        selected=matrix.labels[best],
        overfitting=overfitting,
        deflated=deflated,
        effective_trials=effective_trials,
        flat=flat,
        reality_check=reality_check(
            relative, replications=replications, mean_block_length=mean_block_length, seed=seed
        ),
        superior=superior,
        constant=constant,
        benchmark_id=_digest_text({"benchmark": list(held), "scheme": "alphalab.benchmark.v1"}),
    )
