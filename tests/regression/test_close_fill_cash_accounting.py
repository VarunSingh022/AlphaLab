"""Regression guard for D1 (close-fill cash accounting) at the pipeline level.

Before D1, PortfolioEngine.apply_fill added realized P&L to the cash flow a
second time, inflating cash / NAV / analytics / risk state on every closing or
reducing fill. This drives the real ExecutionPipeline through open -> hold ->
close and asserts that, once the position is flat:

    NAV_end == ending_capital == risk.current_nav
            == starting_cash + realized_pnl - total_commissions

The existing pipeline integration test only opens positions, so this path was
previously unprotected.
"""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.execution.commission import FixedCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.market.quote import Quote
from alphalab.portfolio.account import Account
from alphalab.portfolio.nav import NAVCalculator
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
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineConfig
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy, StrategyProtocol
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

QTY = Decimal("10")
START_CASH = Decimal("100000")
COMMISSION = Decimal("1.00")
EXIT_TS = 4.0


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


class _EnterThenExitStrategy(BaseStrategy):
    def __init__(self, strategy_id: str, asset_id: str) -> None:
        self._sid = strategy_id
        self._aid = asset_id
        self._entered = False
        self._exited = False

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        ts = event.quote.timestamp
        if not self._entered:
            self._entered = True
            return (Intent(strategy_id=self._sid, instrument=self._aid, target=QTY, timestamp=ts),)
        if self._entered and not self._exited and ts >= EXIT_TS:
            self._exited = True
            return (Intent(strategy_id=self._sid, instrument=self._aid, target=-QTY, timestamp=ts),)
        return ()


def _context_factory(strategy_id: str) -> StrategyContext:
    return StrategyContext(
        portfolio=object(),
        market=object(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=object(),
        config={},
        orders=object(),
        history=object(),
        universe=object(),
    )


def _running_strategy_state(strategy_id: str, strategy: StrategyProtocol) -> StrategyRuntimeState:
    state = register_strategy(create_runtime(), strategy_id, strategy)
    ss = state.strategies[strategy_id]
    ss, _ = RuntimeSupervisor.configure(ss, {}, 1.0)
    ss, _ = RuntimeSupervisor.initialize(ss, 1.1)
    ss, _ = RuntimeSupervisor.subscribe(ss, frozenset({"quotes"}), 1.2)
    ss, _ = RuntimeSupervisor.start(ss, 1.3)
    return replace(state, strategies={strategy_id: ss})


def _config(strategy_id: str) -> ExecutionPipelineConfig:
    return ExecutionPipelineConfig(
        account=Account("acct-close", "USD", "Close Test", 1.0),
        starting_cash=START_CASH,
        budget=CapitalBudget(
            global_capital=START_CASH,
            maximum_exposure=START_CASH * Decimal("10"),
            cash_buffer=Decimal("0"),
            strategy_budgets={strategy_id: START_CASH},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True, enforce_integer_quantities=False
        ),
        risk_limits=RiskLimits(
            order_size=OrderSizeLimit(Decimal("1000"), Decimal("100000000")),
            position=PositionLimit(Decimal("100000"), Decimal("100000000")),
            exposure=ExposureLimit(Decimal("100000000"), Decimal("100000000")),
            leverage=LeverageLimit(Decimal("1000")),
            margin=MarginLimit(Decimal("1.00")),
            daily_loss=DailyLossLimit(Decimal("100000000")),
            drawdown=DrawdownLimit(Decimal("1.00")),
        ),
        simulator=ExecutionSimulator(commission_model=FixedCommission(COMMISSION)),
    )


def _quote(asset_id: str, ts: float, mid: Decimal) -> Quote:
    return Quote(
        asset_id=asset_id,
        timestamp=ts,
        bid=mid - Decimal("1.00"),
        ask=mid + Decimal("1.00"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        venue="SIM",
        currency="USD",
    )


def test_open_hold_close_leaves_cash_nav_analytics_risk_consistent() -> None:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    state = ExecutionPipeline.initialize(
        _config(strategy_id),
        _running_strategy_state(strategy_id, _EnterThenExitStrategy(strategy_id, asset_id)),
        1.0,
    )

    entry_mid = Decimal("100.00")
    exit_mid = Decimal("120.00")
    r1 = ExecutionPipeline.process_quote(state, _quote(asset_id, 2.0, entry_mid), _context_factory)
    r2 = ExecutionPipeline.process_quote(
        r1.state, _quote(asset_id, 3.0, Decimal("110.00")), _context_factory
    )  # hold
    r3 = ExecutionPipeline.process_quote(
        r2.state, _quote(asset_id, EXIT_TS, exit_mid), _context_factory
    )
    final_state = ExecutionPipeline.compile_analytics(r3.state, 5.0)

    # one buy + one sell, position flat
    assert len(r1.fills) == 1 and r1.fills[0].side.name == "BUY"
    assert r2.fills == ()
    assert len(r3.fills) == 1 and r3.fills[0].side.name == "SELL"
    assert final_state.portfolio.positions == {}

    realized = (exit_mid - entry_mid) * QTY  # 200.00
    total_commission = COMMISSION * 2  # 2.00
    expected = (START_CASH + realized - total_commission).quantize(Decimal("0.01"))  # 100198.00

    final_cash = final_state.portfolio.cash.balance("USD")
    nav_end = NAVCalculator.calculate(
        final_state.portfolio.cash, final_state.portfolio.positions, "USD"
    )
    report = final_state.analytics.reports[-1]

    assert final_cash == expected
    assert nav_end == expected  # flat -> NAV == cash, not inflated by realized P&L
    assert final_state.risk.current_nav == expected  # risk state reflects the corrected NAV
    assert report.ending_capital == expected  # analytics reflects the corrected NAV
