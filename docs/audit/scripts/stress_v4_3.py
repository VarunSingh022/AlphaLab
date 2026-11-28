"""The v4.3 programme's stress run: v4.1's scenarios, and one per milestone that needs one.

Kept for provenance and re-runnable, not run by the suite (it takes minutes)::

    PYTHONHASHSEED=0 python docs/audit/scripts/stress_v4_3.py [scenario ...]

The scenarios of ``stress_v4_1.py`` -- and through it v4.0's, v3.13's and
v3.12's -- are re-run as they were written (plan WS-21). Each v4.3 scenario
asserts its outcome as well as printing its cost, in CPU seconds by
``time.process_time`` with the collector running, as the earlier programs
measured. M3's capture overhead and M4's experiments are judged by their
benchmarks instead (``benchmarks/benchmark_capture.py`` against PC-07's limits,
``benchmarks/benchmark_experiments.py`` against WS-05's five percent); M8 adds
per-venue execution.

``registries`` (M0, DET-007 and D-54-7)
            Start 10,000 and then 100,000 experiment runs in one tracker inside
            a seeded scope. Every run id must be distinct, the scope's stream
            must not move -- a run's id is derived from its declaration and its
            place, never drawn -- and the cost of a start must stay flat as the
            tracker grows.

``venues`` (M8, ADR-0061 and GR-4)
            Backtest 2,000 and then 20,000 quotes, an order at every fifth, of
            1 to 100 shares, between two declared venues and a router: ALPHA
            charges a cent a share and BETA a flat quarter an order, so the
            cheapest depends on the order's size. Every order must go where its
            size makes it cheapest, a tie to ALPHA, and record so. Each venue's
            commission must be its own rule's, and their sum the run's. The cost
            of a record must stay flat as the run grows; what routing costs over
            the same run with one model is printed.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V41 = _load("stress_v4_1", Path(__file__).with_name("stress_v4_1.py"))


def _starts(runs: int) -> tuple[float, int, bool]:
    """``runs`` starts in one tracker inside a seeded scope.

    Returns CPU microseconds a start, how many distinct ids were made, and
    whether the scope's stream stayed where it was.
    """

    from alphalab.common.ids import current_id_position, id_scope
    from alphalab.experiment_tracking import ExperimentTracker, start_run

    tracker = ExperimentTracker()
    ids: set[str] = set()
    with id_scope(43):
        before = current_id_position()
        started = time.process_time()
        for index in range(runs):
            tracker, run_id = start_run(
                tracker, "sweep", {"lookback": index % 50, "threshold": 0.5}, float(index)
            )
            ids.add(run_id)
        elapsed = time.process_time() - started
        unmoved = current_id_position() == before
    return elapsed / runs * 1e6, len(ids), unmoved


def scenario_registries() -> None:
    print("== registries: experiment runs started inside a seeded scope (DET-007, D-54-7)")
    measured = {runs: _starts(runs) for runs in (10_000, 100_000)}
    for runs, (micros, distinct, unmoved) in measured.items():
        print(f"  {runs:>7,} starts: {micros:.1f} us a start, {distinct:,} distinct ids, "
              f"scope {'unmoved' if unmoved else 'MOVED'}")  # fmt: skip
        assert distinct == runs, "every run id is distinct"
        assert unmoved, "starting a run draws nothing from the scope around it"
    ratio = measured[100_000][0] / measured[10_000][0]
    print(f"  a start at 10x the tracker: {ratio:.2f}x")
    assert ratio < 2.0, "a start's cost stays flat as the tracker grows"


#: Order sizes, each bought and then sold: below, at and above the size where a
#: cent a share costs ALPHA what BETA's quarter an order costs, 25 shares.
SIZES = (1, 10, 25, 40, 100)
STRATEGY = "7e3a9c4d-5f6b-4c7d-8e9f-a0b1c2d3e4f5"
ASSET = "8f4b0d5e-6a7c-4d8e-9fa0-b1c2d3e4f5a6"


def _routed(records: int, venues: bool) -> tuple[float, dict[str, tuple[int, Decimal]]]:
    """One backtest of ``records`` quotes, an order at every fifth, routed or one model.

    Returns CPU microseconds a record, and each venue's fills and commission.
    """

    from dataclasses import replace

    from alphalab.backtesting import BacktestEngine
    from alphalab.execution import CheapestVenue, VenueExecution
    from alphalab.execution.commission import FixedCommission, PerShareCommission
    from alphalab.execution.simulator import ExecutionSimulator
    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        dataset_of_quotes,
        running_strategy_state,
    )

    cent = ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.01")))
    quarter = ExecutionSimulator(commission_model=FixedCommission(Decimal("0.25")))
    plan: dict[float, Decimal] = {}
    for turn, index in enumerate(range(0, records, 5)):
        size = Decimal(SIZES[(turn // 2) % len(SIZES)])
        plan[2.0 + index] = size if turn % 2 == 0 else -size
    config = backtest_config(STRATEGY, seed=47, simulator=cent)
    if venues:
        declared = {"ALPHA": VenueExecution(cent), "BETA": VenueExecution(quarter)}
        pipeline = replace(config.pipeline, venues=declared, router=CheapestVenue())
        config = replace(config, pipeline=pipeline)
    dataset = dataset_of_quotes(
        ASSET, [Decimal(100) + Decimal(index % 7) for index in range(records)]
    )
    strategies = running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, plan))
    result, cpu = V41.V40._timed(
        lambda: BacktestEngine.run(config, dataset, strategies, context_factory)
    )
    assert result.strategy_failures == ()
    orders = result.state.oms.orders.orders()
    assert len(orders) == len(plan), "every planned order was placed"
    by_venue: dict[str, tuple[int, Decimal]] = {}
    for order in orders:
        if venues:
            # The cheaper venue for its size: a cent a share against a quarter,
            # a tie going to the first code (ADR-0061).
            expected = "ALPHA" if order.quantity <= 25 else "BETA"
            routed = (order.metadata["venue"], order.metadata["venue_by"])
            assert routed == (expected, "router"), (order.quantity, routed)
            assert float(order.metadata["venue_at"]) == order.created_at
        else:
            assert "venue" not in order.metadata, "one model routes nothing"
    for report in result.state.execution.history:
        fills, paid = by_venue.get(report.venue, (0, Decimal(0)))
        by_venue[report.venue] = (fills + 1, paid + report.commission)
    if venues:
        for report in result.state.execution.history:
            rule = Decimal("0.25") if report.venue == "BETA" else report.fill_quantity / 100
            assert report.commission == rule, report
    total = sum((paid for _, paid in by_venue.values()), Decimal(0))
    assert total == result.state.portfolio.commission_paid.of("USD"), "the venues' sum"
    return cpu / records * 1e6, by_venue


def scenario_venues() -> None:
    print("== venues: every order routed between two declared venues by its size (ADR-0061)")
    routed = {records: _routed(records, venues=True) for records in (2_000, 20_000)}
    for records, (micros, by_venue) in routed.items():
        costs = ", ".join(
            f"{code} {fills:,} fills {paid} commission"
            for code, (fills, paid) in sorted(by_venue.items())
        )
        print(f"  {records:>6,} records: {micros:.1f} us a record; {costs}")  # fmt: skip
    ratio = routed[20_000][0] / routed[2_000][0]
    print(f"  a record at 10x the run: {ratio:.2f}x")
    assert ratio < 2.0, "routing's cost stays flat as the run grows"
    one_model, _ = _routed(20_000, venues=False)
    print(f"  the same 20,000 records with one model: {one_model:.1f} us a record; routing "
          f"costs {routed[20_000][0] / one_model - 1:+.1%}")  # fmt: skip


SCENARIOS: dict[str, Callable[[], None]] = {
    **V41.SCENARIOS,
    "registries": scenario_registries,
    "venues": scenario_venues,
}

if __name__ == "__main__":
    chosen = sys.argv[1:] or list(SCENARIOS)
    for name in chosen:
        started = time.perf_counter()
        SCENARIOS[name]()
        print(f"  ({name}: {time.perf_counter() - started:.1f}s wall)")
