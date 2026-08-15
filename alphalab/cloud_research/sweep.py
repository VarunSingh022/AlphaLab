"""Parameter sweeps: submit one job per combination in a Cartesian product of parameters.

The direct, practical reason "distributed quantitative research" exists: sweeping
factor lookback windows, ML hyperparameters, or walk-forward configurations across
many combinations in parallel, rather than one at a time in a single process.
"""

from collections.abc import Mapping, Sequence
from itertools import product
from typing import Any

from alphalab.cloud_research.cluster import CloudResearchState, submit_research_job
from alphalab.cloud_research.exceptions import CloudResearchInputError
from alphalab.distributed.job import JobType


def submit_parameter_sweep(
    state: CloudResearchState,
    job_type: JobType,
    task_path: str,
    param_grid: Mapping[str, Sequence[Any]],
    base_kwargs: Mapping[str, Any],
    priority: int,
    timestamp: float,
) -> tuple[CloudResearchState, tuple[str, ...]]:
    """Submits one job per point in the Cartesian product of param_grid's values.

    Each job's kwargs are base_kwargs overlaid with one combination of param_grid's
    values -- base_kwargs supplies anything constant across the sweep (e.g. the
    dataset), param_grid supplies what varies (e.g. l2_penalty candidates).

    Raises:
        CloudResearchInputError: If param_grid is empty, or any of its value
            sequences is empty.
    """
    if not param_grid:
        raise CloudResearchInputError("param_grid cannot be empty.")
    if any(len(values) == 0 for values in param_grid.values()):
        raise CloudResearchInputError("Every param_grid entry must have at least one value.")

    keys = tuple(param_grid.keys())
    combinations = tuple(product(*(param_grid[key] for key in keys)))

    current = state
    job_ids: list[str] = []
    for combo in combinations:
        kwargs = dict(base_kwargs)
        kwargs.update(dict(zip(keys, combo, strict=True)))
        current, job_id = submit_research_job(
            current, job_type, task_path, kwargs, priority, timestamp
        )
        job_ids.append(job_id)

    return current, tuple(job_ids)
