"""AlphaLab Cloud Research Engine.

Real, process-pool-backed remote execution, worker pools, and parameter sweeps for
distributed quantitative research -- built on top of `alphalab.distributed`, not a
reimplementation of it. `alphalab.distributed` already provides a complete, correct
job/worker lifecycle state machine (submission, scheduling, assignment); nothing in
it ever actually ran a job's payload, and Job had nowhere to store a result once
one existed. This package closes that loop with real
`concurrent.futures.ProcessPoolExecutor`-backed execution, not a simulation.

"Remote" here means a separate OS process, not a separate machine: this repository
has no network egress to real cloud infrastructure, and the honest scope is genuine
multi-process parallelism, not a claim of literal multi-machine deployment.
"""

from alphalab.cloud_research.cluster import (
    CloudResearchState,
    initialize_cluster,
    run_cluster_cycle,
    submit_research_job,
)
from alphalab.cloud_research.exceptions import CloudResearchError, CloudResearchInputError
from alphalab.cloud_research.sweep import submit_parameter_sweep
from alphalab.cloud_research.task import resolve_task, run_task

__all__ = [
    "CloudResearchError",
    "CloudResearchInputError",
    "CloudResearchState",
    "initialize_cluster",
    "resolve_task",
    "run_cluster_cycle",
    "run_task",
    "submit_parameter_sweep",
    "submit_research_job",
]
