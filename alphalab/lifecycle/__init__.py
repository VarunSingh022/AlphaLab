"""AlphaLab Lifecycle: one path from a research run to a rolled-back deployment.

This package adds no engine. Like :mod:`alphalab.backtesting`, which composed
the execution engines into one deterministic run in v2.2, it composes packages
AlphaLab already had -- experiment tracking, the model registry, strategy
definitions, and the deployment manager -- into the flow they were each written
for one stage of:

.. code-block:: text

    research candidate                 alphalab.research_assistant
          |
          v
    experiment run                     alphalab.experiment_tracking
          |
          v
    validation evidence                alphalab.lifecycle.evidence
          |                            (from alphalab.backtesting /
          |                             alphalab.research reports)
          v
    model version                      alphalab.model_registry
          |
          v
    strategy version                   alphalab.lifecycle.strategy_version
          |
          v
    promotion                          alphalab.lifecycle.promotion
          |
          v
    deployment                         alphalab.deployment_manager
          |
          v
    rollback

Each of those packages still works on its own, and none of them changed shape to
be composed here.

What this package adds
----------------------
**Typed references.** A model version and a strategy version are both a name and
a number, which is why they were previously passed around as opaque strings in a
release manifest. :mod:`~alphalab.lifecycle.identity` gives each its own type and
one canonical rendering, and refuses a name that would not round-trip.

**Validation evidence.** :mod:`~alphalab.lifecycle.evidence` records what was
measured, over what data, with what seed, and against thresholds stated in
advance. The measurements come from the two deterministic producers AlphaLab
already has -- a run's ``PerformanceReport`` and the research engine's
``ResearchScore`` -- and are referenced, not recomputed.

**A strategy version.** :mod:`~alphalab.lifecycle.strategy_version` is the
immutable, numbered record that was missing: distinct from the strategy line,
from the model version it runs, and from the deployments it appears in.

**A gate.** :mod:`~alphalab.lifecycle.promotion` refuses a promotion that no
passing evidence stands behind, and refuses to put anything into production at
all -- a strategy version reaches production by being deployed, so the ledger is
the only thing that ever puts one live.

**Checked references.** :mod:`~alphalab.lifecycle.registration` is where a model
version's ``run_id`` and a strategy version's model reference are checked
against the runs and versions the lifecycle actually holds, which neither
package could do alone without depending on the other.

Deliberate limits
-----------------
A deployment here is a lifecycle fact, not an operation on a machine: it records
that an environment should be running a strategy version. It starts no process,
opens no connection, and reaches no venue. That is a statement about what a
*deployment* is and is unchanged by v2.15 adding a venue transport
(ADR-0012, ADR-0031): making a release active still records a fact and sends
nothing. Artifact bytes are held from v2.15 by
:mod:`alphalab.model_registry.artifact_store`; this package references artifacts
and stores none of them itself.

The lifecycle sits *above* :class:`~alphalab.runtime.execution_pipeline.ExecutionPipeline`
and is not wired into it. A deployment names what should run; running it is the
execution path's job.

**The two are joined by :mod:`alphalab.lifecycle.execution` from v2.16.** Until
then this paragraph ended "and the two are joined by the caller", which meant
nothing checked that a run was serving the version an environment actually had
live -- a run could execute a version that was never promoted, or one rolled
back an hour earlier, and nothing would notice.
:func:`~alphalab.lifecycle.execution.run_plan` resolves a deployment into what
should run and :func:`~alphalab.lifecycle.execution.authorize_run` refuses a run
that would serve anything else. Neither starts a process, constructs a strategy
or builds a ``RunConfig``: the join is a query with a refusal, not a second
runtime. See ADR-0033.

What v3.5 adds
--------------

The same package, carried past the deployment record into the thing a deployment
becomes. Five capabilities, each an extension of what was already here rather
than a parallel system, and none of them a runtime:

:mod:`~alphalab.lifecycle.progression`
    The research-to-live axis the roadmap names --
    ``RESEARCH -> BACKTEST -> VALIDATION -> PAPER -> PRODUCTION_CANDIDATE ->
    LIVE -> PAUSED -> ARCHIVED`` -- with declared transitions and a history.
    Distinct from :class:`~alphalab.model_registry.registry.ModelStage`, which
    asks whether an artifact is promotable, and from
    :class:`alphalab.strategy.state.LifecycleState`, which asks whether an
    instance in a session is running.

:mod:`~alphalab.lifecycle.specification`
    What a strategy version needs in order to be deployed as it was researched:
    its parameters, its dataset assumptions by derived identity, the risk limits
    the pre-trade gate will enforce, the capital, and typed broker, market and
    runtime requirements. Self-identifying by the same content digest
    :func:`~alphalab.lifecycle.evidence.evidence_id_for` uses.

:mod:`~alphalab.lifecycle.health`
    Supplied observations judged against those runtime requirements, producing
    structured findings with severities. The structured counterpart to
    :func:`~alphalab.runtime.live.live_health`, which reports a live driver's own
    aggregate in plain sentences and is unchanged.

:mod:`~alphalab.lifecycle.comparison`
    Expected against paper against live -- trades, fills, slippage, P&L,
    exposure and execution latency -- with declared alignment and explicit
    tolerances.

:mod:`~alphalab.lifecycle.reconciliation`
    AlphaLab's execution state against a normalized broker state, with
    deterministic mismatch detection.
    :func:`alphalab.broker.reconciliation.reconcile` remains the authority for
    the *other* pair, the venue's records against AlphaLab's mirror of them.

None of the five opens a connection, holds a credential, names a venue or
changes any state. Detection is not remediation, and a deployment specification
is still a statement rather than an operation on a machine.

What v3.6 adds
--------------

The contracts an application needs in order to evaluate strategies it did not
write -- to admit, compare or list them -- without AlphaLab knowing anything
about that application. Four capabilities, each a value produced by a pure
function, and none of them a store, a registry or a workflow:

:mod:`~alphalab.lifecycle.fingerprint`
    One immutable identity per strategy version, derived from its code, its
    declared dependencies (with how complete that declaration is), its
    parameters, its research configuration and its engine version. Nothing
    environment-specific enters it, so it is the same identity in research,
    paper and live.

:mod:`~alphalab.lifecycle.reproducibility`
    A manifest naming everything one result was produced from -- dataset,
    strategy, configuration, seed, engine -- with every identity read from the
    authority that owns it, and an assessment that keeps identity, metadata
    completeness, a rerun and external dependencies as four separate answers.

:mod:`~alphalab.lifecycle.certification`
    Eight machine-verifiable properties -- deterministic, reproducible, risk
    limits, maximum leverage, supported markets, required data, resource usage,
    runtime behaviour -- each ``PASS``, ``FAIL``, ``NOT_ASSESSED`` or
    ``INSUFFICIENT_EVIDENCE``, with its methodology, evidence and limitations,
    and no overall score. Evidence is observed and never asserted: every verdict
    is derived here.

:mod:`~alphalab.lifecycle.portability`
    Whether a strategy version can move, unchanged, to an environment that
    declares its capabilities -- research, paper, live, or two brokers that
    differ -- and exactly what blocks it where it cannot.

None of the four persists anything, lists, ranks, sells or publishes a
strategy, or knows who is asking.

What v3.7 adds
--------------

Adaptive strategies and point-in-time research reach the same four contracts
without widening any of them.
:func:`~alphalab.lifecycle.fingerprint.research_configuration_with_adaptive`
names an adaptive component's configuration and starting state through the
research-settings section a fingerprint already hashes, so no fingerprint key
moved. A study that names auxiliary inputs -- event sets, observation sets,
fundamentals, regime definitions -- lists each as an external requirement of
its manifest. :func:`~alphalab.lifecycle.reproducibility.assess_adaptive_replay`
answers, for a research replay of an adaptive component, the question
:func:`~alphalab.lifecycle.reproducibility.assess_reproducibility` answers for a
run: did the same inputs produce the same result, and if not, where did it
first differ. A run of an adaptive strategy needs nothing new: its captured
state is in the run's record, so :func:`~alphalab.lifecycle.reproducibility.digest_run`
already commits to every update it made.
"""

from alphalab.lifecycle.certification import (
    CERTIFICATION_REPORT_SCHEME,
    CertificationEvidence,
    CertificationProperty,
    CertificationReport,
    CertificationStatus,
    MeasurementBasis,
    PropertyAssessment,
    ResourceBudget,
    ResourceMeasurement,
    ResourceMetric,
    certify_strategy,
    resource_counts,
    verify_certification_report,
)
from alphalab.lifecycle.comparison import (
    MONETARY_METRICS,
    AlignmentKey,
    ComparisonEntry,
    ComparisonMetric,
    ComparisonOutcome,
    ComparisonSource,
    FillObservation,
    PairComparison,
    RunObservations,
    ThreeWayComparison,
    TradeObservation,
    compare_expected_paper_live,
    compare_runs,
    observations_from_backtest,
    observations_from_broker,
)
from alphalab.lifecycle.deployment import (
    DEPLOYABLE_STAGES,
    deploy_strategy_version,
    release_manifest,
    rollback_environment,
)
from alphalab.lifecycle.evidence import (
    MetricThreshold,
    ValidationEvidence,
    ValidationMethod,
    ValidationOutcome,
    ValidationPolicy,
    build_evidence,
    evaluate_policy,
    evidence_from_backtest,
    evidence_from_research,
    evidence_from_study,
    evidence_id_for,
    verify_evidence_id,
)
from alphalab.lifecycle.exceptions import (
    LifecycleError,
    LifecycleInputError,
    LifecycleTransitionError,
)
from alphalab.lifecycle.execution import (
    RunAuthorization,
    RunPlan,
    authorize_run,
    run_plan,
)
from alphalab.lifecycle.fingerprint import (
    ADAPTIVE_SETTING_PREFIX,
    NO_DEPENDENCIES,
    STRATEGY_FINGERPRINT_SCHEME,
    STRATEGY_SOURCE_SCHEME,
    UNDECLARED_DEPENDENCIES,
    CodeIdentity,
    DependencyCompleteness,
    DependencyManifest,
    DependencyPin,
    EngineIdentity,
    ResearchConfiguration,
    StrategyFingerprint,
    StudyIdentity,
    build_fingerprint,
    canonical_fingerprint_key,
    code_identity_for,
    derive_strategy_fingerprint,
    differing_parameters,
    fingerprint_differences,
    fingerprint_for_version,
    normalize_distribution_name,
    research_configuration,
    research_configuration_for_study,
    research_configuration_with_adaptive,
    running_engine,
    source_digest,
    verify_fingerprint,
)
from alphalab.lifecycle.governance import (
    LIFECYCLE_PERMISSIONS,
    PERMISSION_APPROVE,
    PERMISSION_DEPLOY,
    PERMISSION_PROMOTE,
    PERMISSION_RETIRE,
    PERMISSION_ROLLBACK,
    ApprovalRecord,
    Governance,
    GovernedAct,
    approval_for,
    approvals_for,
)
from alphalab.lifecycle.health import (
    ExecutionObservation,
    HealthCategory,
    HealthFinding,
    HealthReport,
    HealthSeverity,
    HealthStatus,
    RuntimeObservation,
    StateExpectation,
    UnevaluatedCategory,
    evaluate_health,
    observation_from_live_run,
)
from alphalab.lifecycle.identity import (
    COMPONENT_EVIDENCE,
    COMPONENT_MODEL,
    COMPONENT_RUN,
    COMPONENT_STRATEGY,
    DeploymentRef,
    ModelRef,
    StrategyVersionRef,
    parse_ref,
)
from alphalab.lifecycle.portability import (
    PORTABILITY_REPORT_SCHEME,
    EnvironmentPortability,
    PortabilityReport,
    PortabilityRequirement,
    PortabilityStatus,
    RequirementCheck,
    RequirementOutcome,
    RuntimeProfile,
    TargetEnvironment,
    evaluate_portability,
    verify_portability_report,
)
from alphalab.lifecycle.progression import (
    INITIAL_STAGE,
    LEGAL_PROGRESSION_TRANSITIONS,
    PAUSABLE_STAGES,
    PROGRESSION_MODEL_STAGES,
    StageTransition,
    StrategyLifecycleStage,
    StrategyProgression,
    advance_progression,
    archive_progression,
    begin_progression,
    illegal_progression_move,
    pause_progression,
    progression_conflicts,
    resume_progression,
    resume_target,
)
from alphalab.lifecycle.promotion import (
    STAGEABLE_MODEL_STAGES,
    approve_deployment,
    promote_strategy_version,
    record_evidence,
    record_stage_change,
    retire_strategy_version,
    validate_strategy_version,
)
from alphalab.lifecycle.reconciliation import (
    BROKER_STATUS_EQUIVALENTS,
    Mismatch,
    MismatchCategory,
    ReconciliationTolerances,
    StateReconciliation,
    SymbolMapping,
    UnreconciledArea,
    reconcile_execution_state,
)
from alphalab.lifecycle.registration import register_model_version, register_strategy
from alphalab.lifecycle.reproducibility import (
    REPRODUCIBILITY_MANIFEST_SCHEME,
    AdaptiveReplayAssessment,
    DatasetProvenanceView,
    ExternalInput,
    ExternalRequirement,
    ReproducibilityAssessment,
    ReproducibilityManifest,
    RerunOutcome,
    ResultKind,
    RunDigest,
    SeedRole,
    SourceBytesView,
    VersionedDataset,
    assess_adaptive_replay,
    assess_reproducibility,
    canonical_manifest_key,
    derive_manifest_id,
    digest_run,
    external_requirements,
    manifest_for_run,
    manifest_for_study,
    manifest_gaps,
    verify_manifest,
)
from alphalab.lifecycle.specification import (
    DEPLOYMENT_SPECIFICATION_SCHEME,
    BrokerCapabilities,
    BrokerRequirements,
    CapitalPolicy,
    DatasetAssumption,
    DeploymentSpecification,
    MarketAvailability,
    MarketRequirements,
    RuntimeRequirements,
    build_specification,
    dataset_assumption_from,
    specification_for_version,
    specification_id_for,
    unmet_broker_requirements,
    unmet_market_requirements,
    validate_specification,
    verify_specification_id,
)
from alphalab.lifecycle.state import LifecycleState
from alphalab.lifecycle.strategy_version import (
    StrategyPromotionRecord,
    StrategyVersion,
    StrategyVersionRegistry,
    get_strategy_version,
    latest_strategy_version,
    list_strategy_versions,
    register_strategy_version,
    replace_strategy_version,
    strategy_names,
)
from alphalab.lifecycle.tolerance import Tolerance, ToleranceOutcome
from alphalab.lifecycle.views import (
    active_model_version,
    active_strategy_version,
    approvals_of,
    environments_running,
    evidence_for,
    governance_log,
    live_environments,
)

__all__ = [
    "ADAPTIVE_SETTING_PREFIX",
    "BROKER_STATUS_EQUIVALENTS",
    "CERTIFICATION_REPORT_SCHEME",
    "COMPONENT_EVIDENCE",
    "COMPONENT_MODEL",
    "COMPONENT_RUN",
    "COMPONENT_STRATEGY",
    "DEPLOYABLE_STAGES",
    "DEPLOYMENT_SPECIFICATION_SCHEME",
    "INITIAL_STAGE",
    "LEGAL_PROGRESSION_TRANSITIONS",
    "LIFECYCLE_PERMISSIONS",
    "MONETARY_METRICS",
    "NO_DEPENDENCIES",
    "PAUSABLE_STAGES",
    "PERMISSION_APPROVE",
    "PERMISSION_DEPLOY",
    "PERMISSION_PROMOTE",
    "PERMISSION_RETIRE",
    "PERMISSION_ROLLBACK",
    "PORTABILITY_REPORT_SCHEME",
    "PROGRESSION_MODEL_STAGES",
    "REPRODUCIBILITY_MANIFEST_SCHEME",
    "STAGEABLE_MODEL_STAGES",
    "STRATEGY_FINGERPRINT_SCHEME",
    "STRATEGY_SOURCE_SCHEME",
    "UNDECLARED_DEPENDENCIES",
    "AdaptiveReplayAssessment",
    "AlignmentKey",
    "ApprovalRecord",
    "BrokerCapabilities",
    "BrokerRequirements",
    "CapitalPolicy",
    "CertificationEvidence",
    "CertificationProperty",
    "CertificationReport",
    "CertificationStatus",
    "CodeIdentity",
    "ComparisonEntry",
    "ComparisonMetric",
    "ComparisonOutcome",
    "ComparisonSource",
    "DatasetAssumption",
    "DatasetProvenanceView",
    "DependencyCompleteness",
    "DependencyManifest",
    "DependencyPin",
    "DeploymentRef",
    "DeploymentSpecification",
    "EngineIdentity",
    "EnvironmentPortability",
    "ExecutionObservation",
    "ExternalInput",
    "ExternalRequirement",
    "FillObservation",
    "Governance",
    "GovernedAct",
    "HealthCategory",
    "HealthFinding",
    "HealthReport",
    "HealthSeverity",
    "HealthStatus",
    "LifecycleError",
    "LifecycleInputError",
    "LifecycleState",
    "LifecycleTransitionError",
    "MarketAvailability",
    "MarketRequirements",
    "MeasurementBasis",
    "MetricThreshold",
    "Mismatch",
    "MismatchCategory",
    "ModelRef",
    "PairComparison",
    "PortabilityReport",
    "PortabilityRequirement",
    "PortabilityStatus",
    "PropertyAssessment",
    "ReconciliationTolerances",
    "ReproducibilityAssessment",
    "ReproducibilityManifest",
    "RequirementCheck",
    "RequirementOutcome",
    "RerunOutcome",
    "ResearchConfiguration",
    "ResourceBudget",
    "ResourceMeasurement",
    "ResourceMetric",
    "ResultKind",
    "RunAuthorization",
    "RunDigest",
    "RunObservations",
    "RunPlan",
    "RuntimeObservation",
    "RuntimeProfile",
    "RuntimeRequirements",
    "SeedRole",
    "SourceBytesView",
    "StageTransition",
    "StateExpectation",
    "StateReconciliation",
    "StrategyFingerprint",
    "StrategyLifecycleStage",
    "StrategyProgression",
    "StrategyPromotionRecord",
    "StrategyVersion",
    "StrategyVersionRef",
    "StrategyVersionRegistry",
    "StudyIdentity",
    "SymbolMapping",
    "TargetEnvironment",
    "ThreeWayComparison",
    "Tolerance",
    "ToleranceOutcome",
    "TradeObservation",
    "UnevaluatedCategory",
    "UnreconciledArea",
    "ValidationEvidence",
    "ValidationMethod",
    "ValidationOutcome",
    "ValidationPolicy",
    "VersionedDataset",
    "active_model_version",
    "active_strategy_version",
    "advance_progression",
    "approval_for",
    "approvals_for",
    "approvals_of",
    "approve_deployment",
    "archive_progression",
    "assess_adaptive_replay",
    "assess_reproducibility",
    "authorize_run",
    "begin_progression",
    "build_evidence",
    "build_fingerprint",
    "build_specification",
    "canonical_fingerprint_key",
    "canonical_manifest_key",
    "certify_strategy",
    "code_identity_for",
    "compare_expected_paper_live",
    "compare_runs",
    "dataset_assumption_from",
    "deploy_strategy_version",
    "derive_manifest_id",
    "derive_strategy_fingerprint",
    "differing_parameters",
    "digest_run",
    "environments_running",
    "evaluate_health",
    "evaluate_policy",
    "evaluate_portability",
    "evidence_for",
    "evidence_from_backtest",
    "evidence_from_research",
    "evidence_from_study",
    "evidence_id_for",
    "external_requirements",
    "fingerprint_differences",
    "fingerprint_for_version",
    "get_strategy_version",
    "governance_log",
    "illegal_progression_move",
    "latest_strategy_version",
    "list_strategy_versions",
    "live_environments",
    "manifest_for_run",
    "manifest_for_study",
    "manifest_gaps",
    "normalize_distribution_name",
    "observation_from_live_run",
    "observations_from_backtest",
    "observations_from_broker",
    "parse_ref",
    "pause_progression",
    "progression_conflicts",
    "promote_strategy_version",
    "reconcile_execution_state",
    "record_evidence",
    "record_stage_change",
    "register_model_version",
    "register_strategy",
    "register_strategy_version",
    "release_manifest",
    "replace_strategy_version",
    "research_configuration",
    "research_configuration_for_study",
    "research_configuration_with_adaptive",
    "resource_counts",
    "resume_progression",
    "resume_target",
    "retire_strategy_version",
    "rollback_environment",
    "run_plan",
    "running_engine",
    "source_digest",
    "specification_for_version",
    "specification_id_for",
    "strategy_names",
    "unmet_broker_requirements",
    "unmet_market_requirements",
    "validate_specification",
    "validate_strategy_version",
    "verify_certification_report",
    "verify_evidence_id",
    "verify_fingerprint",
    "verify_manifest",
    "verify_portability_report",
    "verify_specification_id",
]
