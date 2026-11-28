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

Since v4.3 a cycle given a :class:`~alphalab.common.registry.RunnerRegistry` looks
each job's task up by name and resolves no path (GAP-05), and takes the assignment
policy the cluster's workers follow, ``alphalab.cluster_scheduler``'s included
(GAP-31). The trial ledger runs an experiment's trials on this cycle
(``alphalab.lifecycle.experiments.ClusterExecutor``).
"""

from alphalab.cloud_research.cluster import (
    AssignmentPolicy,
    CloudResearchState,
    initialize_cluster,
    run_cluster_cycle,
    submit_research_job,
)
from alphalab.cloud_research.exceptions import CloudResearchError, CloudResearchInputError
from alphalab.cloud_research.sweep import collect_sweep, submit_parameter_sweep, sweep_space
from alphalab.cloud_research.task import resolve_task, run_task

__all__ = [
    "AssignmentPolicy",
    "CloudResearchError",
    "CloudResearchInputError",
    "CloudResearchState",
    "collect_sweep",
    "initialize_cluster",
    "resolve_task",
    "run_cluster_cycle",
    "run_task",
    "submit_parameter_sweep",
    "submit_research_job",
    "sweep_space",
]
