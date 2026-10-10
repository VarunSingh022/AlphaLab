"""AlphaLab Experiment Tracking.

Experiment history, metrics, parameters, and versioning.

`ExperimentRun`/`ExperimentTracker` provide multi-metric history tracking (a full
sequence of logged values per metric, not just the latest -- training loss per
epoch, reward per episode), mixed-type parameters, and lineage tracking for
re-runs of the same logical experiment. (Until v3.11 a `studio_bridge` wrote a
single-metric record into Strategy Studio's state; Studio is removed, and this
tracker is the one experiment record.)
"""

from alphalab.experiment_tracking.comparison import (
    best_metric_value,
    best_run,
    compare_runs,
    latest_metric_value,
)
from alphalab.experiment_tracking.exceptions import (
    ExperimentTrackingError,
    ExperimentTrackingInputError,
)
from alphalab.experiment_tracking.tracker import (
    ExperimentRun,
    ExperimentTracker,
    ParamValue,
    RunStatus,
    complete_run,
    fail_run,
    log_metric,
    log_metrics,
    start_run,
)
from alphalab.experiment_tracking.versioning import lineage, new_version, version_number

__all__ = [
    "ExperimentRun",
    "ExperimentTracker",
    "ExperimentTrackingError",
    "ExperimentTrackingInputError",
    "ParamValue",
    "RunStatus",
    "best_metric_value",
    "best_run",
    "compare_runs",
    "complete_run",
    "fail_run",
    "latest_metric_value",
    "lineage",
    "log_metric",
    "log_metrics",
    "new_version",
    "start_run",
    "version_number",
]
