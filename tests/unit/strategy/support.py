"""Helpers for driving a strategy runtime outside a run, shared by tests and a benchmark.

Kept apart from ``test_strategy.py`` so that ``benchmarks/benchmark_strategy_runtime.py``
can use them without importing a test module -- which imports ``pytest``, a
development dependency the benchmark itself does not need (v4.1; the benchmark
job had failed on exactly that import before v4.0.0 installed the ``dev`` extra).
Nothing here imports anything but the standard library and ``alphalab``.
"""

from typing import Any

from alphalab.strategy import (
    RuntimeSupervisor,
    StrategyContext,
    StrategyProtocol,
    create_runtime,
    get_strategy,
    register_strategy,
)
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView

__all__ = ["DummyClock", "DummyContextBuilder", "DummyLogger", "setup_running_strategy"]


class DummyClock:
    def now(self) -> float:
        return 0.0


class DummyLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


class DummyContextBuilder:
    def __init__(self) -> None:
        pass

    def build(self, strategy_id: str) -> StrategyContext:
        return StrategyContext(
            portfolio=NoPortfolio(),
            market=NoMarket(),
            clock=DummyClock(),
            logger=DummyLogger(),
            risk_view=NoRiskView(),
            config=None,
            orders=NoOrders(),
        )


def setup_running_strategy(strat_id: str, instance: StrategyProtocol) -> Any:
    state = create_runtime()
    state = register_strategy(state, strat_id, instance)
    strat = get_strategy(state, strat_id)
    assert strat is not None

    # Push through lifecycle to RUNNING
    s1, _ = RuntimeSupervisor.configure(strat, {"opt": 1}, 1.0)
    s2, _ = RuntimeSupervisor.initialize(s1, 2.0)
    s3, _ = RuntimeSupervisor.subscribe(s2, frozenset({"fills"}), 3.0)
    s4, _ = RuntimeSupervisor.start(s3, 4.0)

    new_strats = dict(state.strategies)
    new_strats[strat_id] = s4
    return type(state)(strategies=new_strats, events=state.events)
