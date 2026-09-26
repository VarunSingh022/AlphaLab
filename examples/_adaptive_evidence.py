"""Shared setup for the adaptive-strategy examples (54 and 55).

The adaptive strategy both examples run, the configuration it learns under, and
the execution path its backtests take -- kept here for the reason
``_strategy_evidence.py`` exists: example 55's rerun has to build exactly the
strategy example 54 builds, and a second copy would be a second strategy.

The strategy is the smallest useful one. It reads AAA's daily closes from the
point-in-time price panel, keeps the last five in a
:class:`~alphalab.strategy.TrailingZScoreRule`, and asks for ten shares against
any close more than one standard deviation from their mean. Its two methods say
which market event becomes an observation and what a decision means as an
intent; :class:`~alphalab.strategy.AdaptiveStrategy` does the rest through the
same function a research replay folds. Nothing here reads a clock or draws a
random number.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from _point_in_time import SYMBOLS

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import backtest, to_market_dataset
from alphalab.backtesting.state import BacktestResult
from alphalab.core.enums import AssetType
from alphalab.data.dataset import Dataset
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import EngineIdentity
from alphalab.market.bar import Bar as MarketBar
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
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
from alphalab.runtime.run import ExecutionMode, RunConfig
from alphalab.strategy import (
    AdaptationMode,
    AdaptiveConfiguration,
    AdaptiveDecision,
    AdaptiveObservation,
    AdaptiveState,
    AdaptiveStrategy,
    DecisionTiming,
    StrategyClassRegistry,
    TrailingZScoreRule,
    UpdateCadence,
    runtime_for,
)
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from alphalab.studio.strategy import StrategyDefinition

STRATEGY_ID = "EX-ADAPTIVE"
PROVIDER = "pit-vendor"
SEED = 540_000
CASH = Decimal("1000000")

#: Every name on the panel is registered -- a bar for an unregistered symbol has
#: no canonical asset and is refused -- and the strategy trades AAA.
RECORDS = {
    symbol: InstrumentRecord(symbol, AssetType.EQUITY, "XNYS", "USD", aliases={PROVIDER: symbol})
    for symbol in SYMBOLS
}
INSTRUMENTS: InstrumentRegistry = register_instruments(InstrumentRegistry(), RECORDS.values())
ASSET_ID = RECORDS["AAA"].asset_id
NORMALIZATION = NormalizationPolicy(
    venue="XNYS", currency="USD", timeframe=TimeFrame.D1, identity=INSTRUMENTS, provider=PROVIDER
)

#: Which stream the observations come from. Part of every observation's identity.
STREAM = f"{PROVIDER}:{ASSET_ID}"

#: The registered parameters: the window the z-score is taken over, and how far
#: from the window's mean a close must be before the strategy trades against it.
DEFINITION = StrategyDefinition(
    strategy_id=STRATEGY_ID,
    name="Adaptive Reversion",
    version="1",
    author="examples",
    description="trades AAA against its own trailing z-score",
    parameters={"window": 5.0, "entry": 1.0},
)
RULE = TrailingZScoreRule()

#: The engine the examples fingerprint under, stated so their printed identities
#: are the same on every machine; a caller records ``running_engine()``.
ENGINE = EngineIdentity("alphalab", "3.7.0")

#: The strategy's source, which its code identity is derived from.
SOURCES: Mapping[str, bytes] = {"_adaptive_evidence.py": Path(__file__).read_bytes()}


def configuration_for(
    parameters: Mapping[str, float],
    cadence: UpdateCadence = UpdateCadence.EVERY_OBSERVATION,
    every: int | None = None,
) -> AdaptiveConfiguration:
    """What learns and how, read from the registered parameters."""

    return AdaptiveConfiguration(
        name="aaa_reversion",
        rule_id=RULE.rule_id,
        rule_version=RULE.rule_version,
        inputs=("price",),
        parameters={"window": parameters["window"], "entry": parameters["entry"]},
        cadence=cadence,
        cadence_every=every,
        cadence_seconds=None,
        decision_timing=DecisionTiming.BEFORE_UPDATE,
        warmup=5,
    )


CONFIGURATION = configuration_for(DEFINITION.parameters)


class Reversion(AdaptiveStrategy):
    """Reads AAA's closes as observations; asks for ten shares against the signal."""

    def observation_for(self, event: Any, sequence: int) -> AdaptiveObservation | None:
        bar = getattr(event, "bar", None)
        if bar is None or bar.asset_id != ASSET_ID:
            return None
        return AdaptiveObservation(
            float(bar.timestamp), sequence, STREAM, {"price": float(bar.close)}
        )

    def intents_for(
        self, decision: AdaptiveDecision, event: Any, context: StrategyContext
    ) -> Iterable[Intent]:
        signal = decision.outputs.get("signal")
        if not signal:
            return ()
        return (
            Intent(
                strategy_id=self.strategy_id,
                instrument=ASSET_ID,
                target=Decimal(str(signal)) * Decimal("10"),
                timestamp=event.bar.timestamp,
            ),
        )


def reversion_factory(strategy_id: str, parameters: Mapping[str, float], /) -> StrategyProtocol:
    """How the class registry builds the strategy from its registered parameters."""

    return Reversion(
        strategy_id, configuration_for(parameters), RULE, AdaptationMode.LEARNING, None
    )


#: The one mapping from the strategy's identity to the code that runs it.
CLASSES = StrategyClassRegistry().register(STRATEGY_ID, reversion_factory)


def traded_rows(prices: Dataset) -> list[tuple[float, dict[str, float]]]:
    """AAA's closes as observation rows, read exactly as the run reads them."""

    return [
        (float(record.timestamp), {"price": float(record.payload.close)})
        for record in to_market_dataset(prices, NORMALIZATION).records
        if isinstance(record.payload, MarketBar) and record.payload.asset_id == ASSET_ID
    ]


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def context_factory(strategy_id: str) -> StrategyContext:
    """The context each strategy is dispatched with. It reads nothing here."""

    return StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=NoRiskView(),
        config={"strategy_id": strategy_id},
        orders=NoOrders(),
    )


def running(initial: AdaptiveState | None) -> RuntimeState:
    """A fresh strategy, started.

    With no ``initial`` state the class registry builds it from the registered
    parameters, as a real run would; with one, it starts from that state.
    """

    if initial is None:
        state = runtime_for(CLASSES, (DEFINITION,))
    else:
        instance = Reversion(STRATEGY_ID, CONFIGURATION, RULE, AdaptationMode.LEARNING, initial)
        state = register_strategy(create_runtime(), STRATEGY_ID, instance)
    strategy = state.strategies[STRATEGY_ID]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"bars"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy})


def run_config() -> RunConfig:
    """The configuration every adaptive backtest here is driven with."""

    wide = Decimal("100000000")
    return RunConfig(
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX", "USD", "Examples 54-55", 1.0),
            starting_cash=CASH,
            budget=CapitalBudget(
                global_capital=CASH,
                maximum_exposure=CASH * 2,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=False
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(Decimal("1000"), wide),
                position=PositionLimit(wide, wide),
                exposure=ExposureLimit(wide, wide),
                leverage=LeverageLimit(Decimal("10")),
                margin=MarginLimit(Decimal("1.00")),
                daily_loss=DailyLossLimit(wide),
                drawdown=DrawdownLimit(Decimal("1.00")),
            ),
            simulator=ExecutionSimulator(),
            instruments=INSTRUMENTS,
        ),
        mode=ExecutionMode.BACKTEST,
        seed=SEED,
        start_timestamp=1.0,
    )


def run_backtest(
    prices: Dataset, initial: AdaptiveState | None
) -> tuple[BacktestResult, Reversion]:
    """One backtest through the execution path, and the strategy that ran in it."""

    runtime = running(initial)
    strategy = runtime.strategies[STRATEGY_ID].instance
    if not isinstance(strategy, Reversion):
        raise TypeError(f"expected the Reversion strategy, got {type(strategy).__name__}")
    result = backtest(run_config(), prices, runtime, context_factory, NORMALIZATION)
    return result, strategy
