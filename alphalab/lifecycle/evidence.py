"""What a promotion is allowed to rest on.

A stage move at v2.3 needed nothing: any registered version could be sent
straight to production. This module supplies the thing a promotion can be made
to require -- a record of what was measured, over what data, with what seed, and
whether it met thresholds someone stated in advance.

Evidence is not computed here. AlphaLab already has four deterministic
producers of it, and all four are reused rather than reimplemented:

* :class:`~alphalab.analytics.report.PerformanceReport`, compiled by a run
  through the execution path and reachable as
  :attr:`~alphalab.backtesting.state.BacktestResult.report`.
* :attr:`~alphalab.research.state.ResearchState.metrics`, measured by
  :meth:`~alphalab.research.engine.ResearchEngine.run_full_research`. Until
  v3.12 this was the v1 ``ResearchScore`` -- a blended grade from constants
  nobody chose, which a promotion policy could gate on (ledger RES-001).
* :class:`~alphalab.research.study.StudyResult`, produced by a v3.2 research
  study running from a canonical dataset through features and diagnostics.
* :class:`~alphalab.lifecycle.assessment.ExperimentAssessment`, v4.3's
  statistics over a ledgered experiment: the search's size, and what it did to
  the selection (ADR-0073).

:func:`evidence_from_backtest`, :func:`evidence_from_research`,
:func:`evidence_from_study` and :func:`evidence_from_experiment` extract a flat
metric mapping from those reports and record where it came from. The full
report stays where it was produced; evidence references it by id.

``evidence_id_for`` did not move
--------------------------------

v3.2 adds :attr:`ValidationMethod.STUDY` and nothing else to this module's
digest. The rendering hashes ``method.name``, so a new member changes no
existing identity: every promotion recorded since v2.6 still verifies, which is
the property ADR-0017 established and v3.1 preserved through the dataset
version change.

What a passing outcome does and does not claim
----------------------------------------------
:func:`evaluate_policy` states one thing: every threshold in the policy was met
by the numbers in the evidence. It is not a significance test, it does not
correct for how many candidates were searched before this one, and it says
nothing about out-of-sample behaviour beyond whatever the evidence's own dataset
was. A metric the policy asks for and the evidence does not carry is a failure,
not a skipped check -- an absent number is not a passing one.

Selection-aware evidence (v4.3)
-------------------------------
:attr:`ValidationMethod.EXPERIMENT` evidence carries statistics that do correct
for the search -- PBO, the Reality Check and SPA -- and, always, the search's
size: :data:`EXPERIMENT_FAMILY_METRIC`, every trial the experiment's ledger
recorded, failures included. :func:`evaluate_policy` fails ``EXPERIMENT``
evidence that does not state that count as a whole number of trials, whatever
the policy's thresholds: a selection that hides the search behind it is not
evidence (ADR-0073 section 8). The member is appended, and
``evidence_id_for`` hashes ``method.name``, so no existing identity moved.

An evidence identity covers its metrics, so they are class 1 or 2 (ADR-0058),
as a backtest's are: its CAGR is a correctly rounded power. The deflated Sharpe
ratio runs through the normal distribution's host-libm functions, class 3, so
it is read from the assessment ``source_id`` names, beside the evidence, and is
not among its metrics until it has a class-2 path (ledger FUT-051).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final

from alphalab.backtesting.state import BacktestResult
from alphalab.lifecycle.assessment import ExperimentAssessment
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.experiments import ExperimentRecord, ExperimentSpec
from alphalab.research.state import ResearchState
from alphalab.research.study import StudyResult

__all__ = [
    "EXPERIMENT_FAMILY_METRIC",
    "MetricThreshold",
    "ValidationEvidence",
    "ValidationMethod",
    "ValidationOutcome",
    "ValidationPolicy",
    "build_evidence",
    "evaluate_policy",
    "evidence_from_backtest",
    "evidence_from_experiment",
    "evidence_from_research",
    "evidence_from_study",
    "evidence_id_for",
    "verify_evidence_id",
]


class ValidationMethod(Enum):
    """How a piece of evidence was produced."""

    #: A run through the execution path; metrics from its ``PerformanceReport``.
    BACKTEST = auto()
    #: ``ResearchEngine.run_full_research``; metrics are its measurements.
    RESEARCH = auto()
    #: Measured outside AlphaLab. The metrics are taken at face value, and the
    #: evidence says so rather than implying this repository computed them.
    EXTERNAL = auto()
    #: A v3.2 research study: a ``ResearchStudy`` run from a canonical dataset
    #: through features, diagnostics and validation splits. Distinct from
    #: ``RESEARCH``, which scores a completed run's returns and trades and
    #: never sees a dataset.
    STUDY = auto()
    #: A v4.3 experiment's statistics over its ledger: the search's size and
    #: what it did to the selection (ADR-0073). Appended, so no member's name
    #: or evidence identity moved.
    EXPERIMENT = auto()


#: The metric ``EXPERIMENT`` evidence states its family count under: every trial
#: its experiment's ledger recorded, failures included (ADR-0073 section 8).
EXPERIMENT_FAMILY_METRIC: Final = "experiment.trials"


def evidence_id_for(
    method: ValidationMethod,
    subject: str,
    dataset_id: str,
    seed: int | None,
    metrics: Mapping[str, float],
) -> str:
    """Returns the deterministic id of evidence with this content.

    A SHA-256 digest over a canonical rendering, the same construction
    :func:`~alphalab.deployment_manager.packaging.compute_checksum` uses for a
    release manifest, and for the same reason: the same measurement always
    identifies itself the same way, and altering the recorded numbers changes
    the id. Metric names are sorted, so the digest does not depend on mapping
    order, and each value is rendered with ``repr`` so a float round-trips
    exactly.
    """

    canonical = "\n".join(
        [
            f"method={method.name}",
            f"subject={subject}",
            f"dataset={dataset_id}",
            f"seed={'none' if seed is None else seed}",
            "metrics",
            *(f"{key}={metrics[key]!r}" for key in sorted(metrics)),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    """One deterministic, serializable measurement supporting a promotion.

    Attributes:
        evidence_id: Digest over this evidence's own content, from
            :func:`evidence_id_for`.
        method: How the measurement was produced.
        subject: What was measured, as a rendered reference -- a
            :class:`~alphalab.lifecycle.identity.StrategyVersionRef` or
            :class:`~alphalab.lifecycle.identity.ModelRef`.
        dataset_id: The data it was measured over. Named by the caller: neither
            a ``BacktestResult`` nor a ``ResearchState`` carries the dataset it
            consumed, and inventing one would be a guess.
        metrics: The numbers, flat and named.
        seed: The identifier seed the producing run used, if it was seeded.
            ``None`` means the run's quantities reproduce but its identifiers do
            not.
        produced_at: Unix timestamp the measurement was taken.
        source_id: The full report this was extracted from -- a
            ``PerformanceReport.report_id`` or a ``ResearchState.research_id``.
            Empty for ``EXTERNAL`` evidence.
    """

    evidence_id: str
    method: ValidationMethod
    subject: str
    dataset_id: str
    metrics: Mapping[str, float] = field(default_factory=dict)
    seed: int | None = None
    produced_at: float = 0.0
    source_id: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise LifecycleInputError("evidence_id cannot be empty.")
        if not self.subject.strip():
            raise LifecycleInputError("evidence subject cannot be empty.")


def build_evidence(
    method: ValidationMethod,
    subject: str,
    dataset_id: str,
    metrics: Mapping[str, float],
    produced_at: float,
    seed: int | None = None,
    source_id: str = "",
) -> ValidationEvidence:
    """Builds a :class:`ValidationEvidence` with its id computed."""

    frozen = dict(metrics)
    return ValidationEvidence(
        evidence_id=evidence_id_for(method, subject, dataset_id, seed, frozen),
        method=method,
        subject=subject,
        dataset_id=dataset_id,
        metrics=frozen,
        seed=seed,
        produced_at=produced_at,
        source_id=source_id,
    )


def verify_evidence_id(evidence: ValidationEvidence) -> bool:
    """Returns whether the stored id still matches the evidence's content."""

    return evidence.evidence_id == evidence_id_for(
        evidence.method, evidence.subject, evidence.dataset_id, evidence.seed, evidence.metrics
    )


def evidence_from_backtest(
    result: BacktestResult, subject: str, produced_at: float
) -> ValidationEvidence:
    """Extracts evidence from a finished run through the execution path.

    The metrics are read off the run's compiled
    :class:`~alphalab.analytics.report.PerformanceReport` -- returns, the
    risk-adjusted ratios, drawdown, and the trade summary. Nothing is
    recomputed here; a number that AlphaLab's analytics engine does not produce
    does not appear.

    The dataset is **derived** from the run, not supplied. Until v2.7 this
    function took a ``dataset_id`` argument and a caller could name any dataset
    at all, including one the run never touched -- and ``evidence_id_for``
    hashes that value, so the digest was only as trustworthy as a string
    somebody typed. Two runs over genuinely different data could be given one
    identity, verify cleanly, and pass the gate. The parameter is removed rather
    than accepted and ignored: an argument that is silently discarded leaves a
    caller believing they set something. See ADR-0017.

    Raises:
        LifecycleInputError: If the run compiled no report, which is what
            ``RunConfig(compile_analytics=False)`` produces, or if the run
            names no dataset. Evidence without measurements is not evidence,
            and neither is a measurement over data nobody can identify; both
            are refused rather than recorded as an empty pass.
    """
    report = result.report
    if report is None:
        raise LifecycleInputError(
            "The backtest compiled no performance report, so there is nothing to "
            "record as evidence; run it with RunConfig(compile_analytics=True)."
        )

    dataset_id = result.dataset_id
    if dataset_id is None:
        raise LifecycleInputError(
            "The backtest names no dataset, so there is nothing to record as the "
            "data it was measured over. A run driven through BacktestEngine.run "
            "carries its dataset identity; one driven by hand through "
            "initialize/advance/finalize carries whatever identity finalize was "
            "given, and None means it was given none. Inventing one here would be "
            "a guess recorded as a fact."
        )

    measured: dict[str, float | None] = {
        "total_return": report.returns.total_return,
        "cagr": report.returns.cagr,
        "arithmetic_return": report.returns.arithmetic_return,
        "geometric_return": report.returns.geometric_return,
        "sharpe_ratio": report.risk.sharpe_ratio,
        "sortino_ratio": report.risk.sortino_ratio,
        "calmar_ratio": report.risk.calmar_ratio,
        "value_at_risk_95": report.risk.value_at_risk_95,
        "cvar_95": report.risk.cvar_95,
        "annualized_volatility": report.risk.annualized_volatility,
        "max_drawdown": report.drawdowns.max_drawdown,
        "ulcer_index": report.drawdowns.ulcer_index,
        "win_rate": report.trades.win_rate,
        "profit_factor": report.trades.profit_factor,
        "turnover": report.trades.turnover,
    }
    # An undefined statistic is not evidence of anything: it is left out rather
    # than recorded as a number, so a policy threshold on it fails for want of
    # the metric instead of passing on a placeholder. Until v3.10 the report
    # wrote 0.0 (or an infinite profit factor) and both reached the evidence.
    metrics = {name: value for name, value in measured.items() if value is not None}
    return build_evidence(
        method=ValidationMethod.BACKTEST,
        subject=subject,
        dataset_id=dataset_id,
        metrics=metrics,
        produced_at=produced_at,
        seed=result.seed,
        source_id=report.report_id,
    )


def evidence_from_research(
    state: ResearchState, subject: str, dataset_id: str, produced_at: float
) -> ValidationEvidence:
    """Extracts evidence from a completed research evaluation.

    The metrics are every measurement the evaluation made
    (:func:`~alphalab.research.research.research_metrics`): returns,
    volatility, drawdowns, the bootstrap and walk-forward Sharpe ratios, the
    parameter search's surface -- each only when it was measured. A promotion
    policy states its own thresholds over them. The underlying reports stay on
    the ``ResearchState``; ``source_id`` points back at it.

    Until v3.12 the metrics were the v1 engine's eight 0-100 scores, an
    ``overall_score`` among them (ledger RES-001).

    Raises:
        LifecycleInputError: If the research has not completed.
    """
    if not state.completed:
        raise LifecycleInputError(
            f"Research '{state.research_id}' has not completed; run "
            "ResearchEngine.run_full_research before recording it as evidence."
        )

    return build_evidence(
        method=ValidationMethod.RESEARCH,
        subject=subject,
        dataset_id=dataset_id,
        metrics=dict(state.metrics),
        produced_at=produced_at,
        seed=None,
        source_id=state.research_id,
    )


def evidence_from_study(
    result: StudyResult, subject: str, produced_at: float
) -> ValidationEvidence:
    """Record a v3.2 research study's result as evidence.

    The dataset is **derived** from the study rather than supplied, for the
    reason ADR-0017 gives for :func:`evidence_from_backtest`: a ``dataset_id``
    a caller typed is hashed into the digest and is only as trustworthy as the
    typing. A study names its dataset version, the inputs were checked against
    it when the result was built, and that version is what reaches the digest.

    The seed is the study's own, so evidence from a study with a stochastic
    step says which seed produced it, and evidence from a study with none says
    ``None`` rather than implying a reproducibility it does not have.

    Raises:
        LifecycleInputError: If the study names no dataset version, or if the
            result's stored identity no longer matches its metrics. Evidence
            over data nobody can identify is not evidence, and neither are
            numbers that were edited after they were recorded.
    """

    if result.dataset_version is None:
        raise LifecycleInputError(
            f"Study {result.study.study_name!r} names no dataset version, so there is "
            "nothing to record as the data it was measured over. Build the study from a "
            "dataset whose provenance was recorded; inventing an identity here would be a "
            "guess recorded as a fact."
        )
    if not result.verify():
        raise LifecycleInputError(
            f"Study result {result.result_id} does not match its own metrics; it was "
            "altered after it was recorded."
        )

    return build_evidence(
        method=ValidationMethod.STUDY,
        subject=subject,
        dataset_id=result.dataset_version,
        metrics=result.metrics,
        produced_at=produced_at,
        seed=result.study.seed,
        source_id=result.result_id,
    )


def evidence_from_experiment(
    spec: ExperimentSpec,
    record: ExperimentRecord,
    assessment: ExperimentAssessment,
    subject: str,
    produced_at: float,
) -> ValidationEvidence:
    """Record an assessed experiment as selection-aware evidence (ADR-0073 section 8).

    ``subject`` is the strategy version that runs the selection. The metrics
    always carry :data:`EXPERIMENT_FAMILY_METRIC`, the search's size, beside
    the statistics, under the ``experiment.`` prefix:

    * ``trials``: the family count;
    * ``pbo``: the probability of backtest overfitting;
    * ``reality_check_p``: the Reality Check's p-value;
    * ``spa_p_lower``, ``spa_p_consistent``, ``spa_p_upper``: SPA's;
    * ``sharpe`` and ``deflated_sharpe_trials``: the selection's per-period
      Sharpe ratio, and the ``N`` its deflation took.

    Every metric is class 1 or 2 (ADR-0058), so the evidence's identity is the
    same on every host. The deflated Sharpe ratio itself is class 3, and is
    read from the assessment (ledger FUT-051). A statistic the assessment could
    not compute -- SPA or the deflation, when too few members have a dispersion
    -- is left out rather than recorded as a number, as
    :func:`evidence_from_backtest` leaves one out: a threshold on it fails for
    want of the metric. The dataset and the seed are derived from the
    experiment: its dataset's ``dataset_id``, spelled as a backtest over it
    spells it, and its own seed. ``source_id`` is the assessment's identity,
    which covers every choice the statistics took.

    Raises:
        LifecycleInputError: If the record is not of ``spec``, or the
            assessment is not of the record.
    """

    if record.experiment_id != spec.experiment_id:
        raise LifecycleInputError(
            f"The record is of experiment {record.experiment_id}, not {spec.experiment_id}."
        )
    if assessment.experiment_record_id != record.experiment_record_id:
        raise LifecycleInputError(
            f"The assessment is of record {assessment.experiment_record_id}, not "
            f"{record.experiment_record_id}: evidence from it would be about another search."
        )
    metrics: dict[str, float] = {
        EXPERIMENT_FAMILY_METRIC: float(assessment.family),
        "experiment.pbo": float(assessment.overfitting.probability),
        "experiment.reality_check_p": float(assessment.reality_check.p_value),
    }
    superior = assessment.superior
    if superior is not None:
        metrics["experiment.spa_p_lower"] = float(superior.p_lower)
        metrics["experiment.spa_p_consistent"] = float(superior.p_consistent)
        metrics["experiment.spa_p_upper"] = float(superior.p_upper)
    deflated = assessment.deflated
    if deflated is not None:
        metrics["experiment.sharpe"] = deflated.inference.sharpe
        metrics["experiment.deflated_sharpe_trials"] = float(deflated.independent_trials)
    return build_evidence(
        method=ValidationMethod.EXPERIMENT,
        subject=subject,
        dataset_id=spec.dataset.dataset_id,
        metrics=metrics,
        produced_at=produced_at,
        seed=spec.seed,
        source_id=assessment.assessment_id,
    )


def _family_count_failure(evidence: ValidationEvidence) -> str | None:
    """Why ``EXPERIMENT`` evidence does not state its family count, or ``None``."""

    count = evidence.metrics.get(EXPERIMENT_FAMILY_METRIC)
    if count is None:
        return (
            f"EXPERIMENT evidence '{evidence.evidence_id}' states no family count "
            f"('{EXPERIMENT_FAMILY_METRIC}'): a selection that hides the search behind it "
            "is not evidence."
        )
    if (
        isinstance(count, bool)
        or not isinstance(count, int | float)
        or not math.isfinite(count)
        or count < 1
        or count != int(count)
    ):
        return (
            f"EXPERIMENT evidence '{evidence.evidence_id}' states its family count "
            f"'{EXPERIMENT_FAMILY_METRIC}' as {count!r}: it is a whole number of trials, at "
            "least one."
        )
    return None


@dataclass(frozen=True, slots=True)
class MetricThreshold:
    """One stated bound on one metric.

    At least one of ``minimum`` / ``maximum`` must be set: a threshold that
    bounds nothing would pass silently and read as a check that happened.
    """

    metric: str
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        if not self.metric.strip():
            raise LifecycleInputError("MetricThreshold.metric cannot be empty.")
        if self.minimum is None and self.maximum is None:
            raise LifecycleInputError(
                f"MetricThreshold for '{self.metric}' sets neither a minimum nor a "
                "maximum, so it checks nothing."
            )


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """The thresholds a piece of evidence must meet to support a promotion.

    Attributes:
        policy_id: Identifies which policy an outcome was produced under, so a
            recorded pass says what it passed.
        thresholds: Every bound to check. Evaluated in the order given.
        required_method: Restricts which kind of evidence satisfies this policy
            -- a policy that means to be backed by a real run says
            ``ValidationMethod.BACKTEST`` rather than accepting numbers typed in
            by hand. ``None`` accepts any method.
    """

    policy_id: str
    thresholds: tuple[MetricThreshold, ...] = ()
    required_method: ValidationMethod | None = None

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise LifecycleInputError("ValidationPolicy.policy_id cannot be empty.")
        if not self.thresholds:
            raise LifecycleInputError(
                f"ValidationPolicy '{self.policy_id}' states no thresholds; a policy "
                "that checks nothing would make every promotion look validated."
            )


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """The result of checking one piece of evidence against one policy.

    Attributes:
        policy_id: The policy that was applied.
        evidence_id: The evidence it was applied to.
        passed: Whether every check held.
        failures: One sentence per failed check, in policy order. Empty exactly
            when ``passed`` is true, so a failure is never a bare ``False``
            whose reason has to be guessed.
    """

    policy_id: str
    evidence_id: str
    passed: bool
    failures: tuple[str, ...] = ()


def evaluate_policy(policy: ValidationPolicy, evidence: ValidationEvidence) -> ValidationOutcome:
    """Checks ``evidence`` against ``policy``, deterministically.

    Every check runs; the outcome names all of the failures rather than the
    first. A metric the policy names and the evidence does not carry fails, and
    evidence whose stored id no longer matches its content fails before any
    threshold is looked at -- numbers that were edited after the fact are not
    evidence. ``EXPERIMENT`` evidence that does not state its family count
    (:data:`EXPERIMENT_FAMILY_METRIC`) as a whole number of trials fails under
    every policy (ADR-0073 section 8).
    """
    failures: list[str] = []

    if not verify_evidence_id(evidence):
        failures.append(
            f"evidence '{evidence.evidence_id}' does not match its own content; "
            "it was altered after it was recorded."
        )

    if evidence.method is ValidationMethod.EXPERIMENT:
        failure = _family_count_failure(evidence)
        if failure is not None:
            failures.append(failure)

    if policy.required_method is not None and evidence.method is not policy.required_method:
        failures.append(
            f"policy requires {policy.required_method.name} evidence, got {evidence.method.name}."
        )

    for threshold in policy.thresholds:
        if threshold.metric not in evidence.metrics:
            failures.append(f"'{threshold.metric}' is not among the recorded metrics.")
            continue
        value = evidence.metrics[threshold.metric]
        if threshold.minimum is not None and value < threshold.minimum:
            failures.append(
                f"'{threshold.metric}' is {value!r}, below the minimum {threshold.minimum!r}."
            )
        if threshold.maximum is not None and value > threshold.maximum:
            failures.append(
                f"'{threshold.metric}' is {value!r}, above the maximum {threshold.maximum!r}."
            )

    return ValidationOutcome(
        policy_id=policy.policy_id,
        evidence_id=evidence.evidence_id,
        passed=not failures,
        failures=tuple(failures),
    )
