"""Machine-verifiable properties of a strategy version, each with the evidence behind it.

An application that lists, compares or admits strategies needs to know things
about one that it can check rather than take on trust: does it produce the same
result from the same inputs, can its results be recreated, does it stay inside
its declared limits, what leverage does it actually run at, which markets and
which data does it need, what does it cost to run, and how did it behave once
deployed. This module states each of those as a separate, inspectable
:class:`PropertyAssessment` and assembles them into a
:class:`CertificationReport`. What the application does with the report --
admits the strategy, ranks it, shows it to somebody -- is the application's, and
nothing here knows who is asking.

There is no overall score
-------------------------

A report is eight assessments and no verdict. A blended figure would have to
decide how many failed properties one passing one outweighs, which is a policy,
and it would read as a measurement. The same position v3.2 takes on overfitting
(``nowandfuture.md`` invariant 21): a measurement, a threshold and an
interpretation are separate fields, and the interpretation is the reader's.

Four statuses, because "not established" is two things
------------------------------------------------------

``PASS`` and ``FAIL`` are for what the evidence establishes. ``NOT_ASSESSED``
means nothing was supplied to assess the property with. ``INSUFFICIENT_EVIDENCE``
means something was supplied and it cannot settle the question -- one execution
where determinism needs two, a run gated by different limits than the ones
declared, a health report with a category nobody observed. Neither of the last
two is ever ``PASS``: an absent measurement is not a passing one, the rule
:func:`~alphalab.lifecycle.evidence.evaluate_policy` has applied since v2.4.

Evidence is observed, never asserted
------------------------------------

:class:`CertificationEvidence` carries runs, datasets, manifests, runtime
observations and resource measurements -- things that happened -- and **no
verdict**. Every judgement is derived here from what was supplied, through the
authority that owns it: a run's record digest through
:func:`~alphalab.lifecycle.reproducibility.digest_run`, a reproducibility
assessment through
:func:`~alphalab.lifecycle.reproducibility.assess_reproducibility`, runtime
health through :func:`~alphalab.lifecycle.health.evaluate_health`, leverage and
drawdown through the pre-trade gate's own
:class:`~alphalab.risk.state.RiskState` readings. A caller cannot hand this
module a ``HealthReport`` that says ``HEALTHY`` or an assessment that says
``REPRODUCED``; it can only hand over what was observed and let the check run.

What each property rests on
---------------------------

=================== ==============================================================
``DETERMINISTIC``   repeated executions of identical recorded inputs, compared by
                    their complete canonical record
``REPRODUCIBLE``    a reproducibility manifest for this fingerprint, and a rerun
                    of it
``RISK_LIMITS``     runs gated by exactly the declared ``RiskLimits``: no refusal,
                    and drawdown and gross exposure within them at every snapshot
``MAX_LEVERAGE``    ``RiskState.current_leverage`` at every recorded snapshot,
                    against ``RiskLimits.leverage``
``SUPPORTED_MARKETS`` what the runs traded, against ``MarketRequirements`` and the
                    run's instrument registry
``REQUIRED_DATA``   supplied datasets whose provenance records the declared
                    versions over real bytes, and runs measured on nothing else
``RESOURCE_USAGE``  counts read off the runs and measurements supplied with their
                    method and environment, against stated budgets
``RUNTIME_BEHAVIOR`` supplied runtime observations judged against the
                    specification's runtime requirements
=================== ==============================================================

A report is deterministic: identical inputs produce an identical report and an
identical :attr:`CertificationReport.report_id` in any process. Nothing here
reads a clock, measures anything or starts a run.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from types import MappingProxyType
from typing import Final

from alphalab.backtesting.state import BacktestResult
from alphalab.data.dataset import Dataset
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.fingerprint import (
    StrategyFingerprint,
    differing_parameters,
    verify_fingerprint,
)
from alphalab.lifecycle.health import HealthStatus, RuntimeObservation, evaluate_health
from alphalab.lifecycle.progression import (
    PAUSABLE_STAGES,
    StrategyLifecycleStage,
    StrategyProgression,
)
from alphalab.lifecycle.reproducibility import (
    ReproducibilityManifest,
    RerunOutcome,
    assess_reproducibility,
    digest_run,
    verify_manifest,
)
from alphalab.lifecycle.specification import (
    DeploymentSpecification,
    validate_specification,
    verify_specification_id,
)
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.state import RiskState

__all__ = [
    "CERTIFICATION_REPORT_SCHEME",
    "CertificationEvidence",
    "CertificationProperty",
    "CertificationReport",
    "CertificationStatus",
    "MeasurementBasis",
    "PropertyAssessment",
    "ResourceBudget",
    "ResourceMeasurement",
    "ResourceMetric",
    "certify_strategy",
    "resource_counts",
    "verify_certification_report",
]

#: Scheme tag, and the first line of every canonical report key. Changing it is
#: an ADR, as for every other identity scheme in AlphaLab.
CERTIFICATION_REPORT_SCHEME: Final = "alphalab.certification_report.v1"


class CertificationStatus(Enum):
    """What the evidence established about one property."""

    #: The evidence establishes the property.
    PASS = auto()

    #: The evidence establishes that the property does not hold.
    FAIL = auto()

    #: Nothing was supplied to assess it with.
    NOT_ASSESSED = auto()

    #: Something was supplied, and it cannot settle the question either way.
    INSUFFICIENT_EVIDENCE = auto()


class CertificationProperty(Enum):
    """The eight properties a report covers, always all eight, in this order."""

    DETERMINISTIC = auto()
    REPRODUCIBLE = auto()
    RISK_LIMITS = auto()
    MAX_LEVERAGE = auto()
    SUPPORTED_MARKETS = auto()
    REQUIRED_DATA = auto()
    RESOURCE_USAGE = auto()
    RUNTIME_BEHAVIOR = auto()


@dataclass(frozen=True, slots=True)
class PropertyAssessment:
    """One property, what was concluded, and exactly what that rests on.

    Attributes:
        claim: Which property.
        status: What the evidence established.
        methodology: How it was assessed, in one stable sentence -- the same
            sentence for every assessment of this property, so two reports can
            be compared field by field.
        evidence: The machine-readable half: identities, digests, declared
            limits, observed values. Keys are stable per property. A reader never
            has to parse a sentence to learn a threshold or a measured value.
        findings: What the assessment found, in sentences -- why a status is
            ``FAIL`` or ``INSUFFICIENT_EVIDENCE``, and what was checked.
        limitations: What this assessment does **not** establish, even when it
            passes. Stated every time, so a ``PASS`` cannot be read as more than
            it is.
    """

    claim: CertificationProperty
    status: CertificationStatus
    methodology: str
    evidence: Mapping[str, str]
    findings: tuple[str, ...]
    limitations: tuple[str, ...]


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #


class ResourceMetric(Enum):
    """A quantity of resource, with its unit in its name."""

    #: Market records the run processed. A count.
    RECORDS_PROCESSED = auto()

    #: Orders the run submitted. A count.
    ORDERS_SUBMITTED = auto()

    #: Fills the run produced. A count.
    FILLS = auto()

    #: CPU time consumed, in seconds.
    CPU_SECONDS = auto()

    #: Elapsed time, in seconds.
    WALL_CLOCK_SECONDS = auto()

    #: Peak memory, in bytes.
    PEAK_MEMORY_BYTES = auto()


#: The metrics a run's record answers exactly.
_COUNTABLE: Final = frozenset(
    {
        ResourceMetric.RECORDS_PROCESSED,
        ResourceMetric.ORDERS_SUBMITTED,
        ResourceMetric.FILLS,
    }
)


class MeasurementBasis(Enum):
    """Where a resource figure came from, which decides what it can certify."""

    #: Read off a run's own record by :func:`resource_counts`. Exact, and the
    #: same in every environment.
    COUNTED = auto()

    #: Observed in an environment by whoever ran it -- a process timer, a
    #: memory tracer. True of that environment and not reproducible elsewhere.
    MEASURED = auto()

    #: Derived from a model rather than observed. Recorded, and **never**
    #: enough to certify a budget: a theoretical figure is not a measurement.
    ESTIMATED = auto()


@dataclass(frozen=True, slots=True)
class ResourceMeasurement:
    """One resource figure, and how it was obtained.

    Attributes:
        metric: What was measured, with its unit in the name.
        value: How much.
        basis: Where the figure came from.
        method: How it was obtained, stated so a reader can repeat it --
            ``"time.process_time around one BacktestEngine.run"``.
        environment: Where it was obtained, for a ``MEASURED`` figure --
            interpreter, machine class, load. ``""`` for a count, which does not
            depend on one.

    Raises:
        LifecycleInputError: If the value is negative, the method is blank, a
            ``MEASURED`` figure names no environment, or a ``COUNTED`` figure is
            of something a run's record does not count.
    """

    metric: ResourceMetric
    value: Decimal
    basis: MeasurementBasis
    method: str
    environment: str

    def __post_init__(self) -> None:
        if self.value < Decimal("0"):
            raise LifecycleInputError(
                f"A {self.metric.name} measurement of {self.value} is negative; no resource "
                "is consumed in negative amounts."
            )
        if not self.method.strip():
            raise LifecycleInputError(
                f"A {self.metric.name} measurement states no method. A figure nobody can say "
                "how to obtain again is not evidence."
            )
        if self.basis is MeasurementBasis.MEASURED and not self.environment.strip():
            raise LifecycleInputError(
                f"A measured {self.metric.name} names no environment. A timing or a memory "
                "figure is true of the machine it was taken on, and says so."
            )
        if self.basis is MeasurementBasis.COUNTED and self.metric not in _COUNTABLE:
            raise LifecycleInputError(
                f"{self.metric.name} is not something a run's record counts; it has to be "
                "MEASURED or ESTIMATED."
            )


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    """The most of one resource a strategy may consume in one run.

    Declared by whoever asks for certification. There is no default budget for
    any metric, for the reason there is no default freshness budget in a
    :class:`~alphalab.lifecycle.specification.RuntimeRequirements`: what is
    affordable depends on the deployment, and a number chosen here would be a
    policy nobody chose.

    Raises:
        LifecycleInputError: If the maximum is negative.
    """

    metric: ResourceMetric
    maximum: Decimal

    def __post_init__(self) -> None:
        if self.maximum < Decimal("0"):
            raise LifecycleInputError(
                f"A {self.metric.name} budget of {self.maximum} is negative, so every run "
                "would exceed it."
            )


def resource_counts(result: BacktestResult) -> tuple[ResourceMeasurement, ...]:
    """The resource counts a run's own record answers exactly.

    Records processed, orders submitted and fills produced -- deterministic,
    and the same in every environment the run is repeated in.
    """

    return (
        ResourceMeasurement(
            ResourceMetric.RECORDS_PROCESSED,
            Decimal(result.records_processed),
            MeasurementBasis.COUNTED,
            "RunState.processed",
            "",
        ),
        ResourceMeasurement(
            ResourceMetric.ORDERS_SUBMITTED,
            Decimal(len(result.orders)),
            MeasurementBasis.COUNTED,
            "orders held by the run's OMS book",
            "",
        ),
        ResourceMeasurement(
            ResourceMetric.FILLS,
            Decimal(len(result.fills)),
            MeasurementBasis.COUNTED,
            "fills recorded by the run",
            "",
        ),
    )


# --------------------------------------------------------------------------- #
# Evidence and report
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CertificationEvidence:
    """Everything supplied to certify a strategy with. Observations only.

    Every field is something that happened or was measured; none is a verdict.
    An empty tuple or ``None`` means nothing of that kind was supplied, and the
    properties that needed it are ``NOT_ASSESSED``.

    Attributes:
        runs: Finished runs of this strategy whose behaviour is observed -- for
            its risk limits, leverage, markets, data and resource counts. Each
            must have executed this strategy and no other, because a shared
            book's leverage is not one strategy's.
        repeated_runs: Executions of one set of inputs, compared for
            determinism.
        datasets: The datasets the specification assumes, so their provenance
            can be verified and described.
        manifest: A reproducibility manifest of a result of this strategy.
        rerun: A manifest of the same result produced again, if it was.
        observations: Runtime observations of a deployment of this strategy.
        measurements: Resource figures ``MEASURED`` or ``ESTIMATED`` by the
            caller. Counts are read off :attr:`runs` here, never supplied.
    """

    runs: tuple[BacktestResult, ...] = ()
    repeated_runs: tuple[BacktestResult, ...] = ()
    datasets: tuple[Dataset, ...] = ()
    manifest: ReproducibilityManifest | None = None
    rerun: ReproducibilityManifest | None = None
    observations: tuple[RuntimeObservation, ...] = ()
    measurements: tuple[ResourceMeasurement, ...] = ()


@dataclass(frozen=True, slots=True)
class CertificationReport:
    """Eight assessments of one strategy version, and nothing blended from them.

    Attributes:
        report_id: SHA-256 over the report's canonical rendering. Identical
            inputs produce an identical id in any process.
        fingerprint: The strategy version certified.
        specification_id: The specification whose declared requirements the
            properties were judged against.
        stage: Where the version's progression stood, when one was supplied.
        specification_findings: What
            :func:`~alphalab.lifecycle.specification.validate_specification`
            reports about the specification's own coherence. Carried, not
            re-judged.
        assessments: One per :class:`CertificationProperty`, in its order.
    """

    report_id: str
    fingerprint: str
    specification_id: str
    stage: StrategyLifecycleStage | None
    specification_findings: tuple[str, ...]
    assessments: tuple[PropertyAssessment, ...]

    def assessment(self, claim: CertificationProperty) -> PropertyAssessment:
        """The assessment of one property."""

        for assessment in self.assessments:
            if assessment.claim is claim:
                return assessment
        raise LifecycleInputError(f"The report holds no assessment of {claim.name}.")

    def status_of(self, claim: CertificationProperty) -> CertificationStatus:
        """The status of one property."""

        return self.assessment(claim).status

    def _with(self, status: CertificationStatus) -> tuple[CertificationProperty, ...]:
        return tuple(item.claim for item in self.assessments if item.status is status)

    @property
    def passed(self) -> tuple[CertificationProperty, ...]:
        """Every property the evidence established."""

        return self._with(CertificationStatus.PASS)

    @property
    def failed(self) -> tuple[CertificationProperty, ...]:
        """Every property the evidence refuted."""

        return self._with(CertificationStatus.FAIL)

    @property
    def unassessed(self) -> tuple[CertificationProperty, ...]:
        """Every property nothing was supplied for."""

        return self._with(CertificationStatus.NOT_ASSESSED)

    @property
    def insufficient(self) -> tuple[CertificationProperty, ...]:
        """Every property the supplied evidence could not settle."""

        return self._with(CertificationStatus.INSUFFICIENT_EVIDENCE)


def _assessment(
    claim: CertificationProperty,
    status: CertificationStatus,
    methodology: str,
    evidence: Mapping[str, str],
    findings: Sequence[str] = (),
    limitations: Sequence[str] = (),
) -> PropertyAssessment:
    return PropertyAssessment(
        claim=claim,
        status=status,
        methodology=methodology,
        evidence=MappingProxyType(dict(evidence)),
        findings=tuple(findings),
        limitations=tuple(limitations),
    )


# --------------------------------------------------------------------------- #
# Runs of this strategy
# --------------------------------------------------------------------------- #


def _executed(result: BacktestResult) -> tuple[str, ...]:
    """The strategies a run executed, read from its own strategy runtime."""

    return tuple(sorted(result.state.strategy.strategies))


#: What matching a run to a fingerprint can and cannot establish. Stated on
#: every property read off runs, so a PASS is never read as more than it is.
_RUN_CONFIGURATION_UNOBSERVED: Final = (
    "Runs are matched to the fingerprint by the strategy id a run records. The parameters "
    "its strategy instance was built with live in that instance, which a run records by "
    "type only (ADR-0023), so that the runs were of this fingerprint's configuration is the "
    "supplier's record, not something observed here."
)


def _foreign_runs(
    fingerprint: StrategyFingerprint, executed: Sequence[tuple[str, ...]]
) -> list[str]:
    """Why any of these runs is not a run of this strategy alone."""

    return [
        f"run {index} executed {list(strategies)}, not {fingerprint.strategy_id!r} alone; a "
        "book shared with another strategy is not this strategy's evidence."
        for index, strategies in enumerate(executed)
        if strategies != (fingerprint.strategy_id,)
    ]


# --------------------------------------------------------------------------- #
# DETERMINISTIC
# --------------------------------------------------------------------------- #

_DETERMINISM_METHOD = (
    "Each supplied execution's complete canonical record -- runtime.run_snapshot.capture, "
    "serialized by persistence.serialize -- is digested with SHA-256; the executions must "
    "share their recorded inputs (dataset, seed, recorded configuration and strategies), and "
    "they are deterministic when every record digest is identical."
)

_DETERMINISM_LIMITS = (
    _RUN_CONFIGURATION_UNOBSERVED,
    "The executions were run by the caller: whether in one process or several is the "
    "caller's record, not something this assessment observed.",
    "Parameters held inside live objects -- a simulator's costs, a sizing model -- are "
    "recorded by type only (ADR-0023); keeping them identical is the caller's, and a "
    "difference there shows as a divergence.",
)


def _deterministic(
    fingerprint: StrategyFingerprint, repeated: Sequence[BacktestResult]
) -> PropertyAssessment:
    claim = CertificationProperty.DETERMINISTIC
    if not repeated:
        return _assessment(
            claim,
            CertificationStatus.NOT_ASSESSED,
            _DETERMINISM_METHOD,
            {"executions": "0"},
            ("no repeated executions were supplied.",),
            _DETERMINISM_LIMITS,
        )

    digests = [digest_run(result) for result in repeated]
    records = sorted({digest.result_id for digest in digests})
    inputs = sorted(
        {
            (repr(digest.seed), repr(digest.dataset_id), digest.configuration_id)
            for digest in digests
        }
    )
    evidence = {
        "executions": str(len(digests)),
        "distinct_records": str(len(records)),
        "records": ",".join(records),
        "seeds": ",".join(sorted({repr(digest.seed) for digest in digests})),
        "datasets": ",".join(sorted({repr(digest.dataset_id) for digest in digests})),
        "configurations": ",".join(sorted({digest.configuration_id for digest in digests})),
    }

    insufficient: list[str] = []
    if len(digests) < 2:
        insufficient.append("one execution cannot show that a second would agree with it.")
    insufficient.extend(_foreign_runs(fingerprint, [digest.strategy_ids for digest in digests]))
    if any(digest.seed is None for digest in digests):
        insufficient.append(
            "an execution is unseeded, so its identifiers came from uuid4 and its record "
            "cannot equal another's however deterministic the strategy is; seed the runs."
        )
    if len(inputs) > 1:
        insufficient.append(
            f"the executions were not of identical recorded inputs ({len(inputs)} distinct "
            "combinations of seed, dataset and configuration), so a difference between them "
            "would say nothing about determinism."
        )
    if insufficient:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _DETERMINISM_METHOD,
            evidence,
            insufficient,
            _DETERMINISM_LIMITS,
        )
    if len(records) == 1:
        return _assessment(
            claim,
            CertificationStatus.PASS,
            _DETERMINISM_METHOD,
            evidence,
            (f"{len(digests)} executions of identical inputs produced one record, {records[0]}.",),
            _DETERMINISM_LIMITS,
        )
    return _assessment(
        claim,
        CertificationStatus.FAIL,
        _DETERMINISM_METHOD,
        evidence,
        (
            f"{len(digests)} executions of identical recorded inputs produced {len(records)} "
            "different records.",
        ),
        _DETERMINISM_LIMITS,
    )


# --------------------------------------------------------------------------- #
# REPRODUCIBLE
# --------------------------------------------------------------------------- #

_REPRODUCIBILITY_METHOD = (
    "The manifest is assessed by assess_reproducibility: its identity must recompute from "
    "its declared content, nothing it rests on may be approximate, and a rerun manifest -- "
    "which must itself recompute -- of the same declared inputs must identify the same result."
)


def _reproducible(
    fingerprint: StrategyFingerprint,
    manifest: ReproducibilityManifest | None,
    rerun: ReproducibilityManifest | None,
) -> PropertyAssessment:
    claim = CertificationProperty.REPRODUCIBLE
    if manifest is None:
        return _assessment(
            claim,
            CertificationStatus.NOT_ASSESSED,
            _REPRODUCIBILITY_METHOD,
            {},
            ("no reproducibility manifest was supplied.",),
        )
    named = None if manifest.fingerprint is None else manifest.fingerprint.fingerprint
    if named != fingerprint.fingerprint:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _REPRODUCIBILITY_METHOD,
            {"manifest_id": manifest.manifest_id, "manifest_strategy": repr(named)},
            (
                f"the manifest is of strategy {named!r}, not {fingerprint.fingerprint!r}; it "
                "is evidence about another strategy version.",
            ),
        )

    # Both records must verify before they are compared: a rerun matching an
    # altered record, or an altered rerun, establishes nothing. Each is reported
    # here rather than raised, because an altered record is evidence too.
    identity_verified = verify_manifest(manifest)
    rerun_verified = rerun is not None and verify_manifest(rerun)
    assessment = assess_reproducibility(
        manifest, rerun if identity_verified and rerun_verified else None
    )
    evidence = {
        "manifest_id": manifest.manifest_id,
        "result_id": manifest.result_id,
        "dataset": manifest.dataset_version,
        "configuration": manifest.configuration_id,
        "seed": repr(manifest.seed),
        "seed_role": manifest.seed_role.name,
        "engine": str(manifest.engine),
        "identity_verified": repr(assessment.identity_verified),
        "rerun": assessment.rerun.name,
        "external_inputs": ",".join(
            requirement.input.name for requirement in assessment.external_requirements
        ),
        **(
            {}
            if rerun is None
            else {"rerun_manifest_id": rerun.manifest_id, "rerun_verified": repr(rerun_verified)}
        ),
    }
    limitations = [
        "Reproduction needs inputs AlphaLab does not hold: "
        + "; ".join(
            f"{requirement.input.name}: {requirement.detail}"
            for requirement in assessment.external_requirements
        )
        + ".",
        *assessment.findings,
    ]
    if not assessment.recreatable:
        limitations.append(
            "At least one input can never be supplied again, so the result is identified "
            "and cannot be recreated."
        )

    if not assessment.identity_verified:
        return _assessment(
            claim,
            CertificationStatus.FAIL,
            _REPRODUCIBILITY_METHOD,
            evidence,
            (
                "the manifest does not recompute from its own content.",
                *(
                    ()
                    if rerun is None
                    else ("the rerun was not compared with it: there is no record to compare.",)
                ),
            ),
            limitations,
        )
    if rerun is not None and not rerun_verified:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _REPRODUCIBILITY_METHOD,
            evidence,
            (
                f"the rerun's manifest {rerun.manifest_id} does not recompute from its own "
                "content; an altered record establishes nothing about reproduction.",
                *assessment.gaps,
            ),
            limitations,
        )
    if assessment.rerun is RerunOutcome.DIVERGED:
        return _assessment(
            claim,
            CertificationStatus.FAIL,
            _REPRODUCIBILITY_METHOD,
            evidence,
            assessment.rerun_detail,
            limitations,
        )
    if assessment.rerun is RerunOutcome.REPRODUCED and assessment.metadata_complete:
        return _assessment(
            claim,
            CertificationStatus.PASS,
            _REPRODUCIBILITY_METHOD,
            evidence,
            assessment.rerun_detail,
            limitations,
        )
    findings: list[str] = []
    if assessment.rerun is RerunOutcome.NOT_ATTEMPTED:
        findings.append(
            "no rerun was supplied; an identity that recomputes is not a result produced again."
        )
    else:
        findings.extend(assessment.rerun_detail)
    findings.extend(assessment.gaps)
    return _assessment(
        claim,
        CertificationStatus.INSUFFICIENT_EVIDENCE,
        _REPRODUCIBILITY_METHOD,
        evidence,
        findings,
        limitations,
    )


# --------------------------------------------------------------------------- #
# RISK_LIMITS and MAX_LEVERAGE
# --------------------------------------------------------------------------- #

_RISK_METHOD = (
    "Each supplied run must have been gated by exactly the declared RiskLimits; every "
    "pre-trade decision it recorded must be an approval; and the drawdown and gross exposure "
    "the pre-trade gate reads (RiskState) are observed at every recorded portfolio snapshot "
    "against the declared limits."
)

_RISK_LIMITS_NOT_OBSERVED = (
    _RUN_CONFIGURATION_UNOBSERVED,
    "Order-size, position and margin limits are enforced at order time; they are covered "
    "here through the gate's decisions, not re-observed between orders.",
    "A refusal is reported as the gate recorded it and is not re-judged here. The gate's "
    "position check adds the asset's notional exposure (RiskState.exposure.asset_exposure, "
    "a market value) to the order's quantity before comparing with "
    "PositionLimit.max_quantity, so a PositionLimit refusal can reflect that check's units "
    "rather than the strategy's position (ADR-0041).",
    "max_daily_loss is not assessed: the execution path does not maintain "
    "RiskState.daily_loss, so the daily-loss check reads zero and a run record carries no "
    "per-day loss to observe.",
    "max_net_exposure is not assessed: no pre-trade check reads it and AlphaLab defines no "
    "sign convention for it, so a comparison here would be an interpretation nobody chose.",
    "Limits are observed at recorded snapshots -- one per processed record, and one at "
    "funding -- not between them.",
)

_LEVERAGE_METHOD = (
    "The declared cap is RiskLimits.leverage.max_leverage. At every portfolio snapshot a "
    "supplied run recorded, leverage is read exactly as the pre-trade gate reads it -- "
    "RiskState.current_leverage: gross exposure (long plus the magnitude of short market "
    "value) over net asset value, to four decimal places -- and the peak is compared with the "
    "cap. The unit is a multiple of net asset value."
)


def _declared_risk(specification: DeploymentSpecification) -> dict[str, str]:
    risk = specification.risk
    return {
        "declared.order_size.max_quantity": str(risk.order_size.max_quantity),
        "declared.order_size.max_notional": str(risk.order_size.max_notional),
        "declared.position.max_quantity": str(risk.position.max_quantity),
        "declared.position.max_notional": str(risk.position.max_notional),
        "declared.exposure.max_gross_exposure": str(risk.exposure.max_gross_exposure),
        "declared.exposure.max_net_exposure": str(risk.exposure.max_net_exposure),
        "declared.leverage.max_leverage": str(risk.leverage.max_leverage),
        "declared.margin.max_margin_utilization": str(risk.margin.max_margin_utilization),
        "declared.daily_loss.max_daily_loss": str(risk.daily_loss.max_daily_loss),
        "declared.drawdown.max_drawdown_pct": str(risk.drawdown.max_drawdown_pct),
    }


def _risk_limits(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    runs: Sequence[BacktestResult],
    executed: Sequence[tuple[str, ...]],
) -> PropertyAssessment:
    claim = CertificationProperty.RISK_LIMITS
    evidence = _declared_risk(specification)
    if not runs:
        return _assessment(
            claim,
            CertificationStatus.NOT_ASSESSED,
            _RISK_METHOD,
            evidence,
            (
                "no runs were supplied; the declared limits are recorded and nothing was observed "
                "against them.",
            ),
            _RISK_LIMITS_NOT_OBSERVED,
        )
    foreign = _foreign_runs(fingerprint, executed)
    mismatched = [
        f"run {index} was gated by different RiskLimits than the specification declares, so "
        "its decisions say nothing about these."
        for index, result in enumerate(runs)
        if result.config.pipeline.risk_limits != specification.risk
    ]
    if foreign or mismatched:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _RISK_METHOD,
            evidence,
            [*foreign, *mismatched],
            _RISK_LIMITS_NOT_OBSERVED,
        )

    limits = specification.risk
    refusals: dict[str, int] = {}
    decisions = 0
    peak_drawdown = Decimal("0")
    peak_gross = Decimal("0")
    snapshots = 0
    for result in runs:
        for decision in result.state.risk.history:
            decisions += 1
            if not decision.approved:
                for violation in decision.violations:
                    refusals[violation.rule] = refusals.get(violation.rule, 0) + 1
        peak_nav = Decimal("0")
        for snapshot in result.equity_curve:
            snapshots += 1
            peak_nav = max(peak_nav, snapshot.total_equity)
            reading = RiskState(
                active_limits=limits,
                peak_nav=peak_nav,
                current_nav=snapshot.total_equity,
            )
            peak_drawdown = max(peak_drawdown, reading.current_drawdown_pct)
            peak_gross = max(peak_gross, snapshot.long_exposure + abs(snapshot.short_exposure))

    evidence.update(
        {
            "runs": str(len(runs)),
            "risk_decisions": str(decisions),
            "refusals": ",".join(f"{rule}={count}" for rule, count in sorted(refusals.items())),
            "snapshots": str(snapshots),
            "observed.peak_drawdown_pct": str(peak_drawdown),
            "observed.peak_gross_exposure": str(peak_gross),
        }
    )
    failures: list[str] = []
    if refusals:
        failures.append(
            f"the pre-trade gate, configured with exactly the declared limits, refused "
            f"{sum(refusals.values())} order(s) ({', '.join(sorted(refusals))}); the strategy "
            "did not trade inside its limits without the gate intervening."
        )
    if peak_drawdown > limits.drawdown.max_drawdown_pct:
        failures.append(
            f"drawdown reached {peak_drawdown}, beyond the declared "
            f"{limits.drawdown.max_drawdown_pct}."
        )
    if peak_gross > limits.exposure.max_gross_exposure:
        failures.append(
            f"gross exposure reached {peak_gross}, beyond the declared "
            f"{limits.exposure.max_gross_exposure}."
        )
    if failures:
        return _assessment(
            claim,
            CertificationStatus.FAIL,
            _RISK_METHOD,
            evidence,
            failures,
            _RISK_LIMITS_NOT_OBSERVED,
        )
    return _assessment(
        claim,
        CertificationStatus.PASS,
        _RISK_METHOD,
        evidence,
        (
            f"{decisions} pre-trade decision(s) across {len(runs)} run(s), all approvals; "
            f"drawdown peaked at {peak_drawdown} and gross exposure at {peak_gross}, within "
            "the declared limits.",
        ),
        _RISK_LIMITS_NOT_OBSERVED,
    )


def _max_leverage(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    runs: Sequence[BacktestResult],
    executed: Sequence[tuple[str, ...]],
) -> PropertyAssessment:
    claim = CertificationProperty.MAX_LEVERAGE
    cap = specification.risk.leverage.max_leverage
    evidence = {"declared.max_leverage": str(cap)}
    limitations = (
        _RUN_CONFIGURATION_UNOBSERVED,
        "Leverage is observed at recorded snapshots -- one per processed record, and one at "
        "funding -- not between them.",
        "A multi-currency book is valued in the run's reporting currency with the rates the "
        "run was given; the reading is only as good as those rates.",
    )
    if not runs:
        return _assessment(
            claim,
            CertificationStatus.NOT_ASSESSED,
            _LEVERAGE_METHOD,
            evidence,
            ("no runs were supplied; the declared cap is recorded and no leverage was observed.",),
            limitations,
        )
    foreign = _foreign_runs(fingerprint, executed)
    if foreign:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _LEVERAGE_METHOD,
            evidence,
            foreign,
            limitations,
        )

    peak = Decimal("0")
    peak_at: float | None = None
    unbounded: list[str] = []
    snapshots = 0
    for index, result in enumerate(runs):
        for snapshot in result.equity_curve:
            snapshots += 1
            gross = snapshot.long_exposure + abs(snapshot.short_exposure)
            if snapshot.total_equity <= Decimal("0"):
                # RiskState.current_leverage reads zero here, which is the gate
                # declining to divide -- not a leverage of zero. An exposed book
                # with no equity is unbounded, and is reported as such.
                if gross > Decimal("0"):
                    unbounded.append(
                        f"run {index} held {gross} of gross exposure on "
                        f"{snapshot.total_equity} of equity at {snapshot.timestamp!r}; "
                        "leverage there is unbounded."
                    )
                continue
            reading = RiskState(
                active_limits=specification.risk,
                exposure=ExposureStatus(gross_exposure=gross),
                current_nav=snapshot.total_equity,
            ).current_leverage
            if reading > peak:
                peak, peak_at = reading, snapshot.timestamp
    evidence.update(
        {
            "runs": str(len(runs)),
            "snapshots": str(snapshots),
            "observed.peak_leverage": str(peak),
            "observed.peak_at": repr(peak_at),
        }
    )
    if unbounded:
        return _assessment(
            claim, CertificationStatus.FAIL, _LEVERAGE_METHOD, evidence, unbounded, limitations
        )
    if peak > cap:
        return _assessment(
            claim,
            CertificationStatus.FAIL,
            _LEVERAGE_METHOD,
            evidence,
            (f"leverage reached {peak}x at {peak_at!r}, beyond the declared {cap}x.",),
            limitations,
        )
    return _assessment(
        claim,
        CertificationStatus.PASS,
        _LEVERAGE_METHOD,
        evidence,
        (f"leverage peaked at {peak}x across {snapshots} snapshot(s); the cap is {cap}x.",),
        limitations,
    )


# --------------------------------------------------------------------------- #
# SUPPORTED_MARKETS
# --------------------------------------------------------------------------- #

_MARKETS_METHOD = (
    "Every instrument a supplied run filled must be one the specification's "
    "MarketRequirements declare; where the run was configured with an InstrumentRegistry, "
    "each traded instrument's listing exchange and currency must also be declared venues and "
    "quote currencies."
)


def _supported_markets(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    runs: Sequence[BacktestResult],
    executed: Sequence[tuple[str, ...]],
) -> PropertyAssessment:
    claim = CertificationProperty.SUPPORTED_MARKETS
    market = specification.market
    evidence = {
        "declared.instruments": ",".join(sorted(market.instruments)),
        "declared.venues": ",".join(sorted(market.venues)),
        "declared.calendar_ids": ",".join(sorted(market.calendar_ids)),
        "declared.quote_currencies": ",".join(sorted(market.quote_currencies)),
    }
    limitations: list[str] = [
        _RUN_CONFIGURATION_UNOBSERVED,
        "Market calendars are declared by id and not checked against the runs: a run records "
        "the instants it traded, not the calendar they fell in.",
        "Support is established for what the runs actually traded, not for every declared "
        "instrument.",
    ]
    if not runs:
        return _assessment(
            claim,
            CertificationStatus.NOT_ASSESSED,
            _MARKETS_METHOD,
            evidence,
            ("no runs were supplied; the declared markets are recorded and none was exercised.",),
            limitations,
        )
    foreign = _foreign_runs(fingerprint, executed)
    if foreign:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _MARKETS_METHOD,
            evidence,
            foreign,
            limitations,
        )

    declared = set(market.instruments)
    traded: set[str] = set()
    failures: list[str] = []
    unregistered_runs = 0
    for index, result in enumerate(runs):
        run_traded = {str(fill.asset_id) for fill in result.fills}
        traded |= run_traded
        registry = result.config.pipeline.instruments
        if registry is None:
            unregistered_runs += 1
            continue
        for asset_id in sorted(run_traded):
            record = registry.record_for(asset_id)
            if record is None:
                failures.append(
                    f"run {index} traded {asset_id}, which its own instrument registry does "
                    "not hold."
                )
                continue
            if record.exchange not in market.venues:
                failures.append(
                    f"{asset_id} is listed on {record.exchange}, which is not a declared venue."
                )
            if record.currency not in market.quote_currencies:
                failures.append(
                    f"{asset_id} trades in {record.currency}, which is not a declared quote "
                    "currency."
                )
    undeclared = sorted(traded - declared)
    if undeclared:
        failures.insert(
            0,
            f"the runs traded {undeclared}, which the specification does not declare; a "
            "deployment provisioned from it would not have them.",
        )
    evidence["traded"] = ",".join(sorted(traded))
    if unregistered_runs:
        limitations.append(
            f"{unregistered_runs} run(s) configured no InstrumentRegistry, so the listing "
            "exchange and currency of what they traded were not checked."
        )
    if failures:
        return _assessment(
            claim, CertificationStatus.FAIL, _MARKETS_METHOD, evidence, failures, limitations
        )
    if not traded:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _MARKETS_METHOD,
            evidence,
            ("the runs filled nothing, so no market was exercised.",),
            limitations,
        )
    return _assessment(
        claim,
        CertificationStatus.PASS,
        _MARKETS_METHOD,
        evidence,
        (f"every instrument traded ({len(traded)}) is declared.",),
        limitations,
    )


# --------------------------------------------------------------------------- #
# REQUIRED_DATA
# --------------------------------------------------------------------------- #

_DATA_METHOD = (
    "Each dataset the specification assumes must be verified by a supplied Dataset whose "
    "provenance records exactly that derived version over a non-empty source payload; every "
    "supplied run and manifest must have been measured on one of the declared versions."
)


def _required_data(
    specification: DeploymentSpecification,
    runs: Sequence[BacktestResult],
    datasets: Sequence[Dataset],
    manifest: ReproducibilityManifest | None,
) -> PropertyAssessment:
    claim = CertificationProperty.REQUIRED_DATA
    declared = {
        assumption.dataset_version: assumption.role for assumption in specification.datasets
    }
    evidence: dict[str, str] = {
        f"declared.{role}": version for version, role in sorted(declared.items())
    }
    limitations = (
        "A declared version is an identity: the bytes behind it are the application's to "
        "supply, and AlphaLab stores none of them.",
        "A dataset's version is read from its provenance as the data layer derived it at "
        "ingestion; it is not re-derived from the source bytes here.",
    )
    if not (datasets or runs or manifest is not None):
        return _assessment(
            claim,
            CertificationStatus.NOT_ASSESSED,
            _DATA_METHOD,
            evidence,
            ("no dataset, run or manifest was supplied to check the declared data against.",),
            limitations,
        )

    findings: list[str] = []
    failures: list[str] = []
    verified: set[str] = set()
    for dataset in datasets:
        provenance = dataset.provenance
        if provenance is None:
            findings.append(
                f"dataset {dataset.dataset_id!r} carries no provenance, so it verifies no "
                "declared version."
            )
            continue
        if provenance.source.byte_count == 0:
            # The lineage hole a manifest refuses (ADR-0041): a version derived
            # over an empty payload is shared by any rows under that name.
            findings.append(
                f"dataset {provenance.dataset_version} records an empty source payload, so its "
                "version identifies no content and verifies nothing."
            )
            continue
        role = declared.get(provenance.dataset_version)
        if role is None:
            findings.append(
                f"dataset {provenance.dataset_version} is not one the specification declares."
            )
            continue
        verified.add(provenance.dataset_version)
        evidence.update(
            {
                f"{role}.record_type": str(provenance.schema.data_type),
                f"{role}.frequency": provenance.frequency.name,
                f"{role}.price_basis": provenance.price_basis.name,
                f"{role}.timezone": provenance.timezone_name,
                f"{role}.calendar_id": repr(provenance.calendar_id),
                f"{role}.fields": ",".join(sorted(provenance.schema.bindings)),
            }
        )

    consumed: list[tuple[str, str | None]] = [
        (f"run {index}", result.dataset_id) for index, result in enumerate(runs)
    ]
    if manifest is not None:
        consumed.append((f"manifest {manifest.manifest_id}", manifest.dataset_version))
    unidentified = False
    for label, version in consumed:
        if version is None:
            unidentified = True
            findings.append(f"{label} names no dataset, so what it was measured on is unknown.")
        elif version not in declared:
            failures.append(
                f"{label} was measured on {version}, which the specification does not declare."
            )
    unverified = sorted(set(declared) - verified)
    evidence["verified"] = ",".join(sorted(verified))
    if failures:
        return _assessment(
            claim,
            CertificationStatus.FAIL,
            _DATA_METHOD,
            evidence,
            [*failures, *findings],
            limitations,
        )
    if unverified:
        findings.append(
            f"{len(unverified)} declared dataset version(s) were not verified by a supplied "
            f"dataset: {unverified}."
        )
    if unverified or unidentified:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _DATA_METHOD,
            evidence,
            findings,
            limitations,
        )
    return _assessment(
        claim,
        CertificationStatus.PASS,
        _DATA_METHOD,
        evidence,
        (
            f"all {len(declared)} declared dataset version(s) recorded by a supplied dataset's "
            "provenance; every supplied measurement was taken on declared data.",
            *findings,
        ),
        limitations,
    )


# --------------------------------------------------------------------------- #
# RESOURCE_USAGE
# --------------------------------------------------------------------------- #

_RESOURCE_METHOD = (
    "Counts are read off each supplied run's record; timings and memory are supplied with "
    "their method and environment; each stated budget must be met by a counted or measured "
    "figure, and an estimate never meets one."
)


def _resource_usage(
    runs: Sequence[BacktestResult],
    measurements: Sequence[ResourceMeasurement],
    budgets: Sequence[ResourceBudget],
) -> PropertyAssessment:
    claim = CertificationProperty.RESOURCE_USAGE
    figures = [*(count for result in runs for count in resource_counts(result)), *measurements]
    evidence: dict[str, str] = {}
    by_metric: dict[ResourceMetric, list[ResourceMeasurement]] = {}
    for figure in figures:
        by_metric.setdefault(figure.metric, []).append(figure)
    for metric, entries in sorted(by_metric.items(), key=lambda item: item[0].value):
        observed = [entry for entry in entries if entry.basis is not MeasurementBasis.ESTIMATED]
        if observed:
            evidence[f"observed.{metric.name}"] = str(max(entry.value for entry in observed))
            evidence[f"basis.{metric.name}"] = ",".join(
                sorted({entry.basis.name for entry in observed})
            )
            environments = sorted({entry.environment for entry in observed if entry.environment})
            if environments:
                evidence[f"environment.{metric.name}"] = " | ".join(environments)
        estimates = [entry for entry in entries if entry.basis is MeasurementBasis.ESTIMATED]
        if estimates:
            evidence[f"estimated.{metric.name}"] = str(max(entry.value for entry in estimates))
    limitations = (
        _RUN_CONFIGURATION_UNOBSERVED,
        "A MEASURED figure is true of the environment it names and is not reproducible "
        "elsewhere; a COUNTED figure reproduces exactly.",
        "Budgets are the caller's; AlphaLab declares none.",
    )

    if not budgets:
        return _assessment(
            claim,
            CertificationStatus.NOT_ASSESSED,
            _RESOURCE_METHOD,
            evidence,
            ("no resource budget was stated; a figure without a budget certifies nothing.",),
            limitations,
        )

    failures: list[str] = []
    insufficient: list[str] = []
    for budget in sorted(budgets, key=lambda item: item.metric.value):
        evidence[f"budget.{budget.metric.name}"] = str(budget.maximum)
        entries = by_metric.get(budget.metric, [])
        observed = [entry for entry in entries if entry.basis is not MeasurementBasis.ESTIMATED]
        if observed:
            peak = max(entry.value for entry in observed)
            if peak > budget.maximum:
                failures.append(
                    f"{budget.metric.name} reached {peak}, beyond the budget of {budget.maximum}."
                )
        elif entries:
            insufficient.append(
                f"{budget.metric.name} has only an estimate; a theoretical figure is not a "
                "measurement and cannot meet a budget."
            )
        else:
            insufficient.append(f"{budget.metric.name} was budgeted and never measured.")
    if failures:
        return _assessment(
            claim,
            CertificationStatus.FAIL,
            _RESOURCE_METHOD,
            evidence,
            [*failures, *insufficient],
            limitations,
        )
    if insufficient:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _RESOURCE_METHOD,
            evidence,
            insufficient,
            limitations,
        )
    return _assessment(
        claim,
        CertificationStatus.PASS,
        _RESOURCE_METHOD,
        evidence,
        (f"all {len(budgets)} budget(s) met by counted or measured figures.",),
        limitations,
    )


# --------------------------------------------------------------------------- #
# RUNTIME_BEHAVIOR
# --------------------------------------------------------------------------- #

_RUNTIME_METHOD = (
    "Each supplied runtime observation is judged by evaluate_health against the "
    "specification's runtime requirements; the behaviour passes only when every report is "
    "HEALTHY."
)


def _has_run(progression: StrategyProgression) -> bool:
    """Whether the progression has ever been in a stage where something ran."""

    if progression.stage in PAUSABLE_STAGES:
        return True
    return any(transition.to_stage in PAUSABLE_STAGES for transition in progression.history)


def _runtime_behavior(
    specification: DeploymentSpecification,
    observations: Sequence[RuntimeObservation],
    progression: StrategyProgression | None,
) -> PropertyAssessment:
    claim = CertificationProperty.RUNTIME_BEHAVIOR
    limitations = (
        "Health is judged at each supplied observation instant, not between them.",
        "Observations are supplied by whoever watched the deployment; AlphaLab observes "
        "nothing on its own.",
    )
    if not observations:
        return _assessment(
            claim,
            CertificationStatus.NOT_ASSESSED,
            _RUNTIME_METHOD,
            {"observations": "0"},
            ("no runtime observations were supplied.",),
            limitations,
        )

    reports = [evaluate_health(specification, observation) for observation in observations]
    counts = dict.fromkeys(HealthStatus, 0)
    for report in reports:
        counts[report.status] += 1
    evidence = {
        "observations": str(len(reports)),
        "first_observed_at": repr(min(report.evaluated_at for report in reports)),
        "last_observed_at": repr(max(report.evaluated_at for report in reports)),
        **{f"reports.{status.name}": str(count) for status, count in counts.items()},
    }
    failing = [
        f"at {report.evaluated_at!r}: {finding.category.name} ({finding.severity.name}) -- "
        f"{finding.summary}"
        for report in reports
        if report.status in (HealthStatus.BREACHED, HealthStatus.DEGRADED)
        for finding in report.findings
    ]
    if failing:
        return _assessment(
            claim, CertificationStatus.FAIL, _RUNTIME_METHOD, evidence, failing, limitations
        )
    unevaluated = [
        f"at {report.evaluated_at!r}: {entry.category.name} was not evaluated -- {entry.reason}"
        for report in reports
        for entry in report.unevaluated
    ]
    if unevaluated:
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _RUNTIME_METHOD,
            evidence,
            unevaluated,
            limitations,
        )
    if progression is not None and not _has_run(progression):
        return _assessment(
            claim,
            CertificationStatus.INSUFFICIENT_EVIDENCE,
            _RUNTIME_METHOD,
            evidence,
            (
                f"the progression records that {progression.reference} has never been in a "
                "running stage, and runtime observations of it were supplied; one of the two "
                "is wrong, and this assessment cannot say which.",
            ),
            limitations,
        )
    return _assessment(
        claim,
        CertificationStatus.PASS,
        _RUNTIME_METHOD,
        evidence,
        (f"all {len(reports)} observation(s) HEALTHY against the declared requirements.",),
        limitations,
    )


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def _canonical_report_key(
    fingerprint: str,
    specification_id: str,
    stage: StrategyLifecycleStage | None,
    specification_findings: Sequence[str],
    assessments: Sequence[PropertyAssessment],
) -> str:
    lines = [
        CERTIFICATION_REPORT_SCHEME,
        f"fingerprint={fingerprint!r}",
        f"specification={specification_id!r}",
        f"stage={None if stage is None else stage.name}",
        "specification_findings",
        *(repr(finding) for finding in specification_findings),
    ]
    for assessment in assessments:
        lines.extend(
            [
                "assessment",
                f"claim={assessment.claim.name}",
                f"status={assessment.status.name}",
                f"methodology={assessment.methodology!r}",
                "evidence",
                *(f"{key!r}={assessment.evidence[key]!r}" for key in sorted(assessment.evidence)),
                "findings",
                *(repr(finding) for finding in assessment.findings),
                "limitations",
                *(repr(limitation) for limitation in assessment.limitations),
            ]
        )
    return "\n".join(lines)


def _report_id(
    fingerprint: str,
    specification_id: str,
    stage: StrategyLifecycleStage | None,
    specification_findings: Sequence[str],
    assessments: Sequence[PropertyAssessment],
) -> str:
    key = _canonical_report_key(
        fingerprint, specification_id, stage, specification_findings, assessments
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def certify_strategy(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    evidence: CertificationEvidence,
    budgets: Sequence[ResourceBudget] = (),
    progression: StrategyProgression | None = None,
) -> CertificationReport:
    """Assess every certification property of one strategy version.

    Pure and deterministic. Every property is assessed or reported
    ``NOT_ASSESSED`` with the reason, so the report is total over
    :class:`CertificationProperty` and a reader never has to wonder whether a
    check ran.

    Args:
        fingerprint: The strategy version. Must verify.
        specification: What it declares it needs -- the limits, markets, data
            and runtime budgets the properties are judged against. Must verify,
            and must describe the same strategy line with the same parameters
            as the fingerprint.
        evidence: What was observed. See :class:`CertificationEvidence`.
        budgets: The resource budgets to hold it to. None is an honest answer
            and leaves ``RESOURCE_USAGE`` unassessed.
        progression: Where the version's progression stands, if the caller
            holds it. Recorded on the report, and checked against runtime
            evidence.

    Raises:
        LifecycleInputError: If the fingerprint or the specification does not
            verify; if the two disagree about the strategy line or its
            parameters -- a specification configuring different parameters
            would attach its limits to a strategy that is not this one; if the
            progression is of another version; or if a supplied measurement
            claims to be ``COUNTED``, which only a run's record can be.
    """

    if not verify_fingerprint(fingerprint):
        raise LifecycleInputError(
            f"Fingerprint {fingerprint.fingerprint} does not match its own content; it was "
            "altered after it was derived."
        )
    if not verify_specification_id(specification):
        raise LifecycleInputError(
            f"Specification {specification.specification_id} does not match its own content; "
            "it was altered after it was built."
        )
    if specification.strategy.name != fingerprint.name:
        raise LifecycleInputError(
            f"The specification is for {specification.strategy} and the fingerprint is of "
            f"strategy line {fingerprint.name!r}."
        )
    differing = differing_parameters(fingerprint.parameters, specification.parameters)
    if differing:
        raise LifecycleInputError(
            f"The specification configures parameters {list(differing)} differently from the "
            "fingerprinted version. Certifying the pair would attach one strategy's limits to "
            "another's logic."
        )
    if progression is not None and progression.reference != specification.strategy:
        raise LifecycleInputError(
            f"The progression is of {progression.reference} and the specification is for "
            f"{specification.strategy}."
        )
    counted = [
        measurement
        for measurement in evidence.measurements
        if measurement.basis is MeasurementBasis.COUNTED
    ]
    if counted:
        raise LifecycleInputError(
            f"{len(counted)} supplied measurement(s) claim to be COUNTED. Counts are read off "
            "the runs by the certification itself; a supplied one is a claim, not a count."
        )

    executed = [_executed(result) for result in evidence.runs]
    assessments = (
        _deterministic(fingerprint, evidence.repeated_runs),
        _reproducible(fingerprint, evidence.manifest, evidence.rerun),
        _risk_limits(fingerprint, specification, evidence.runs, executed),
        _max_leverage(fingerprint, specification, evidence.runs, executed),
        _supported_markets(fingerprint, specification, evidence.runs, executed),
        _required_data(specification, evidence.runs, evidence.datasets, evidence.manifest),
        _resource_usage(evidence.runs, evidence.measurements, budgets),
        _runtime_behavior(specification, evidence.observations, progression),
    )
    stage = None if progression is None else progression.stage
    specification_findings = validate_specification(specification)
    return CertificationReport(
        report_id=_report_id(
            fingerprint.fingerprint,
            specification.specification_id,
            stage,
            specification_findings,
            assessments,
        ),
        fingerprint=fingerprint.fingerprint,
        specification_id=specification.specification_id,
        stage=stage,
        specification_findings=specification_findings,
        assessments=assessments,
    )


def verify_certification_report(report: CertificationReport) -> bool:
    """Whether the report's id still matches its content, and it is still total."""

    claims = [assessment.claim for assessment in report.assessments]
    if claims != list(CertificationProperty):
        return False
    return report.report_id == _report_id(
        report.fingerprint,
        report.specification_id,
        report.stage,
        report.specification_findings,
        report.assessments,
    )
