"""End-to-end execution pipeline orchestration.

This module connects the existing pure subsystem engines without introducing
replacement domain models. Boundary conversions stay here so package ownership
remains explicit and the canonical core entities are preserved.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from uuid import UUID

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.sizing import FixedQuantitySizing, SizingModel
from alphalab.allocation.state import AllocationState
from alphalab.analytics.attribution import TradeRecord
from alphalab.analytics.engine import AnalyticsEngine, PortfolioSnapshot
from alphalab.analytics.state import AnalyticsState
from alphalab.common.append_log import AppendOnlyLog
from alphalab.core.enums import Side as CoreSide
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.order_request import OrderRequest
from alphalab.core.trade import Trade as CoreTrade
from alphalab.execution.engine import ExecutionEngine
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.report import ExecutionReport
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.state import ExecutionState
from alphalab.market.engine import MarketEngine
from alphalab.market.events import BarClosed, MarketEvent, QuoteReceived, TickReceived
from alphalab.market.quote import Quote
from alphalab.market.state import MarketState
from alphalab.oms.engine import OMSEngine
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order as OMSOrder
from alphalab.oms.state import OMSState
from alphalab.oms.status import OrderStatus, OrderType
from alphalab.oms.status import Side as OMSSide
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.events import PortfolioEvent, PositionClosed, PositionReduced
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.valuation import PortfolioValuation, PortfolioValuationSnapshot
from alphalab.risk.decision import RiskDecision
from alphalab.risk.engine import RiskEngine
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import RiskLimits
from alphalab.risk.margin import MarginStatus
from alphalab.risk.state import RiskState
from alphalab.runtime.execution_adapters import canonical_execution_from_report
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import Intent
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

ContextFactory = Callable[[str], StrategyContext]

#: Execution outcomes that produce no report: the order never trades, so its
#: reservation is released and its OMS order is closed out.
_NON_TRADING_STATUSES = (FillStatus.REJECTED, FillStatus.EXPIRED, FillStatus.NO_FILL)


@dataclass(frozen=True, slots=True)
class ExecutionPipelineConfig:
    """Configuration required to connect the production execution path.

    Attributes:
        account: Portfolio account used by the resulting portfolio state.
        starting_cash: Initial cash deposited before the first market event.
        budget: Allocation budget used to size strategy intents.
        allocation_constraints: Constraints applied by the allocation engine.
        risk_limits: Limits applied by the risk engine.
        sizing_model: Sizing model used by allocation.
        simulator: Execution simulator used for deterministic fills.
        venue: Execution venue label for generated instructions.
        currency: Currency used for cash, execution reports, and portfolio fills.
    """

    account: Account
    starting_cash: Decimal
    budget: CapitalBudget
    allocation_constraints: AllocationConstraints
    risk_limits: RiskLimits
    sizing_model: SizingModel = field(default_factory=FixedQuantitySizing)
    simulator: ExecutionSimulator = field(default_factory=ExecutionSimulator)
    venue: str = "SIM"
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class ExecutionPipelineState:
    """Immutable snapshot of all subsystems in the execution path."""

    config: ExecutionPipelineConfig
    market: MarketState
    strategy: StrategyRuntimeState
    allocation: AllocationState
    risk: RiskState
    oms: OMSState
    execution: ExecutionState
    portfolio: PortfolioState
    analytics: AnalyticsState
    market_prices: Mapping[str, Decimal] = field(default_factory=dict)
    fills: AppendOnlyLog[CoreFill] = field(default_factory=AppendOnlyLog)
    trades: AppendOnlyLog[CoreTrade] = field(default_factory=AppendOnlyLog)
    trade_records: AppendOnlyLog[TradeRecord] = field(default_factory=AppendOnlyLog)
    portfolio_snapshots: AppendOnlyLog[PortfolioSnapshot] = field(default_factory=AppendOnlyLog)


@dataclass(frozen=True, slots=True)
class ExecutionPipelineResult:
    """Result emitted after one market event moves through the execution path."""

    state: ExecutionPipelineState
    market_event: MarketEvent
    intents: tuple[Intent, ...]
    order_requests: tuple[OrderRequest, ...]
    risk_decisions: tuple[RiskDecision, ...]
    oms_orders: tuple[OMSOrder, ...]
    execution_reports: tuple[ExecutionReport, ...]
    fills: tuple[CoreFill, ...]
    trades: tuple[CoreTrade, ...]
    unpriced_requests: tuple[OrderRequest, ...] = field(default_factory=tuple)
    valuation: PortfolioValuationSnapshot | None = None


class ExecutionPipeline:
    """Pure functional facade for the real AlphaLab execution path."""

    @staticmethod
    def initialize(
        config: ExecutionPipelineConfig,
        strategy_state: StrategyRuntimeState,
        timestamp: float,
    ) -> ExecutionPipelineState:
        """Create a fresh composite pipeline state."""

        portfolio = PortfolioState(account=config.account)
        portfolio = PortfolioEngine.apply_deposit(
            portfolio, config.starting_cash, config.currency, timestamp
        )
        risk = _sync_risk_from_portfolio(RiskEngine.reset(config.risk_limits), portfolio)
        snapshot = _portfolio_snapshot(portfolio, config.currency, timestamp)

        return ExecutionPipelineState(
            config=config,
            market=MarketEngine.reset(),
            strategy=strategy_state,
            allocation=AllocationEngine.initialize(config.budget),
            risk=risk,
            oms=OMSState(),
            execution=ExecutionState(),
            portfolio=portfolio,
            analytics=AnalyticsEngine.initialize(),
            portfolio_snapshots=AppendOnlyLog((snapshot,)),
        )

    @staticmethod
    def process_quote(
        state: ExecutionPipelineState,
        quote: Quote,
        context_factory: ContextFactory,
        fill_status: FillStatus = FillStatus.FULL_FILL,
        fill_quantity: Decimal | None = None,
    ) -> ExecutionPipelineResult:
        """Publish a quote and process the resulting market event."""

        market = MarketEngine.publish_quote(state.market, quote)
        event = market.events[-1]
        return ExecutionPipeline.process_market_event(
            replace(state, market=market),
            event,
            context_factory,
            fill_status,
            fill_quantity,
        )

    @staticmethod
    def process_market_event(
        state: ExecutionPipelineState,
        event: MarketEvent,
        context_factory: ContextFactory,
        fill_status: FillStatus = FillStatus.FULL_FILL,
        fill_quantity: Decimal | None = None,
    ) -> ExecutionPipelineResult:
        """Route one market event through strategy, order, execution, and portfolio.

        Order of operations, and why:

        1. The event's price updates the known market prices.
        2. Open positions are marked to market at those prices, so unrealized
           P&L and NAV reflect the market as of this event *before* anything is
           decided on it, and the risk state is resynced from the marked book so
           risk evaluates against the current valuation.
           Note that the strategy does *not* see the marked portfolio: its
           context comes from the caller's ``context_factory``, which this
           pipeline does not populate. Allocation sizes from market prices and
           its capital budget, not from the portfolio.
        3. Strategy, allocation, risk, OMS, execution and portfolio run.
        4. One portfolio snapshot is recorded for the event, after every fill
           it produced has been applied.
        """

        market_prices = _market_prices_with_event(state.market_prices, event)
        portfolio = PortfolioEngine.update_market_prices(
            state.portfolio, market_prices, event.timestamp
        )
        risk = _sync_risk_from_portfolio(state.risk, portfolio)

        strategy, intents = StrategyEngine.process_event(
            state.strategy, event, context_factory, event.timestamp
        )
        allocation, requests = AllocationEngine.allocate(
            state.allocation,
            intents,
            market_prices,
            state.config.sizing_model,
            state.config.allocation_constraints,
            event.timestamp,
        )
        current = replace(
            state,
            strategy=strategy,
            allocation=allocation,
            market_prices=market_prices,
            portfolio=portfolio,
            risk=risk,
        )
        return _process_requests(current, event, intents, requests, fill_status, fill_quantity)

    @staticmethod
    def compile_analytics(
        state: ExecutionPipelineState,
        timestamp: float,
        years_elapsed: float = 1.0,
        risk_free_rate: float = 0.0,
    ) -> ExecutionPipelineState:
        """Compile analytics from portfolio snapshots and execution trade records."""

        analytics = AnalyticsEngine.compile_report(
            state.analytics,
            state.portfolio_snapshots,
            state.trade_records,
            timestamp,
            years_elapsed,
            risk_free_rate,
        )
        return replace(state, analytics=analytics)


def _process_requests(
    state: ExecutionPipelineState,
    event: MarketEvent,
    intents: tuple[Intent, ...],
    requests: tuple[OrderRequest, ...],
    fill_status: FillStatus,
    fill_quantity: Decimal | None,
) -> ExecutionPipelineResult:
    decisions: list[RiskDecision] = []
    orders: list[OMSOrder] = []
    reports: list[ExecutionReport] = []
    fills: list[CoreFill] = []
    trades: list[CoreTrade] = []
    unpriced: list[OrderRequest] = []
    current = state

    for request in requests:
        # An order cannot be priced, executed or valued without a market price
        # for its asset -- allocation prices unknown assets at 0.00. Drop the
        # request here, deterministically and before it reaches the OMS, rather
        # than submitting an order the execution leg cannot price.
        if request.asset_id not in current.market_prices:
            unpriced.append(request)
            continue
        current, decision = _evaluate_risk(current, request, event.timestamp)
        decisions.append(decision)
        if not decision.approved:
            continue
        current, order = _submit_and_accept_order(current, request, event.timestamp)
        current, new_reports = _execute_order(current, order, fill_status, fill_quantity)
        # A rejected, expired or unfilled execution produces no report. The
        # order never trades, so release its reserved allocation and close it
        # out of the OMS instead of leaving it open forever awaiting a fill.
        if not new_reports and fill_status in _NON_TRADING_STATUSES:
            released_notional = request.quantity * request.price
            allocation_state = AllocationEngine.release_reservation(
                current.allocation, request.order_id, released_notional, event.timestamp
            )
            current = replace(
                current,
                allocation=allocation_state,
                oms=_close_unfilled_order(current.oms, order, fill_status, event.timestamp),
            )

        current, new_fills, new_trades = _apply_reports(current, order, new_reports)
        orders.append(order)
        reports.extend(new_reports)
        fills.extend(new_fills)
        trades.extend(new_trades)

    snapshot = _portfolio_snapshot(current.portfolio, current.config.currency, event.timestamp)
    current = replace(current, portfolio_snapshots=current.portfolio_snapshots.append(snapshot))
    valuation = PortfolioValuation.snapshot(
        current.portfolio, event.timestamp, current.config.currency
    )

    return ExecutionPipelineResult(
        current,
        event,
        intents,
        requests,
        tuple(decisions),
        tuple(orders),
        tuple(reports),
        tuple(fills),
        tuple(trades),
        tuple(unpriced),
        valuation,
    )


def _evaluate_risk(
    state: ExecutionPipelineState, request: OrderRequest, timestamp: float
) -> tuple[ExecutionPipelineState, RiskDecision]:
    risk, decision = RiskEngine.evaluate(state.risk, request, timestamp)
    return replace(state, risk=risk), decision


def _submit_and_accept_order(
    state: ExecutionPipelineState, request: OrderRequest, timestamp: float
) -> tuple[ExecutionPipelineState, OMSOrder]:
    submitted_order = _oms_order(request)
    oms = OMSEngine.submit(state.oms, submitted_order, timestamp)
    oms = OMSEngine.accept(oms, submitted_order.order_id, timestamp)
    accepted = oms.orders.find(submitted_order.order_id)
    return replace(state, oms=oms), accepted


def _execute_order(
    state: ExecutionPipelineState,
    order: OMSOrder,
    fill_status: FillStatus,
    fill_quantity: Decimal | None,
) -> tuple[ExecutionPipelineState, tuple[ExecutionReport, ...]]:
    before = len(state.execution.history)
    instruction = _instruction(order, state)
    quantity = order.remaining_quantity if fill_quantity is None else fill_quantity
    execution = ExecutionEngine.simulate(
        state.execution,
        state.config.simulator,
        instruction,
        quantity,
        instruction.price,
        order.updated_at,
        fill_status,
    )
    return replace(state, execution=execution), execution.history[before:]


def _apply_reports(
    state: ExecutionPipelineState,
    order: OMSOrder,
    reports: tuple[ExecutionReport, ...],
) -> tuple[ExecutionPipelineState, tuple[CoreFill, ...], tuple[CoreTrade, ...]]:
    current = state
    fills: list[CoreFill] = []
    trades: list[CoreTrade] = []

    for report in reports:
        current = _apply_report_to_oms(current, order.order_id, report)
        fill, trade = _canonical_execution(report, order.side)
        current = _apply_report_to_portfolio(current, report, order.side)
        # Reconcile allocation budgets with executed notional
        executed_notional = report.fill_quantity * report.fill_price
        allocation_state = AllocationEngine.apply_execution(
            current.allocation, str(report.order_id), executed_notional, report.timestamp
        )
        current = replace(current, allocation=allocation_state)
        fills.append(fill)
        trades.append(trade)

    return (
        replace(
            current,
            fills=current.fills.extend(fills),
            trades=current.trades.extend(trades),
        ),
        tuple(fills),
        tuple(trades),
    )


def _apply_report_to_oms(
    state: ExecutionPipelineState, order_id: OrderId, report: ExecutionReport
) -> ExecutionPipelineState:
    if report.status is FillStatus.FULL_FILL:
        oms = OMSEngine.fill(
            state.oms, order_id, report.fill_quantity, report.fill_price, report.timestamp
        )
    elif report.status is FillStatus.PARTIAL_FILL:
        oms = OMSEngine.partial_fill(
            state.oms, order_id, report.fill_quantity, report.fill_price, report.timestamp
        )
    else:
        return state
    return replace(state, oms=oms)


def _apply_report_to_portfolio(
    state: ExecutionPipelineState, report: ExecutionReport, side: OMSSide
) -> ExecutionPipelineState:
    signed_quantity = report.fill_quantity if side is OMSSide.BUY else -report.fill_quantity
    before = len(state.portfolio.events)
    portfolio = PortfolioEngine.apply_fill(
        state.portfolio,
        report.asset_id,
        signed_quantity,
        report.fill_price,
        report.commission,
        report.timestamp,
        report.currency,
    )
    risk = _sync_risk_from_portfolio(state.risk, portfolio)
    record = _trade_record(report, portfolio.events[before:])
    return replace(
        state,
        portfolio=portfolio,
        risk=risk,
        trade_records=state.trade_records.append(record),
    )


def _close_unfilled_order(
    oms: OMSState, order: OMSOrder, fill_status: FillStatus, timestamp: float
) -> OMSState:
    """Move an accepted order the venue never filled into a terminal state.

    Each non-trading outcome maps to the lifecycle state that describes it:
    the venue refused the order (REJECTED), it timed out (EXPIRED), or it was
    simply never filled (NO_FILL). The pipeline mints a fresh order per market
    event and never re-works an existing one, so an unfilled order will not be
    worked again and is withdrawn rather than left open.
    """

    if fill_status is FillStatus.EXPIRED:
        return OMSEngine.expire(oms, order.order_id, timestamp)
    if fill_status is FillStatus.NO_FILL:
        return OMSEngine.cancel(oms, order.order_id, timestamp)
    return OMSEngine.reject(oms, order.order_id, "Rejected by execution venue", timestamp)


def _oms_order(request: OrderRequest) -> OMSOrder:
    return OMSOrder(
        OrderId(UUID(request.order_id)),
        request.strategy_id,
        request.asset_id,
        request.side,
        OrderType.MARKET,
        OrderStatus.NEW,
        request.quantity,
        Decimal("0"),
        request.quantity,
        None,
        None,
        Decimal("0"),
        request.timestamp,
        request.timestamp,
        {"reference_price": str(request.price)},
    )


def _instruction(order: OMSOrder, state: ExecutionPipelineState) -> OrderInstruction:
    return OrderInstruction(
        str(order.order_id.value),
        order.strategy_id,
        order.asset_id,
        order.remaining_quantity,
        state.market_prices[order.asset_id],
        order.side,
        state.config.venue,
        state.config.currency,
    )


def _canonical_execution(report: ExecutionReport, side: CoreSide) -> tuple[CoreFill, CoreTrade]:
    return canonical_execution_from_report(report, side)


def _trade_record(report: ExecutionReport, fill_events: Sequence[PortfolioEvent]) -> TradeRecord:
    """Build the analytics trade record for one execution report.

    ``fill_events`` are only the portfolio events this fill produced. Scanning
    the whole portfolio history instead would attribute an earlier close's
    realized P&L to an opening fill that realized nothing.
    """

    realized = Decimal("0.00")
    for evt in fill_events:
        # PositionReduced(timestamp, account_id, asset_id, reduced_quantity, price, realized_pnl)
        # PositionClosed(timestamp, account_id, asset_id, price, realized_pnl)
        if isinstance(evt, PositionReduced | PositionClosed):
            realized = evt.realized_pnl
            break

    return TradeRecord(
        trade_id=report.execution_id,
        strategy_id=report.strategy_id,
        asset_id=report.asset_id,
        sector_id="UNCLASSIFIED",
        realized_pnl=realized,
        notional_value=report.fill_quantity * report.fill_price,
        holding_period_seconds=0.0,
    )


def _market_prices_with_event(
    prices: Mapping[str, Decimal], event: MarketEvent
) -> Mapping[str, Decimal]:
    update = _market_price(event)
    if update is None:
        return prices
    asset_id, price = update
    new_prices = dict(prices)
    new_prices[asset_id] = price
    return new_prices


def _market_price(event: MarketEvent) -> tuple[str, Decimal] | None:
    if isinstance(event, QuoteReceived):
        quote = event.quote
        return quote.asset_id, (quote.bid + quote.ask) / Decimal("2")
    if isinstance(event, BarClosed):
        return event.bar.asset_id, event.bar.close
    if isinstance(event, TickReceived):
        return event.tick.asset_id, event.tick.price
    return None


def _portfolio_snapshot(
    portfolio: PortfolioState, currency: str, timestamp: float
) -> PortfolioSnapshot:
    """Project the canonical portfolio valuation into the analytics snapshot."""

    valuation = PortfolioValuation.snapshot(portfolio, timestamp, currency)
    return PortfolioSnapshot(
        timestamp,
        valuation.equity,
        valuation.cash,
        valuation.long_value,
        valuation.short_value,
    )


def _sync_risk_from_portfolio(risk: RiskState, portfolio: PortfolioState) -> RiskState:
    cash = portfolio.cash.balance(portfolio.account.base_currency)
    nav = NAVCalculator.calculate(
        portfolio.cash, portfolio.positions, portfolio.account.base_currency
    )
    exposure = _risk_exposure(portfolio)

    # Delegate exposure and margin updates to the RiskEngine so that
    # risk events and history are produced consistently with other
    # codepaths. We still set numeric snapshots (cash, buying_power,
    # current_nav, peak_nav) on the returned RiskState to keep the
    # snapshot coherent.
    risk_with_exposure = RiskEngine.update_exposure(risk, exposure, 0.0)
    margin = MarginStatus(available_margin=cash, margin_used=exposure.gross_exposure)
    risk_with_margin = RiskEngine.update_margin(risk_with_exposure, margin, 0.0)

    peak_nav = max(risk_with_margin.peak_nav, nav)
    return replace(
        risk_with_margin,
        cash=cash,
        buying_power=max(Decimal("0.00"), cash),
        current_nav=nav,
        peak_nav=peak_nav,
    )


def _risk_exposure(portfolio: PortfolioState) -> ExposureStatus:
    asset_exposure = {k: p.market_value for k, p in portfolio.positions.items()}
    long_exposure = sum((v for v in asset_exposure.values() if v > 0), Decimal("0.00"))
    short_exposure = sum((v for v in asset_exposure.values() if v < 0), Decimal("0.00"))
    return ExposureStatus(
        gross_exposure=long_exposure + abs(short_exposure),
        net_exposure=long_exposure + short_exposure,
        long_exposure=long_exposure,
        short_exposure=short_exposure,
        asset_exposure=asset_exposure,
    )
