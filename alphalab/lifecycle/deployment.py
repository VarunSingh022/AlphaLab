"""Deploying a strategy version, and rolling one back.

This is where the lifecycle meets :mod:`alphalab.deployment_manager`. What a
deployment *is* stays that package's answer -- a checksummed release package
plus an append-only ledger of which release is active in which environment. What
this module adds is the translation in both directions:

* forward, a staged strategy version becomes a release manifest whose components
  are typed references rendered canonically (``"ma-crossover@2"``,
  ``"momentum@3"``) rather than strings a caller assembled by hand, and its
  deployment moves the version to ``PRODUCTION``;
* backward, the release active in an environment is read back into the strategy
  version and model version it names, which is how
  :mod:`alphalab.lifecycle.views` answers "what is running here?" without a
  second flag to keep true.

Scope
-----
Deployment here is a lifecycle fact, not an operation on a machine. Making a
release active in ``"live-eu"`` records that ``"live-eu"`` should be running that
strategy version; it starts no process, opens no connection and reaches no
venue. AlphaLab has no transport to any venue at all (see ADR-0012), and a
deployment manager that pretended otherwise would be the one part of this
release that could not be tested.

Rollback is the same fact in reverse, and it is deterministic: the version to
return to is the one the ledger says was active immediately before, and the
ledger is append-only, so the answer does not depend on when it is asked.
"""

from __future__ import annotations

from dataclasses import replace

from alphalab.deployment_manager.deployment import deploy as deploy_release
from alphalab.deployment_manager.exceptions import DeploymentManagerInputError
from alphalab.deployment_manager.packaging import ReleasePackage
from alphalab.deployment_manager.releases import get_release, register_release
from alphalab.deployment_manager.rollback import previous_release
from alphalab.deployment_manager.rollback import rollback as rollback_release
from alphalab.lifecycle.exceptions import LifecycleInputError, LifecycleTransitionError
from alphalab.lifecycle.identity import (
    COMPONENT_EVIDENCE,
    COMPONENT_MODEL,
    COMPONENT_RUN,
    COMPONENT_STRATEGY,
    DeploymentRef,
)
from alphalab.lifecycle.promotion import record_stage_change
from alphalab.lifecycle.state import LifecycleState
from alphalab.lifecycle.strategy_version import StrategyVersion, get_strategy_version
from alphalab.lifecycle.views import active_strategy_version, environments_running
from alphalab.model_registry.deployment import set_deployment_metadata
from alphalab.model_registry.registry import DeploymentMetadata, ModelStage, get_version

__all__ = [
    "DEPLOYABLE_STAGES",
    "deploy_strategy_version",
    "environments_running",
    "release_manifest",
    "rollback_environment",
]

#: Stages a strategy version can be deployed from. ``PRODUCTION`` is included
#: because a version already live in one environment may be rolled out to
#: another; ``NONE`` and ``ARCHIVED`` are not, because neither has passing
#: evidence standing behind it right now.
DEPLOYABLE_STAGES = (ModelStage.STAGING, ModelStage.PRODUCTION)


def release_manifest(strategy: StrategyVersion) -> tuple[dict[str, str], dict[str, str]]:
    """Returns the ``(components, config)`` a strategy version packages as.

    The components are the typed references this version holds, rendered
    canonically -- the strategy version itself always, and the model, the
    experiment run and the evidence when it has them. A key is absent rather
    than empty when there is nothing to name, so a manifest never claims a
    reference that does not exist.

    The config is the strategy definition's parameters, rendered with ``repr``
    so a float round-trips exactly. Both feed the release checksum, so a
    manifest that is edited afterwards stops verifying.
    """
    components = {COMPONENT_STRATEGY: str(strategy.ref)}
    if strategy.model is not None:
        components[COMPONENT_MODEL] = str(strategy.model)
    if strategy.run_id is not None:
        components[COMPONENT_RUN] = strategy.run_id
    if strategy.evidence_id is not None:
        components[COMPONENT_EVIDENCE] = strategy.evidence_id

    config = {key: repr(value) for key, value in strategy.definition.parameters.items()}
    return components, config


def _release_for(
    state: LifecycleState, strategy: StrategyVersion, timestamp: float
) -> tuple[LifecycleState, ReleasePackage]:
    """The release package standing for this strategy version, registering it once.

    A strategy version is immutable, so its manifest is fixed and one release
    package stands for it however many environments it reaches. Registering a
    fresh, identical package per environment would fill the release line with
    duplicates that differ only in version number.
    """
    reference = str(strategy.ref)
    existing = state.releases.get(reference)
    if existing is not None:
        return state, get_release(state.deployments, strategy.name, existing)

    components, config = release_manifest(strategy)
    manager, package = register_release(
        state.deployments, strategy.name, components, config, timestamp
    )
    return (
        replace(
            state, deployments=manager, releases=state.releases.set(reference, package.version)
        ),
        package,
    )


def _note_model_deployment(
    state: LifecycleState,
    strategy: StrategyVersion,
    environment: str,
    timestamp: float,
    deployed_by: str,
    notes: str,
) -> LifecycleState:
    """Record on the model version where the strategy running it was deployed.

    ``ModelVersion.deployment`` is a note, and the ledger is the fact. This
    derives the note from the deployment that just happened rather than letting
    a caller assert one, which is what kept the two able to disagree.
    """
    if strategy.model is None:
        return state
    model = get_version(state.models, strategy.model.name, strategy.model.version)
    if model.stage not in DEPLOYABLE_STAGES:
        return state
    metadata = DeploymentMetadata(
        environment=environment,
        deployed_at=timestamp,
        deployed_by=deployed_by,
        notes=notes,
    )
    return replace(
        state,
        models=set_deployment_metadata(
            state.models, strategy.model.name, strategy.model.version, metadata
        ),
    )


def _retire_if_idle(
    state: LifecycleState, outgoing: StrategyVersion | None, reason: str, timestamp: float
) -> LifecycleState:
    """Archive the version a deployment displaced, unless it still runs elsewhere."""

    if outgoing is None or outgoing.stage is not ModelStage.PRODUCTION:
        return state
    if environments_running(state, outgoing.name, outgoing.version):
        return state
    return record_stage_change(state, outgoing, ModelStage.ARCHIVED, reason, timestamp)


def deploy_strategy_version(
    state: LifecycleState,
    name: str,
    version: int,
    environment: str,
    timestamp: float,
    deployed_by: str = "",
    notes: str = "",
) -> tuple[LifecycleState, DeploymentRef]:
    """Makes a strategy version the one active in ``environment``.

    The version moves to ``PRODUCTION``, and whichever version it displaced is
    archived -- unless that version is still active in another environment, in
    which case it stays in ``PRODUCTION`` where it belongs.

    Raises:
        LifecycleInputError: If the version is unknown or ``environment`` is
            blank.
        LifecycleTransitionError: If the version is not staged, or is already
            the active version in ``environment``. A redeploy of what is already
            running is refused rather than silently appending a second identical
            ledger entry; nothing is registered before the refusal.
    """
    if not environment.strip():
        raise LifecycleInputError("environment cannot be empty.")

    strategy = get_strategy_version(state.strategies, name, version)
    if strategy.stage not in DEPLOYABLE_STAGES:
        raise LifecycleTransitionError(
            f"Strategy '{name}' version {version} is in stage {strategy.stage.name}; "
            f"only a "
            f"{' or '.join(stage.name for stage in DEPLOYABLE_STAGES)} version can be "
            "deployed. Promote it on passing evidence first."
        )

    incumbent = active_strategy_version(state, environment)
    if incumbent is not None and incumbent.ref == strategy.ref:
        raise LifecycleTransitionError(
            f"Strategy '{name}' version {version} is already active in '{environment}'."
        )

    deployed, package = _release_for(state, strategy, timestamp)
    deployed = replace(
        deployed,
        deployments=deploy_release(
            deployed.deployments, package.name, package.version, environment, timestamp
        ),
    )

    if strategy.stage is not ModelStage.PRODUCTION:
        deployed = record_stage_change(
            deployed,
            strategy,
            ModelStage.PRODUCTION,
            reason=f"deployed to '{environment}'",
            timestamp=timestamp,
        )
    deployed = _retire_if_idle(
        deployed, incumbent, f"replaced in '{environment}' by {strategy.ref}", timestamp
    )
    deployed = _note_model_deployment(
        deployed, strategy, environment, timestamp, deployed_by, notes
    )

    return deployed, DeploymentRef(environment, package.name, package.version)


def rollback_environment(
    state: LifecycleState,
    environment: str,
    timestamp: float,
    deployed_by: str = "",
    notes: str = "",
) -> tuple[LifecycleState, DeploymentRef]:
    """Returns ``environment`` to the strategy version that ran before the current one.

    Deterministic: the version restored is the one the append-only ledger names
    as previously active, so the same state and environment always roll back to
    the same place. The restored version returns to ``PRODUCTION`` and the one
    being taken down is archived, unless it is still running somewhere else.

    Raises:
        LifecycleInputError: If ``environment`` has never been deployed to, or
            has had only one deployment and so has nothing to return to.
    """
    target = previous_release(state.deployments, environment)
    if target is None:
        raise LifecycleInputError(
            f"Environment '{environment}' has no previous deployment to roll back to."
        )

    outgoing = active_strategy_version(state, environment)
    try:
        manager = rollback_release(state.deployments, environment, timestamp)
    except DeploymentManagerInputError as error:  # pragma: no cover - guarded above
        raise LifecycleInputError(str(error)) from error

    rolled = replace(state, deployments=manager)
    restored = active_strategy_version(rolled, environment)
    if restored is None:
        raise LifecycleInputError(
            f"The release restored in '{environment}' names no strategy version; it was "
            "not built by alphalab.lifecycle and cannot be rolled back through it."
        )

    if restored.stage is not ModelStage.PRODUCTION:
        rolled = record_stage_change(
            rolled,
            restored,
            ModelStage.PRODUCTION,
            reason=f"rolled back in '{environment}' from {outgoing.ref if outgoing else 'nothing'}",
            timestamp=timestamp,
        )
    rolled = _retire_if_idle(
        rolled, outgoing, f"rolled back in '{environment}' to {restored.ref}", timestamp
    )
    rolled = _note_model_deployment(rolled, restored, environment, timestamp, deployed_by, notes)

    name, release_version = target
    return rolled, DeploymentRef(environment, name, release_version)
