"""Task resolution and execution for real, process-pool-backed job execution.

A task is identified by a dotted path (e.g.
"alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model"), not a
Python object -- multiprocessing pickles whatever is submitted to a worker
process, and closures/lambdas/bound methods generally aren't picklable across
process boundaries. Only top-level, importable module functions work as tasks,
the same real constraint any process-pool-based task system has to work within.
"""

import importlib
from collections.abc import Callable, Mapping
from typing import Any, cast

from alphalab.cloud_research.exceptions import CloudResearchInputError


def resolve_task(task_path: str) -> Callable[..., Any]:
    """Resolves a dotted path into the callable it names.

    Raises:
        CloudResearchInputError: If task_path is malformed, its module cannot be
            imported, or the module has no such callable attribute.
    """
    if "." not in task_path:
        raise CloudResearchInputError(
            f"task_path must be a dotted module path like 'package.module.function', "
            f"got '{task_path}'."
        )

    module_path, _, function_name = task_path.rpartition(".")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise CloudResearchInputError(f"Could not import module '{module_path}': {exc}") from exc

    function = getattr(module, function_name, None)
    if function is None or not callable(function):
        raise CloudResearchInputError(f"'{task_path}' does not resolve to a callable.")
    return cast(Callable[..., Any], function)


def run_task(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Executes a task described by payload: {"task_path": str, "kwargs": dict}.

    This function itself is what gets submitted to a ProcessPoolExecutor -- it is
    a top-level module function, satisfying the same picklability constraint it
    exists to enforce for the tasks it resolves.

    Never raises: any failure (bad task_path, missing kwargs, an exception inside
    the task itself) is captured and returned as a structured failure result,
    since an exception crossing a process boundary needs to arrive back as data,
    not as a raised exception in the parent process's control flow.

    Returns:
        {"success": True, "result": <task's return value>} on success, or
        {"success": False, "error": "<ExceptionType>: <message>"} on any failure.
    """
    task_path = payload.get("task_path")
    if not isinstance(task_path, str):
        return {"success": False, "error": "payload is missing a string 'task_path'."}

    kwargs = payload.get("kwargs", {})
    if not isinstance(kwargs, Mapping):
        return {"success": False, "error": "payload's 'kwargs' must be a mapping."}

    try:
        function = resolve_task(task_path)
        result = function(**kwargs)
        return {"success": True, "result": result}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
