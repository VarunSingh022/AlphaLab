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
opens no connection, and reaches no venue -- AlphaLab has no transport to any
venue (ADR-0012). The registry references artifacts and stores no bytes; there
is no object store here.

The lifecycle sits *above* :class:`~alphalab.runtime.execution_pipeline.ExecutionPipeline`
and is not wired into it. A deployment names what should run; running it is the
execution path's job, and the two are joined by the caller.
"""

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
    evidence_id_for,
    verify_evidence_id,
)
from alphalab.lifecycle.exceptions import (
    LifecycleError,
    LifecycleInputError,
    LifecycleTransitionError,
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
from alphalab.lifecycle.promotion import (
    STAGEABLE_MODEL_STAGES,
    promote_strategy_version,
    record_evidence,
    record_stage_change,
    retire_strategy_version,
    validate_strategy_version,
)
from alphalab.lifecycle.registration import register_model_version, register_strategy
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
from alphalab.lifecycle.views import (
    active_model_version,
    active_strategy_version,
    environments_running,
    evidence_for,
    live_environments,
)

__all__ = [
    "COMPONENT_EVIDENCE",
    "COMPONENT_MODEL",
    "COMPONENT_RUN",
    "COMPONENT_STRATEGY",
    "DEPLOYABLE_STAGES",
    "STAGEABLE_MODEL_STAGES",
    "DeploymentRef",
    "LifecycleError",
    "LifecycleInputError",
    "LifecycleState",
    "LifecycleTransitionError",
    "MetricThreshold",
    "ModelRef",
    "StrategyPromotionRecord",
    "StrategyVersion",
    "StrategyVersionRef",
    "StrategyVersionRegistry",
    "ValidationEvidence",
    "ValidationMethod",
    "ValidationOutcome",
    "ValidationPolicy",
    "active_model_version",
    "active_strategy_version",
    "build_evidence",
    "deploy_strategy_version",
    "environments_running",
    "evaluate_policy",
    "evidence_for",
    "evidence_from_backtest",
    "evidence_from_research",
    "evidence_id_for",
    "get_strategy_version",
    "latest_strategy_version",
    "list_strategy_versions",
    "live_environments",
    "parse_ref",
    "promote_strategy_version",
    "record_evidence",
    "record_stage_change",
    "register_model_version",
    "register_strategy",
    "register_strategy_version",
    "release_manifest",
    "replace_strategy_version",
    "retire_strategy_version",
    "rollback_environment",
    "strategy_names",
    "validate_strategy_version",
    "verify_evidence_id",
]
