"""Benchmark for the v3.3 institutional paths.

Six surfaces, each measured across a 4x workload increase and each with a
scaling ceiling that catches a regression toward super-linear behaviour rather
than policing constant factors -- the convention
``benchmark_execution_pipeline.py`` established:

==================== =============================================== ==========
surface              what grows                                      expected
==================== =============================================== ==========
execution simulation fills priced                                    linear
partial fills        fills produced from one order                   linear
capacity             names in the universe                           linear
attribution          trades attributed, across nine dimensions       linear
risk decomposition   **positions**, and the covariance is O(n^2)     quadratic
scenario             exposures shocked                               linear
==================== =============================================== ==========

Risk decomposition is the one term that is deliberately super-linear, and it is
measured separately below with its own ceiling. A pairwise covariance over ``n``
assets has ``n(n+1)/2`` distinct entries; there is no linear algorithm for it,
and pretending otherwise by sampling or by assuming a diagonal matrix would
change the number rather than the cost. The ceiling is set to catch a *cubic*
regression -- a matrix operation accidentally nested one level deeper -- which
is the failure that would actually matter.

Nothing here claims a complexity improvement. Each figure is measured on the
development machine at the version stated, and the ceilings are what a later
change has to stay under.
"""

import time
from collections.abc import Callable
from decimal import Decimal

from alphalab.analytics.attribution import (
    AttributionDimension,
    TradeFacts,
    TradeRecord,
    attribute,
)
from alphalab.analytics.decomposition import (
    PositionRisk,
    VaRMethod,
    VaRPolicy,
    decompose,
)
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import Side
from alphalab.execution.capacity import AssetLiquidity, CapacityModel
from alphalab.execution.commission import PercentageCommission
from alphalab.execution.costs import (
    CostContext,
    ExecutionCostModel,
    PerTradeFee,
    ProportionalTax,
    QuotedHalfSpread,
    SquareRootImpact,
    itemized,
)
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.policy import LiquidityCappedFill, LiquidityContext
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import PercentageSlippage
from alphalab.scenario import ScenarioExposure, ScenarioState, scenario

#: Linear scaling predicts 4.00 for a 4x workload. The ceiling leaves room for
#: allocator and garbage-collector noise, as ``benchmark_execution_pipeline``
#: does, and catches a return to quadratic behaviour.
MAX_LINEAR_SCALING = 6.0

#: Risk decomposition builds an n-by-n covariance matrix, so a 4x increase in
#: positions is a 16x increase in pairs. Quadratic predicts 16.00; the ceiling
#: catches a cubic regression, which would predict 64.
MAX_QUADRATIC_SCALING = 26.0


def _timed[T](work: Callable[[int], T], size: int) -> tuple[float, T]:
    start = time.perf_counter()
    result = work(size)
    return time.perf_counter() - start, result


def _report(label: str, size: int, duration: float, unit: str) -> None:
    rate = size / duration if duration > 0 else float("inf")
    print(f"  {label:<22} {size:>7,} {unit:<12} in {duration:6.3f}s  ({rate:>12,.0f}/s)")


def _scaling(label: str, small: float, large: float, ceiling: float, expected: str) -> float:
    scaling = large / max(small, 1e-9)
    print(f"  {label:<22} 4x workload cost {scaling:5.2f}x the time ({expected})")
    if scaling > ceiling:
        raise SystemExit(
            f"{label} scaling factor {scaling:.2f}x exceeds {ceiling:.2f}x; "
            "the institutional path has regressed."
        )
    return scaling


# --------------------------------------------------------------------------- #
# 1. Execution simulation
# --------------------------------------------------------------------------- #


def _cost_model() -> ExecutionCostModel:
    return ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=PercentageSlippage(Decimal("0.0005")),
        impact_model=SquareRootImpact(Decimal("0.05")),
        commission_model=PercentageCommission(Decimal("0.0002")),
        fee_model=PerTradeFee(Decimal("0.50")),
        tax_model=ProportionalTax(Decimal("0.001"), frozenset({Side.BUY})),
    )


def _instruction(index: int) -> OrderInstruction:
    return OrderInstruction(
        order_id=f"ORD-{index}",
        strategy_id="MOM",
        asset_id=f"ASSET-{index % 50}",
        quantity=Decimal("100"),
        price=Decimal("50"),
        side=Side.BUY,
        venue="SIM",
        currency="USD",
    )


def bench_execution(fills: int) -> int:
    simulator = ExecutionSimulator(cost_model=_cost_model())
    priced = 0
    for index in range(fills):
        report = simulator.simulate_fill(
            _instruction(index),
            Decimal("100"),
            Decimal("50"),
            float(index),
            FillStatus.FULL_FILL,
            bid=Decimal("49.95"),
            ask=Decimal("50.05"),
            available_liquidity=Decimal("4000"),
        )
        priced += 1 if report.commission > 0 else 0
    return priced


# --------------------------------------------------------------------------- #
# 2. Partial fills
# --------------------------------------------------------------------------- #


def bench_partial_fills(orders: int) -> int:
    """Work one order down to nothing through a capped venue, repeatedly."""

    policy = LiquidityCappedFill(Decimal("0.50"))
    produced = 0
    for index in range(orders):
        remaining = Decimal("64")
        while remaining > Decimal("0"):
            decision = policy.decide(
                LiquidityContext(
                    f"ASSET-{index % 50}",
                    Side.BUY,
                    remaining,
                    Decimal("50"),
                    Decimal("16"),
                    float(index),
                )
            )
            quantity = decision.quantity if decision.quantity is not None else remaining
            remaining -= quantity
            produced += 1
    return produced


# --------------------------------------------------------------------------- #
# 3. Capacity
# --------------------------------------------------------------------------- #


def bench_capacity(names: int) -> Decimal:
    universe = tuple(
        AssetLiquidity(
            f"ASSET-{index:06d}",
            Decimal("50"),
            Decimal(1_000_000 + index),
            Decimal("1") / Decimal(names),
            "USD",
            "SIM",
        )
        for index in range(names)
    )
    model = CapacityModel(
        participation_limit=Decimal("0.10"),
        turnover=Decimal("0.20"),
        impact_model=SquareRootImpact(Decimal("0.05")),
        impact_budget=Decimal("0.002"),
        search_ceiling=Decimal("1e12"),
    )
    return model.capacity(universe, 0.0).capacity


# --------------------------------------------------------------------------- #
# 4. Attribution
# --------------------------------------------------------------------------- #


def bench_attribution(trades: int) -> int:
    records = tuple(
        TradeRecord(
            f"T{index}",
            f"ASSET-{index % 50}",
            f"SECTOR-{index % 11}",
            Decimal("1.00") if index % 2 else Decimal("-0.50"),
            Decimal("5000"),
            3600.0,
            (
                StrategyContribution("MOM", Decimal("60")),
                StrategyContribution("MR", Decimal("40")),
            ),
        )
        for index in range(trades)
    )
    model = _cost_model()
    costs = itemized(
        model.quote(
            CostContext(
                "ASSET-0",
                Side.BUY,
                Decimal("100"),
                Decimal("50"),
                "USD",
                "SIM",
                0.0,
                bid=Decimal("49.95"),
                ask=Decimal("50.05"),
                available_liquidity=Decimal("4000"),
            )
        ),
        Decimal("100"),
    )
    facts = {
        f"T{index}": TradeFacts(
            f"T{index}",
            currency="USD",
            venue=f"VENUE-{index % 4}",
            broker=f"BROKER-{index % 3}",
            country=f"C{index % 7}",
            factor_pnl={"momentum": Decimal("0.40"), "value": Decimal("0.10")},
            execution_costs=costs,
        )
        for index in range(trades)
    }
    report = attribute(records, facts)
    return len(report.dimensions[AttributionDimension.ASSET].buckets)


# --------------------------------------------------------------------------- #
# 5. Risk decomposition -- the quadratic one
# --------------------------------------------------------------------------- #


def _returns(seed: int, length: int) -> tuple[float, ...]:
    """A deterministic, non-constant series. No RNG: the benchmark reproduces."""

    return tuple(
        ((seed * 7919 + index * 104_729) % 2_003) / 100_000.0 - 0.01 for index in range(length)
    )


def bench_risk(positions: int) -> float:
    book = tuple(
        PositionRisk(
            f"ASSET-{index:06d}",
            Decimal(1_000 + index) * (Decimal("-1") if index % 5 == 0 else Decimal("1")),
            "USD",
            _returns(index, 60),
        )
        for index in range(positions)
    )
    portfolio = _returns(999, 60)
    curve = [1_000_000.0]
    for value in portfolio:
        curve.append(curve[-1] * (1.0 + value))

    result = decompose(
        book,
        portfolio,
        Decimal("1000000"),
        tuple(curve),
        VaRPolicy(VaRMethod.HISTORICAL, 0.95),
    )
    return result.volatility


# --------------------------------------------------------------------------- #
# 6. Scenario application
# --------------------------------------------------------------------------- #


def bench_scenario(exposures: int) -> Decimal:
    state = ScenarioState(
        exposures=tuple(
            ScenarioExposure(
                f"ASSET-{index:06d}",
                Decimal(100 + index),
                Decimal("50"),
                "USD",
                volatility=0.2,
                available_liquidity=Decimal("10000"),
                sector=f"SECTOR-{index % 11}",
            )
            for index in range(exposures)
        ),
        base_currency="USD",
        rates={},
    )
    stress = scenario(
        "crash",
        price_shock=Decimal("-0.30"),
        volatility_shock=Decimal("1.50"),
        liquidity_shock=Decimal("-0.60"),
    )
    return stress.apply(state).change


# --------------------------------------------------------------------------- #


def run_benchmark() -> None:
    print("Starting Institutional (v3.3) Benchmark...")

    print("\nExecution simulation (itemized six-role costs):")
    small_exec, _ = _timed(bench_execution, 2_000)
    _report("execution simulation", 2_000, small_exec, "fills")
    large_exec, _ = _timed(bench_execution, 8_000)
    _report("execution simulation", 8_000, large_exec, "fills")

    print("\nPartial fills (liquidity-capped, worked to completion):")
    small_fill, small_count = _timed(bench_partial_fills, 2_000)
    _report("partial fills", small_count, small_fill, "fills")
    large_fill, large_count = _timed(bench_partial_fills, 8_000)
    _report("partial fills", large_count, large_fill, "fills")

    print("\nCapacity (per-name ceilings, impact solved by bisection):")
    small_cap, _ = _timed(bench_capacity, 250)
    _report("capacity", 250, small_cap, "names")
    large_cap, _ = _timed(bench_capacity, 1_000)
    _report("capacity", 1_000, large_cap, "names")

    print("\nAttribution (nine dimensions):")
    small_attr, _ = _timed(bench_attribution, 1_000)
    _report("attribution", 1_000, small_attr, "trades")
    large_attr, _ = _timed(bench_attribution, 4_000)
    _report("attribution", 4_000, large_attr, "trades")

    print("\nRisk decomposition (pairwise covariance -- quadratic by construction):")
    small_risk, _ = _timed(bench_risk, 40)
    _report("risk decomposition", 40, small_risk, "positions")
    large_risk, _ = _timed(bench_risk, 160)
    _report("risk decomposition", 160, large_risk, "positions")

    print("\nScenario application (three shocks over the book):")
    small_scen, _ = _timed(bench_scenario, 5_000)
    _report("scenario", 5_000, small_scen, "exposures")
    large_scen, _ = _timed(bench_scenario, 20_000)
    _report("scenario", 20_000, large_scen, "exposures")

    print("\nScaling across a 4x workload increase:")
    _scaling("execution simulation", small_exec, large_exec, MAX_LINEAR_SCALING, "linear = 4.00x")
    _scaling("partial fills", small_fill, large_fill, MAX_LINEAR_SCALING, "linear = 4.00x")
    _scaling("capacity", small_cap, large_cap, MAX_LINEAR_SCALING, "linear = 4.00x")
    _scaling("attribution", small_attr, large_attr, MAX_LINEAR_SCALING, "linear = 4.00x")
    _scaling(
        "risk decomposition",
        small_risk,
        large_risk,
        MAX_QUADRATIC_SCALING,
        "quadratic = 16.00x",
    )
    _scaling("scenario", small_scen, large_scen, MAX_LINEAR_SCALING, "linear = 4.00x")

    print(
        "\n  note: risk decomposition is quadratic on purpose -- a pairwise\n"
        "        covariance over n assets has n(n+1)/2 entries. Its ceiling\n"
        "        catches a cubic regression, not the quadratic term itself.\n"
        "        No complexity improvement is claimed anywhere in this file."
    )


if __name__ == "__main__":
    run_benchmark()
