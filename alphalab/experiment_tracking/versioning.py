"""Experiment lineage: tracking that one run is a new version of another."""

from collections.abc import Mapping

from alphalab.experiment_tracking.exceptions import ExperimentTrackingInputError
from alphalab.experiment_tracking.tracker import (
    ExperimentRun,
    ExperimentTracker,
    ParamValue,
    start_run,
)


def new_version(
    tracker: ExperimentTracker,
    parent_run_id: str,
    parameters: Mapping[str, ParamValue],
    timestamp: float,
) -> tuple[ExperimentTracker, str]:
    """Starts a new run explicitly versioned as a successor of parent_run_id.

    The new run reuses the parent's name -- versions of the same experiment share
    an identity, distinguished by lineage position, not by name.

    Raises:
        ExperimentTrackingInputError: If parent_run_id doesn't exist.
    """
    parent = tracker.runs.get(parent_run_id)
    if parent is None:
        raise ExperimentTrackingInputError(f"parent_run_id '{parent_run_id}' not found.")
    return start_run(tracker, parent.name, parameters, timestamp, parent_run_id=parent_run_id)


def lineage(tracker: ExperimentTracker, run_id: str) -> tuple[ExperimentRun, ...]:
    """Returns the full version chain for a run, oldest first, ending with run_id.

    Raises:
        ExperimentTrackingInputError: If run_id doesn't exist, or a cycle is
            detected in the parent chain -- a data integrity problem that should
            never occur given start_run/new_version only ever point parent_run_id
            at an already-existing run, but checked explicitly rather than risking
            an infinite loop if it somehow did.
    """
    if run_id not in tracker.runs:
        raise ExperimentTrackingInputError(f"Run '{run_id}' not found.")

    chain = []
    current_id: str | None = run_id
    seen: set[str] = set()
    while current_id is not None:
        if current_id in seen:
            raise ExperimentTrackingInputError(
                f"Cycle detected in lineage chain at run '{current_id}'."
            )
        seen.add(current_id)
        run = tracker.runs[current_id]
        chain.append(run)
        current_id = run.parent_run_id

    return tuple(reversed(chain))


def version_number(tracker: ExperimentTracker, run_id: str) -> int:
    """Returns this run's 1-based position in its own lineage chain."""
    return len(lineage(tracker, run_id))
