"""AlphaLab Experiment Tracking.

Experiment history, metrics, parameters, and versioning.

`alphalab.studio` already defines `ExperimentResult`, a `StrategyStudioState.experiments`
field, and an `experiment_summary()` reader -- but grepping the whole package for
"experiments[" turns up zero writes anywhere. `studio_bridge.record_experiment` is
the missing write path, completing that gap using `ExperimentResult` exactly as
already defined, not replacing it.

Beyond that: `ExperimentResult` supports only float parameters and a single final
metric, with no history and no versioning. `ExperimentRun`/`ExperimentTracker`
provide multi-metric history tracking (a full sequence of logged values per metric,
not just the latest -- training loss per epoch, reward per episode), mixed-type
parameters, and lineage tracking for re-runs of the same logical experiment.
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
from alphalab.experiment_tracking.studio_bridge import ExperimentRecorded, record_experiment
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
    "ExperimentRecorded",
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
    "record_experiment",
    "start_run",
    "version_number",
]
