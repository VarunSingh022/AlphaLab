"""Shared harness for driving the real execution pipeline in integration tests.

Nothing here is a stub of a pipeline stage: every test that uses this harness
runs the actual strategy -> allocation -> risk -> OMS -> execution -> portfolio
path. Only the strategy itself is scripted, so a test can state exactly which
intent it wants at which market event.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting.config import BacktestConfig
from alphalab.backtesting.dataset import MarketDataset
from alphalab.execution.policy import FillPolicy, ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
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
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy, StrategyProtocol
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

START_CASH = Decimal("1000000")


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


class ScriptedStrategy(BaseStrategy):
    """Emits a pre-scripted signed quantity delta at chosen quote timestamps.

    ``plan`` maps a quote timestamp to the signed quantity to trade at it:
    positive buys, negative sells. Timestamps absent from the plan produce no
    intent, which is how a test holds a position across a mark-to-market event.
    ``asset_for`` optionally redirects an entry to a different asset, which the
    missing-market-price test uses to trade an asset that never had a quote.
    """

    def __init__(
        self,
        strategy_id: str,
        asset_id: str,
        plan: Mapping[float, Decimal],
        asset_for: Mapping[float, str] | None = None,
    ) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._plan = dict(plan)
        self._asset_for = dict(asset_for or {})

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        timestamp = event.quote.timestamp
        delta = self._plan.get(timestamp)
        if delta is None:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_for.get(timestamp, self._asset_id),
                target=delta,
                timestamp=timestamp,
            ),
        )


def context_factory(strategy_id: str) -> StrategyContext:
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


def running_strategy_state(strategy_id: str, strategy: StrategyProtocol) -> StrategyRuntimeState:
    state = register_strategy(create_runtime(), strategy_id, strategy)
    strategy_state = state.strategies[strategy_id]
    configured, _ = RuntimeSupervisor.configure(strategy_state, {}, 1.0)
    initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
    subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"quotes"}), 1.2)
    running, _ = RuntimeSupervisor.start(subscribed, 1.3)
    return replace(state, strategies={strategy_id: running})


def permissive_risk_limits(max_order_quantity: Decimal = Decimal("100000")) -> RiskLimits:
    """Limits wide enough that risk approves anything but an oversized order."""

    huge = Decimal("100000000")
    return RiskLimits(
        order_size=OrderSizeLimit(max_order_quantity, huge),
        position=PositionLimit(huge, huge),
        exposure=ExposureLimit(huge, huge),
        leverage=LeverageLimit(Decimal("1000")),
        margin=MarginLimit(Decimal("1.00")),
        daily_loss=DailyLossLimit(huge),
        drawdown=DrawdownLimit(Decimal("1.00")),
    )


def pipeline_config(
    strategy_id: str,
    starting_cash: Decimal = START_CASH,
    simulator: ExecutionSimulator | None = None,
    risk_limits: RiskLimits | None = None,
) -> ExecutionPipelineConfig:
    return ExecutionPipelineConfig(
        account=Account("acct-v21", "USD", "v2.1 Integration Account", 1.0),
        starting_cash=starting_cash,
        budget=CapitalBudget(
            global_capital=starting_cash,
            maximum_exposure=starting_cash * Decimal("10"),
            cash_buffer=Decimal("0"),
            strategy_budgets={strategy_id: starting_cash},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True, enforce_integer_quantities=False
        ),
        risk_limits=risk_limits if risk_limits is not None else permissive_risk_limits(),
        simulator=simulator if simulator is not None else ExecutionSimulator(),
    )


def quote(asset_id: str, timestamp: float, mid: Decimal, spread: Decimal = Decimal("0")) -> Quote:
    """A quote whose mid -- the price the pipeline trades and marks at -- is ``mid``."""

    half = spread / Decimal("2")
    return Quote(
        asset_id=asset_id,
        timestamp=timestamp,
        bid=mid - half,
        ask=mid + half,
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        venue="SIM",
        currency="USD",
    )


# ---------------------------------------------------------------------------
# Backtest / replay helpers (v2.2)
# ---------------------------------------------------------------------------


def sized_quote(
    asset_id: str,
    timestamp: float,
    mid: Decimal,
    size: Decimal,
    spread: Decimal = Decimal("0"),
) -> Quote:
    """A quote showing exactly ``size`` on both sides, for liquidity policies."""

    half = spread / Decimal("2")
    return Quote(
        asset_id=asset_id,
        timestamp=timestamp,
        bid=mid - half,
        ask=mid + half,
        bid_size=size,
        ask_size=size,
        venue="SIM",
        currency="USD",
    )


def backtest_config(
    strategy_id: str,
    seed: int | None = 20220,
    fill_policy: FillPolicy | None = None,
    starting_cash: Decimal = START_CASH,
    simulator: ExecutionSimulator | None = None,
    risk_limits: RiskLimits | None = None,
    compile_analytics: bool = True,
) -> BacktestConfig:
    """A seeded backtest config over the shared permissive pipeline config."""

    return BacktestConfig(
        pipeline=pipeline_config(strategy_id, starting_cash, simulator, risk_limits),
        fill_policy=fill_policy if fill_policy is not None else ImmediateFill(),
        seed=seed,
        start_timestamp=1.0,
        compile_analytics=compile_analytics,
    )


def dataset_of_quotes(
    asset_id: str,
    mids: Sequence[Decimal],
    dataset_id: str = "DS",
    first_timestamp: float = 2.0,
    size: Decimal = Decimal("100"),
) -> MarketDataset:
    """One quote per mid, one second apart, starting at ``first_timestamp``."""

    return MarketDataset.of(
        dataset_id,
        [
            sized_quote(asset_id, first_timestamp + index, mid, size)
            for index, mid in enumerate(mids)
        ],
    )


def scripted_run(
    plan: Mapping[float, Decimal],
    mids: Sequence[Decimal],
    strategy_id: str,
    asset_id: str,
    **config_kwargs: object,
) -> tuple[BacktestConfig, MarketDataset, StrategyRuntimeState]:
    """Everything one scripted backtest needs, built consistently."""

    config = backtest_config(strategy_id, **config_kwargs)  # type: ignore[arg-type]
    dataset = dataset_of_quotes(asset_id, mids)
    state = running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan))
    return config, dataset, state
