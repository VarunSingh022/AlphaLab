"""Rich experiment run tracking: multi-metric histories, mixed-type parameters.

A training run's metric HISTORY (loss per epoch from
alphalab.deep_learning.train_network, or reward per episode from
alphalab.reinforcement_learning) and hyperparameters that aren't just floats (an
optimizer name, a boolean flag) are what ExperimentRun and ExperimentTracker
record.

Container choice
----------------
A tracker's runs and a run's metric histories are grown one write at a time, and
every write returns a new immutable value. Copying a ``dict`` or rebuilding a
``tuple`` per write makes ``N`` writes cost ``O(N^2)``: at v2.3, logging 8000
values to one metric took 13x as long as logging 2000. They are now a
:class:`~alphalab.common.persistent_map.PersistentMap` and an
:class:`~alphalab.common.append_log.AppendOnlyLog`, which share structure instead
of copying, so both are O(1) amortized per write and the value semantics are
unchanged. Both containers accept and compare equal to the plain ``dict`` /
``tuple`` they replaced, so ``run.metrics["loss"] == (0.5, 0.3)`` still holds.

``metrics`` is a persistent map and not a plain ``dict`` because a run has two
independent growth axes, not one: the values logged to a metric, and the number
of distinct metric names. A run logging a value per feature, per asset or per
layer has thousands of the latter, and rebuilding a plain dict on every write
would be quadratic in exactly that -- which is what
``tests/regression/test_lifecycle_registry_complexity.py`` holds on both axes.

The cost is a constant one on the read side: resolving a key in a persistent map
is a chain probe rather than a hash lookup, so a reader that scans every run
(``comparison.best_run``) is slower than it was against plain dicts. That is the
trade, and it is the right way round: a reader stays linear either way, and a
writer does not.

Run identity
------------
A run's ``run_id`` is derived, not drawn (DET-007, plan D-54-7): ``uuid5`` over a
canonical key -- the scheme tag, how many runs the tracker already held, then the
declared name, parameters, start instant, parent and tags -- as an instrument's
``asset_id`` is derived (ADR-0016). Until v4.3 it came from the ambient
identifier stream: a run started inside a seeded scope consumed one of that
scope's identifiers, so what the scope minted next depended on whether anything
was tracked, and outside one the same run had a new identity every time it was
started.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Final
from uuid import UUID, uuid5

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.common.types import ParamValue
from alphalab.experiment_tracking.exceptions import ExperimentTrackingInputError

__all__ = [
    "ExperimentRun",
    "ExperimentTracker",
    "ParamValue",
    "RunStatus",
    "complete_run",
    "fail_run",
    "log_metric",
    "log_metrics",
    "start_run",
]


class RunStatus(Enum):
    """Lifecycle states for an experiment run."""

    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class ExperimentRun:
    """A single experiment run's identity, parameters, and metric history.

    Attributes:
        run_id: The run's identifier, derived from its declaration and its
            place in the tracker (see the module docstring).
        name: Human-readable name, e.g. "linreg_l2_sweep". Versions of the same
            logical experiment share a name -- see
            alphalab.experiment_tracking.versioning.
        status: Current lifecycle status.
        parameters: Hyperparameters/configuration this run was started with,
            fixed at start_run and never mutated afterward.
        metrics: Named metric histories -- each value is the full sequence of
            logged values for that metric, in the order logged, not just the
            latest. Training loss per epoch, reward per episode, etc. all fit
            naturally as one growing log per metric name. A plain mapping of
            sequences is accepted and converted; each history compares equal to
            the tuple it stands in for.
        parent_run_id: The run this one is a new version of, if any.
        tags: Free-form labels for filtering/grouping.
        created_at: Unix timestamp the run started.
        completed_at: Unix timestamp the run finished, if it has.
    """

    run_id: str
    name: str
    status: RunStatus
    parameters: Mapping[str, ParamValue]
    metrics: PersistentMap[str, AppendOnlyLog[float]] = field(default_factory=PersistentMap)
    parent_run_id: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    completed_at: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", _as_histories(self.metrics))


@dataclass(frozen=True, slots=True)
class ExperimentTracker:
    """Immutable collection of every experiment run.

    ``runs`` is keyed by ``run_id`` and iterates in the order runs were started.
    """

    runs: PersistentMap[str, ExperimentRun] = field(default_factory=PersistentMap)

    def __post_init__(self) -> None:
        if not isinstance(self.runs, PersistentMap):
            object.__setattr__(self, "runs", PersistentMap(self.runs))


def _as_histories(
    metrics: Mapping[str, Sequence[float]],
) -> PersistentMap[str, AppendOnlyLog[float]]:
    """Convert a plain mapping of sequences into metric histories.

    Only the outer container's type is checked -- this runs on every ``replace``
    and so on every logged value; see ``ModelRegistry.__post_init__`` for why
    inspecting the entries here would be quadratic.
    """

    if isinstance(metrics, PersistentMap):
        return metrics
    return PersistentMap(
        (name, history if isinstance(history, AppendOnlyLog) else AppendOnlyLog(history))
        for name, history in metrics.items()
    )


#: The namespace every run identifier is derived under: ``uuid5(NAMESPACE_DNS,
#: "experiment-run.alphalab.dev")``, written out as a literal -- as the instrument
#: namespace is -- so that it is a fixed fact of the scheme, not a value an
#: expression could drift from. Any reader can recompute it.
_RUN_NAMESPACE: Final = UUID("bfa1d581-9245-5fe3-9592-0b9e651a716b")

#: The scheme tag, and the first line of every run key. A change to the key's
#: shape bumps it, and every identifier derived under ``v1`` stays reproducible.
_RUN_KEY_SCHEME: Final = "alphalab.experiment_run.v1"


def _canonical(value: object) -> str:
    """JSON with sorted keys and no whitespace: one text for one value."""

    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _run_id(
    tracker: ExperimentTracker,
    name: str,
    parameters: Mapping[str, ParamValue],
    timestamp: float,
    parent_run_id: str | None,
    tags: Mapping[str, str] | None,
) -> str:
    """The identifier of the run ``start_run`` is about to add (DET-007, plan D-54-7).

    ``uuid5`` over a canonical key, in a fixed field order: the scheme tag, the
    number of runs ``tracker`` already holds, then each declared field --
    strings and mappings as sorted JSON, so a parameter's type is part of it
    and ``20`` is not ``20.0``. Nothing is drawn from the identifier stream. A
    tracker only grows, so the count keeps two runs declared alike apart.
    """

    key = "\n".join(
        (
            _RUN_KEY_SCHEME,
            f"sequence={len(tracker.runs)}",
            f"name={_canonical(name)}",
            f"parameters={_canonical(dict(parameters))}",
            f"timestamp={float(timestamp)!r}",
            f"parent_run_id={_canonical(parent_run_id)}",
            f"tags={_canonical(dict(tags) if tags else {})}",
        )
    )
    return str(uuid5(_RUN_NAMESPACE, key))


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

    run_id = _run_id(tracker, name, parameters, timestamp, parent_run_id, tags)
    run = ExperimentRun(
        run_id=run_id,
        name=name,
        status=RunStatus.RUNNING,
        parameters=dict(parameters),
        parent_run_id=parent_run_id,
        tags=dict(tags) if tags else {},
        created_at=timestamp,
    )
    return ExperimentTracker(runs=tracker.runs.set(run_id, run)), run_id


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

    history = run.metrics.get(metric_name, AppendOnlyLog[float]())
    updated_run = replace(run, metrics=run.metrics.set(metric_name, history.append(value)))
    return ExperimentTracker(runs=tracker.runs.set(run_id, updated_run))


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
    return ExperimentTracker(runs=tracker.runs.set(run_id, updated_run))


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
