"""Parameter sweeps: submit one job per candidate of a parameter space, then count them.

The direct, practical reason "distributed quantitative research" exists: sweeping
factor lookback windows, ML hyperparameters, or walk-forward configurations across
many combinations in parallel, rather than one at a time in a single process.

The space and the count are the research authority's (v3.12, ledger SCF-003).
Candidates are enumerated by
:class:`~alphalab.research.walk_forward_optimization.ParameterSpace` -- the
one search space, so a grid submitted here enumerates exactly as the research
assistant and walk-forward optimization enumerate it -- and a finished sweep is
read back by :func:`collect_sweep` as a
:class:`~alphalab.research.overfitting.SweepResult`, whose trial count is what
every multiple-testing correction needs. This module adds the parallelism and
nothing else.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from alphalab.cloud_research.cluster import CloudResearchState, submit_research_job
from alphalab.cloud_research.exceptions import CloudResearchInputError
from alphalab.common.types import ParamValue
from alphalab.distributed.job import JobType
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.overfitting import SweepResult, parameter_sweep
from alphalab.research.walk_forward_optimization import ParameterSpace

__all__ = ["collect_sweep", "submit_parameter_sweep", "sweep_space"]


def sweep_space(param_grid: ParameterSpace | Mapping[str, Sequence[ParamValue]]) -> ParameterSpace:
    """``param_grid`` as the research authority's :class:`ParameterSpace`.

    A mapping of axes becomes :meth:`ParameterSpace.grid`: axes by name, each in
    its given order.

    Raises:
        CloudResearchInputError: If the grid is empty, an axis has no value, or
            a value is not a finite bool, int, float or str.
    """
    if isinstance(param_grid, ParameterSpace):
        return param_grid
    if not param_grid:
        raise CloudResearchInputError("param_grid cannot be empty.")
    if any(len(values) == 0 for values in param_grid.values()):
        raise CloudResearchInputError("Every param_grid entry must have at least one value.")
    try:
        return ParameterSpace.grid(param_grid)
    except ResearchValidationError as error:
        raise CloudResearchInputError(str(error)) from error


def submit_parameter_sweep(
    state: CloudResearchState,
    job_type: JobType,
    task_path: str,
    param_grid: ParameterSpace | Mapping[str, Sequence[ParamValue]],
    base_kwargs: Mapping[str, Any],
    priority: int,
    timestamp: float,
) -> tuple[CloudResearchState, tuple[str, ...]]:
    """Submits one job per candidate of ``param_grid``, in the space's order.

    Each job's kwargs are base_kwargs overlaid with one candidate's values --
    base_kwargs supplies anything constant across the sweep (e.g. the dataset),
    the space supplies what varies (e.g. l2_penalty candidates).

    Returns:
        The state, and the job ids in the space's order -- the order
        :func:`collect_sweep` reads them back in.

    Raises:
        CloudResearchInputError: If param_grid is empty, any of its value
            sequences is empty, or a value is not a finite bool, int, float or str.
    """
    space = sweep_space(param_grid)

    current = state
    job_ids: list[str] = []
    for candidate in space.candidates:
        kwargs = dict(base_kwargs)
        kwargs.update(candidate)
        current, job_id = submit_research_job(
            current, job_type, task_path, kwargs, priority, timestamp
        )
        job_ids.append(job_id)

    return current, tuple(job_ids)


def collect_sweep(
    state: CloudResearchState,
    param_grid: ParameterSpace | Mapping[str, Sequence[ParamValue]],
    job_ids: Sequence[str],
    metric: str,
    score: Callable[[Any], float],
    *,
    higher_is_better: bool = True,
) -> SweepResult:
    """A finished sweep as the research authority counts a search.

    Args:
        state: The cluster after its jobs ran.
        param_grid: The space the sweep was submitted over.
        job_ids: What :func:`submit_parameter_sweep` returned for it, in order.
        metric: What ``score`` reads.
        score: Reads the metric from one job's result.
        higher_is_better: Which way the metric improves.

    Raises:
        CloudResearchInputError: If the job ids do not match the space, or a job
            has not completed -- a sweep counted without one of its trials would
            report a search that was not the one performed.
    """
    space = sweep_space(param_grid)
    if len(job_ids) != len(space):
        raise CloudResearchInputError(
            f"{len(job_ids)} job ids for a space of {len(space)} candidates; a sweep is read "
            "back with the ids its submission returned."
        )
    unfinished = [job_id for job_id in job_ids if job_id not in state.results]
    if unfinished:
        raise CloudResearchInputError(
            f"{len(unfinished)} of the sweep's jobs have no result yet ({unfinished[:3]}...); "
            "every trial is counted, so every one must have run."
        )
    by_rendered = dict(zip(space.rendered, job_ids, strict=True))
    return parameter_sweep(
        metric,
        space.rendered,
        lambda rendered: score(state.results[by_rendered[rendered]]),
        higher_is_better=higher_is_better,
    )
