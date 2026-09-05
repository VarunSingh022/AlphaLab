"""Entering the lifecycle: runs, model versions and strategy versions.

The registries underneath accept references without checking them.
:attr:`~alphalab.model_registry.registry.ModelVersion.run_id` is a bare
``str | None``, so a model version can name an experiment run that does not
exist, was never completed, or belongs to a different tracker entirely -- and
nothing notices until someone tries to answer "which run produced this?" and
finds it cannot be answered.

Each package is right not to check on its own: a model registry that validated
run ids would have to depend on the experiment tracker, and the two are useful
apart. The lifecycle holds both, so it is where a reference can be checked, and
these are the entry points that check it.

The checks are deliberately narrow. A run must exist and must have finished --
a model produced by a run that is still going, or that failed, is a model whose
provenance is a work in progress. Nothing here inspects metrics or decides
whether the model is any good; that is what evidence and a policy are for.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from alphalab.experiment_tracking.tracker import RunStatus
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.identity import ModelRef, StrategyVersionRef
from alphalab.lifecycle.state import LifecycleState
from alphalab.lifecycle.strategy_version import register_strategy_version
from alphalab.model_registry.registry import ArtifactRef, ParamValue, get_version, register_model
from alphalab.studio.strategy import StrategyDefinition

__all__ = ["register_model_version", "register_strategy"]


def _require_completed_run(state: LifecycleState, run_id: str | None) -> None:
    """Raise unless ``run_id`` is absent or names a completed run."""

    if run_id is None:
        return
    run = state.experiments.runs.get(run_id)
    if run is None:
        raise LifecycleInputError(
            f"No experiment run '{run_id}' is recorded; a version cannot cite a run "
            "the lifecycle does not hold."
        )
    if run.status is not RunStatus.COMPLETED:
        raise LifecycleInputError(
            f"Experiment run '{run_id}' has status {run.status.name}, not COMPLETED; "
            "a version's provenance cannot be a run that has not finished."
        )


def register_model_version(
    state: LifecycleState,
    name: str,
    model: object,
    timestamp: float,
    run_id: str | None = None,
    metrics: Mapping[str, float] | None = None,
    parameters: Mapping[str, ParamValue] | None = None,
    tags: Mapping[str, str] | None = None,
    artifact: ArtifactRef | None = None,
) -> tuple[LifecycleState, ModelRef]:
    """Registers a model version, checking the run it cites.

    Raises:
        LifecycleInputError: If ``run_id`` is given and names no run, or names
            one that has not completed.
        ModelRegistryInputError: If ``name`` is blank.
    """
    _require_completed_run(state, run_id)
    registry, version = register_model(
        state.models,
        name,
        model,
        timestamp,
        metrics=metrics,
        parameters=parameters,
        tags=tags,
        run_id=run_id,
        artifact=artifact,
    )
    return replace(state, models=registry), ModelRef(version.name, version.version)


def register_strategy(
    state: LifecycleState,
    name: str,
    definition: StrategyDefinition,
    timestamp: float,
    model: ModelRef | None = None,
    run_id: str | None = None,
    tags: Mapping[str, str] | None = None,
) -> tuple[LifecycleState, StrategyVersionRef]:
    """Registers a strategy version, checking the model and run it cites.

    The definition is the canonical
    :class:`~alphalab.studio.strategy.StrategyDefinition`, so a candidate from
    :func:`~alphalab.research_assistant.studio_bridge.to_strategy_definition`
    reaches a strategy version without a second parameter format in between.

    Raises:
        LifecycleInputError: If ``name`` is blank or contains ``"@"``, if
            ``run_id`` names no completed run, or if ``model`` names a version
            the registry does not hold.
    """
    _require_completed_run(state, run_id)
    if model is not None:
        get_version(state.models, model.name, model.version)

    registry, version = register_strategy_version(
        state.strategies, name, definition, timestamp, model=model, run_id=run_id, tags=tags
    )
    return replace(state, strategies=registry), StrategyVersionRef(version.name, version.version)
