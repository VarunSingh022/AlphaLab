"""Pure queries across the lifecycle.

Every answer here is derived from the deployment ledger rather than from a flag
kept alongside it. "Which strategy version is live in paper?" has one source,
and these functions read it.
"""

from __future__ import annotations

from alphalab.deployment_manager.deployment import active_release, deployed_environments
from alphalab.lifecycle.evidence import ValidationEvidence
from alphalab.lifecycle.identity import COMPONENT_MODEL, COMPONENT_STRATEGY, ModelRef, parse_ref
from alphalab.lifecycle.identity import StrategyVersionRef as _Ref
from alphalab.lifecycle.state import LifecycleState
from alphalab.lifecycle.strategy_version import StrategyVersion, get_strategy_version

__all__ = [
    "active_model_version",
    "active_strategy_version",
    "environments_running",
    "evidence_for",
    "live_environments",
]


def live_environments(state: LifecycleState) -> tuple[str, ...]:
    """Every environment that has ever been deployed to, in first-seen order."""

    return deployed_environments(state.deployments)


def active_strategy_version(state: LifecycleState, environment: str) -> StrategyVersion | None:
    """The strategy version currently running in ``environment``, or ``None``.

    Read from the release active in that environment, which is the ledger's
    answer and the only one.
    """
    release = active_release(state.deployments, environment)
    if release is None:
        return None
    reference = release.components.get(COMPONENT_STRATEGY)
    if reference is None:
        return None
    name, version = parse_ref(reference)
    return get_strategy_version(state.strategies, name, version)


def active_model_version(state: LifecycleState, environment: str) -> ModelRef | None:
    """The model version backing what runs in ``environment``, or ``None``.

    ``None`` both when nothing is deployed there and when what is deployed runs
    no model -- a strategy version need not have one.
    """
    release = active_release(state.deployments, environment)
    if release is None:
        return None
    reference = release.components.get(COMPONENT_MODEL)
    if reference is None:
        return None
    name, version = parse_ref(reference)
    return ModelRef(name, version)


def environments_running(state: LifecycleState, name: str, version: int) -> tuple[str, ...]:
    """Every environment in which strategy ``name`` version ``version`` is active.

    Empty when it is deployed nowhere. One version can be active in several
    environments at once, which is why a version cannot answer "am I live?" on
    its own.
    """
    reference = str(_Ref(name, version))
    running = []
    for environment in deployed_environments(state.deployments):
        release = active_release(state.deployments, environment)
        if release is not None and release.components.get(COMPONENT_STRATEGY) == reference:
            running.append(environment)
    return tuple(running)


def evidence_for(state: LifecycleState, name: str, version: int) -> ValidationEvidence | None:
    """The evidence that supported a strategy version's promotion, if any."""

    strategy = get_strategy_version(state.strategies, name, version)
    if strategy.evidence_id is None:
        return None
    return state.evidence.get(strategy.evidence_id)
