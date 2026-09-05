"""What a promotion is allowed to rest on.

A stage move at v2.3 needed nothing: any registered version could be sent
straight to production. This module supplies the thing a promotion can be made
to require -- a record of what was measured, over what data, with what seed, and
whether it met thresholds someone stated in advance.

Evidence is not computed here. AlphaLab already has two deterministic producers
of it, and both are reused rather than reimplemented:

* :class:`~alphalab.analytics.report.PerformanceReport`, compiled by a run
  through the execution path and reachable as
  :attr:`~alphalab.backtesting.state.BacktestResult.report`.
* :class:`~alphalab.research.research.ResearchScore`, produced by
  :meth:`~alphalab.research.engine.ResearchEngine.run_full_research`.

:func:`evidence_from_backtest` and :func:`evidence_from_research` extract a flat
metric mapping from those reports and record where it came from. The full report
stays where it was produced; evidence references it by id.

What a passing outcome does and does not claim
----------------------------------------------
:func:`evaluate_policy` states one thing: every threshold in the policy was met
by the numbers in the evidence. It is not a significance test, it does not
correct for how many candidates were searched before this one, and it says
nothing about out-of-sample behaviour beyond whatever the evidence's own dataset
was. A metric the policy asks for and the evidence does not carry is a failure,
not a skipped check -- an absent number is not a passing one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto

from alphalab.backtesting.state import BacktestResult
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.research.state import ResearchState

__all__ = [
    "MetricThreshold",
    "ValidationEvidence",
    "ValidationMethod",
    "ValidationOutcome",
    "ValidationPolicy",
    "build_evidence",
    "evaluate_policy",
    "evidence_from_backtest",
    "evidence_from_research",
    "evidence_id_for",
    "verify_evidence_id",
]


class ValidationMethod(Enum):
    """How a piece of evidence was produced."""

    #: A run through the execution path; metrics from its ``PerformanceReport``.
    BACKTEST = auto()
    #: ``ResearchEngine.run_full_research``; metrics from its ``ResearchScore``.
    RESEARCH = auto()
    #: Measured outside AlphaLab. The metrics are taken at face value, and the
    #: evidence says so rather than implying this repository computed them.
    EXTERNAL = auto()


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
    result: BacktestResult, subject: str, dataset_id: str, produced_at: float
) -> ValidationEvidence:
    """Extracts evidence from a finished run through the execution path.

    The metrics are read off the run's compiled
    :class:`~alphalab.analytics.report.PerformanceReport` -- returns, the
    risk-adjusted ratios, drawdown, and the trade summary. Nothing is
    recomputed here; a number that AlphaLab's analytics engine does not produce
    does not appear.

    Raises:
        LifecycleInputError: If the run compiled no report, which is what
            ``BacktestConfig(compile_analytics=False)`` produces. Evidence
            without measurements is not evidence, so this is refused rather
            than recorded as an empty pass.
    """
    report = result.report
    if report is None:
        raise LifecycleInputError(
            "The backtest compiled no performance report, so there is nothing to "
            "record as evidence; run it with BacktestConfig(compile_analytics=True)."
        )

    metrics = {
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

    The metrics are the eight scores
    :func:`~alphalab.research.research.compute_overall_score` produces. The nine
    underlying reports stay on the ``ResearchState``; ``source_id`` points back
    at it.

    Raises:
        LifecycleInputError: If the research has not completed and so has no
            score.
    """
    score = state.score
    if score is None:
        raise LifecycleInputError(
            f"Research '{state.research_id}' has produced no score yet; run "
            "ResearchEngine.run_full_research before recording it as evidence."
        )

    metrics = {
        "bias_score": score.bias_score,
        "confidence_score": score.confidence_score,
        "robustness_score": score.robustness_score,
        "capacity_score": score.capacity_score,
        "stability_score": score.stability_score,
        "generalisation_score": score.generalisation_score,
        "stress_score": score.stress_score,
        "overall_score": score.overall_score,
    }
    return build_evidence(
        method=ValidationMethod.RESEARCH,
        subject=subject,
        dataset_id=dataset_id,
        metrics=metrics,
        produced_at=produced_at,
        seed=None,
        source_id=state.research_id,
    )


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
    evidence.
    """
    failures: list[str] = []

    if not verify_evidence_id(evidence):
        failures.append(
            f"evidence '{evidence.evidence_id}' does not match its own content; "
            "it was altered after it was recorded."
        )

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
