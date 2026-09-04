"""Benchmark for the end-to-end execution pipeline.

Drives the real market -> strategy -> allocation -> risk -> OMS -> execution ->
portfolio path and reports throughput plus the scaling factor across a 4x
workload increase. Every subsystem on this path grows an append-only history, so
this benchmark is the direct check that the whole spine stays linear -- not just
the risk engine the v2.1 work started from.
"""

import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.market.quote import Quote
from alphalab.portfolio.account import Account
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineState,
)
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

START_CASH = Decimal("10000000")
# Linear scaling predicts 4.0 for a 4x workload; quadratic predicts ~16.
#
# Measured on the development machine: v2.0.0 scaled 16.98x (4000 events in
# 6.76s) because every engine on the path rebuilt its history tuple per event.
# v2.1 scales ~7.4x (4000 events in 1.79s): the event logs are now O(1)
# amortized, and what remains is NOT event accumulation but the OMS's immutable
# order book -- OrderBook.add/replace copy the whole order dict, and
# OMSEngine._update_sets copies both order-id frozensets, once per order stored.
# Fixing that needs a persistent map and is deliberately out of v2.1's scope; see
# docs/ARCHITECTURE.md. The ceiling below is set to catch a regression back
# toward v2.0.0 behaviour, not to certify linearity.
MAX_SCALING_FACTOR = 12.0


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


class _PingPongStrategy(BaseStrategy):
    """Alternates buying and selling one share so every event trades."""

    def __init__(self, strategy_id: str, asset_id: str) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        target = Decimal("1") if int(event.quote.timestamp) % 2 == 0 else Decimal("-1")
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=target,
                timestamp=event.quote.timestamp,
            ),
        )


def _context_factory(strategy_id: str) -> StrategyContext:
    return StrategyContext(
        portfolio=object(),
        market=object(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=object(),
        config={"strategy_id": strategy_id},
        orders=object(),
        history=object(),
        universe=object(),
    )


def _running_state(strategy_id: str, asset_id: str) -> RuntimeState:
    state = register_strategy(
        create_runtime(), strategy_id, _PingPongStrategy(strategy_id, asset_id)
    )
    strategy_state = state.strategies[strategy_id]
    strategy_state, _ = RuntimeSupervisor.configure(strategy_state, {}, 1.0)
    strategy_state, _ = RuntimeSupervisor.initialize(strategy_state, 1.1)
    strategy_state, _ = RuntimeSupervisor.subscribe(strategy_state, frozenset({"quotes"}), 1.2)
    strategy_state, _ = RuntimeSupervisor.start(strategy_state, 1.3)
    return replace(state, strategies={strategy_id: strategy_state})


def _config(strategy_id: str) -> ExecutionPipelineConfig:
    huge = Decimal("1000000000")
    return ExecutionPipelineConfig(
        account=Account("acct-bench", "USD", "Benchmark Account", 1.0),
        starting_cash=START_CASH,
        budget=CapitalBudget(
            global_capital=START_CASH,
            maximum_exposure=huge,
            cash_buffer=Decimal("0"),
            strategy_budgets={strategy_id: START_CASH},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True, enforce_integer_quantities=False
        ),
        risk_limits=RiskLimits(
            order_size=OrderSizeLimit(huge, huge),
            position=PositionLimit(huge, huge),
            exposure=ExposureLimit(huge, huge),
            leverage=LeverageLimit(Decimal("1000")),
            margin=MarginLimit(Decimal("1.00")),
            daily_loss=DailyLossLimit(huge),
            drawdown=DrawdownLimit(Decimal("1.00")),
        ),
    )


def _run(events: int) -> tuple[float, ExecutionPipelineState]:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    state = ExecutionPipeline.initialize(
        _config(strategy_id), _running_state(strategy_id, asset_id), 1.0
    )

    start = time.perf_counter()
    for i in range(events):
        mid = Decimal("100.00") + Decimal(i % 20)
        quote = Quote(
            asset_id=asset_id,
            timestamp=2.0 + i,
            bid=mid,
            ask=mid,
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
            venue="SIM",
            currency="USD",
        )
        state = ExecutionPipeline.process_quote(state, quote, _context_factory).state
    return time.perf_counter() - start, state


def _report(events: int, duration: float, state: ExecutionPipelineState) -> None:
    print(f"  events={events:>6}  time={duration:8.4f}s  {events / duration:>10.1f} events/sec")
    print(
        f"           fills={len(state.fills):<7} snapshots={len(state.portfolio_snapshots):<7}"
        f" risk events={len(state.risk.events)}"
    )


def _totals(state: ExecutionPipelineState) -> Mapping[str, Decimal]:
    return {
        "cash": state.portfolio.cash.balance("USD"),
        "realized_pnl": state.portfolio.realized_pnl,
        "commission_paid": state.portfolio.commission_paid,
    }


def run_benchmark() -> None:
    small_events = 1_000
    large_events = small_events * 4

    print("Starting Execution Pipeline Benchmark...")
    small_duration, small_state = _run(small_events)
    _report(small_events, small_duration, small_state)

    large_duration, large_state = _run(large_events)
    _report(large_events, large_duration, large_state)

    scaling = large_duration / max(small_duration, 1e-9)
    print(f"  4x workload cost {scaling:.2f}x the time (linear would be 4.00x)")
    print(f"  final portfolio totals: {_totals(large_state)}")
    print(
        "  note: the residual super-linearity is the OMS order book and order-id\n"
        "        sets, which copy per stored order. Event/history accumulation is\n"
        "        O(1) amortized as of v2.1."
    )

    if scaling > MAX_SCALING_FACTOR:
        raise SystemExit(
            f"Pipeline scaling factor {scaling:.2f}x exceeds {MAX_SCALING_FACTOR:.2f}x; "
            "the execution path has regressed toward quadratic behaviour."
        )


if __name__ == "__main__":
    run_benchmark()
