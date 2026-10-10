"""
AlphaLab Examples
=================

Example 70 : Build, Run, Check and Reproduce a Strategy

Difficulty : Beginner

Estimated Time : 15 minutes

Topics
------

• Ingesting rows into a versioned dataset, refusing anything malformed
• Writing a strategy as a class with one hook
• Starting it, running it, and reading what it did
• Checking every fill and the accounting against arithmetic done by hand
• A run with no signal, and parameters and data that are refused
• Running it again, continuing it from a snapshot, and naming it by its digests

What this shows
---------------

The whole public path a new strategy takes, using only supported interfaces and
no host application, vendor or network: ``alphalab.api`` for data and the run,
``alphalab.strategy`` for the strategy, ``alphalab.runtime.run_snapshot`` for a
durable run and ``alphalab.lifecycle.digest_run`` for its identity.

The prices below are **synthetic** -- twelve daily closes written out by hand so
that every signal can be followed with a pencil. Nothing here says anything
about whether a moving-average crossover makes money.

The strategy holds ``size`` shares while the fast moving average of closes is
above the slow one, and none otherwise. It states the position it wants
(``TARGET_QUANTITY``); allocation turns the difference from what it holds into
an order. Fills are simulated at the close of the bar that decided them
(``FillTiming.SAME_EVENT``, recorded in the result) with a commission of one
cent a share. ``tests/integration/test_strategy_building_end_to_end.py`` runs
this file's functions and asserts what it prints.

Run

    python examples/70_build_a_strategy.py
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import backtest, ingest_rows, to_market_dataset, validate_dataset
from alphalab.backtesting import BacktestEngine, BacktestResult, ExecutionMode, RunConfig
from alphalab.common.ids import id_scope
from alphalab.core.enums import AssetType
from alphalab.data import (
    REFUSE_EVERYTHING,
    DataAssetClass,
    DataQualityError,
    Dataset,
    IngestionRequest,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)
from alphalab.data.time import BarStamp
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.policy import FillTiming
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import digest_run
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.persistence import StateDecodeError, deserialize, serialize
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
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import (
    BaseStrategy,
    DiscardingLogger,
    FixedClock,
    Intent,
    IntentKind,
    RuntimeState,
    StrategyContext,
    context_factory,
    create_runtime,
    start_strategy,
)

STRATEGY_ID = "EX70-MA-CROSS"
SYMBOL = "SYNTH"
PROVIDER = "synthetic"
SEED = 70
START_CASH = Decimal("100000.00")
COMMISSION_PER_SHARE = Decimal("0.01")
DAY = 86_400.0
#: 2024-01-02 00:00 UTC; each bar is stamped at the end of its day.
FIRST_BAR = 1_704_153_600.0

#: Twelve synthetic daily closes: flat, a rise, a fall. Hand-written, not market data.
TRENDING = ("100", "100", "100", "100", "101", "103", "106", "104", "100", "96", "95", "95")
#: Twelve closes with nothing in them: the fast average never rises above the slow.
FLAT = ("100",) * 12

#: What the symbol is: an equity listed on a made-up venue, settling in dollars.
INSTRUMENT = InstrumentRecord(SYMBOL, AssetType.EQUITY, "XSYN", "USD", aliases={PROVIDER: SYMBOL})
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (INSTRUMENT,))
NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="XSYN",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=REGISTRY,
    provider=PROVIDER,
)
CONTEXTS = context_factory(FixedClock(FIRST_BAR), DiscardingLogger())


# --------------------------------------------------------------------------- #
# 1. Data
# --------------------------------------------------------------------------- #


def rows_of(closes: Sequence[str]) -> list[dict[str, object]]:
    """One bar per close, open = high = low = close, so the bar is the close."""

    return [
        {
            "timestamp": str(FIRST_BAR + index * DAY),
            "symbol": SYMBOL,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": "1000000",
        }
        for index, close in enumerate(closes)
    ]


def dataset_of(rows: Sequence[Mapping[str, object]]) -> Dataset:
    """Ingest under ``REFUSE_EVERYTHING``: a defect of any kind stops it."""

    request = IngestionRequest(
        name="EX70-SYNTHETIC",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "synthetic://example-70", b"", FIRST_BAR, "application/json"
        ),
        frequency=TimeFrequency.DAILY,
        bar_stamp=BarStamp.INTERVAL_END,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=REFUSE_EVERYTHING,
        price_basis=PriceBasis.RAW,
    )
    return ingest_rows(rows, request).dataset


# --------------------------------------------------------------------------- #
# 2. The strategy
# --------------------------------------------------------------------------- #


class MovingAverageCrossover(BaseStrategy):
    """Hold ``size`` shares while the fast average of closes is above the slow one.

    Its parameters are checked where they are given: a crossover whose fast
    window is not shorter than its slow one compares an average with itself.

    The closes it has seen are its memory, and it declares them -- the three
    ``StrategyStateProtocol`` members at the end -- so a run snapshot carries
    them and a restored instance continues where this one stopped.
    """

    def __init__(self, asset_id: str, fast: int, slow: int, size: Decimal) -> None:
        if not 0 < fast < slow:
            raise ValueError(f"need 0 < fast < slow, got fast={fast}, slow={slow}")
        if not size > 0:
            raise ValueError(f"size must be positive, got {size}")
        self._asset_id = asset_id
        self._fast = fast
        self._slow = slow
        self._size = size
        self._closes: list[Decimal] = []

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        self._closes.append(bar.close)
        if len(self._closes) < self._slow:
            return ()
        fast = sum(self._closes[-self._fast :]) / self._fast
        slow = sum(self._closes[-self._slow :]) / self._slow
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=self._asset_id,
                target=self._size if fast > slow else Decimal("0"),
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )

    def strategy_state_version(self) -> int:
        return 1

    def capture_state(self) -> dict[str, list[str]]:
        return {"closes": [str(close) for close in self._closes]}

    def restore_state(self, payload: Any, version: int) -> None:
        if version != 1:
            raise ValueError(f"MovingAverageCrossover cannot read state version {version}")
        self._closes = [Decimal(close) for close in payload["closes"]]


def strategy() -> MovingAverageCrossover:
    return MovingAverageCrossover(INSTRUMENT.asset_id, fast=2, slow=4, size=Decimal("10"))


def runtime(instance: BaseStrategy) -> RuntimeState:
    """The strategy, registered and running: configured, initialized, subscribed to bars."""

    return start_strategy(
        create_runtime(),
        STRATEGY_ID,
        instance,
        config={},
        subscriptions={"bars"},
        at=FIRST_BAR - 1.0,
    )


def run_config(seed: int = SEED, commission: Decimal = COMMISSION_PER_SHARE) -> RunConfig:
    """Capital, limits and costs -- every one stated, none assumed."""

    ample = Decimal("1000000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX70", "USD", "Example 70", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=False, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("1")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(ample, "UTC"),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            simulator=ExecutionSimulator(commission_model=PerShareCommission(commission)),
            instruments=REGISTRY,
            fill_timing=FillTiming.SAME_EVENT,
        ),
        seed=seed,
        start_timestamp=FIRST_BAR - 1.0,
    )


def run(closes: Sequence[str], seed: int = SEED) -> BacktestResult:
    """Ingest, start and run: the three calls a backtest is."""

    return backtest(
        run_config(seed), dataset_of(rows_of(closes)), runtime(strategy()), CONTEXTS, NORMALIZATION
    )


# --------------------------------------------------------------------------- #
# 3. The same thing by hand: what the run must have done
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HandFill:
    side: str
    quantity: Decimal
    price: Decimal
    commission: Decimal


@dataclass(frozen=True)
class HandResult:
    fills: tuple[HandFill, ...]
    cash: Decimal
    position: Decimal
    realized_pnl: Decimal
    commission: Decimal


def by_hand(
    closes: Sequence[str], fast: int = 2, slow: int = 4, size: Decimal = Decimal("10")
) -> HandResult:
    """The crossover and the accounting, in plain arithmetic, independent of the engine.

    Same-event fills: an order fills at the close that decided it. Average-cost
    accounting: a sale realizes (price - average cost) on what it closes.
    """

    prices = [Decimal(close) for close in closes]
    held, cost, cash, realized, paid = Decimal(0), Decimal(0), START_CASH, Decimal(0), Decimal(0)
    fills: list[HandFill] = []
    for index in range(slow - 1, len(prices)):
        window = prices[: index + 1]
        target = size if sum(window[-fast:]) / fast > sum(window[-slow:]) / slow else Decimal(0)
        delta, price = target - held, prices[index]
        if delta == 0:
            continue
        commission = abs(delta) * COMMISSION_PER_SHARE
        if delta > 0:
            cost = (cost * held + price * delta) / (held + delta)
        else:
            realized += (price - cost) * -delta
        held += delta
        cash -= delta * price + commission
        paid += commission
        fills.append(HandFill("buy" if delta > 0 else "sell", abs(delta), price, commission))
    return HandResult(tuple(fills), cash, held, realized.quantize(Decimal("0.01")), paid)


def fills_of(result: BacktestResult) -> tuple[HandFill, ...]:
    return tuple(
        HandFill(fill.side.value, fill.quantity, fill.price, fill.commission)
        for fill in result.fills
    )


# --------------------------------------------------------------------------- #
# 4. A durable run: stop after some records, continue from the payload
# --------------------------------------------------------------------------- #


def continued(
    closes: Sequence[str], stop_after: int, commission: Decimal = COMMISSION_PER_SHARE
) -> BacktestResult:
    """Run ``stop_after`` records, serialize the run, restore it with fresh objects, finish it.

    The loop is :meth:`BacktestEngine.run`'s, written out: each record first
    closes the instant before it, then advances; the end of the data closes the
    last, and the result is finalized inside the run's identifier scope. Objects
    the snapshot records but does not carry -- the sizing model, the simulator,
    the strategy, the fill policy -- are built again, as a fresh process would;
    the strategy's declared state comes back from the payload. ``commission``
    lets the caller supply a *differently configured* simulator, which
    ``restore`` refuses.
    """

    config = run_config()
    records = to_market_dataset(dataset_of(rows_of(closes)), NORMALIZATION).records
    assert config.seed is not None
    with id_scope(config.seed):
        # A run records the dataset it consumed, as BacktestEngine.run does.
        state = replace(
            BacktestEngine.initialize(config, runtime(strategy())),
            source_id=dataset_of(rows_of(closes)).dataset_version,
        )
        for record in records[:stop_after]:
            state = BacktestEngine.close_slice(state, CONTEXTS, before=record.timestamp)
            state, _ = BacktestEngine.advance(state, record, CONTEXTS)
    payload = serialize(capture(state))

    fresh_config = run_config(commission=commission)
    restored = restore(
        from_primitives(deserialize(payload)),
        RunObjects(
            pipeline=RuntimeObjects(
                sizing_model=fresh_config.pipeline.sizing_model,
                simulator=fresh_config.pipeline.simulator,
                strategies={STRATEGY_ID: strategy()},
                instruments=REGISTRY,
            ),
            fill_policy=fresh_config.fill_policy,
        ),
    )
    with BacktestEngine.resume(restored):
        for record in records[stop_after:]:
            restored = BacktestEngine.close_slice(restored, CONTEXTS, before=record.timestamp)
            restored, _ = BacktestEngine.advance(restored, record, CONTEXTS)
        return BacktestEngine.finalize(BacktestEngine.close_slice(restored, CONTEXTS))


# --------------------------------------------------------------------------- #
# 5. What is refused
# --------------------------------------------------------------------------- #


def refusals() -> dict[str, str]:
    """Each invalid input, and the error that refused it."""

    refused: dict[str, str] = {}

    def attempt(label: str, action: Any) -> None:
        try:
            action()
        except (ValueError, DataQualityError, StateDecodeError) as error:
            refused[label] = f"{type(error).__name__}: {str(error)[:90]}"
        else:
            refused[label] = "ACCEPTED"

    attempt(
        "fast window not shorter than slow",
        lambda: MovingAverageCrossover(INSTRUMENT.asset_id, fast=4, slow=4, size=Decimal("10")),
    )
    attempt(
        "a close that is not a number",
        lambda: dataset_of(rows_of((*TRENDING[:3], "NaN", *TRENDING[4:]))),
    )
    attempt("a missing close", lambda: dataset_of(rows_of((*TRENDING[:3], "", *TRENDING[4:]))))
    attempt("a negative close", lambda: dataset_of(rows_of((*TRENDING[:3], "-1", *TRENDING[4:]))))
    attempt(
        "continuing under another commission",
        lambda: continued(TRENDING, stop_after=6, commission=Decimal("5.00")),
    )
    return refused


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    print("=" * 70)
    print("AlphaLab Example 70 : Build, Run, Check and Reproduce a Strategy")
    print("=" * 70)
    print("Synthetic prices, hand-written; no claim about profitability.")

    dataset = dataset_of(rows_of(TRENDING))
    assert validate_dataset(dataset) == (), "a clean dataset has no findings"
    print()
    print("1. Data")
    print(f"   bars            : {len(dataset.records)}")
    print(f"   dataset version : {dataset.dataset_version}")

    result = run(TRENDING)
    print()
    print("2. The run")
    print(f"   records         : {result.records_processed}")
    print(f"   orders          : {len(result.orders)}")
    print(f"   failures        : {len(result.strategy_failures)}")
    for fill in fills_of(result):
        print(f"     {fill.side:<4} {fill.quantity} @ {fill.price}  commission {fill.commission}")

    expected = by_hand(TRENDING)
    valuation = result.valuation
    position = result.state.portfolio.positions.get(INSTRUMENT.asset_id)
    held = Decimal(0) if position is None else position.quantity
    print()
    print("3. Checked by hand")
    assert fills_of(result) == expected.fills, (fills_of(result), expected.fills)
    assert valuation.cash == expected.cash, (valuation.cash, expected.cash)
    assert valuation.realized_pnl == expected.realized_pnl
    assert held == expected.position
    assert result.state.portfolio.commission_paid["USD"] == expected.commission
    # Equity is cash plus what is held, marked at the last close.
    assert valuation.equity == valuation.cash + held * Decimal(TRENDING[-1])
    print(f"   fills           : {len(expected.fills)} of {len(expected.fills)} as computed")
    print(f"   ending cash     : {valuation.cash} (by hand {expected.cash})")
    print(f"   realized P&L    : {valuation.realized_pnl} (by hand {expected.realized_pnl})")
    print(f"   commission      : {expected.commission}")
    print(f"   position        : {held}")
    report = result.report
    assert report is not None and report.returns.total_return is not None
    print(f"   total return    : {report.returns.total_return:+.6f}")
    print(f"   closed trades   : {report.trades.closed_trades}")
    print(f"   fill timing     : {result.execution_assumptions.fill_timing.value}")

    flat = run(FLAT)
    assert not flat.orders and not flat.fills and flat.valuation.cash == START_CASH
    assert by_hand(FLAT).fills == ()
    print()
    print("4. No signal")
    print(f"   orders {len(flat.orders)}, fills {len(flat.fills)}, cash {flat.valuation.cash}")

    again, other_seed = run(TRENDING), run(TRENDING, seed=SEED + 1)
    first, second, third = digest_run(result), digest_run(again), digest_run(other_seed)
    assert (first.result_id, first.configuration_id) == (second.result_id, second.configuration_id)
    assert fills_of(other_seed) == fills_of(result) and third.result_id != first.result_id
    resumed = continued(TRENDING, stop_after=6)
    assert digest_run(resumed).result_id == first.result_id, "continuation is byte-identical"
    print()
    print("5. Reproduced")
    print(f"   result id       : {first.result_id}")
    print(f"   configuration id: {first.configuration_id}")
    print("   same inputs     : same result id")
    print("   another seed    : same fills, another result id (identifiers differ)")
    print("   stopped after 6 records, serialized, restored, continued: same result id")

    print()
    print("6. Refused")
    for label, outcome in refusals().items():
        assert outcome != "ACCEPTED", label
        print(f"   {label:<36}: {outcome}")


if __name__ == "__main__":
    main()
