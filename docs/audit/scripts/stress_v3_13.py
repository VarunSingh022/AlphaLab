"""The v3.13 release stress run: v3.12's scenarios, and the scale of what v3.13 added.

Kept for provenance and re-runnable, not run by the suite (it takes minutes)::

    PYTHONHASHSEED=0 python3.12 docs/audit/scripts/stress_v3_13.py [scenario ...]

The scenarios of ``stress_v3_12.py`` are re-run as they were written -- ``assets``,
``strategies``, ``venues``, ``construction``, ``checkpoints`` -- and v3.13 adds
these, each checking an outcome as well as printing what it cost:

``per_order``    What one more order costs a run in memory, by ``tracemalloc``,
                 at two run lengths with every log under a retention policy --
                 the per-order state PRF-011 keeps as a stated limitation -- and
                 the same with no orders, to show the rest is flat.
``segments``     A checkpoint chain over a run placing an order every ten
                 records: the segments early and late (PRF-011).
``lattice``      An American put at 1,000, 2,500 and 5,000 steps (``MAX_STEPS``),
                 and an implied volatility inverted through 500 (NUM-006).
``split``        The optimal split at its ceiling -- ten fixed-charge venues
                 beside ten free ones -- for orders of 1,000 and 20,000 units,
                 against the greedy sweep (BRK-005).
``factor``       A factor-model covariance built, decomposed and constructed
                 over at 1,000, 2,000 and 4,000 assets (FEA-007; PRF-013).
``box``          A long-short box-robust construction over 200 assets (FEA-009).
``cron``         The longest search a cron schedule makes: the next 29 February
                 from 1 March 2097, eight years on (DAT-006).
"""

from __future__ import annotations

import gc
import importlib.util
import math
import random
import sys
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V312 = _load("stress_v3_12", Path(__file__).with_name("stress_v3_12.py"))


def _timed(work: Callable[[], Any]) -> tuple[Any, float]:
    gc.collect()
    start = time.process_time()
    result = work()
    return result, time.process_time() - start


# --------------------------------------------------------------------------- #
# Per-order state and checkpoint segments (PRF-011)
# --------------------------------------------------------------------------- #


def _run(records: int, order_every: int | None, checkpoint_every: int | None = None) -> Any:
    from alphalab.market.record import MarketRecord
    from alphalab.runtime.checkpoint import checkpoint
    from alphalab.runtime.retention import RetentionPolicy
    from alphalab.runtime.run import RunEngine
    from tests.integration.harness import ScriptedStrategy, context_factory, sized_quote

    asset = str(UUID(int=0x5151, version=4))
    strategy_id = str(UUID(int=0x5152, version=4))
    plan = (
        {}
        if order_every is None
        else {
            2.0 + i: (Decimal("2") if i % 3 else Decimal("-1"))
            for i in range(0, records, order_every)
        }
    )
    pipeline = replace(
        V312._pipeline(),
        retention=RetentionPolicy(market_history=500, steps=500, audit_events=500, results=500),
    )
    state = RunEngine.initialize(
        V312._config(pipeline),
        V312._running({strategy_id: ScriptedStrategy(strategy_id, asset, plan)}, frozenset({"*"})),
    )
    payloads: list[str] = []
    mark = None
    for index in range(records):
        quote = sized_quote(asset, 2.0 + index, Decimal(100 + index % 7), Decimal("100"))
        state, _ = RunEngine.advance(
            state, MarketRecord(f"R-{index}", quote.timestamp, quote), context_factory
        )
        if checkpoint_every is not None and (index + 1) % checkpoint_every == 0:
            payload, mark = checkpoint(state, mark)
            payloads.append(payload)
    return state, payloads


def _held_bytes(records: int, order_every: int | None) -> int:
    """Bytes ``tracemalloc`` traces as still allocated once the run's state is all that is held."""

    gc.collect()
    tracemalloc.start()
    state, _ = _run(records, order_every)
    gc.collect()
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert state is not None
    return current


def scenario_per_order() -> None:
    print("== per_order: what one more order costs a run in memory (retention 500 on every log)")
    short, long_ = 1_000, 4_000
    quiet = (_held_bytes(long_, None) - _held_bytes(short, None)) / (long_ - short)
    busy = (_held_bytes(long_, 1) - _held_bytes(short, 1)) / (long_ - short)
    print(f"  no orders:        {quiet:,.0f} bytes held per additional record")
    print(f"  an order a record: {busy:,.0f} bytes held per additional record and order")
    assert busy > 10 * max(quiet, 1.0), "the growth is the orders'"


def scenario_segments() -> None:
    from alphalab.common.serialization import to_serializable
    from alphalab.runtime.checkpoint import read_checkpoints
    from alphalab.runtime.run_snapshot import capture as capture_run

    print("== segments: 20,000 records, an order every 10, a checkpoint every 1,000")
    (state, payloads), cpu = _timed(lambda: _run(20_000, 10, 1_000))
    assert to_serializable(read_checkpoints(payloads)) == to_serializable(capture_run(state))
    sizes = [len(payload) / 1e6 for payload in payloads]
    print(
        f"  {cpu:.2f}s CPU; base {sizes[0]:.2f} MB; segments 2, 10, 20: "
        f"{sizes[1]:.2f}, {sizes[9]:.2f}, {sizes[19]:.2f} MB; the chain reads back as the capture"
    )
    assert sizes[19] < 1.5 * sizes[1], "segments are flat across the run"


# --------------------------------------------------------------------------- #
# Options, routing, risk, construction, cron
# --------------------------------------------------------------------------- #


def scenario_lattice() -> None:
    from alphalab.options import (
        BinomialLattice,
        OptionContract,
        binomial_value,
        dividend_yield,
        implied_volatility,
    )
    from alphalab.options.enums import ExerciseStyle, OptionType

    print("== lattice: an American put to the step ceiling (S = K = 50, r = 10%, sigma = 40%)")
    now, year = 1_767_225_600.0, 365.25 * 86_400.0
    put = OptionContract(
        "UNDERLYING",
        Decimal("50"),
        now + 5 / 12 * year,
        OptionType.PUT,
        ExerciseStyle.AMERICAN,
        100,
    )
    carry = dividend_yield(0.0)
    for steps in (1_000, 2_500, 5_000):
        value, cpu = _timed(
            partial(
                binomial_value,
                put,
                50.0,
                0.40,
                0.10,
                now,
                carry=carry,
                lattice=BinomialLattice(steps, ()),
            )
        )
        assert abs(value - 4.28) < 0.01, value
        print(f"  {steps:>5} steps: {value:.4f} in {cpu:.2f}s CPU")
    lattice = BinomialLattice(500, ())
    quoted = Decimal(
        f"{binomial_value(put, 50.0, 0.30, 0.10, now, carry=carry, lattice=lattice):.4f}"
    )
    inverted, cpu = _timed(
        lambda: implied_volatility(
            put, quoted, Decimal("50"), 0.10, now, carry=carry, lattice=lattice
        )
    )
    assert abs(inverted.value - 0.30) < 1e-3
    print(
        f"  implied volatility through 500 steps: {inverted.value:.4f} in {cpu:.2f}s CPU, "
        f"{inverted.iterations} bisections"
    )


def scenario_split() -> None:
    from alphalab.core.enums import Side
    from alphalab.execution import RouteRequest, SplitMethod, VenueProfile, select_route

    example = _load("example_67", ROOT / "examples" / "67_optimal_split_and_urgency.py")
    print(
        "== split: the optimal split at its ceiling, ten fixed-charge venues beside ten free ones"
    )
    rng = random.Random(13)
    quotes, profiles = [], []
    for index in range(20):
        venue = f"V{index:02d}"
        ask = f"{100 + rng.randint(0, 40) / 100:.2f}"
        quotes.append(example.quote(venue, ask, 5_000))
        model = (
            example.costs(per_trade=f"{rng.randint(1, 20)}.00")
            if index < 10
            else example.costs(impact=f"{rng.randint(1, 8) / 10_000:.4f}")
        )
        profiles.append(VenueProfile(venue, example.DECLARATION, model, Decimal("0.001")))
    for shares in (1_000, 20_000):
        request = RouteRequest(
            f"STRESS-{shares}",
            "ACME",
            Side.BUY,
            Decimal(shares),
            None,
            "USD",
            example.REQUIREMENTS,
            Decimal("1"),
            example.AT,
        )
        totals: dict[SplitMethod, Decimal] = {}
        for method in (SplitMethod.GREEDY_SWEEP, SplitMethod.OPTIMAL):

            def route(method: SplitMethod = method, request: RouteRequest = request) -> Any:
                return select_route(request, quotes, profiles, example.policy(method))

            decision, cpu = _timed(route)
            total = sum((leg.all_in_price * leg.quantity for leg in decision.legs), Decimal("0"))
            totals[method] = total
            print(
                f"  {shares:>6} units, {method.name:<12}: {len(decision.legs):>2} legs, "
                f"all-in {total:,.2f}, {cpu:.2f}s CPU"
            )
        assert totals[SplitMethod.OPTIMAL] <= totals[SplitMethod.GREEDY_SWEEP]


def scenario_factor() -> None:
    from alphalab.analytics.risk_model import CovarianceMatrix, FactorLoadings, factor_risk
    from alphalab.portfolio_optimizer.construction import (
        ConstraintSet,
        ConstructionProblem,
        ConstructionStatus,
        ExpectedReturns,
        ExposureRange,
        MeanVariance,
        SolverSettings,
        WeightBounds,
        construct,
    )

    print("== factor: a factor-model covariance built, decomposed and constructed over (k = 5)")
    for count in (1_000, 2_000, 4_000):
        rng = random.Random(7)
        assets = [f"A{i:05d}" for i in range(count)]
        factors = [f"F{j}" for j in range(5)]
        loadings = FactorLoadings.of(
            {a: {f: rng.gauss(0.0, 1.0) for f in factors} for a in assets},
            source="stress",
            lineage=dict.fromkeys(factors, "stress"),
            as_of=None,
        )
        factor_covariance = CovarianceMatrix.from_rows(
            factors,
            [[0.04 if i == j else 0.0 for j in range(5)] for i in range(5)],
            currency="USD",
            period="1M",
            source="stress",
            observations=None,
        )
        specific = {a: 0.01 + 0.02 * rng.random() for a in assets}
        mu = ExpectedReturns({a: rng.gauss(0.05, 0.10) for a in assets}, "USD", "1M", "stress")
        equal = dict.fromkeys(assets, 1.0 / count)
        tracemalloc.start()
        model, build = _timed(
            partial(CovarianceMatrix.factor_model, loadings, factor_covariance, specific)
        )
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        risk, decompose = _timed(partial(factor_risk, equal, model))
        assert abs(risk.residual) <= 1e-12 * risk.volatility
        problem = ConstructionProblem(
            model,
            MeanVariance(mu, 1.0),
            ConstraintSet(ExposureRange.exactly(1.0), WeightBounds(0.0, 0.05)),
            SolverSettings(1e-9, 1e-9, 100_000),
        )
        result, solve = _timed(partial(construct, problem))
        assert result.status is ConstructionStatus.OPTIMAL
        print(
            f"  {count:>5} assets: covariance {build:6.2f}s CPU, {peak / 1e6:,.0f} MB traced; "
            f"factor_risk {decompose:.3f}s; construct {solve:.2f}s"
        )


def scenario_box() -> None:
    from alphalab.analytics.risk_model import CovarianceMatrix
    from alphalab.portfolio_optimizer.construction import (
        BoxUncertainty,
        ConstraintSet,
        ConstructionProblem,
        ConstructionStatus,
        ExpectedReturns,
        ExposureRange,
        RobustMeanVariance,
        SolverSettings,
        WeightBounds,
        construct,
    )

    print("== box: a long-short box-robust construction over 200 assets")
    rng = random.Random(29)
    count = 200
    assets = [f"B{i:03d}" for i in range(count)]
    factors = [[rng.gauss(0.0, 0.1) for _ in range(4)] for _ in range(count)]
    rows = [
        [
            math.fsum(factors[i][f] * factors[j][f] for f in range(4)) + (0.02 if i == j else 0.0)
            for j in range(count)
        ]
        for i in range(count)
    ]
    covariance = CovarianceMatrix.from_rows(
        assets, rows, currency="USD", period="1M", source="stress", observations=None
    )
    mu = ExpectedReturns({a: rng.gauss(0.01, 0.02) for a in assets}, "USD", "1M", "stress")
    box = BoxUncertainty({a: rng.uniform(0.0, 0.02) for a in assets})
    result, cpu = _timed(
        lambda: construct(
            ConstructionProblem(
                covariance,
                RobustMeanVariance(mu, 5.0, box),
                ConstraintSet(ExposureRange.exactly(1.0), WeightBounds(-0.05, 0.10)),
                SolverSettings(1e-9, 1e-9, 100_000),
            )
        )
    )
    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    assert result.weights is not None
    zero = sum(1 for weight in result.weights.values() if weight == 0.0)
    print(
        f"  {count} assets, weights in [-5%, 10%]: {result.status.name} in {cpu:.2f}s CPU, "
        f"{result.diagnostics.iterations} steps; {zero} held at exactly zero"
    )


def scenario_cron() -> None:
    from datetime import UTC, datetime

    from alphalab.scheduler import CronSchedule

    print("== cron: the longest search, the next 29 February from 1 March 2097")
    schedule = CronSchedule("0 0 29 2 *", "UTC")
    after = datetime(2097, 3, 1, tzinfo=UTC).timestamp()
    found, cpu = _timed(lambda: schedule.next_after(after))
    assert found is not None
    print(
        f"  {datetime.fromtimestamp(found, tz=UTC):%Y-%m-%d %H:%M} UTC in {cpu:.3f}s CPU "
        "(2100 is no leap year)"
    )
    assert datetime.fromtimestamp(found, tz=UTC).year == 2104


SCENARIOS: dict[str, Callable[[], None]] = {
    **V312.SCENARIOS,
    "per_order": scenario_per_order,
    "segments": scenario_segments,
    "lattice": scenario_lattice,
    "split": scenario_split,
    "factor": scenario_factor,
    "box": scenario_box,
    "cron": scenario_cron,
}

if __name__ == "__main__":
    chosen = sys.argv[1:] or list(SCENARIOS)
    for name in chosen:
        started = time.perf_counter()
        SCENARIOS[name]()
        print(f"  ({name}: {time.perf_counter() - started:.1f}s wall)")
