"""A real trading environment wired into AlphaLab's actual production pipeline.

This does not simulate portfolio mechanics separately -- it drives the same
`alphalab.runtime.execution_pipeline.ExecutionPipeline` that production trading
uses: `RiskEngine.evaluate` genuinely runs and can reject an action,
`OMSEngine`/`ExecutionEngine` genuinely process the resulting order, and
`PortfolioEngine`/`NAVCalculator` compute genuine realized and unrealized P&L. An
agent's action becomes a real `alphalab.strategy.events.Intent`, emitted by a real
`alphalab.strategy.protocol.StrategyProtocol` implementation (`RLAgentStrategy`)
registered the same way any other strategy would be -- not a bypass of the strategy
layer, a real (if minimal) participant in it.

Two notes from when this environment was built, kept as history:

* The pipeline at the time marked a position to market only when it filled. It
  has marked every position on every price event since v2.1
  (`tests/integration/test_mark_to_market_pipeline.py`); `step_environment` still
  marks the traded asset at the step's price before computing equity, which is
  now the same figure the pipeline holds, and harmless.
* Building it surfaced a defect in
  `alphalab.portfolio.engine.PortfolioEngine.apply_fill`, which computed
  `cash_impact = -(quantity * price) - commission + pnl` and so double-counted
  realized P&L into cash on a closing or reducing fill. **It was fixed in v2.0.0**
  (CHANGELOG, D1): the `+ pnl` term is gone, and
  `tests/regression/test_close_fill_cash_accounting.py` drives the real pipeline
  open -> hold -> close. This environment's rewards therefore match a hand
  calculation -- `test_environment_round_trip_realizes_correct_pnl` asserts the
  correct +20 on a buy at 150 and a sale at 152. Until v4.1.1 this docstring still
  described the defect as present.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.market.quote import Quote
from alphalab.portfolio.account import Account
from alphalab.portfolio.nav import NAVCalculator
from alphalab.reinforcement_learning.action import Action, action_to_signed_quantity
from alphalab.reinforcement_learning.exceptions import RLInputError
from alphalab.risk.limits import (
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import (
    ContextFactory,
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineState,
)
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.state import RuntimeState, StrategyState, StrategyStatus


@dataclass(frozen=True, slots=True)
class _PendingDecision:
    """The current step's decision, threaded through StrategyContext.config.

    This is how a pure-functional agent action becomes a real Intent without any
    mutable state on the strategy instance itself: a new context (and therefore a
    new _PendingDecision) is built fresh for every step.
    """

    strategy_id: str
    asset_id: str
    signed_quantity: Decimal
    timestamp: float


@dataclass(frozen=True, slots=True)
class _SimpleClock:
    timestamp: float

    def now(self) -> float:
        return self.timestamp


class _NullLogger:
    def info(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


class RLAgentStrategy(BaseStrategy):
    """A real StrategyProtocol implementation whose decision comes from
    context.config on each call, rather than internal logic. The RL agent's action,
    passed in via the environment's context_factory, IS the strategy's decision --
    this class only translates it into a well-formed Intent.
    """

    def on_quote(self, context: StrategyContext, event: Any) -> tuple[Intent, ...]:
        pending = context.config
        if not isinstance(pending, _PendingDecision) or pending.signed_quantity == Decimal("0"):
            return ()
        return (
            Intent(
                strategy_id=pending.strategy_id,
                instrument=pending.asset_id,
                target=pending.signed_quantity,
                timestamp=pending.timestamp,
            ),
        )


def _make_context_factory(pending: _PendingDecision) -> ContextFactory:
    def factory(strategy_id: str) -> StrategyContext:
        return StrategyContext(
            portfolio=NoPortfolio(),
            market=NoMarket(),
            clock=_SimpleClock(pending.timestamp),
            logger=_NullLogger(),
            risk_view=NoRiskView(),
            config=pending,
            orders=NoOrders(),
        )

    return factory


@dataclass(frozen=True, slots=True)
class TradingEnvConfig:
    """Configuration for a single-asset RL trading environment.

    Attributes:
        asset_id: The one asset this environment trades.
        strategy_id: Identifier for the RL agent's registered strategy.
        trade_size: Fixed quantity bought or sold on a BUY/SELL action.
        starting_cash: Initial cash deposited when the environment is created.
        currency: Account and trading currency. Required: it defaulted to
            ``"USD"`` until v3.10 (ledger API-003).
    """

    asset_id: str
    strategy_id: str
    trade_size: Decimal
    starting_cash: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class TradingEnvState:
    """Immutable environment state, wrapping the real pipeline state.

    Attributes:
        config: The environment configuration this state belongs to.
        pipeline: The real, complete execution pipeline state -- market, strategy,
            allocation, risk, oms, execution, portfolio, analytics.
        equity: Current portfolio equity (cash + market value of positions),
            marked to the latest known price.
        step_count: Number of steps taken since the environment was created.
    """

    config: TradingEnvConfig
    pipeline: ExecutionPipelineState
    equity: Decimal
    step_count: int


@dataclass(frozen=True, slots=True)
class StepResult:
    """The outcome of one environment step.

    Attributes:
        state: The new environment state after this step.
        reward: Change in equity this step -- realized and unrealized P&L combined.
        done: Always False; this environment has no built-in episode length. A
            caller enforces episode boundaries (e.g. a fixed number of steps).
        info: Diagnostic detail: the action taken, resulting signed quantity,
            number of fills, and whether risk approved the order.
    """

    state: TradingEnvState
    reward: Decimal
    done: bool
    info: Mapping[str, Any]


def _default_pipeline_config(config: TradingEnvConfig) -> ExecutionPipelineConfig:
    """Builds generous-but-real risk/allocation limits.

    These are not disabled -- RiskEngine.evaluate genuinely runs against them on
    every step -- just set wide enough that a reasonably-sized RL action is not
    spuriously rejected by limits this environment isn't trying to teach.
    """
    account = Account(
        account_id="RL-ENV-ACCOUNT",
        base_currency=config.currency,
        name="RL Environment Account",
        created_at=0.0,
    )
    headroom = config.starting_cash * Decimal("10")
    budget = CapitalBudget(global_capital=config.starting_cash, maximum_exposure=headroom)
    allocation_constraints = AllocationConstraints(allow_shorting=True)
    risk_limits = RiskLimits(
        order_size=OrderSizeLimit(max_quantity=Decimal("1000000"), max_notional=headroom),
        position=PositionLimit(max_quantity=Decimal("1000000"), max_notional=headroom),
        exposure=ExposureLimit(max_gross_exposure=headroom, max_net_exposure=headroom),
        leverage=LeverageLimit(max_leverage=Decimal("10")),
        margin=MarginLimit(max_margin_utilization=Decimal("1.0")),
        # No daily loss limit: an episode has no trading day to measure one in.
        daily_loss=None,
        drawdown=DrawdownLimit(max_drawdown_pct=Decimal("1.0")),
    )
    return ExecutionPipelineConfig(
        account=account,
        starting_cash=config.starting_cash,
        budget=budget,
        allocation_constraints=allocation_constraints,
        risk_limits=risk_limits,
        currency=config.currency,
    )


def create_environment(config: TradingEnvConfig, timestamp: float) -> TradingEnvState:
    """Initializes a fresh trading environment with the RL agent registered as a
    real, running strategy.

    Raises:
        RLInputError: If trade_size or starting_cash are not positive.
    """
    if config.trade_size <= Decimal("0"):
        raise RLInputError(f"trade_size must be positive, got {config.trade_size}.")
    if config.starting_cash <= Decimal("0"):
        raise RLInputError(f"starting_cash must be positive, got {config.starting_cash}.")

    pipeline_config = _default_pipeline_config(config)
    strategy_state = RuntimeState(
        strategies={
            config.strategy_id: StrategyState(
                strategy_id=config.strategy_id,
                status=StrategyStatus.RUNNING,
                instance=RLAgentStrategy(),
            )
        }
    )
    pipeline_state = ExecutionPipeline.initialize(pipeline_config, strategy_state, timestamp)
    equity = NAVCalculator.calculate(
        pipeline_state.portfolio.cash, pipeline_state.portfolio.positions, config.currency
    )
    return TradingEnvState(config=config, pipeline=pipeline_state, equity=equity, step_count=0)


def step_environment(
    state: TradingEnvState, action: Action, price: Decimal, timestamp: float
) -> StepResult:
    """Advances the environment by one step: submits the agent's action as a real
    order, runs it through risk/OMS/execution/portfolio, marks the position to the
    current price, and returns the resulting reward.

    Raises:
        RLInputError: If price is not positive.
    """
    if price <= Decimal("0"):
        raise RLInputError(f"price must be positive, got {price}.")

    signed_quantity = action_to_signed_quantity(action, state.config.trade_size)
    pending = _PendingDecision(
        strategy_id=state.config.strategy_id,
        asset_id=state.config.asset_id,
        signed_quantity=signed_quantity,
        timestamp=timestamp,
    )
    context_factory = _make_context_factory(pending)

    quote = Quote(
        asset_id=state.config.asset_id,
        timestamp=timestamp,
        bid=price,
        ask=price,
        bid_size=Decimal("1000000"),
        ask_size=Decimal("1000000"),
        venue="SIM",
        currency=state.config.currency,
    )

    result = ExecutionPipeline.process_quote(state.pipeline, quote, context_factory)
    pipeline_state = result.state

    existing = pipeline_state.portfolio.positions.get(state.config.asset_id)
    if existing is not None:
        marked_positions = dict(pipeline_state.portfolio.positions)
        marked_positions[state.config.asset_id] = existing.update_market_price(price, timestamp)
        pipeline_state = replace(
            pipeline_state, portfolio=replace(pipeline_state.portfolio, positions=marked_positions)
        )

    new_equity = NAVCalculator.calculate(
        pipeline_state.portfolio.cash, pipeline_state.portfolio.positions, state.config.currency
    )
    reward = new_equity - state.equity

    new_state = TradingEnvState(
        config=state.config,
        pipeline=pipeline_state,
        equity=new_equity,
        step_count=state.step_count + 1,
    )
    info = {
        "action": action.name,
        "signed_quantity": signed_quantity,
        "fills": len(result.fills),
        "risk_approved": all(d.approved for d in result.risk_decisions)
        if result.risk_decisions
        else True,
    }
    return StepResult(state=new_state, reward=reward, done=False, info=info)


def current_position(state: TradingEnvState) -> Decimal:
    """Returns the current signed position quantity for the environment's asset."""
    position = state.pipeline.portfolio.positions.get(state.config.asset_id)
    return position.quantity if position is not None else Decimal("0")
