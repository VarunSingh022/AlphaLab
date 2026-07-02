"""Comprehensive unit tests ensuring deterministic isolation and pure transitions."""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import pytest

from alphalab.strategy import (
    BaseStrategy,
    FillEvent,
    Intent,
    InvalidTransitionError,
    LifecycleState,
    RuntimeSupervisor,
    StrategyContext,
    StrategyEngine,
    StrategyProtocol,
    active_strategies,
    create_runtime,
    failed_strategies,
    get_strategy,
    register_strategy,
)


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
            portfolio=None,
            market=None,
            clock=DummyClock(),
            logger=DummyLogger(),
            risk_view=None,
            config=None,
            orders=None,
            history=None,
            universe=None,
        )


class MockHealthyStrategy(BaseStrategy):
    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        return [Intent("S1", "AAPL", Decimal("10"))]


class MockCrashingStrategy(BaseStrategy):
    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        raise ValueError("Simulated unexpected crash")


class MockBadIntentStrategy(BaseStrategy):
    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        return [Intent("S3", "", Decimal("10"))]  # Empty instrument triggers validation failure


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


def test_supervisor_valid_transitions() -> None:
    state = create_runtime()
    state = register_strategy(state, "S1", MockHealthyStrategy())
    strat = get_strategy(state, "S1")
    assert strat is not None
    assert strat.status == LifecycleState.CREATED

    s1, _ = RuntimeSupervisor.configure(strat, {}, 1.0)
    assert s1.status == LifecycleState.CONFIGURED

    s2, _ = RuntimeSupervisor.initialize(s1, 2.0)
    assert s2.status == LifecycleState.INITIALIZED

    s3, _ = RuntimeSupervisor.subscribe(s2, frozenset(), 3.0)
    assert s3.status == LifecycleState.SUBSCRIBED


def test_supervisor_invalid_transitions() -> None:
    state = create_runtime()
    state = register_strategy(state, "S1", MockHealthyStrategy())
    strat = get_strategy(state, "S1")
    assert strat is not None

    # Cannot start from CREATED
    with pytest.raises(InvalidTransitionError):
        RuntimeSupervisor.start(strat, 1.0)


def test_dispatcher_healthy_hook() -> None:
    runtime = setup_running_strategy("S1", MockHealthyStrategy())
    event = FillEvent("EVT-1", 10.0, "ORD-1", "AAPL", Decimal("5"), Decimal("150"))

    ctx_builder = DummyContextBuilder()
    new_runtime, intents = StrategyEngine.process_event(runtime, event, ctx_builder.build, 11.0)

    assert len(intents) == 1
    assert intents[0].target == Decimal("10")
    assert len(active_strategies(new_runtime)) == 1


def test_dispatcher_isolation_on_crash() -> None:
    runtime = setup_running_strategy("S2", MockCrashingStrategy())
    event = FillEvent("EVT-1", 10.0, "ORD-1", "AAPL", Decimal("5"), Decimal("150"))

    ctx_builder = DummyContextBuilder()
    new_runtime, intents = StrategyEngine.process_event(runtime, event, ctx_builder.build, 11.0)

    # Crash isolated, 0 intents returned, strategy transitioned to FAILED
    assert len(intents) == 0
    assert len(active_strategies(new_runtime)) == 0

    failed = failed_strategies(new_runtime)
    assert len(failed) == 1
    assert "HookExecutionError" in str(failed[0].last_error)


def test_dispatcher_isolation_on_bad_intent() -> None:
    runtime = setup_running_strategy("S3", MockBadIntentStrategy())
    event = FillEvent("EVT-1", 10.0, "ORD-1", "AAPL", Decimal("5"), Decimal("150"))

    ctx_builder = DummyContextBuilder()
    new_runtime, intents = StrategyEngine.process_event(runtime, event, ctx_builder.build, 11.0)

    # Strategy emits an Intent missing an instrument. Should be dropped and strategy failed.
    assert len(intents) == 0
    failed = failed_strategies(new_runtime)
    assert len(failed) == 1
    assert "Intent must specify an instrument" in str(failed[0].last_error)
