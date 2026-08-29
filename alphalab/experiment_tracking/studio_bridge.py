"""Completes a real, confirmed gap in alphalab.studio.

alphalab.studio.results.ExperimentResult, StrategyStudioState.experiments, and
views.experiment_summary all already exist -- but nothing anywhere in that package
ever writes an ExperimentResult into state.experiments. Confirmed by grepping the
whole package for "experiments[": zero matches. `record_experiment` is the missing
write path, using ExperimentResult exactly as already defined there, not a
replacement for it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from alphalab.common.ids import new_id
from alphalab.studio.events import StudioEvent
from alphalab.studio.results import ExperimentResult
from alphalab.studio.state import StrategyStudioState
from alphalab.studio.validation import validate_project_exists


@dataclass(frozen=True, slots=True)
class ExperimentRecorded(StudioEvent):
    """The one missing event type for experiment recording, extending StudioEvent
    rather than modifying alphalab.studio.events.
    """

    project_id: str
    experiment_id: str


def record_experiment(
    state: StrategyStudioState,
    project_id: str,
    parameters: Mapping[str, float],
    target_metric: float,
    timestamp: float,
) -> tuple[StrategyStudioState, str]:
    """Records a completed experiment into StrategyStudioState.experiments.

    Raises:
        StudioValidationError: If project_id does not exist.
    """
    validate_project_exists(state, project_id)

    experiment_id = str(new_id())
    result = ExperimentResult(
        experiment_id=experiment_id,
        project_id=project_id,
        parameters=dict(parameters),
        target_metric=target_metric,
        timestamp=timestamp,
    )

    new_experiments = dict(state.experiments)
    new_experiments[experiment_id] = result
    event = ExperimentRecorded(str(new_id()), timestamp, project_id, experiment_id)
    new_state = replace(state, experiments=new_experiments, events=(*state.events, event))
    return new_state, experiment_id
