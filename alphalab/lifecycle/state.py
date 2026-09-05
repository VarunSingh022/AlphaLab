"""One state for the whole lifecycle.

The four packages the lifecycle composes each own an immutable state of their
own, and each remains usable on its own. What they lacked was a value that
holds them together, so that "this experiment produced this model, which backs
this strategy version, which is deployed here" is one thing you can pass around,
serialize and compare -- the role
:class:`~alphalab.runtime.execution_pipeline.ExecutionPipelineState` plays for
the execution path.

Nothing is copied into this state. It holds the four registries as they are, and
adds only what none of them owned: the evidence store, and the record of which
release package stands for which strategy version.

Not to be confused with :class:`alphalab.strategy.state.LifecycleState`, which
is an enum naming the stages of a strategy *instance running inside a session*
-- created, subscribed, running, stopping. That is a different axis: a deployed
strategy version is started and stopped many times without its lifecycle stage
changing at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from alphalab.common.persistent_map import PersistentMap
from alphalab.deployment_manager.releases import DeploymentManager
from alphalab.experiment_tracking.tracker import ExperimentTracker
from alphalab.lifecycle.evidence import ValidationEvidence
from alphalab.lifecycle.strategy_version import StrategyVersionRegistry
from alphalab.model_registry.registry import ModelRegistry

__all__ = ["LifecycleState"]


@dataclass(frozen=True, slots=True)
class LifecycleState:
    """Immutable snapshot of every stage of the model and strategy lifecycle.

    Attributes:
        experiments: Research and training runs, their parameters and their
            metric histories.
        models: Registered model versions and their stage history.
        strategies: Registered strategy versions and their stage history.
        deployments: Release packages and the append-only ledger of what is
            active in which environment. This is the only answer to "what is
            live"; nothing else in this state duplicates it.
        evidence: Every recorded measurement, keyed by ``evidence_id``. A
            strategy version references the one that supported its promotion
            rather than embedding it, so the same run's evidence can support
            more than one promotion without being stored twice.
        releases: Rendered strategy-version reference -> the release version in
            that strategy's release line which packages it. A strategy version
            is immutable, so its manifest is fixed and one release stands for
            it however many environments it reaches. Without this, deploying
            one version to two environments would register two identical
            release packages.
    """

    experiments: ExperimentTracker = field(default_factory=ExperimentTracker)
    models: ModelRegistry = field(default_factory=ModelRegistry)
    strategies: StrategyVersionRegistry = field(default_factory=StrategyVersionRegistry)
    deployments: DeploymentManager = field(default_factory=DeploymentManager)
    evidence: PersistentMap[str, ValidationEvidence] = field(default_factory=PersistentMap)
    releases: PersistentMap[str, int] = field(default_factory=PersistentMap)

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, PersistentMap):
            object.__setattr__(self, "evidence", PersistentMap(self.evidence))
        if not isinstance(self.releases, PersistentMap):
            object.__setattr__(self, "releases", PersistentMap(self.releases))
