"""Comparing and ranking experiment runs by a metric."""

from collections.abc import Mapping

from alphalab.experiment_tracking.exceptions import ExperimentTrackingInputError
from alphalab.experiment_tracking.tracker import ExperimentRun, ExperimentTracker


def latest_metric_value(run: ExperimentRun, metric_name: str) -> float | None:
    """Returns the most recently logged value of a metric, or None if never logged."""
    history = run.metrics.get(metric_name)
    if not history:
        return None
    return history[-1]


def best_metric_value(
    run: ExperimentRun, metric_name: str, higher_is_better: bool = True
) -> float | None:
    """Returns the best (max or min) value ever logged for a metric on a run."""
    history = run.metrics.get(metric_name)
    if not history:
        return None
    return max(history) if higher_is_better else min(history)


def best_run(
    tracker: ExperimentTracker,
    metric_name: str,
    higher_is_better: bool = True,
    use_best_value: bool = False,
) -> ExperimentRun | None:
    """Returns the run with the best value for a metric, among runs that logged it.

    By default compares each run's latest logged value (use_best_value=False); set
    use_best_value=True to compare each run's best-ever value instead -- e.g. to
    find peak validation accuracy across a noisy training run, not just its final
    epoch's value.

    Returns None if no run has logged this metric at all.
    """
    candidates = []
    for run in tracker.runs.values():
        value = (
            best_metric_value(run, metric_name, higher_is_better)
            if use_best_value
            else latest_metric_value(run, metric_name)
        )
        if value is not None:
            candidates.append((value, run))
    if not candidates:
        return None

    selector = max if higher_is_better else min
    _, winner = selector(candidates, key=lambda pair: pair[0])
    return winner


def compare_runs(
    tracker: ExperimentTracker, run_ids: tuple[str, ...], metric_name: str
) -> Mapping[str, float | None]:
    """Returns each run's latest value for a metric, keyed by run_id.

    Raises:
        ExperimentTrackingInputError: If run_ids is empty, or any id isn't found.
    """
    if not run_ids:
        raise ExperimentTrackingInputError("run_ids cannot be empty.")

    result: dict[str, float | None] = {}
    for run_id in run_ids:
        run = tracker.runs.get(run_id)
        if run is None:
            raise ExperimentTrackingInputError(f"Run '{run_id}' not found.")
        result[run_id] = latest_metric_value(run, metric_name)
    return result
