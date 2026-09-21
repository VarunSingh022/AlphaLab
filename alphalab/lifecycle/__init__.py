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
"""

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
    "BROKER_STATUS_EQUIVALENTS",
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
    "PAUSABLE_STAGES",
    "PERMISSION_APPROVE",
    "PERMISSION_DEPLOY",
    "PERMISSION_PROMOTE",
    "PERMISSION_RETIRE",
    "PERMISSION_ROLLBACK",
    "PROGRESSION_MODEL_STAGES",
    "STAGEABLE_MODEL_STAGES",
    "AlignmentKey",
    "ApprovalRecord",
    "BrokerCapabilities",
    "BrokerRequirements",
    "CapitalPolicy",
    "ComparisonEntry",
    "ComparisonMetric",
    "ComparisonOutcome",
    "ComparisonSource",
    "DatasetAssumption",
    "DeploymentRef",
    "DeploymentSpecification",
    "ExecutionObservation",
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
    "MetricThreshold",
    "Mismatch",
    "MismatchCategory",
    "ModelRef",
    "PairComparison",
    "ReconciliationTolerances",
    "RunAuthorization",
    "RunObservations",
    "RunPlan",
    "RuntimeObservation",
    "RuntimeRequirements",
    "StageTransition",
    "StateExpectation",
    "StateReconciliation",
    "StrategyLifecycleStage",
    "StrategyProgression",
    "StrategyPromotionRecord",
    "StrategyVersion",
    "StrategyVersionRef",
    "StrategyVersionRegistry",
    "SymbolMapping",
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
    "active_model_version",
    "active_strategy_version",
    "advance_progression",
    "approval_for",
    "approvals_for",
    "approvals_of",
    "approve_deployment",
    "archive_progression",
    "authorize_run",
    "begin_progression",
    "build_evidence",
    "build_specification",
    "compare_expected_paper_live",
    "compare_runs",
    "dataset_assumption_from",
    "deploy_strategy_version",
    "environments_running",
    "evaluate_health",
    "evaluate_policy",
    "evidence_for",
    "evidence_from_backtest",
    "evidence_from_research",
    "evidence_from_study",
    "evidence_id_for",
    "get_strategy_version",
    "governance_log",
    "illegal_progression_move",
    "latest_strategy_version",
    "list_strategy_versions",
    "live_environments",
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
    "resume_progression",
    "resume_target",
    "retire_strategy_version",
    "rollback_environment",
    "run_plan",
    "specification_for_version",
    "specification_id_for",
    "strategy_names",
    "unmet_broker_requirements",
    "unmet_market_requirements",
    "validate_specification",
    "validate_strategy_version",
    "verify_evidence_id",
    "verify_specification_id",
]
