"""S-02's experiment, defined where a spawned worker can load it (ADR-0072, ADR-0074 section 8).

A universe of 21 synthetic daily closes: sixteen independent walks in steps of
eighths, and five partners, each ``10 + 0.75`` times one of the first five walks
plus independent noise in eighths. Its pairs give 200 candidates, the five
planted pairs among them. The last fifteen of 60 days are held back as a
lockbox, so every trial, and the screen, sees the first 45 only.

Each trial's runner fits its pair by Engle and Granger's regression on the 45
days before the lockbox -- its own window -- and trades the spread: long below
one deviation of the fitted mean, short above, flat within half of one. A pair
the test refuses raises AlphaLab's own error, so its trial is refused, and
counted. The planted noise is independent, so the residuals' regression needs
no lagged differences: ``FixedLags(0)``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import ingest_rows, to_market_dataset
from alphalab.backtesting import ExecutionMode, RunConfig
from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
)
from alphalab.backtesting.schedule import EMPTY_SCHEDULE
from alphalab.common.registry import RunnerRegistry
from alphalab.core.enums import AssetType
from alphalab.data import (
    REFUSE_EVERYTHING,
    DataAssetClass,
    IngestionRequest,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)
from alphalab.data.time import BarStamp
from alphalab.econometrics import Deterministic, FixedLags, engle_granger
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.policy import FillTiming
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.instrument.snapshot import registry_digest
from alphalab.lifecycle.experiments import (
    ExperimentDefinition,
    ExperimentSpec,
    FullSample,
    Lockbox,
    TrialInputs,
    TrialLimits,
    TrialSpec,
    dataset_identity,
)
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.portfolio.account import Account
from alphalab.research.walk_forward_optimization import ParameterSpace
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
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.runtime.retention import RetentionPolicy
from alphalab.strategy import (
    BaseStrategy,
    DiscardingLogger,
    FixedClock,
    Intent,
    IntentKind,
    StrategyContext,
    context_factory,
    create_runtime,
    start_strategy,
)

STRATEGY_ID = "S02-PAIRS"
PROVIDER = "synthetic"
DAY = 86_400.0
FIRST_BAR = 1_704_153_600.0
BARS = 60
FITTED = 45
LOCKBOX_START = FIRST_BAR + FITTED * DAY
START_CASH = Decimal("10000000.00")
WALKS = 16
PLANTED = 5
LAGS = FixedLags(0)
CONDITION = 1e10


def _closes() -> dict[str, list[float]]:
    """Walks in steps of eighths from 100, and partners tied to the first five, to the cent."""

    state = 2020

    def draw(modulus: int) -> int:
        nonlocal state
        state = (1103515245 * state + 12345) % 2**31
        return (state >> 8) % modulus

    closes: dict[str, list[float]] = {}
    for i in range(WALKS):
        level, walk = 100.0, []
        for _ in range(BARS):
            level += (draw(17) - 8) / 8
            walk.append(level)
        closes[f"W{i:02d}"] = walk
    for i in range(PLANTED):
        closes[f"P{i:02d}"] = [
            10.0 + 0.75 * value + (draw(9) - 4) / 8 for value in closes[f"W{i:02d}"]
        ]
    return {name: [round(value, 2) for value in series] for name, series in closes.items()}


CLOSES = _closes()
SYMBOLS = sorted(CLOSES)
RECORDS = {
    symbol: InstrumentRecord(symbol, AssetType.EQUITY, "XSYN", "USD", aliases={PROVIDER: symbol})
    for symbol in SYMBOLS
}
INSTRUMENTS: InstrumentRegistry = register_instruments(
    InstrumentRegistry(), tuple(RECORDS.values())
)
ASSETS = {RECORDS[symbol].asset_id: symbol for symbol in SYMBOLS}
NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="XSYN",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=INSTRUMENTS,
    provider=PROVIDER,
)
_REQUEST = IngestionRequest(
    name="S02-UNIVERSE",
    source=raw_source_from_bytes(
        SourceKind.IN_MEMORY, "synthetic://s02-universe", b"", FIRST_BAR, "application/json"
    ),
    frequency=TimeFrequency.DAILY,
    bar_stamp=BarStamp.INTERVAL_END,
    asset_class=DataAssetClass.EQUITY,
    cleaning_policy=REFUSE_EVERYTHING,
    price_basis=PriceBasis.RAW,
)
DATASET = to_market_dataset(
    ingest_rows(
        [
            {
                "timestamp": str(FIRST_BAR + day * DAY),
                "symbol": symbol,
                "open": f"{CLOSES[symbol][day]:.2f}",
                "high": f"{CLOSES[symbol][day]:.2f}",
                "low": f"{CLOSES[symbol][day]:.2f}",
                "close": f"{CLOSES[symbol][day]:.2f}",
                "volume": "1000000",
            }
            for day in range(BARS)
            for symbol in SYMBOLS
        ],
        _REQUEST,
    ).dataset,
    NORMALIZATION,
)
CONTEXTS = context_factory(FixedClock(FIRST_BAR), DiscardingLogger())


@dataclass(frozen=True)
class Pair:
    """A candidate: the dependent is regressed on the regressor."""

    dependent: str
    regressor: str


def _candidates() -> list[Pair]:
    planted = [Pair(f"P{i:02d}", f"W{i:02d}") for i in range(PLANTED)]
    others = [
        Pair(a, b)
        for i, a in enumerate(SYMBOLS)
        for b in SYMBOLS[i + 1 :]
        if Pair(a, b) not in planted and Pair(b, a) not in planted
    ]
    return planted + others[: 200 - PLANTED]


CANDIDATES = _candidates()
#: What the runner fitted, by pair, in this process: read back to show a trial fits its own window.
FITTED_RATIOS: dict[Pair, float] = {}


class PairSpread(BaseStrategy):
    """Long the spread below one deviation of its fitted mean, short above, flat inside a half."""

    def __init__(self, pair: Pair, ratio: float, mean: float, deviation: float) -> None:
        self._pair = pair
        self._ratio = ratio
        self._mean = mean
        self._deviation = deviation
        self._last: dict[str, Decimal] = {}

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        symbol = ASSETS.get(event.bar.asset_id)
        if symbol not in (self._pair.dependent, self._pair.regressor):
            return ()
        self._last[symbol] = event.bar.close
        if symbol != self._pair.regressor or self._pair.dependent not in self._last:
            return ()
        spread = float(self._last[self._pair.dependent]) - self._ratio * float(event.bar.close)
        score = (spread - self._mean) / self._deviation
        units = Decimal(100) if score < -1.0 else Decimal(-100) if score > 1.0 else None
        if units is None and abs(score) > 0.5:
            return ()
        held = units or Decimal(0)
        hedge = Decimal(round(-float(held) * self._ratio))
        return tuple(
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=RECORDS[name].asset_id,
                target=target,
                timestamp=event.bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            )
            for name, target in ((self._pair.dependent, held), (self._pair.regressor, hedge))
        )


def run_config() -> RunConfig:
    ample = Decimal("1000000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-S02", "USD", "S-02", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("2")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(ample, "UTC"),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.01"))),
            instruments=INSTRUMENTS,
            fill_timing=FillTiming.SAME_EVENT,
        ),
        seed=2,
        start_timestamp=FIRST_BAR - 1.0,
    )


def build(trial: TrialSpec) -> TrialInputs:
    """The runner: fit the pair on the days before the lockbox only, then trade its spread.

    A pair the test refuses raises AlphaLab's own error, so the trial is refused, and counted.
    """

    pair: Pair = trial.configuration
    dependent = CLOSES[pair.dependent][:FITTED]
    regressor = CLOSES[pair.regressor][:FITTED]
    test = engle_granger(
        dependent,
        [regressor],
        deterministic=Deterministic.CONSTANT,
        lags=LAGS,
        maximum_condition=CONDITION,
    )
    ratio = test.cointegrating.parameters["beta_1"]
    spread = [y - ratio * x for y, x in zip(dependent, regressor, strict=True)]
    mean = sum(spread) / len(spread)
    deviation = (sum((s - mean) ** 2 for s in spread) / len(spread)) ** 0.5
    FITTED_RATIOS[pair] = ratio
    return TrialInputs(
        config=run_config(),
        dataset=DATASET,
        strategy_state=start_strategy(
            create_runtime(),
            STRATEGY_ID,
            PairSpread(pair, ratio, mean, deviation),
            config={},
            subscriptions={"bars"},
            at=FIRST_BAR - 1.0,
        ),
        context_factory=CONTEXTS,
        schedule=EMPTY_SCHEDULE,
        observations=(),
    )


REGISTRY = RunnerRegistry().register("pairs", build)
_CONFIG = run_config()
SPEC = ExperimentSpec(
    name="s02-pairs-screen",
    hypothesis="a pair the screen selects keeps a stationary spread",
    strategy="pairs@" + "02" * 32,
    configuration_type=Pair,
    space=ParameterSpace.of(
        [{"dependent": p.dependent, "regressor": p.regressor} for p in CANDIDATES]
    ),
    dataset=dataset_identity(DATASET),
    schedule=EMPTY_SCHEDULE.binding,
    instruments=registry_digest(INSTRUMENTS),
    execution=execution_assumptions(_CONFIG.pipeline, _CONFIG.fill_policy),
    capture=CaptureSpec(
        ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.TRUNCATE),
        trace=False,
    ),
    retention=RetentionPolicy(market_history=8, steps=8, audit_events=32, results=8),
    validation=FullSample(Lockbox(LOCKBOX_START, None)),
    seed=2,
    parent_id=None,
    limits=TrialLimits(None),
)

DEFINITION = ExperimentDefinition(SPEC, REGISTRY)
