"""Objective function evaluation and scoring models."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto


class OptimizationDirection(Enum):
    """Defines whether the objective function should be maximized or minimized."""

    MAXIMIZE = auto()
    MINIMIZE = auto()


@dataclass(frozen=True, slots=True)
class ObjectiveFunction:
    """Immutable configuration for evaluating the success of a trial."""

    name: str
    direction: OptimizationDirection
    evaluator: Callable[[dict[str, float]], float]


# Standard Objective Evaluators extracting metrics from an expected dictionary map


def evaluate_sharpe(metrics: dict[str, float]) -> float:
    return metrics.get("sharpe_ratio", 0.0)


def evaluate_sortino(metrics: dict[str, float]) -> float:
    return metrics.get("sortino_ratio", 0.0)


def evaluate_calmar(metrics: dict[str, float]) -> float:
    return metrics.get("calmar_ratio", 0.0)


def evaluate_total_return(metrics: dict[str, float]) -> float:
    return metrics.get("total_return", 0.0)


def evaluate_cagr(metrics: dict[str, float]) -> float:
    return metrics.get("cagr", 0.0)


def evaluate_max_drawdown(metrics: dict[str, float]) -> float:
    # Max drawdown is typically positive (e.g. 0.15 for 15%). Return as is; the
    # direction enum (MINIMIZE) handles the logic.
    return metrics.get("max_drawdown", 0.0)
