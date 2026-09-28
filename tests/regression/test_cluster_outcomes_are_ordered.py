"""A cluster cycle applies job outcomes in submission order (ledger DET-001).

Until v3.10 ``run_cluster_cycle`` applied each outcome as the executor finished
it, so the results mapping, the distributed event log and every seeded
identifier those events mint depended on how the operating system scheduled
the workers. Here the first job submitted is the slowest, so completion order is
the reverse of submission order -- and the recorded order must not be.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from alphalab.cloud_research.cluster import (
    initialize_cluster,
    run_cluster_cycle,
    submit_research_job,
)
from alphalab.common.ids import id_scope
from alphalab.distributed.job import JobType

#: Seconds each job sleeps: the first submitted finishes last.
DELAYS = (0.30, 0.15, 0.0)
TASK = f"{__name__}.nap"


def nap(seconds: float) -> float:
    """A task that takes ``seconds`` and returns them -- importable, as a task must be."""

    time.sleep(seconds)
    return seconds


def _cycle() -> tuple[list[str], list[str], list[str]]:
    with id_scope(4242):
        state = initialize_cluster("C", num_workers=3, capacity_per_worker=1, timestamp=0.0)
        submitted = []
        for index, delay in enumerate(DELAYS):
            state, job_id = submit_research_job(
                state, JobType.PLUGIN_TASK, TASK, {"seconds": delay}, 10 - index, 1.0
            )
            submitted.append(job_id)
        with ThreadPoolExecutor(max_workers=3) as executor:
            state = run_cluster_cycle(state, executor, timestamp=2.0)
    events = [
        type(event).__name__ + ":" + str(getattr(event, "job_id", ""))
        for event in state.distributed.events
    ]
    return submitted, list(state.results), events


def test_outcomes_are_recorded_in_submission_order_whatever_finishes_first() -> None:
    submitted, results, events = _cycle()

    assert results == submitted
    completed = [entry.split(":", 1)[1] for entry in events if entry.startswith("JobCompleted")]
    assert completed == submitted


def test_two_cycles_record_the_same_history() -> None:
    assert _cycle() == _cycle()
