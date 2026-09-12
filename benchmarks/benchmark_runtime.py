"""Benchmark for the run runtime: RunEngine over the execution pipeline.

Until v2.14 this file benchmarked ``EventDispatcher`` and ``RuntimeEngine`` -- a
lifecycle state machine with heartbeats that had zero production importers and
never drove the execution path (ADR-0030 decision 11, which deprecates it). A
file named ``benchmark_runtime`` that measured a dead abstraction reported a
throughput nobody could act on. This measures the run layer that actually
exists.

Two questions, and the second is the one this release cares about:

1. **Does the run path stay linear?** Every subsystem under it grows an
   append-only history, and :class:`~alphalab.runtime.run.RunState` adds a
   ``RunStep`` per record on top.
2. **What does the run layer cost above the execution step it wraps?** The same
   dataset is driven twice -- once through ``ExecutionPipeline.process_record``
   directly, once through ``RunEngine.advance`` -- in one process, so the
   difference is the gates, the ``RunStep`` and one ``dataclasses.replace`` and
   not whatever the machine is doing. Measured on the development machine at
   v2.14 this is ~1.6%, against a ceiling set well above it to catch a
   structural regression rather than to police constant factors.

Capture is measured too, because ADR-0029 decision 8 made the checkpoint cost a
release-visible number and v2.14 nests the pipeline envelope inside a run
envelope: the run layer must stay a rounding error on top of the core.
"""

import gc
import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.common.ids import id_scope
from alphalab.market.quote import Quote
from alphalab.market.record import MarketRecord, records_from_inputs
from alphalab.persistence import serialize
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
from alphalab.runtime import run_snapshot
from alphalab.runtime import snapshot as pipeline_snapshot
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
)
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine, RunState
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

START_CASH = Decimal("10000000")
SEED = 20241114

# Linear scaling predicts 4.0 for a 4x workload; quadratic predicts ~16. The
# ceiling matches `benchmark_execution_pipeline.py`'s and for its reason: what a
# run measures above 4.00x is the cyclic collector walking a growing live heap,
# not an algorithmic term, and the benchmark leaves the collector on because that
# is what a real run pays.
MAX_SCALING_FACTOR = 6.0

# What RunEngine.advance costs above ExecutionPipeline.process_record over the
# same records in the same process: two gates, a RunStep and one replace.
# Measured ~1.6% at v2.14; the ceiling catches a structural regression (a second
# traversal, a per-record copy) rather than constant-factor noise.
MAX_RUN_LAYER_OVERHEAD = 1.25

# The run envelope nests the pipeline envelope unchanged, so capturing a run must
# not cost meaningfully more than capturing its core.
MAX_CAPTURE_OVERHEAD = 1.30


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


def _pipeline_config(strategy_id: str) -> ExecutionPipelineConfig:
    huge = Decimal("1000000000")
    return ExecutionPipelineConfig(
        account=Account("acct-bench", "USD", "Runtime Benchmark Account", 1.0),
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


def _records(asset_id: str, count: int) -> tuple[MarketRecord, ...]:
    quotes = [
        Quote(
            asset_id=asset_id,
            timestamp=2.0 + index,
            bid=Decimal("100.00") + Decimal(index % 20),
            ask=Decimal("100.00") + Decimal(index % 20),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
            venue="SIM",
            currency="USD",
        )
        for index in range(count)
    ]
    return records_from_inputs("BENCH", quotes)


def _run_config(strategy_id: str) -> RunConfig:
    return RunConfig(
        pipeline=_pipeline_config(strategy_id),
        mode=ExecutionMode.BACKTEST,
        seed=SEED,
        start_timestamp=1.0,
    )


def _through_run_engine(records: int) -> tuple[float, RunState]:
    """The run layer: gates, canonical step, RunStep, cursor."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    config = _run_config(strategy_id)
    stream = _records(asset_id, records)

    with id_scope(SEED):
        state = RunEngine.initialize(config, _running_state(strategy_id, asset_id))
        start = time.perf_counter()
        for record in stream:
            state, _ = RunEngine.advance(state, record, _context_factory)
        duration = time.perf_counter() - start
    return duration, state


def _through_pipeline_only(records: int) -> float:
    """The same records through the execution step alone -- the control."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    config = _run_config(strategy_id)
    stream = _records(asset_id, records)

    with id_scope(SEED):
        state = ExecutionPipeline.initialize(
            config.pipeline, _running_state(strategy_id, asset_id), 1.0
        )
        start = time.perf_counter()
        for record in stream:
            state = ExecutionPipeline.process_record(
                state, record, _context_factory, config.fill_policy
            ).state
        return time.perf_counter() - start


def _report(records: int, duration: float, state: RunState) -> None:
    print(f"  records={records:>6}  time={duration:8.4f}s  {records / duration:>10.1f} records/sec")
    print(
        f"           steps={len(state.steps):<7} fills={len(state.pipeline.fills):<7}"
        f" skipped={len(state.skipped):<4} draws={state.pipeline.id_position.draws}"
    )


def _totals(state: RunState) -> Mapping[str, Decimal]:
    return {
        "cash": state.pipeline.portfolio.cash.balance("USD"),
        "realized_pnl": state.pipeline.portfolio.realized_pnl,
        "commission_paid": state.pipeline.portfolio.commission_paid,
    }


def _best(measure: Any, records: int, repeats: int = 3) -> float:
    """Lowest of ``repeats``, with the collector run first.

    The two paths are measured this way and interleaved rather than one after the
    other, because a single timing of each compares one heap state against a
    different one and reports the machine rather than the code.
    """

    lowest = float("inf")
    for _ in range(repeats):
        gc.collect()
        result = measure(records)
        lowest = min(lowest, result[0] if isinstance(result, tuple) else result)
    return lowest


def run_benchmark() -> None:
    small, large = 1_000, 4_000

    print("Starting Run Runtime Benchmark (RunEngine over ExecutionPipeline)...")
    small_duration, small_state = _through_run_engine(small)
    _report(small, small_duration, small_state)

    large_duration, large_state = _through_run_engine(large)
    _report(large, large_duration, large_state)

    scaling = large_duration / max(small_duration, 1e-9)
    print(f"  4x workload cost {scaling:.2f}x the time (linear would be 4.00x)")

    control = _best(_through_pipeline_only, small)
    driven = _best(_through_run_engine, small)
    overhead = driven / max(control, 1e-9)
    print(
        f"  run layer cost {overhead:.3f}x the bare execution step over {small} records "
        f"({(overhead - 1) * 100:+.2f}%)"
    )

    # Capture against capture. Serialization is reported beside them rather than
    # inside the ratio: it is the same encoder either way and it dominates both,
    # so folding it in would measure the codec and call it envelope overhead.
    gc.collect()
    core_start = time.perf_counter()
    pipeline_snapshot.capture(large_state.pipeline)
    core = time.perf_counter() - core_start

    gc.collect()
    envelope_start = time.perf_counter()
    snapshot = run_snapshot.capture(large_state)
    envelope = time.perf_counter() - envelope_start

    serialize_start = time.perf_counter()
    payload = serialize(snapshot)
    encoding = time.perf_counter() - serialize_start

    capture_ratio = envelope / max(core, 1e-9)
    print(
        f"  capture(pipeline)={core * 1000:.1f}ms  capture(run)={envelope * 1000:.1f}ms "
        f"({capture_ratio:.2f}x)"
    )
    print(f"  serialize(run)={encoding:.4f}s  payload {len(payload) / 1e6:.2f} MB")
    print(f"  final totals: {_totals(large_state)}")
    print(
        "  note: the run layer is two gates, one RunStep and one replace per record;\n"
        "        the execution step underneath it is unchanged since v2.13."
    )

    if scaling > MAX_SCALING_FACTOR:
        raise SystemExit(
            f"Run scaling factor {scaling:.2f}x exceeds {MAX_SCALING_FACTOR:.2f}x; "
            "the run path has regressed toward quadratic behaviour."
        )
    if overhead > MAX_RUN_LAYER_OVERHEAD:
        raise SystemExit(
            f"The run layer cost {overhead:.3f}x the bare execution step, above "
            f"{MAX_RUN_LAYER_OVERHEAD:.2f}x; RunEngine.advance has grown work the "
            "pipeline does not do."
        )
    if capture_ratio > MAX_CAPTURE_OVERHEAD:
        raise SystemExit(
            f"Capturing a run cost {capture_ratio:.2f}x capturing its pipeline core, "
            f"above {MAX_CAPTURE_OVERHEAD:.2f}x; the run envelope has stopped being a "
            "thin wrapper."
        )


if __name__ == "__main__":
    run_benchmark()
