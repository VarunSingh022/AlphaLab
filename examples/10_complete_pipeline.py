"""
AlphaLab Examples
=================

Example 10 : From Research to a Traded Book, End to End

Difficulty : Expert

Estimated Time : 20 minutes

Prerequisites
-------------

✓ Examples 02, 08 and 18

Topics
------

• A research sample and a trading sample, kept apart
• Walk-forward optimization: choosing a parameter without seeing its test (FEA-003)
• An implementation lag, and purging labels that reach across a boundary
• A shrunk covariance (Ledoit-Wolf) and expected returns from a signal (OFE-002)
• Mean-variance construction that pays for trading, then whole shares
• Trading the book through the canonical execution path (FEA-001)
• Judging it against a benchmark (FEA-002)

What this shows
---------------

The whole arc a quantitative idea takes, on the ten-name research panel, with
nothing printed that was not computed::

    research sample (first 120 sessions)
      -> momentum at three lookbacks, rank IC against 5-day returns entered a
         session after the signal (implementation lag 1)
      -> walk-forward folds, each label purged where it reaches the next part
      -> each fold selects a lookback on validation, reports it on test
    at the boundary (session 120)
      -> Ledoit-Wolf covariance of the last 60 sessions' returns
      -> expected returns = IC x volatility x score (Grinold and Kahn), with
         the *out-of-sample* IC -- floored at zero, so a signal that failed its
         own test is given no weight at all
      -> mean-variance, long-only, 25% cap, paying 5 bp per unit traded
      -> rounded to whole shares toward zero
    trading sample (last 80 sessions)
      -> the book bought as target quantities and held
      -> equity against an equal-weighted buy-and-hold of the same ten names

Every choice here -- the lookbacks, the horizon, the lag, the cap, the cost --
is stated where it is made. None is a default, and none is a recommendation:
80 sessions of one synthetic panel prove nothing about any strategy.

On this panel the walk-forward result is the lesson: each fold's validation IC
looks usable, and the mean *test* IC is negative. Selection alone would have
shipped the signal; reporting on data the selection never saw is what shows
it did not hold, and the construction then builds the minimum-variance book
instead of betting on it.

(Until v3.11 this file printed check marks for steps it never ran; it was
rewritten for v3.11.)

Run

    python examples/10_complete_pipeline.py
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

from _research_panel import banner, load_panel

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.analytics.benchmark import BenchmarkBasis, benchmark_statistics
from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.api import backtest, observe
from alphalab.backtesting import ExecutionMode, RunConfig
from alphalab.common.types import ParamValue
from alphalab.conventions.economics import CASH_EQUITY
from alphalab.core.enums import AssetType
from alphalab.data.time import BarStamp
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    compute_panel,
    forward_returns,
    information_coefficient,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.portfolio.account import Account
from alphalab.portfolio_optimizer import (
    ConstraintSet,
    ConstructionProblem,
    ExpectedReturns,
    ExposureRange,
    LinearCosts,
    MeanVariance,
    SolverSettings,
    WeightBounds,
    construct,
    round_to_lots,
)
from alphalab.research import (
    ParameterSpace,
    PurgePolicy,
    Refit,
    ResearchStudy,
    WalkForwardDesign,
    WindowMode,
    label_ends_from_horizon,
    walk_forward_optimize,
    walk_forward_splits,
)
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
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

SEED = 20_250_110
PRODUCED_AT = 1_736_500_000.0
RESEARCH_SESSIONS = 120
HORIZON = 5
LAG = 1
LOOKBACKS = (10, 20, 40)
COVARIANCE_WINDOW = 60
CAPITAL = Decimal("1000000")
INVESTED = 0.95
CAP = 0.25
#: Five basis points of notional per unit of weight traded, paid once and
#: amortized over the 20-session holding the forecast is for.
COST_PER_WEIGHT = 0.0005 / 20
STRATEGY_ID = "EX10-BOOK"
PROVIDER = "research-panel"


def momentum(lookback: int) -> FeatureDefinition:
    return FeatureDefinition(
        f"mom_{lookback}", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=lookback
    )


# --------------------------------------------------------------------------- #
# The traded half: instruments, a holding strategy, a configuration
# --------------------------------------------------------------------------- #


class HoldTheBook(BaseStrategy):
    """Buys the constructed book on the first trading session, then holds it."""

    def __init__(self, book: Mapping[str, Decimal], start: float) -> None:
        self._book = dict(book)
        self._start = start

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        quantity = self._book.pop(bar.asset_id, None)
        if bar.timestamp < self._start or quantity is None:
            if quantity is not None:
                self._book[bar.asset_id] = quantity
            return ()
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=bar.asset_id,
                target=quantity,
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


def context_factory(strategy_id: str) -> StrategyContext:
    return StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=NoRiskView(),
        config={"strategy_id": strategy_id},
        orders=NoOrders(),
    )


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def running(strategy: BaseStrategy) -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, strategy)
    lifecycle = state.strategies[STRATEGY_ID]
    lifecycle, _ = RuntimeSupervisor.configure(lifecycle, {}, 1.0)
    lifecycle, _ = RuntimeSupervisor.initialize(lifecycle, 1.1)
    lifecycle, _ = RuntimeSupervisor.subscribe(lifecycle, frozenset({"bars"}), 1.2)
    lifecycle, _ = RuntimeSupervisor.start(lifecycle, 1.3)
    return replace(state, strategies={STRATEGY_ID: lifecycle})


def run_config(registry: InstrumentRegistry, start: float) -> RunConfig:
    ample = Decimal("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX10", "USD", "Example 10", 1.0),
            starting_cash=CAPITAL,
            budget=CapitalBudget(
                global_capital=CAPITAL,
                maximum_exposure=CAPITAL,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: CAPITAL},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=False, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("1.5")),
                margin=MarginLimit(Decimal("1.00")),
                daily_loss=DailyLossLimit(ample, "America/New_York"),
                drawdown=DrawdownLimit(Decimal("1.00")),
            ),
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.005"))),
            instruments=registry,
        ),
        seed=SEED,
        start_timestamp=start,
    )


def main() -> None:
    banner(10, "From Research to a Traded Book, End to End")

    # ------------------------------------------------------------------
    # 1. Data, and the boundary between researching and trading
    # ------------------------------------------------------------------

    dataset = load_panel()
    frame = observe(dataset, FeatureField.CLOSE)
    instants = frame.timestamps
    boundary = instants[RESEARCH_SESSIONS]
    symbols = frame.symbols
    closes = {symbol: frame.series[symbol] for symbol in symbols}
    print()
    print("1. Data")
    print(f"   {len(symbols)} names x {len(instants)} sessions; research on the first")
    print(f"   {RESEARCH_SESSIONS}, trading from session {RESEARCH_SESSIONS + 1} on.")

    # ------------------------------------------------------------------
    # 2. Walk-forward optimization of the lookback, inside the research half
    # ------------------------------------------------------------------

    returns = forward_returns(frame, HORIZON, lag=LAG, delistings=())
    rank_ic: dict[int, Mapping[float, float]] = {}
    for lookback in LOOKBACKS:
        ic = information_coefficient(compute_panel(momentum(lookback), frame), returns)
        # Every cross-section's rank IC, as (instant, IC) pairs; keyed here.
        rank_ic[lookback] = dict(ic.per_instant_rank)

    # A label realizes HORIZON + LAG sessions after its instant. The research
    # instants are those whose label realizes before the boundary, so no
    # research number reads a trading-half price.
    reach = HORIZON + LAG
    research = instants[: RESEARCH_SESSIONS - reach]
    splits = walk_forward_splits(
        research,
        train_size=30,
        validation_size=20,
        test_size=15,
        mode=WindowMode.ROLLING,
        policy=PurgePolicy(label_ends_from_horizon(instants, reach)),
    )
    design = WalkForwardDesign(
        ParameterSpace.grid({"lookback": list(LOOKBACKS)}), Refit.TRAIN_AND_VALIDATION
    )
    study = ResearchStudy(
        study_name="example-10-momentum",
        dataset_version=dataset.require_provenance().dataset_version,
        universe=symbols,
        features=tuple(momentum(lookback) for lookback in LOOKBACKS),
        horizons=(HORIZON,),
        splits=splits.scheme,
        seed=SEED,
        inputs={"walk_forward": design.design_id},
        implementation_lag=LAG,
    )

    def mean_rank_ic(
        parameters: Mapping[str, ParamValue],
        fit: tuple[float, ...],
        evaluate: tuple[float, ...],
        seed: int,
    ) -> float:
        # A momentum signal has nothing to fit: it is scored where it is used.
        by_instant = rank_ic[int(parameters["lookback"])]
        measured = [by_instant[t] for t in evaluate if t in by_instant]
        return math.fsum(measured) / len(measured) if measured else 0.0

    optimized = walk_forward_optimize(study, design, mean_rank_ic, splits, produced_at=PRODUCED_AT)
    print()
    print("2. Walk-forward optimization of the momentum lookback")
    print(f"   {len(splits.folds)} folds; {optimized.trials} evaluations to select")
    for fold in optimized.folds:
        print(
            f"   fold {fold.fold}: selected {fold.selected:<12} "
            f"validation IC {fold.validation_score:+.4f}   test IC {fold.test_score:+.4f}"
        )
    chosen = int(optimized.folds[-1].parameters["lookback"])
    oos_ic = optimized.result.metrics["wfo.test.mean"]
    print(f"   mean out-of-sample IC {oos_ic:+.4f}; the latest fold's choice: {chosen} sessions")
    print(f"   study result id {optimized.result.result_id[:32]}...")

    # ------------------------------------------------------------------
    # 3. Construction at the boundary, from research-half data only
    # ------------------------------------------------------------------

    last = RESEARCH_SESSIONS - 1
    window = {
        symbol: [
            closes[symbol].values[i] / closes[symbol].values[i - 1] - 1.0
            for i in range(last - COVARIANCE_WINDOW + 1, last + 1)
        ]
        for symbol in symbols
    }
    covariance = CovarianceMatrix.ledoit_wolf(
        window, currency="USD", period="1D", source="research panel daily closes"
    )
    signal = compute_panel(momentum(chosen), frame)
    scores = {
        symbol: value
        for symbol, value in signal.cross_section(instants[last]).items()
        if value is not None
    }
    mean = math.fsum(scores.values()) / len(scores)
    spread = math.sqrt(math.fsum((v - mean) ** 2 for v in scores.values()) / (len(scores) - 1))
    ic_used = max(oos_ic, 0.0)
    volatility = {
        symbol: math.sqrt(covariance.values[i][i]) for i, symbol in enumerate(covariance.assets)
    }
    expected = ExpectedReturns(
        {
            symbol: ic_used * volatility[symbol] * (scores[symbol] - mean) / spread
            for symbol in scores
        },
        "USD",
        "1D",
        "IC x volatility x score (Grinold and Kahn), out-of-sample IC",
    )
    result = construct(
        ConstructionProblem(
            covariance,
            MeanVariance(
                expected,
                risk_aversion=2.0,
                costs=LinearCosts(
                    dict.fromkeys(symbols, 0.0), dict.fromkeys(symbols, COST_PER_WEIGHT)
                ),
            ),
            ConstraintSet(ExposureRange.exactly(INVESTED), WeightBounds.long_only(CAP)),
            SolverSettings(1e-10, 1e-10, 500),
        )
    )
    assert result.weights is not None, result.diagnostics.detail
    prices = {symbol: Decimal(str(closes[symbol].values[last])) for symbol in symbols}
    lots = round_to_lots(
        result.weights,
        capital=CAPITAL,
        prices=prices,
        economics=dict.fromkeys(symbols, CASH_EQUITY),
        quantum=Decimal("1"),
    )
    print()
    print("3. Construction at the boundary")
    verdict = (
        "the signal failed out of sample: no weight, a minimum-variance book"
        if ic_used == 0.0
        else "the signal's out-of-sample IC scales the expected returns"
    )
    print(f"   IC used {ic_used:+.4f} -- {verdict}")
    print(
        f"   Ledoit-Wolf covariance over {COVARIANCE_WINDOW} sessions: "
        f"{(covariance.derivation or '')[:46]}..."
    )
    cost = result.diagnostics.transaction_cost
    shown = "-" if cost is None else f"{cost:.2e}"
    print(f"   status {result.status.name}; transaction cost {shown}")
    for symbol in sorted(result.weights):
        weight = result.weights[symbol]
        if lots.quantities[symbol] == 0 and abs(weight) < 1e-9:
            continue
        print(
            f"   {symbol:<5} weight {weight:6.2%} -> {lots.quantities[symbol]:>6} shares "
            f"({lots.weights[symbol]:6.2%})"
        )
    print(f"   left uninvested by rounding: {lots.residual_weight:.4%} of capital")

    # ------------------------------------------------------------------
    # 4. Trade it through the canonical path, out of sample
    # ------------------------------------------------------------------

    records = tuple(
        InstrumentRecord(symbol, AssetType.EQUITY, "XNYS", "USD", aliases={PROVIDER: symbol})
        for symbol in symbols
    )
    registry = register_instruments(InstrumentRegistry(), records)
    asset_of = {record.symbol: record.asset_id for record in records}
    book = {asset_of[s]: q for s, q in lots.quantities.items() if q != 0}
    normalization = NormalizationPolicy(
        bar_stamp=BarStamp.INTERVAL_END,
        venue="SIM",
        currency="USD",
        timeframe=TimeFrame.D1,
        identity=registry,
        provider=PROVIDER,
    )
    traded = backtest(
        run_config(registry, instants[0] - 1.0),
        dataset,
        running(HoldTheBook(book, boundary)),
        context_factory,
        normalization,
    )
    print()
    print("4. Trading the book, sessions 121-200")
    print(f"   orders {len(traded.orders)}, fills {len(traded.fills)}")
    print(f"   ending equity {traded.valuation.equity}")

    # ------------------------------------------------------------------
    # 5. Against a benchmark
    # ------------------------------------------------------------------

    equity = {
        snapshot.timestamp: snapshot.total_equity
        for snapshot in traded.equity_curve
        if snapshot.timestamp >= boundary
    }
    first_prices = {symbol: closes[symbol].values[RESEARCH_SESSIONS] for symbol in symbols}
    benchmark = {
        instants[i]: math.fsum(
            closes[symbol].values[i] / first_prices[symbol] for symbol in symbols
        )
        / len(symbols)
        for i in range(RESEARCH_SESSIONS, len(instants))
    }
    statistics = benchmark_statistics(
        equity, benchmark, BenchmarkBasis(periods_per_year=252.0, risk_free_rate=0.0)
    )
    print()
    print("5. Against an equal-weighted buy-and-hold of the same ten names")
    print(f"   aligned sessions {statistics.observations}")
    print(f"   relative return  {statistics.relative_return:+.4%}")
    if statistics.beta is not None and statistics.tracking_error is not None:
        print(
            f"   beta {statistics.beta:.3f}; tracking error {statistics.tracking_error:.2%} a year"
        )
    if statistics.information_ratio is not None:
        print(f"   information ratio {statistics.information_ratio:+.2f}")
    print()
    print("Eighty sessions of a synthetic panel: a demonstration of the path, not evidence.")


if __name__ == "__main__":
    main()
