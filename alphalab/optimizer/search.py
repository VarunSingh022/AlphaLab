"""Deterministic parameter space generation algorithms."""

import itertools
import random
from typing import Any

from alphalab.optimizer.exceptions import OptimizerValidationError
from alphalab.optimizer.parameter import Parameter, ParameterType


def _generate_numeric_range(param: Parameter) -> tuple[Any, ...]:
    """Generates a discrete sequence of values for a numeric parameter."""
    if param.minimum is None or param.maximum is None:
        return (param.default,)

    step = param.step
    if step is None:
        # If no step provided, fallback to only boundaries
        if param.minimum == param.maximum:
            return (param.minimum,)
        return (param.minimum, param.maximum)

    values: list[Any] = []
    current = param.minimum

    # Epsilon applied to avoid precision-based infinite loops or missing bounds
    epsilon = step * 0.0001 if param.param_type == ParameterType.FLOAT else 0

    while current <= (param.maximum + epsilon):
        # Format explicitly to standard primitive types
        if param.param_type == ParameterType.INT:
            values.append(int(current))
        else:
            values.append(float(round(current, 8)))
        current += step

    return tuple(values)


def _get_parameter_values(param: Parameter) -> tuple[Any, ...]:
    """Resolves all possible discrete values for a parameter."""
    if param.choices is not None:
        return param.choices
    if param.param_type in {ParameterType.INT, ParameterType.FLOAT}:
        return _generate_numeric_range(param)
    return (param.default,)


def generate_grid_search(parameters: tuple[Parameter, ...]) -> tuple[dict[str, Any], ...]:
    """Generates an exhaustive Cartesian product of all parameter values."""
    keys = [p.name for p in parameters]
    value_lists = [_get_parameter_values(p) for p in parameters]

    combinations = list(itertools.product(*value_lists))
    if not combinations:
        raise OptimizerValidationError("Grid search generated zero combinations.")

    # Generate dictionaries
    results: list[dict[str, Any]] = []
    for combo in combinations:
        results.append(dict(zip(keys, combo, strict=True)))

    return tuple(results)


def generate_random_search(
    parameters: tuple[Parameter, ...], num_trials: int, seed: int = 42
) -> tuple[dict[str, Any], ...]:
    """Deterministically generates random parameter sets using a seeded PRNG."""
    if num_trials <= 0:
        raise OptimizerValidationError("Random search requires num_trials > 0.")

    prng = random.Random(seed)
    keys = [p.name for p in parameters]
    results: list[dict[str, Any]] = []
    seen = set()

    # Cap attempts to prevent infinite loops if the space is smaller than num_trials
    max_attempts = num_trials * 10
    attempts = 0

    while len(results) < num_trials and attempts < max_attempts:
        attempts += 1
        combo = []
        for p in parameters:
            if p.choices is not None:
                combo.append(prng.choice(p.choices))
            elif (
                p.param_type == ParameterType.INT
                and p.minimum is not None
                and p.maximum is not None
            ):
                combo.append(prng.randint(int(p.minimum), int(p.maximum)))
            elif (
                p.param_type == ParameterType.FLOAT
                and p.minimum is not None
                and p.maximum is not None
            ):
                val = prng.uniform(float(p.minimum), float(p.maximum))
                combo.append(float(round(val, 6)))
            else:
                combo.append(p.default)

        combo_tuple = tuple(combo)
        if combo_tuple not in seen:
            seen.add(combo_tuple)
            results.append(dict(zip(keys, combo_tuple, strict=True)))

    if not results:
        raise OptimizerValidationError("Random search generated zero valid combinations.")

    return tuple(results)


def generate_single_run(parameters: tuple[Parameter, ...]) -> tuple[dict[str, Any], ...]:
    """Generates exactly one trial using the default parameter values."""
    result = {p.name: p.default for p in parameters}
    return (result,)
