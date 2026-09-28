"""The same search recorded twice is the same record (ledger DET-002).

Until v3.10 the optimizer stamped its events with ``time.time()`` and put a
``time.perf_counter`` duration into every trial result, so two runs of one
seeded search never compared equal and their serialized histories differed.
The instant is now the caller's, and a duration is measured only when a clock is
supplied -- and is excluded from equality when it is.
"""

import ast
import itertools
from pathlib import Path
from typing import Any

from alphalab.common.ids import id_scope
from alphalab.optimizer import (
    ObjectiveFunction,
    OptimizationDirection,
    Parameter,
    ParameterType,
    evaluate_sharpe,
)
from alphalab.optimizer.optimizer import Optimizer
from alphalab.persistence import serialize

ROOT = Path(__file__).resolve().parents[2]
PARAMETERS = (
    Parameter("x", ParameterType.INT, default=0, minimum=0, maximum=10, step=5),
    Parameter("y", ParameterType.INT, default=0, minimum=0, maximum=10, step=5),
)
OBJECTIVE = ObjectiveFunction("Sharpe", OptimizationDirection.MAXIMIZE, evaluate_sharpe)


class _Evaluator:
    def evaluate(self, parameters: dict[str, Any]) -> dict[str, float]:
        x, y = float(parameters["x"]), float(parameters["y"])
        return {"sharpe_ratio": 10.0 - abs(x - 5.0) - abs(y - 5.0)}


def _search(**kwargs: Any) -> Any:
    with id_scope(99):
        return Optimizer.run_random_search(
            "OPT",
            PARAMETERS,
            OBJECTIVE,
            _Evaluator(),
            num_trials=5,
            seed=3,
            timestamp=1_700_000_000.0,
            **kwargs,
        )


def test_two_runs_of_one_search_record_the_same_history() -> None:
    first, second = _search(), _search()

    assert first == second
    assert serialize(first.completed_trials) == serialize(second.completed_trials)
    assert {event.timestamp for event in first.events} == {1_700_000_000.0}
    assert all(result.execution_time_seconds is None for result in first.completed_trials)


def test_a_supplied_clock_measures_without_changing_what_compares_equal() -> None:
    ticks = itertools.count()
    timed = _search(clock=lambda: float(next(ticks)))

    assert all(result.execution_time_seconds == 1.0 for result in timed.completed_trials)
    assert timed.completed_trials == _search().completed_trials


def test_the_optimizer_reads_no_clock() -> None:
    forbidden = {"time", "monotonic", "perf_counter", "now", "utcnow", "process_time"}
    offenders = []
    for path in sorted((ROOT / "alphalab" / "optimizer").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden
            ):
                offenders.append(f"{path.name}:{node.lineno} {node.func.attr}()")
    assert offenders == []
