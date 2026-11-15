"""Example 80's experiment, defined where a spawned worker can load it (ADR-0072).

A spawned worker imports an experiment's definition by its module's name, and a
script's is ``__main__``, so the experiment that ``80_a_ledgered_sweep.py``
runs lives here. Everything it turns on is stated: the hypothesis, the
candidates the research assistant generated, the data, the instruments, the
execution, the capture, what each run keeps, the held-back lockbox and the seed.

The prices are **synthetic**: 300 daily closes from a written-out formula, a
slow wave on a gentle rise, so every run can be reproduced anywhere. Nothing
here says anything about whether a moving-average crossover makes money.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
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
from alphalab.research_assistant import candidate_space, generate_candidates
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

STRATEGY_ID = "EX80-MA-CROSS"
SYMBOL = "SYNTH"
PROVIDER = "synthetic"
START_CASH = Decimal("100000.00")
DAY = 86_400.0
#: 2024-01-02 00:00 UTC; each bar is stamped at the end of its day.
FIRST_BAR = 1_704_153_600.0
BARS = 300
#: The last fifty days are held back from every trial: the lockbox.
LOCKBOX_START = FIRST_BAR + (BARS - 50) * DAY


def close(day: int) -> Decimal:
    """A slow wave on a gentle rise, to the cent."""

    value = 100.0 + 8.0 * math.sin(day / 17.0) + 0.04 * day
    return Decimal(f"{value:.2f}")


INSTRUMENT = InstrumentRecord(SYMBOL, AssetType.EQUITY, "XSYN", "USD", aliases={PROVIDER: SYMBOL})
INSTRUMENTS: InstrumentRegistry = register_instruments(InstrumentRegistry(), (INSTRUMENT,))
NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="XSYN",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=INSTRUMENTS,
    provider=PROVIDER,
)
CONTEXTS = context_factory(FixedClock(FIRST_BAR), DiscardingLogger())


def _rows() -> list[dict[str, object]]:
    return [
        {
            "timestamp": str(FIRST_BAR + day * DAY),
            "symbol": SYMBOL,
            "open": str(close(day)),
            "high": str(close(day)),
            "low": str(close(day)),
            "close": str(close(day)),
            "volume": "1000000",
        }
        for day in range(BARS)
    ]


_REQUEST = IngestionRequest(
    name="EX80-SYNTHETIC",
    source=raw_source_from_bytes(
        SourceKind.IN_MEMORY, "synthetic://example-80", b"", FIRST_BAR, "application/json"
    ),
    frequency=TimeFrequency.DAILY,
    bar_stamp=BarStamp.INTERVAL_END,
    asset_class=DataAssetClass.EQUITY,
    cleaning_policy=REFUSE_EVERYTHING,
    price_basis=PriceBasis.RAW,
)
DATASET = to_market_dataset(ingest_rows(_rows(), _REQUEST).dataset, NORMALIZATION)


@dataclass(frozen=True)
class Crossover:
    """A candidate: the fast and slow windows, in days."""

    fast: int
    slow: int


class MovingAverageCrossover(BaseStrategy):
    """Hold 800 shares while the fast average of closes is above the slow one."""

    def __init__(self, plan: Crossover) -> None:
        if not 0 < plan.fast < plan.slow:
            raise ValueError(f"need 0 < fast < slow, got fast={plan.fast}, slow={plan.slow}")
        self._plan = plan
        self._closes: list[Decimal] = []

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self._closes.append(event.bar.close)
        fast, slow = self._plan.fast, self._plan.slow
        if len(self._closes) < slow:
            return ()
        above = sum(self._closes[-fast:]) / fast > sum(self._closes[-slow:]) / slow
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=INSTRUMENT.asset_id,
                target=Decimal("800") if above else Decimal("0"),
                timestamp=event.bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


def run_config() -> RunConfig:
    """Capital, limits and costs -- every one stated, none assumed."""

    ample = Decimal("1000000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX80", "USD", "Example 80", 1.0),
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
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.01"))),
            instruments=INSTRUMENTS,
            fill_timing=FillTiming.SAME_EVENT,
        ),
        seed=80,
        start_timestamp=FIRST_BAR - 1.0,
    )


def build(trial: TrialSpec) -> TrialInputs:
    """The runner: one trial's inputs from its typed candidate.

    A candidate whose fast window is not shorter than its slow one is refused by
    the strategy, so the trial fails, and is counted.
    """

    strategy = MovingAverageCrossover(trial.configuration)
    return TrialInputs(
        config=run_config(),
        dataset=DATASET,
        strategy_state=start_strategy(
            create_runtime(),
            STRATEGY_ID,
            strategy,
            config={},
            subscriptions={"bars"},
            at=FIRST_BAR - 1.0,
        ),
        context_factory=CONTEXTS,
        schedule=EMPTY_SCHEDULE,
        observations=(),
    )


REGISTRY = RunnerRegistry().register("ma-cross", build)

#: The research assistant's grid: nine candidates, two of them not crossovers at all.
AXES: Mapping[str, Sequence[int]] = {"fast": [3, 5, 21], "slow": [13, 21, 34]}
CANDIDATES = generate_candidates("ma-cross", AXES)

_CONFIG = run_config()
SPEC = ExperimentSpec(
    name="ma-cross-synthetic",
    hypothesis="a fast average above a slow one marks the rising half of the wave",
    strategy="ma-cross@" + "80" * 32,
    configuration_type=Crossover,
    space=candidate_space(CANDIDATES),
    dataset=dataset_identity(DATASET),
    schedule=EMPTY_SCHEDULE.binding,
    instruments=registry_digest(INSTRUMENTS),
    execution=execution_assumptions(_CONFIG.pipeline, _CONFIG.fill_policy),
    capture=CaptureSpec(
        ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.TRUNCATE),
        trace=True,
    ),
    retention=RetentionPolicy(market_history=64, steps=64, audit_events=256, results=256),
    validation=FullSample(Lockbox(LOCKBOX_START, None)),
    seed=80,
    parent_id=None,
    limits=TrialLimits(60.0),
)

DEFINITION = ExperimentDefinition(SPEC, REGISTRY)
