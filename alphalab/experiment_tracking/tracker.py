"""Rich experiment run tracking: multi-metric histories, mixed-type parameters.

alphalab.studio.results.ExperimentResult supports only float-valued parameters and
a single final target_metric -- fine for a simple hyperparameter-to-score record,
but not for tracking a training run's metric HISTORY (loss per epoch from
alphalab.deep_learning.train_network, or reward per episode from
alphalab.reinforcement_learning), or hyperparameters that aren't just floats (an
optimizer name, a boolean flag). ExperimentRun and ExperimentTracker provide that,
independently of alphalab.studio -- see studio_bridge.py for the integration point
if the simpler studio.ExperimentResult shape is what's specifically wanted instead.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum, auto

from alphalab.common.ids import new_id
from alphalab.experiment_tracking.exceptions import ExperimentTrackingInputError

ParamValue = str | int | float | bool


class RunStatus(Enum):
    """Lifecycle states for an experiment run."""

    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class ExperimentRun:
    """A single experiment run's identity, parameters, and metric history.

    Attributes:
        run_id: Unique identifier for this run.
        name: Human-readable name, e.g. "linreg_l2_sweep". Versions of the same
            logical experiment share a name -- see
            alphalab.experiment_tracking.versioning.
        status: Current lifecycle status.
        parameters: Hyperparameters/configuration this run was started with,
            fixed at start_run and never mutated afterward.
        metrics: Named metric histories -- each value is the full sequence of
            logged values for that metric, in the order logged, not just the
            latest. Training loss per epoch, reward per episode, etc. all fit
            naturally as one growing tuple per metric name.
        parent_run_id: The run this one is a new version of, if any.
        tags: Free-form labels for filtering/grouping.
        created_at: Unix timestamp the run started.
        completed_at: Unix timestamp the run finished, if it has.
    """

    run_id: str
    name: str
    status: RunStatus
    parameters: Mapping[str, ParamValue]
    metrics: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    parent_run_id: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    completed_at: float | None = None


@dataclass(frozen=True, slots=True)
class ExperimentTracker:
    """Immutable collection of every experiment run."""

    runs: Mapping[str, ExperimentRun] = field(default_factory=dict)


def start_run(
    tracker: ExperimentTracker,
    name: str,
    parameters: Mapping[str, ParamValue],
    timestamp: float,
    parent_run_id: str | None = None,
    tags: Mapping[str, str] | None = None,
) -> tuple[ExperimentTracker, str]:
    """Starts a new experiment run in RUNNING status.

    Raises:
        ExperimentTrackingInputError: If name is empty, or parent_run_id is given
            but doesn't exist in tracker.
    """
    if not name.strip():
        raise ExperimentTrackingInputError("name cannot be empty.")
    if parent_run_id is not None and parent_run_id not in tracker.runs:
        raise ExperimentTrackingInputError(f"parent_run_id '{parent_run_id}' not found.")

    run_id = str(new_id())
    run = ExperimentRun(
        run_id=run_id,
        name=name,
        status=RunStatus.RUNNING,
        parameters=dict(parameters),
        parent_run_id=parent_run_id,
        tags=dict(tags) if tags else {},
        created_at=timestamp,
    )
    new_runs = dict(tracker.runs)
    new_runs[run_id] = run
    return ExperimentTracker(runs=new_runs), run_id


def log_metric(
    tracker: ExperimentTracker, run_id: str, metric_name: str, value: float
) -> ExperimentTracker:
    """Appends a value to a named metric's history for a run.

    Raises:
        ExperimentTrackingInputError: If run_id doesn't exist, or the run is not
            RUNNING.
    """
    run = tracker.runs.get(run_id)
    if run is None:
        raise ExperimentTrackingInputError(f"Run '{run_id}' not found.")
    if run.status is not RunStatus.RUNNING:
        raise ExperimentTrackingInputError(
            f"Cannot log a metric to run '{run_id}' with status {run.status.name}; "
            "only RUNNING runs accept metrics."
        )

    new_metrics = dict(run.metrics)
    existing = new_metrics.get(metric_name, ())
    new_metrics[metric_name] = (*existing, value)
    updated_run = replace(run, metrics=new_metrics)

    new_runs = dict(tracker.runs)
    new_runs[run_id] = updated_run
    return ExperimentTracker(runs=new_runs)


def log_metrics(
    tracker: ExperimentTracker, run_id: str, metrics: Mapping[str, float]
) -> ExperimentTracker:
    """Logs multiple metrics for a run in one call."""
    current = tracker
    for name, value in metrics.items():
        current = log_metric(current, run_id, name, value)
    return current


def _transition(
    tracker: ExperimentTracker, run_id: str, new_status: RunStatus, timestamp: float
) -> ExperimentTracker:
    run = tracker.runs.get(run_id)
    if run is None:
        raise ExperimentTrackingInputError(f"Run '{run_id}' not found.")
    if run.status is not RunStatus.RUNNING:
        raise ExperimentTrackingInputError(
            f"Run '{run_id}' has status {run.status.name}, not RUNNING; cannot transition."
        )
    updated_run = replace(run, status=new_status, completed_at=timestamp)
    new_runs = dict(tracker.runs)
    new_runs[run_id] = updated_run
    return ExperimentTracker(runs=new_runs)


def complete_run(tracker: ExperimentTracker, run_id: str, timestamp: float) -> ExperimentTracker:
    """Marks a RUNNING run as COMPLETED.

    Raises:
        ExperimentTrackingInputError: If run_id doesn't exist or is not RUNNING.
    """
    return _transition(tracker, run_id, RunStatus.COMPLETED, timestamp)


def fail_run(tracker: ExperimentTracker, run_id: str, timestamp: float) -> ExperimentTracker:
    """Marks a RUNNING run as FAILED.

    Raises:
        ExperimentTrackingInputError: If run_id doesn't exist or is not RUNNING.
    """
    return _transition(tracker, run_id, RunStatus.FAILED, timestamp)
