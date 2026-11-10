"""A registry's records are identified by what they are, not by an ambient stream (DET-007).

``experiment_tracking.start_run`` and ``cloud_research.submit_research_job`` drew
their record identifiers from ``new_id()``, the ambient stream. Inside a seeded
scope each start or submission consumed one of that scope's identifiers, so what
the scope minted next depended on whether anything had been tracked. Outside
one, the same record had a new identity every time it was made (plan D-54-7,
GAP-03).

Both now derive the identifier the way an instrument's ``asset_id`` is derived
(ADR-0016): ``uuid5`` over a canonical key naming the scheme, the record's place
in its registry and what it declares. Nothing is drawn, and the same declaration
in the same place has the same identifier in every process. The place -- how
many runs the tracker held, how many jobs the cluster had taken -- keeps two
records declared alike apart.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from alphalab.cloud_research import initialize_cluster, submit_research_job
from alphalab.common.ids import current_id_position, id_scope
from alphalab.distributed.job import JobType
from alphalab.experiment_tracking import ExperimentTracker, new_version, start_run

ROOT = Path(__file__).resolve().parents[2]
TASK = "research.tasks.evaluate"
DECLARED: dict[str, Any] = {
    "name": "momentum",
    "parameters": {"lookback": 20, "threshold": 0.5, "long_only": True},
    "timestamp": 1000.0,
    "tags": {"desk": "research"},
}


def _start(
    tracker: ExperimentTracker | None = None, **changed: Any
) -> tuple[ExperimentTracker, str]:
    declared = {**DECLARED, **changed}
    return start_run(
        ExperimentTracker() if tracker is None else tracker,
        declared["name"],
        declared["parameters"],
        declared["timestamp"],
        parent_run_id=declared.get("parent_run_id"),
        tags=declared["tags"],
    )


def test_starting_a_run_draws_nothing_from_the_scope_around_it() -> None:
    with id_scope(11):
        before = current_id_position()
        _start()
        after = current_id_position()

    assert after == before


def test_a_run_identifier_is_its_declaration_and_its_place() -> None:
    tracker, first = _start()
    with id_scope(5):
        _, under_one_scope = _start()
    with id_scope(6):
        _, under_another = _start()
    _, same_again = _start()

    assert first == under_one_scope == under_another == same_again
    assert UUID(first).version == 5
    # Started again, alike, in the tracker that now holds the first: another run.
    held, second = _start(tracker)
    assert second != first and set(held.runs) == {first, second}


@pytest.mark.parametrize(
    "changed",
    [
        {"name": "mean-reversion"},
        {"parameters": {**DECLARED["parameters"], "lookback": 21}},
        {"parameters": {**DECLARED["parameters"], "lookback": 20.0}},
        {"timestamp": 1000.5},
        {"tags": {}},
    ],
    ids=["name", "parameter value", "parameter type", "timestamp", "tags"],
)
def test_every_declared_field_enters_the_identifier(changed: dict[str, Any]) -> None:
    _, original = _start()
    _, other = _start(**changed)

    assert other != original


def test_the_order_a_declaration_was_written_in_does_not_enter_the_identifier() -> None:
    """Parameters and tags are mappings: the same ones given in another order are one run."""

    _, forward = _start(parameters={"lookback": 20, "threshold": 0.5}, tags={"a": "1", "b": "2"})
    _, reverse = _start(parameters={"threshold": 0.5, "lookback": 20}, tags={"b": "2", "a": "1"})

    assert forward == reverse


def test_a_new_version_is_identified_by_its_parent_as_well() -> None:
    tracker, parent = _start()
    tracker, version = new_version(tracker, parent, {"lookback": 30}, 2000.0)
    _, unrelated = _start(tracker, parameters={"lookback": 30}, timestamp=2000.0, tags={})

    assert len({parent, version, unrelated}) == 3
    assert tracker.runs[version].parent_run_id == parent


_IN_A_FRESH_INTERPRETER = textwrap.dedent(
    """
    from tests.regression.test_registries_derive_their_ids import _start
    print(_start()[1])
    """
)


def test_a_run_identifier_is_the_same_in_a_fresh_interpreter() -> None:
    finished = subprocess.run(
        [sys.executable, "-c", _IN_A_FRESH_INTERPRETER],
        cwd=ROOT,
        env={**os.environ, "PYTHONHASHSEED": "13", "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == _start()[1]


def _submit(state: Any, **changed: Any) -> tuple[Any, str]:
    submitted: dict[str, Any] = {
        "job_type": JobType.BACKTEST,
        "kwargs": {"a": 1, "b": 2},
        "priority": 5,
    }
    submitted.update(changed)
    return submit_research_job(
        state,
        submitted["job_type"],
        TASK,
        submitted["kwargs"],
        submitted["priority"],
        timestamp=10.0,
    )


def test_a_job_identifier_is_its_submission_and_its_place_not_the_scope() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    taken, first = _submit(state)
    with id_scope(5):
        _, under_one_scope = _submit(state)
    with id_scope(6):
        _, under_another = _submit(state)

    assert first == under_one_scope == under_another
    assert UUID(first).version == 5
    _, second = _submit(taken)
    assert second != first
    assert _submit(state, priority=6)[1] != first
    assert _submit(state, job_type=JobType.OPTIMIZATION)[1] != first


def test_a_job_identifier_leaves_the_scope_with_only_the_queues_event_stamp() -> None:
    """The queue stamps its ``JobSubmitted`` event in the caller's scope, as every engine does.

    The identifier of the job itself is no longer drawn there; the event's is,
    and that one draw is the whole of what a submission takes from the scope.
    """

    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    with id_scope(11):
        before = current_id_position()
        _submit(state)
        after = current_id_position()

    assert after.draws - before.draws == 1
