"""End-to-end execution pipeline orchestration.

This module connects the existing pure subsystem engines without introducing
replacement domain models. Boundary conversions stay here so package ownership
remains explicit and the canonical core entities are preserved.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum, auto
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
from alphalab.common.ids import IdStreamPosition, current_id_position
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import Side as CoreSide
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.order_request import OrderRequest
from alphalab.core.trade import Trade as CoreTrade
from alphalab.execution.engine import ExecutionEngine
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.policy import (
    FillDecision,
    FillPolicy,
    LiquidityContext,
    StaticFill,
)
from alphalab.execution.report import ExecutionReport
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.state import ExecutionState
from alphalab.instrument.registry import InstrumentRegistry
from alphalab.market.bar import Bar
from alphalab.market.engine import MarketEngine
from alphalab.market.events import (
    BarClosed,
    MarketEvent,
    QuoteReceived,
    TickReceived,
    TradeReceived,
)
from alphalab.market.exceptions import UnsupportedRecordError
from alphalab.market.quote import Quote
from alphalab.market.record import MarketRecord
from alphalab.market.state import MarketState
from alphalab.market.tick import Tick
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
from alphalab.portfolio.valuation import (
    PortfolioValuation,
    PortfolioValuationSnapshot,
    assert_single_currency_book,
)
from alphalab.risk.decision import RiskDecision
from alphalab.risk.engine import RiskEngine
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import RiskLimits
from alphalab.risk.margin import MarginStatus
from alphalab.risk.state import RiskState
from alphalab.runtime.context_views import (
    MarketView,
    OrderShare,
    OrderView,
    PortfolioView,
    RiskView,
    order_shares_by_strategy,
)
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_adapters import canonical_execution_from_report
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import Intent
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

ContextFactory = Callable[[str], StrategyContext]

#: Execution outcomes that produce no report: the order never trades, so its
#: reservation is released and its OMS order is closed out.
_NON_TRADING_STATUSES = (FillStatus.REJECTED, FillStatus.EXPIRED, FillStatus.NO_FILL)

#: The terminal outcomes a caller may report for an order that never completed.
#:
#: Deliberately the three :class:`~alphalab.core.enums.OrderStatus` members that
#: end an order without it trading, and not
#: :class:`~alphalab.execution.fill.FillStatus`: a venue reporting that an order
#: is dead is describing the *order*, not a fill it did not make. ``FILLED`` and
#: ``PARTIALLY_FILLED`` are reached by applying an execution report, never by
#: assertion, which is why they are not here.
_TERMINAL_OUTCOMES = (OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED)


class ExecutionRouting(Enum):
    """Where an accepted order executes -- the one thing environments differ in.

    Everything before this point is identical in a backtest, a replay, a paper
    run and a live session: the same market event, strategy, allocation, risk
    and OMS. What changes is only what happens to an order the OMS has
    accepted.
    """

    #: The order executes against :class:`~alphalab.execution.simulator.ExecutionSimulator`,
    #: with a :class:`~alphalab.execution.policy.FillPolicy` deciding the outcome
    #: from the liquidity the market event showed. Backtest, replay and paper.
    SIMULATED = auto()

    #: The order is left working in the OMS for a broker adapter to route; no
    #: fill is invented for it. Its allocation reservation stays held, because
    #: the order is still live and that capital is still committed. Fills come
    #: back later through :mod:`alphalab.runtime.broker_routing`. Live only.
    EXTERNAL = auto()


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
        venue: Execution venue label for generated instructions. This is the
            venue an order *executes at*, and it is not the venue market data
            was attributed to, nor the exchange an instrument is listed on. See
            :class:`~alphalab.instrument.record.InstrumentRecord` for the
            listing exchange and :class:`~alphalab.market.quote.Quote` for
            market-data attribution; the three are distinct.
        currency: The **settlement currency** of this pipeline -- what cash is
            funded in, what an ``OrderInstruction`` and its
            :class:`~alphalab.execution.report.ExecutionReport` are denominated
            in, and therefore what every :class:`~alphalab.portfolio.position.Position`
            is booked in. It must equal ``account.base_currency``, which risk
            and NAV read; :meth:`ExecutionPipeline.initialize` refuses a config
            where the two disagree. It is *not* the currency an instrument
            trades in -- that is
            :attr:`~alphalab.instrument.record.InstrumentRecord.currency`, and
            it does not reach the execution path.
        routing: Where an accepted order executes. Defaults to ``SIMULATED``,
            which is what every environment before v2.3 did.
        instruments: The registry the run's market data was resolved against, or
            ``None``. **Read-only, and used for exactly two things**: when a
            request is dropped for want of a price, deciding whether the
            ``asset_id`` names a registered instrument at all; and, since v2.11,
            reading the sector a fill's asset is classified as, which is frozen
            onto that fill's :class:`~alphalab.analytics.attribution.TradeRecord`
            (ADR-0027). Both are
            :meth:`~alphalab.instrument.registry.InstrumentRegistry.record_for`,
            which remains the only method ever called on it. The pipeline never
            resolves a provider symbol, never derives an ``asset_id``, never
            registers anything and never classifies anything: ADR-0016 gives
            resolution to the wire boundary and classification to the operator,
            and this takes neither back. Leaving it ``None`` is fully supported
            and changes nothing except how precisely an unpriced asset can be
            described and whether a run can report P&L by sector.
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
    routing: ExecutionRouting = ExecutionRouting.SIMULATED
    instruments: InstrumentRegistry | None = None


class UnpricedReason(Enum):
    """Why a request was dropped for want of a price.

    Three members, and no fourth for "unknown": :attr:`NO_PRICE_OBSERVED` is not
    a stand-in for an answer the pipeline failed to get, it is the complete
    answer when no registry was configured to ask.
    """

    #: No :class:`~alphalab.instrument.registry.InstrumentRegistry` was
    #: configured, so the run knows only that it never priced this asset. It
    #: cannot say whether the identifier names a real instrument.
    NO_PRICE_OBSERVED = auto()

    #: A registry was configured and holds no instrument under this
    #: ``asset_id``. This is the ADR-0016 section 3 failure mode -- a strategy
    #: naming an instrument the registry does not have -- which until now
    #: produced zero fills and no explanation.
    NOT_REGISTERED = auto()

    #: A registry was configured and does hold this instrument; the run simply
    #: never saw a price for it. Widening the data window, not the registry, is
    #: the fix.
    REGISTERED_BUT_UNPRICED = auto()


@dataclass(frozen=True, slots=True)
class UnpricedAsset:
    """One asset the run declined to trade, and what it knows about why.

    Aggregated per ``asset_id`` rather than logged per occurrence. The five
    hundredth drop of one asset for one reason says nothing the first did not,
    and a live session that is misconfigured drops one request per event
    indefinitely -- so the count is kept and the repetition is not.

    Attributes:
        asset_id: The asset no price was observed for.
        reason: What the run could establish about why.
        detail: The same in a sentence, naming the instrument when a registry
            supplied one. Descriptive; read ``reason`` to branch on.
        first_timestamp: Market timestamp of the first drop.
        last_timestamp: Market timestamp of the most recent drop. Equal to
            ``first_timestamp`` after a single occurrence.
        occurrences: How many requests were dropped, not how many events passed.
    """

    asset_id: str
    reason: UnpricedReason
    detail: str
    first_timestamp: float
    last_timestamp: float
    occurrences: int


@dataclass(frozen=True, slots=True)
class SettlementRefusal:
    """One request refused because the instrument does not settle here.

    A pipeline settles in exactly one currency -- ``ExecutionPipelineConfig.currency``,
    which :func:`_require_one_account_currency` already requires to equal
    ``Account.base_currency``. An instrument trades in whatever
    :attr:`~alphalab.instrument.record.InstrumentRecord.currency` declares, and
    that is an *identity* input, so it is immutable and cannot be reclassified.
    When the two disagree the run cannot trade the instrument: booking it in the
    settlement currency would label a position with a currency the instrument
    does not trade in, and booking it honestly would make the book mixed, which
    the very next portfolio snapshot refuses. See ADR-0028.

    **This is not a missing rate.** ``MixedCurrencyValuationError`` is about the
    absence of FX; this is about a pipeline that settles in one currency being
    pointed at an instrument that trades in another. The fix is to run a pipeline
    that settles in the instrument's currency, not to supply a rate.

    Reported per occurrence on :class:`ExecutionPipelineResult`, and deliberately
    not accumulated onto :class:`ExecutionPipelineState`: an aggregated, durable
    record in the manner of :class:`UnpricedAsset` would add a field to the state
    and move the snapshot schema for observability the result already carries.
    That belongs with the release that moves the schema anyway (ADR-0028).

    Attributes:
        asset_id: The instrument the request named.
        instrument_currency: What the registry says it trades in.
        settlement_currency: What this pipeline settles in.
        detail: The same in a sentence, naming the instrument and the fix.
        timestamp: Market timestamp of the event whose request was refused.
    """

    asset_id: str
    instrument_currency: str
    settlement_currency: str
    detail: str
    timestamp: float


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
    #: Assets the run declined to trade for want of a price, keyed by
    #: ``asset_id`` and never removed -- this records what happened, not what is
    #: unpriced now. Of the ways a request can end without a fill, this was the
    #: only one leaving no reason behind: allocation and risk rejections land in
    #: their own event logs, and a non-trading execution leaves the order closed
    #: in the OMS with its status, but a dropped request emitted only an
    #: ``AllocationReservationReleased`` identical to the one three other
    #: outcomes emit. Bounded by the distinct instruments the run's strategies
    #: named, never by event count. Captured and restored by
    #: :mod:`alphalab.runtime.snapshot` since v2.9, so a continued run does not
    #: forget what it declined to trade -- this comment claimed the opposite
    #: until v2.11, and the code always did the right thing.
    unpriced_assets: PersistentMap[str, UnpricedAsset] = field(default_factory=PersistentMap)
    #: How far this run's identifier stream had advanced when the last transition
    #: finished. Refreshed by every method that returns a state and mints
    #: identifiers, so the state -- not the ambient
    #: :class:`~alphalab.common.ids.DeterministicIdSource` -- is what says where
    #: the stream is. A run that stops here and rebuilds its source with
    #: :func:`~alphalab.common.ids.id_source_for` continues the stream instead of
    #: restarting it, which is what stops a continued run re-minting identifiers
    #: it has already used. ``seed=None`` for an unseeded run, which has no
    #: cursor and needs none. See ADR-0022.
    id_position: IdStreamPosition = field(default_factory=IdStreamPosition)


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
    #: Requests refused because the instrument does not settle in this
    #: pipeline's currency. Appended after ``valuation`` rather than beside
    #: ``unpriced_requests`` so that positional construction of this result
    #: keeps working. Derived and never persisted: no snapshot carries an
    #: ``ExecutionPipelineResult``. See :class:`SettlementRefusal` and ADR-0028.
    settlement_refusals: tuple[SettlementRefusal, ...] = field(default_factory=tuple)


def _populate_context(
    context_factory: ContextFactory,
    *,
    portfolio: PortfolioState,
    risk: RiskState,
    market: MarketState,
    market_prices: Mapping[str, Decimal],
    shares: Mapping[str, tuple[OrderShare, ...]],
) -> ContextFactory:
    """Wrap a caller's factory so the pipeline owns what only it can know.

    The caller's :data:`ContextFactory` signature is unchanged and every
    existing factory keeps working: this calls it, then overlays the four fields
    the pipeline is authoritative for. ``clock``, ``logger`` and ``config`` are
    passed through exactly as supplied -- which is what keeps
    :mod:`alphalab.reinforcement_learning`'s ``_PendingDecision`` channel, which
    rides on ``config``, working untouched.

    **Pipeline-owned fields always win.** A caller that supplies a ``portfolio``
    has it replaced rather than merged, because a caller-installable portfolio
    is the second source of truth ADR-0026 decision 1 exists to prevent, and a
    test could otherwise pass against a fabricated book.

    Each context costs a ``replace`` and four references. The per-strategy order
    slice was computed once for the whole event by
    :func:`~alphalab.runtime.context_views.order_shares_by_strategy`; nothing
    here scans, copies or recomputes.

    Raises:
        RuntimeValidationError: If the caller's factory does not return a
            :class:`~alphalab.strategy.context.StrategyContext`. Substituting one
            would hand the strategy a context whose provenance nobody can state,
            which is the failure ADR-0026 decision 10 refuses.
    """

    portfolio_view = PortfolioView(portfolio)
    risk_view = RiskView(risk)
    market_view = MarketView(market, market_prices)

    def populate(strategy_id: str) -> StrategyContext:
        supplied = context_factory(strategy_id)
        if not isinstance(supplied, StrategyContext):
            raise RuntimeValidationError(
                f"The context factory returned {type(supplied).__name__} for strategy "
                f"{strategy_id!r}, not a StrategyContext. The pipeline overlays the "
                "portfolio, orders, risk and market it owns onto the context a caller "
                "builds, and it cannot overlay them onto something else. Nothing is "
                "substituted: a strategy is never handed a context this pipeline "
                "cannot vouch for."
            )
        return replace(
            supplied,
            portfolio=portfolio_view,
            orders=OrderView(shares.get(strategy_id, ())),
            risk_view=risk_view,
            market=market_view,
        )

    return populate


def _require_one_account_currency(config: ExecutionPipelineConfig) -> None:
    """Refuse a configuration that names two account currencies.

    ``ExecutionPipelineConfig.currency`` funds the cash ledger and denominates
    every fill; ``Account.base_currency`` is what
    :func:`_sync_risk_from_portfolio` and
    :class:`~alphalab.portfolio.nav.NAVCalculator` read. Two fields, one role --
    and until v2.8 nothing checked that they agreed.

    When they disagree the run does not fail; it goes quiet. Cash is deposited
    under ``config.currency``, so ``cash.balance(account.base_currency)`` is
    zero, so ``risk.cash``, ``buying_power``, ``current_nav`` and ``peak_nav``
    are all zero. ``check_buying_power`` then refuses every order, while
    ``check_leverage`` returns early on a non-positive NAV and ``check_margin``
    passes vacuously against zero available margin. The run produces no fills,
    reports its full starting equity, and two risk limits silently stop
    checking.

    Comparison is exact: no ``strip``, no ``upper``.
    :class:`~alphalab.portfolio.cash.CashLedger` keys balances by the string it
    is given, so ``"usd"`` and ``"USD"`` are already two separate balances --
    normalizing here would let the check pass while the ledger still split.

    Raises:
        RuntimeValidationError: If the two currencies differ.
    """

    if config.currency != config.account.base_currency:
        raise RuntimeValidationError(
            f"ExecutionPipelineConfig.currency is {config.currency!r} and "
            f"Account.base_currency is {config.account.base_currency!r}. These name "
            "one thing -- the account's settlement currency -- and must agree. "
            "Cash would be funded in "
            f"{config.currency!r} while risk read {config.account.base_currency!r}, "
            "leaving buying power and NAV at zero: every order would be rejected, "
            "the leverage and margin checks would stop checking, and the run would "
            "report its full starting equity while producing no fills."
        )


class ExecutionPipeline:
    """Pure functional facade for the real AlphaLab execution path."""

    @staticmethod
    def initialize(
        config: ExecutionPipelineConfig,
        strategy_state: StrategyRuntimeState,
        timestamp: float,
    ) -> ExecutionPipelineState:
        """Create a fresh composite pipeline state.

        Raises:
            RuntimeValidationError: If ``config.currency`` and
                ``config.account.base_currency`` disagree. The check runs before
                the portfolio exists, so nothing is funded against a refused
                configuration.
        """

        _require_one_account_currency(config)

        portfolio = PortfolioState(account=config.account)
        portfolio = PortfolioEngine.apply_deposit(
            portfolio, config.starting_cash, config.currency, timestamp
        )
        risk = _sync_risk_from_portfolio(
            RiskEngine.reset(config.risk_limits), portfolio, config.instruments
        )
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
            # Funding the portfolio mints identifiers, so a state is never handed
            # back without saying where the stream stands.
            id_position=current_id_position(),
        )

    @staticmethod
    def process_quote(
        state: ExecutionPipelineState,
        quote: Quote,
        context_factory: ContextFactory,
        fill_status: FillStatus = FillStatus.FULL_FILL,
        fill_quantity: Decimal | None = None,
        fill_policy: FillPolicy | None = None,
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
            fill_policy,
        )

    @staticmethod
    def publish_record(market: MarketState, record: MarketRecord) -> MarketState:
        """Publish one market record to the market engine.

        The record's payload decides which publication it is; nothing else in
        the path needs to know which kind of input drove an event.
        """

        payload = record.payload
        if isinstance(payload, Quote):
            return MarketEngine.publish_quote(market, payload)
        if isinstance(payload, Bar):
            return MarketEngine.publish_bar(market, payload)
        if isinstance(payload, Tick):
            return MarketEngine.publish_tick(market, payload)
        raise UnsupportedRecordError(
            f"Record {record.event_id} carries an unsupported market input: "
            f"{type(payload).__name__}"
        )

    @staticmethod
    def process_record(
        state: ExecutionPipelineState,
        record: MarketRecord,
        context_factory: ContextFactory,
        fill_policy: FillPolicy | None = None,
    ) -> ExecutionPipelineResult:
        """Move one market record through the whole execution path.

        This is *the* canonical step, and every environment takes it: a
        backtest walking a dataset, a replay driven by its cursor, a paper run
        reading a live source, and a live session -- see
        :mod:`alphalab.runtime.session`. They differ in where the record came
        from and in where an accepted order executes, and in nothing else.
        Sharing this function is what makes that a structural guarantee rather
        than a convention four call sites have to keep.
        """

        market = ExecutionPipeline.publish_record(state.market, record)
        return ExecutionPipeline.process_market_event(
            replace(state, market=market),
            market.events[-1],
            context_factory,
            fill_policy=fill_policy,
        )

    @staticmethod
    def process_market_event(
        state: ExecutionPipelineState,
        event: MarketEvent,
        context_factory: ContextFactory,
        fill_status: FillStatus = FillStatus.FULL_FILL,
        fill_quantity: Decimal | None = None,
        fill_policy: FillPolicy | None = None,
    ) -> ExecutionPipelineResult:
        """Route one market event through strategy, order, execution, and portfolio.

        Order of operations, and why:

        1. The event's price updates the known market prices.
        2. Open positions are marked to market at those prices, so unrealized
           P&L and NAV reflect the market as of this event *before* anything is
           decided on it, and the risk state is resynced from the marked book so
           risk evaluates against the current valuation.
           The strategy *does* see that marked portfolio, as of v2.10: its
           context is the caller's ``context_factory`` result with the
           pipeline-owned fields overlaid from the marked locals below, never
           from ``state.portfolio`` or ``state.risk``, which are still the
           pre-mark values. See :func:`_populate_context` and ADR-0026.
           Allocation still sizes from market prices and its capital budget,
           not from the portfolio.
        3. Strategy, allocation, risk, OMS, execution and portfolio run.
        4. One portfolio snapshot is recorded for the event, after every fill
           it produced has been applied.

        ``fill_policy`` decides each order's execution outcome from the
        liquidity the event showed, and takes precedence over ``fill_status`` /
        ``fill_quantity``, which apply one fixed outcome to every order. Passing
        neither fills every order in full, as it always has.
        """

        market_prices = _market_prices_with_event(state.market_prices, event)
        portfolio = PortfolioEngine.update_market_prices(
            state.portfolio, market_prices, event.timestamp
        )
        risk = _sync_risk_from_portfolio(state.risk, portfolio, state.config.instruments)

        # Assembled from the marked locals above, after marking and after the
        # risk resync, and before dispatch. The order is the guarantee: reading
        # ``state.portfolio`` here would show the strategy a book marked at the
        # previous event's prices while risk evaluated its order against these.
        populated = _populate_context(
            context_factory,
            portfolio=portfolio,
            risk=risk,
            market=state.market,
            market_prices=market_prices,
            shares=order_shares_by_strategy(state.oms, state.allocation),
        )
        strategy, intents = StrategyEngine.process_event(
            state.strategy, event, populated, event.timestamp
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
        policy: FillPolicy = (
            fill_policy if fill_policy is not None else StaticFill(fill_status, fill_quantity)
        )
        return _process_requests(current, event, intents, requests, policy)

    @staticmethod
    def apply_execution_report(
        state: ExecutionPipelineState,
        order: OMSOrder,
        report: ExecutionReport,
    ) -> tuple[ExecutionPipelineState, tuple[CoreFill, ...], tuple[CoreTrade, ...]]:
        """Apply one execution report that did not come from the simulator.

        This is the seam a real venue arrives through. A fill reported by a
        broker is turned into an :class:`~alphalab.execution.report.ExecutionReport`
        by :mod:`alphalab.runtime.broker_routing` and then applied *here* -- by
        the same function a simulated fill goes through, so the OMS transition,
        the portfolio accounting, the allocation reconciliation and the
        analytics trade record are identical whether the fill was simulated or
        real. Duplicating that logic for live trading is precisely the mistake
        this method exists to prevent.

        Applying a report is **idempotent in its** ``execution_id``. A venue
        redelivers fills after a reconnect, delivers them out of order, and
        delivers them again for orders it is unsure about;
        :mod:`alphalab.broker.reconciliation` calls that "the normal behaviour of
        a network" and answers a repeated ``execution_id`` with a no-op rather
        than an error, "because a reconnect makes it routine". Until v2.9 this
        method did not, and it could not: it applied the economics without ever
        recording the report, so :class:`~alphalab.execution.state.ExecutionState`
        -- whose ``reports`` map is keyed by execution id and is exactly the
        ledger such a check reads -- stayed empty on this path while a simulated
        fill populated it. One report delivered twice therefore charged cash
        twice and doubled the position. A full fill was caught incidentally,
        because the second application asked the OMS to fill an order already
        ``FILLED``; a *partial* fill was not caught at all, and simply applied
        again.

        The identity is the ``execution_id`` and nothing else. Two reports that
        agree on order, price, quantity and timestamp but differ in
        ``execution_id`` are two executions and both apply -- which is what a
        legitimate sequence of partial fills looks like.

        The simulated path needs no such guard and does not get one: its reports
        are recorded by :meth:`~alphalab.execution.engine.ExecutionEngine.simulate`
        before the economics are applied, and the simulator mints a fresh
        execution id per event, so it cannot redeliver.

        Returns:
            The next state and the fills and trades this report produced. A
            report whose ``execution_id`` has already been applied returns the
            state unchanged and no fills or trades.
        """

        if report.execution_id in state.execution.reports:
            return state, (), ()

        applied, fills, trades = _apply_reports(
            _record_venue_execution(state, order, report), order, (report,)
        )
        return replace(applied, id_position=current_id_position()), fills, trades

    @staticmethod
    def apply_terminal_outcome(
        state: ExecutionPipelineState,
        order: OMSOrder,
        outcome: OrderStatus,
        timestamp: float,
        reason: str = "",
    ) -> ExecutionPipelineState:
        """End a working order the venue has reported dead, and free what it held.

        The counterpart to :meth:`apply_execution_report`, for the outcome that
        is not a fill. Under :attr:`ExecutionRouting.EXTERNAL` an accepted order
        is left working for a broker adapter to route, and its reservation and
        contribution stay held -- correctly, because the order is live and that
        capital is committed. Until now nothing could end such an order: a venue
        *fill* had a route home and a venue rejection, cancellation or expiry had
        none, so a working order and both its allocation ledgers were held
        indefinitely. That is the gap this closes.

        The caller supplies the outcome; the pipeline never infers it. Nothing
        here contacts a venue, builds a ``BrokerState``, or consults
        :mod:`alphalab.broker.reconciliation` -- the venue is the authority for
        what happened, and this is only the state transition that follows from
        being told. It is therefore independent of transport, and of routing: it
        works on any working order, and checks no routing mode.

        The transition itself is the existing one.
        :class:`~alphalab.oms.engine.OMSEngine` moves the order and emits its
        lifecycle event, which also moves it between the active and completed
        sets, and :func:`_release_if_terminal` retires the reservation and the
        contribution exactly as it does for every other terminal transition. No
        second terminal state machine is introduced and no new event type is
        added.

        A partial fill already applied is untouched. ``Order.cancel`` preserves
        ``filled_quantity`` and ``average_fill_price``, so cancelling a partially
        filled order closes only the quantity still working. **Cancellation is
        not an implicit fill**: no fill, trade or portfolio effect is produced
        here at all.

        ``reason`` is recorded only for ``REJECTED``, because
        :class:`~alphalab.oms.events.OrderRejected` is the only one of the three
        events that carries the field. Giving ``OrderCancelled`` and
        ``OrderExpired`` one would change the OMS event shape, and OMS events are
        part of the snapshot payload whose schema v2.9 has just declared -- so a
        reason that cannot be recorded is dropped rather than the payload
        changed. An empty ``reason`` is passed through as empty: a caller who
        said nothing is recorded as having said nothing, not given an invented
        explanation.

        Args:
            state: The pipeline state holding the working order.
            order: The order the venue reported on.
            outcome: ``CANCELLED``, ``REJECTED`` or ``EXPIRED``.
            timestamp: When the outcome is recorded.
            reason: Why the venue refused it. Recorded for ``REJECTED`` only.

        Returns:
            The state with the order terminal and both allocation ledgers
            retired.

        Raises:
            RuntimeValidationError: If ``outcome`` is not one of the three
                terminal outcomes. A status that is reached by trading is not
                something a caller may assert.
            UnknownOrderError: If the order is not in the book.
            InvalidTransitionError: If the order cannot make the transition --
                it is already terminal, or it is partially filled and the
                outcome is ``REJECTED``, which ``Order.reject`` refuses because
                an order that has already traded cannot be rejected. Both rules
                are the OMS lifecycle's own and are preserved rather than
                relaxed here.
        """

        if outcome not in _TERMINAL_OUTCOMES:
            permitted = ", ".join(status.name for status in _TERMINAL_OUTCOMES)
            raise RuntimeValidationError(
                f"{outcome} is not a terminal outcome a caller may report. "
                f"Permitted outcomes are {permitted}. A status reached by trading "
                "is applied from an execution report, not asserted -- see "
                "ExecutionPipeline.apply_execution_report."
            )

        terminated = replace(
            state, oms=_terminate_order(state.oms, order, outcome, reason, timestamp)
        )
        released = _release_if_terminal(terminated, order.order_id, timestamp)
        return replace(released, id_position=current_id_position())

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
        return replace(state, analytics=analytics, id_position=current_id_position())


def _process_requests(
    state: ExecutionPipelineState,
    event: MarketEvent,
    intents: tuple[Intent, ...],
    requests: tuple[OrderRequest, ...],
    policy: FillPolicy,
) -> ExecutionPipelineResult:
    decisions: list[RiskDecision] = []
    orders: list[OMSOrder] = []
    reports: list[ExecutionReport] = []
    fills: list[CoreFill] = []
    trades: list[CoreTrade] = []
    unpriced: list[OrderRequest] = []
    refusals: list[SettlementRefusal] = []
    current = state

    for request in requests:
        # An order cannot be priced, executed or valued without a market price
        # for its asset -- allocation prices unknown assets at 0.00. Drop the
        # request here, deterministically and before it reaches the OMS, rather
        # than submitting an order the execution leg cannot price.
        if request.asset_id not in current.market_prices:
            unpriced.append(request)
            current = _record_unpriced(current, request.asset_id, event.timestamp)
            current = _retire_dropped_request(current, request.order_id, event.timestamp)
            continue
        # Seam 1 of ADR-0028, and deliberately *after* the price check: an
        # instrument that is both foreign and unpriced is still reported as
        # unpriced, because the run genuinely never priced it and the
        # classification that was already there is not reinterpreted.
        #
        # Dropped rather than raised. The pipeline is about to create this
        # order and may equally decline to, and this is the point where
        # _retire_dropped_request already frees both allocation ledgers -- so
        # no reservation leaks and no contribution is orphaned. Raising would
        # kill a live session over one misconfigured instrument. The venue
        # side, where the fill has already happened and cannot be declined,
        # raises instead: see _require_settlement_currency.
        refusal = _settlement_refusal(current, request.asset_id, event.timestamp)
        if refusal is not None:
            refusals.append(refusal)
            current = _retire_dropped_request(current, request.order_id, event.timestamp)
            continue
        current, decision = _evaluate_risk(current, request, event.timestamp)
        decisions.append(decision)
        if not decision.approved:
            # Allocation reserved this request's notional when it sized it.
            # Risk refused it, so it will never reach the OMS and never
            # execute: the capital it holds is freed here, at the point its
            # lifecycle ends, and exactly once.
            current = _retire_dropped_request(current, request.order_id, event.timestamp)
            continue
        current, order = _submit_and_accept_order(current, request, event.timestamp)
        if current.config.routing is ExecutionRouting.EXTERNAL:
            # The order is now working and belongs to whoever routes it. No
            # fill is invented, the order is not closed out, and its
            # reservation stays held -- the capital is still committed.
            orders.append(order)
            continue
        decision_out = _decide_fill(policy, order, event, current.market_prices[request.asset_id])
        current, new_reports = _execute_order(current, order, decision_out)
        # A rejected, expired or unfilled execution produces no report. The
        # order never trades, so close it out of the OMS instead of leaving it
        # open forever awaiting a fill, and retire both ledgers it holds. The
        # order is terminal by the time _release_if_terminal is asked, which is
        # what lets that one function serve every terminal transition.
        if not new_reports and decision_out.status in _NON_TRADING_STATUSES:
            current = replace(
                current,
                oms=_close_unfilled_order(current.oms, order, decision_out.status, event.timestamp),
            )
            current = _release_if_terminal(current, order.order_id, event.timestamp)

        current, new_fills, new_trades = _apply_reports(current, order, new_reports)
        current = _withdraw_partial_remainder(current, request, order, event.timestamp)
        orders.append(order)
        reports.extend(new_reports)
        fills.extend(new_fills)
        trades.extend(new_trades)

    snapshot = _portfolio_snapshot(current.portfolio, current.config.currency, event.timestamp)
    # The step boundary, and the only place the position is refreshed for an
    # event: every environment reaches here through process_record,
    # process_market_event or process_quote, and none of them refreshes it
    # earlier -- a position read halfway through a step would describe neither
    # the state before it nor the state after.
    current = replace(
        current,
        portfolio_snapshots=current.portfolio_snapshots.append(snapshot),
        id_position=current_id_position(),
    )
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
        tuple(refusals),
    )


def _classify_unpriced(state: ExecutionPipelineState, asset_id: str) -> tuple[UnpricedReason, str]:
    """What this run can honestly say about an asset it never priced.

    Without a registry the run knows one thing: it saw no price. It says that
    and stops, rather than reporting an absence it did not check.

    With one it can separate the two cases that matter, because they call for
    opposite fixes: an identifier naming nothing is a registry or strategy
    problem, while a registered instrument the run never priced is a data-window
    one. The registry is consulted through
    :meth:`~alphalab.instrument.registry.InstrumentRegistry.record_for` and
    nothing else -- one keyed lookup, on a path a healthy run never takes.
    """

    registry = state.config.instruments
    if registry is None:
        return (
            UnpricedReason.NO_PRICE_OBSERVED,
            f"No market price was observed for asset_id {asset_id!r} in this run. "
            "No InstrumentRegistry is configured on the pipeline, so this run cannot "
            "say whether that identifier names a registered instrument.",
        )

    record = registry.record_for(asset_id)
    if record is None:
        return (
            UnpricedReason.NOT_REGISTERED,
            f"asset_id {asset_id!r} is not a registered instrument. A strategy named "
            "an instrument the registry does not hold, so nothing could ever price "
            "it and no order for it can reach a fill. Register the instrument, or "
            "resolve the identifier through the same registry the run's "
            "NormalizationPolicy uses.",
        )
    return (
        UnpricedReason.REGISTERED_BUT_UNPRICED,
        f"asset_id {asset_id!r} is registered as {record.symbol} on {record.exchange} "
        f"in {record.currency}, and this run observed no price for it. The instrument "
        "exists; the market data did not cover it.",
    )


def _record_unpriced(
    state: ExecutionPipelineState, asset_id: str, timestamp: float
) -> ExecutionPipelineState:
    """Record that a request for ``asset_id`` was dropped, or that it happened again.

    The reason is settled once, on the first drop, and never recomputed: the
    registry is immutable configuration, so its answer cannot change during a
    run, and re-deriving it would put work on a path that a misconfigured run
    takes on every event.
    """

    existing = state.unpriced_assets.get(asset_id)
    if existing is None:
        reason, detail = _classify_unpriced(state, asset_id)
        entry = UnpricedAsset(asset_id, reason, detail, timestamp, timestamp, 1)
    else:
        entry = replace(existing, last_timestamp=timestamp, occurrences=existing.occurrences + 1)
    return replace(state, unpriced_assets=state.unpriced_assets.set(asset_id, entry))


def _evaluate_risk(
    state: ExecutionPipelineState, request: OrderRequest, timestamp: float
) -> tuple[ExecutionPipelineState, RiskDecision]:
    risk, decision = RiskEngine.evaluate(state.risk, request, timestamp)
    return replace(state, risk=risk), decision


def _release_reservation(
    state: ExecutionPipelineState, order_id: str, timestamp: float
) -> ExecutionPipelineState:
    """Free the allocation capital an order holds, once its lifecycle ends.

    The allocation engine owns the amount; the pipeline owns the moment. The
    membership check is what makes this idempotent, and every release point
    depends on that: an order whose capital was already freed -- or which never
    produced a request-level reservation, as a batch dropped by the budget check
    does not -- has nothing to release, and asking the engine a second time
    would raise :class:`~alphalab.allocation.exceptions.UnknownReservationError`.
    """

    if order_id not in state.allocation.reservations:
        return state
    return replace(
        state,
        allocation=AllocationEngine.release_reservation(state.allocation, order_id, timestamp),
    )


def _retire_dropped_request(
    state: ExecutionPipelineState, order_id: str, timestamp: float
) -> ExecutionPipelineState:
    """End the life of a request that will never become an order.

    Allocation opens two ledger entries per emitted request: a reservation for
    the capital it holds, and a contribution entry recording which strategies
    asked for it. They have the same lifetime -- from the moment allocation
    emits the request until that request's life ends -- and both must be retired
    at that point.

    Only one of them was. :func:`_release_if_terminal` retires both, but it
    returns early unless the order is in the OMS book, and a request dropped for
    want of a price or refused by risk never reaches the OMS. So the reservation
    was freed and the contribution entry was immortal: forty such requests left
    forty entries that nothing could ever delete, growing for as long as a
    misconfigured run continued.

    ``AllocationState.contributions`` documented its own lifetime as ending when
    "that request's order reaches a terminal state", which quietly assumed every
    request becomes an order. A request whose life ends before the OMS ends here
    instead; one that ends *as* a terminal order ends in
    :func:`_release_if_terminal`. Between them the invariant holds without a
    gap: a contribution exists exactly as long as the request does.

    Both retirements are idempotent -- ``_release_reservation`` checks
    membership and ``retire_contributions`` returns unchanged for an absent key
    -- so this is safe wherever a request's lifecycle ends, exactly once.
    """

    released = _release_reservation(state, order_id, timestamp)
    return replace(
        released,
        allocation=AllocationEngine.retire_contributions(released.allocation, order_id),
    )


def _release_if_terminal(
    state: ExecutionPipelineState, order_id: OrderId, timestamp: float
) -> ExecutionPipelineState:
    """Free whatever a *terminal* order still holds.

    A reservation is denominated at the request's **reference** price, while
    :meth:`~alphalab.allocation.engine.AllocationEngine.apply_execution` consumes
    at the **execution** price. Those are different units of account, so a fill
    priced away from the reference leaves a residual: ``min(reserved, executed)``
    strands it when the fill was cheaper and silently discards the excess when it
    was dearer.

    Before v2.6 nothing freed that residual. A *partially* filled order is
    withdrawn by :func:`_withdraw_partial_remainder`, but an order the venue
    filled in full reaches ``FILLED`` -- terminal -- holding capital committed to
    nothing, and every later event added more. It is released here, at the
    terminal transition, which is the moment shared by both routings: a
    simulated fill arrives through :func:`_process_requests` and a venue fill
    through :meth:`ExecutionPipeline.apply_execution_report`, and both go through
    :func:`_apply_reports`.

    This is not slippage handling. Slippage is the most common *source* of a
    price divergence; the condition is the divergence itself, whatever caused it.
    An order that is still working keeps its reservation, because that capital is
    still committed. See ADR-0015.

    It retires the contribution ledger too, and until v2.8 it was the only thing
    that did -- which meant an order reaching a terminal state by any route
    other than a report left its contributions behind forever. A ``NO_FILL``,
    ``REJECTED`` or ``EXPIRED`` execution produces no report at all, so
    :func:`_apply_reports`'s per-report loop never ran and never called this; a
    partially filled order was cancelled by :func:`_withdraw_partial_remainder`,
    which freed the reservation and nothing else. Measured on v2.7.0, twenty
    events on each of those four paths left twenty entries apiece. Every
    terminal transition now comes through here, so "terminal" has one meaning
    and one consequence. The pre-OMS paths, where there is no order to be
    terminal, are :func:`_retire_dropped_request`.

    Both retirements are idempotent -- the reservation release checks
    membership, ``retire_contributions`` returns unchanged for an absent key --
    so calling this at more than one point in an order's life is safe and
    retires exactly once.
    """

    if not state.oms.orders.contains(order_id):
        return state
    if state.oms.orders.find(order_id).is_open:
        return state
    released = _release_reservation(state, str(order_id.value), timestamp)
    return replace(
        released,
        allocation=AllocationEngine.retire_contributions(released.allocation, str(order_id.value)),
    )


def _decide_fill(
    policy: FillPolicy, order: OMSOrder, event: MarketEvent, price: Decimal
) -> FillDecision:
    """Ask the policy what the venue does with this order at this event."""

    return policy.decide(
        LiquidityContext(
            asset_id=order.asset_id,
            side=order.side,
            requested_quantity=order.remaining_quantity,
            price=price,
            available_quantity=_available_quantity(event, order.side),
            timestamp=event.timestamp,
        )
    )


def _available_quantity(event: MarketEvent, side: OMSSide) -> Decimal | None:
    """Size the market event showed, on the side the order has to cross."""

    if isinstance(event, QuoteReceived):
        quote = event.quote
        return quote.ask_size if side is OMSSide.BUY else quote.bid_size
    if isinstance(event, BarClosed):
        return event.bar.volume
    if isinstance(event, TickReceived | TradeReceived):
        return event.tick.quantity
    return None


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
    decision: FillDecision,
) -> tuple[ExecutionPipelineState, tuple[ExecutionReport, ...]]:
    before = len(state.execution.history)
    instruction = _instruction(order, state)
    quantity = decision.quantity if decision.quantity is not None else order.remaining_quantity
    execution = ExecutionEngine.simulate(
        state.execution,
        state.config.simulator,
        instruction,
        quantity,
        instruction.price,
        order.updated_at,
        decision.status,
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
            current.allocation, report.order_id, executed_notional, report.timestamp
        )
        current = replace(current, allocation=allocation_state)
        # If this report took the order terminal, whatever the reference price
        # reserved but the execution price did not consume is capital committed
        # to nothing. Free it here -- see _release_if_terminal.
        current = _release_if_terminal(current, order.order_id, report.timestamp)
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


def _record_venue_execution(
    state: ExecutionPipelineState, order: OMSOrder, report: ExecutionReport
) -> ExecutionPipelineState:
    """Record a report that did not come from the simulator in the execution state.

    The simulated path records through
    :meth:`~alphalab.execution.engine.ExecutionEngine.execute` and
    :meth:`~alphalab.execution.engine.ExecutionEngine.partial_fill`, which store
    the report by execution id, append it to the history and emit the matching
    lifecycle event. A venue fill goes through the same two functions, so the
    ledger, the history and the event are identical whichever way the fill
    arrived -- the property this seam exists to guarantee, and the one place it
    was not being kept.

    ``remaining_quantity`` on a partial fill is what is left of the instruction
    after it, which is what the simulated path passes: there the instruction is
    sized at ``order.remaining_quantity``, so the venue equivalent is that
    quantity less the fill. This seam takes fills --
    :func:`~alphalab.runtime.broker_routing.execution_report_from_broker`
    produces only ``FULL_FILL`` and ``PARTIAL_FILL`` -- and a partial fill is the
    only one of those that leaves a remainder.
    """

    if report.status is FillStatus.PARTIAL_FILL:
        execution = ExecutionEngine.partial_fill(
            state.execution, report, order.remaining_quantity - report.fill_quantity
        )
    else:
        execution = ExecutionEngine.execute(state.execution, report)
    return replace(state, execution=execution)


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


def _require_settlement_currency(state: ExecutionPipelineState, report: ExecutionReport) -> None:
    """Refuse a report denominated in something this pipeline does not settle.

    Seam 2 of ADR-0028, and the counterpart to :func:`_settlement_refusal`. They
    answer different questions: Seam 1 asks whether this run may *trade* an
    instrument, and needs the registry to answer; this asks whether a report is
    denominated in what the pipeline *settles*, and needs nothing but the
    configuration. Neither subsumes the other -- with only this check a foreign
    instrument would still slip through, because its report carries the
    settlement currency; with only Seam 1 a venue fill would still slip through,
    because it never passes through :func:`_process_requests`.

    **This raises where Seam 1 drops**, and the asymmetry is the one
    :func:`_close_unfilled_order` and :func:`_terminate_order` already draw. The
    pipeline decides what the simulator does and may decline to create an order;
    a venue *reports* what already happened, and a fill that has occurred cannot
    be declined. Its only honest answers are to book it -- making the book mixed,
    which the next valuation refuses anyway -- or to refuse and say so. Every
    state here is immutable, so a caller that is refused still holds exactly the
    state it passed in.

    The simulated path cannot reach this refusal. :func:`_instruction` builds
    every ``OrderInstruction`` with ``state.config.currency`` and
    :meth:`~alphalab.execution.simulator.ExecutionSimulator.simulate_fill` copies
    it onto the report, so the two are the same string by construction. Here it
    costs one comparison per fill and functions as a structural invariant. The
    live path is where it does work: ``RoutingConfig.currency`` is a fifth
    currency site that ADR-0019 did not name and nothing checked, and a venue
    fill stamped with a mismatched one used to be booked silently.

    Raises:
        RuntimeValidationError: If the report is denominated in a currency this
            pipeline does not settle in. Deliberately the same class
            :func:`_require_one_account_currency` raises: one category of fault
            -- a call into the pipeline naming two currencies where it settles
            one -- gets one name.
    """

    if report.currency == state.config.currency:
        return

    raise RuntimeValidationError(
        f"Execution report {report.execution_id} for asset {report.asset_id} is "
        f"denominated in {report.currency!r}, and this pipeline settles in "
        f"{state.config.currency!r}. Applying it would book a position and move "
        f"cash in {report.currency!r}, leaving a book holding two currencies that "
        "no valuation can express as one figure -- the next portfolio snapshot "
        "would raise. Nothing has been applied and the state you passed in is "
        "unchanged. A venue fill reaches this path through "
        "RoutingConfig.currency, which defaults to 'USD' and is not the "
        "pipeline's settlement currency unless it is set to it."
    )


def _apply_report_to_portfolio(
    state: ExecutionPipelineState, report: ExecutionReport, side: OMSSide
) -> ExecutionPipelineState:
    # Seam 2 of ADR-0028, before anything is read or applied, on the one path a
    # simulated fill and a venue fill both take -- the site ADR-0027 decision 6
    # chose for sector, and for the same parity reason.
    _require_settlement_currency(state, report)
    signed_quantity = report.fill_quantity if side is OMSSide.BUY else -report.fill_quantity
    before = len(state.portfolio.events)
    # Read both *before* the fill is applied. A closing fill removes the
    # position, taking its ``opened_at`` with it, and the contribution ledger is
    # retired once the order goes terminal -- so neither can be recovered after.
    opened_at = _opened_at(state.portfolio, report.asset_id)
    contributions = AllocationEngine.contributions_for(state.allocation, report.order_id)
    # Read here, on the one path both a simulated and a venue fill take, so
    # sector parity across the four environments is structural rather than a
    # convention two call sites have to keep. See ADR-0027 decision 6.
    sector = _sector_for(state, report.asset_id)
    portfolio = PortfolioEngine.apply_fill(
        state.portfolio,
        report.asset_id,
        signed_quantity,
        report.fill_price,
        report.commission,
        report.timestamp,
        report.currency,
    )
    risk = _sync_risk_from_portfolio(state.risk, portfolio, state.config.instruments)
    record = _trade_record(report, portfolio.events[before:], opened_at, contributions, sector)
    return replace(
        state,
        portfolio=portfolio,
        risk=risk,
        trade_records=state.trade_records.append(record),
    )


def _withdraw_partial_remainder(
    state: ExecutionPipelineState,
    request: OrderRequest,
    order: OMSOrder,
    timestamp: float,
) -> ExecutionPipelineState:
    """Retire what a partial fill left behind, and free the capital it held.

    A partially filled order keeps a positive ``remaining_quantity``, and under
    this pipeline's own rule -- stated in :func:`_close_unfilled_order`, and true
    since the pipeline was written -- that remainder will never be worked again:
    a fresh order is minted per market event and no existing one is revisited.
    Before v2.5 the order simply stayed ``PARTIALLY_FILLED`` forever, holding the
    reservation for a quantity nothing would ever execute.
    ``LiquidityCappedFill`` makes partial fills routine, so that was a slowly
    growing set of orders in a state nothing could leave, and capital held
    against them indefinitely.

    The remainder is therefore cancelled, exactly as a never-filled order is, and
    the ledgers the now-terminal order still holds are retired. Nothing else
    changes: no fill is created or destroyed, so cash, positions, realized and
    unrealized P&L, the equity curve and every analytics figure are the same as
    before. ``Order.cancel``
    preserves ``filled_quantity`` and ``average_fill_price``, so the fill that
    did happen is untouched. See ADR-0014.
    """

    current = state.oms.orders.find(order.order_id)
    if current.status is not OrderStatus.PARTIALLY_FILLED:
        return state

    withdrawn = replace(state, oms=OMSEngine.cancel(state.oms, order.order_id, timestamp))
    return _release_if_terminal(withdrawn, order.order_id, timestamp)


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


def _terminate_order(
    oms: OMSState, order: OMSOrder, outcome: OrderStatus, reason: str, timestamp: float
) -> OMSState:
    """Make one working order terminal through the OMS's own transitions.

    The sibling of :func:`_close_unfilled_order`, which answers the same
    question for the simulated path. The two differ only in the vocabulary they
    are asked in -- a ``FillStatus`` there, because the simulator reports what a
    fill did, and an ``OrderStatus`` here, because a venue reports what the order
    became -- and they dispatch to the same three engine calls.
    """

    if outcome is OrderStatus.CANCELLED:
        return OMSEngine.cancel(oms, order.order_id, timestamp)
    if outcome is OrderStatus.EXPIRED:
        return OMSEngine.expire(oms, order.order_id, timestamp)
    return OMSEngine.reject(oms, order.order_id, reason, timestamp)


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


def _opened_at(portfolio: PortfolioState, asset_id: str) -> float | None:
    """When the position this fill is about to touch began, if it exists."""

    position = portfolio.positions.get(asset_id)
    return None if position is None else position.opened_at


def _sector_of(registry: InstrumentRegistry, asset_id: str) -> str | None:
    """What ``registry`` classifies ``asset_id`` as, in one keyed lookup.

    The single rule both readers share -- :func:`_sector_for` on the fill path
    and :func:`_risk_exposure` on the position path -- so the two cannot come to
    disagree about what an unregistered or unclassified asset means. Never
    scans; ``record_for`` is a ``PersistentMap`` lookup.
    """

    record = registry.record_for(asset_id)
    return None if record is None else record.sector


def _currency_of(registry: InstrumentRegistry, asset_id: str) -> str | None:
    """What ``registry`` says ``asset_id`` trades in, in one keyed lookup.

    The exact sibling of :func:`_sector_of`, and deliberately so: same shape,
    same ``None``-for-absent rule, same single ``record_for`` call on a
    ``PersistentMap``. Never scans, and mints no identifier.

    The two differ in one way that matters, and it makes currency the easier
    case. A sector is descriptive and mutable, so ADR-0027 had to freeze it onto
    each fill to stop a reclassification rewriting a finished run. A currency is
    one of the four fields ``asset_id`` is *derived* from (ADR-0016): changing it
    derives a different identifier, so it names a different instrument, and the
    registry's classification write exposes no identity field to reach it with.
    The answer here is therefore already frozen by identity, which is why
    nothing is copied onto :class:`~alphalab.analytics.attribution.TradeRecord`.

    ``None`` means the registry does not hold this ``asset_id``. That is not a
    settlement fault and must not be reported as one:
    :attr:`UnpricedReason.NOT_REGISTERED` already owns "a strategy named an
    instrument the registry does not hold", and one fault reported twice under
    two names is worse than the fault.
    """

    record = registry.record_for(asset_id)
    return None if record is None else record.currency


def _settlement_refusal(
    state: ExecutionPipelineState, asset_id: str, timestamp: float
) -> SettlementRefusal | None:
    """Whether this run may trade ``asset_id``, and why not.

    Seam 1 of ADR-0028, and the only place the authority is exercised. One
    ``record_for`` lookup on the happy path, taken per *request* rather than per
    event or per fill: per event would pay for instruments the run never names,
    and per fill would be too late -- an order would already exist, holding a
    reservation and a contribution entry.

    Returns ``None`` -- meaning "trade it" -- in three situations that are
    deliberately not distinguished, because a run that may trade an instrument
    has no use for the reason:

    * no registry is configured, so this run has no currency authority and books
      in its settlement currency exactly as it did before v2.12;
    * the registry does not hold the asset, which is
      :attr:`UnpricedReason.NOT_REGISTERED`'s question, not this one;
    * the instrument settles here.

    The second ``record_for`` below is on the refusal path only, which a healthy
    run never takes, and it buys a message that names the instrument rather than
    only its identifier.
    """

    registry = state.config.instruments
    if registry is None:
        return None

    settlement = state.config.currency
    currency = _currency_of(registry, asset_id)
    if currency is None or currency == settlement:
        return None

    record = registry.record_for(asset_id)
    named = (
        f"{record.symbol} on {record.exchange}" if record is not None else f"asset_id {asset_id!r}"
    )
    return SettlementRefusal(
        asset_id=asset_id,
        instrument_currency=currency,
        settlement_currency=settlement,
        detail=(
            f"{named} trades in {currency!r} and this pipeline settles in "
            f"{settlement!r}, so it cannot be traded here. The request was dropped "
            "before it reached the OMS and the capital it held was released. "
            f"Run a pipeline whose currency and Account.base_currency are {currency!r} "
            "to trade this instrument, or trade an instrument that settles in "
            f"{settlement!r}. This is a settlement boundary, not a missing FX rate."
        ),
        timestamp=timestamp,
    )


def _sector_for(state: ExecutionPipelineState, asset_id: str) -> str | None:
    """The sector this run's registry classifies ``asset_id`` as, right now.

    One keyed lookup on a registry the pipeline already holds, taken on the
    **fill** path rather than the event path: fills are strictly rarer than
    events, the fact is only needed when a fill exists, and the sector has to be
    frozen as of the fill anyway (ADR-0027 decision 5). Nothing is scanned and
    no identifier is minted.

    ``None`` is the answer in three different situations -- no registry was
    configured, the asset is not registered, the instrument is registered but
    unclassified -- and they are deliberately **not** distinguished. Unlike
    :class:`UnpricedReason`, where three cases call for opposite fixes and the
    run otherwise produced nothing with no explanation, the only question a
    consumer asks here is whether a sector is known, and the absence is already
    visible as an empty breakdown. Recording a reason would add a field to
    :class:`~alphalab.analytics.attribution.TradeRecord`, and therefore to the
    snapshot payload, for observability nobody needs.
    """

    registry = state.config.instruments
    return None if registry is None else _sector_of(registry, asset_id)


def _trade_record(
    report: ExecutionReport,
    fill_events: Sequence[PortfolioEvent],
    opened_at: float | None,
    contributions: tuple[StrategyContribution, ...],
    sector: str | None,
) -> TradeRecord:
    """Build the analytics trade record for one execution report.

    A pure formatter: every fact it records is handed to it. ``sector`` is
    resolved by :func:`_sector_for` at the call site, which is what keeps this
    function free of the pipeline state and keeps the registry read at one
    place.

    ``fill_events`` are only the portfolio events this fill produced. Scanning
    the whole portfolio history instead would attribute an earlier close's
    realized P&L to an opening fill that realized nothing.

    A fill that *reduced or closed* a position gets a holding period measured
    from that position's ``opened_at``. A fill that opened or increased one gets
    ``None``: it has held nothing, and the ``0.0`` this field carried until v2.6
    was a measurement that was never made -- it made ``avg_holding_period`` a
    mean of zeros.

    ``sector`` is the classification **as of this fill**, and it is copied here
    rather than referenced, which is what stops a later reclassification from
    rewriting a completed run's attribution (ADR-0027 decision 5). ``None``
    remains an honest absence; ``"UNCLASSIFIED"`` presented one fictional bucket
    as though it were a breakdown and is not coming back.
    """

    realized = Decimal("0.00")
    holding_period: float | None = None
    for evt in fill_events:
        # PositionReduced(timestamp, account_id, asset_id, reduced_quantity, price, realized_pnl)
        # PositionClosed(timestamp, account_id, asset_id, price, realized_pnl)
        if isinstance(evt, PositionReduced | PositionClosed):
            realized = evt.realized_pnl
            if opened_at is not None:
                holding_period = report.timestamp - opened_at
            break

    return TradeRecord(
        trade_id=report.execution_id,
        asset_id=report.asset_id,
        sector_id=sector,
        realized_pnl=realized,
        notional_value=report.fill_quantity * report.fill_price,
        holding_period_seconds=holding_period,
        contributions=contributions,
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


def _sync_risk_from_portfolio(
    risk: RiskState, portfolio: PortfolioState, instruments: InstrumentRegistry | None
) -> RiskState:
    """Refresh the risk state from a marked book.

    ``instruments`` is threaded rather than defaulted so that no call site can
    silently stop reporting sector exposure by forgetting it. It is read only by
    :func:`_risk_exposure`, and only to classify; nothing here resolves,
    registers or classifies anything.
    """

    cash = portfolio.cash.balance(portfolio.account.base_currency)
    nav = NAVCalculator.calculate(
        portfolio.cash, portfolio.positions, portfolio.account.base_currency
    )
    exposure = _risk_exposure(portfolio, instruments)

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


def _risk_exposure(
    portfolio: PortfolioState, instruments: InstrumentRegistry | None
) -> ExposureStatus:
    """Exposure by asset and, when the run classifies its instruments, by sector.

    :attr:`~alphalab.risk.exposure.ExposureStatus.sector_exposure` has been
    declared, typed, persisted and decoded since before v2.6 and populated by
    nothing at all. It is filled here, in the **same pass** that builds
    ``asset_exposure`` -- this runs on the per-event path and again on the
    per-fill path, so a second traversal is not free enough to spend.

    Bucketed on **signed** market value, matching ``asset_exposure`` and
    ``net_exposure`` rather than ``gross_exposure``: the sectors therefore sum
    to the net exposure of the classified positions. An unclassified position is
    omitted rather than bucketed under a placeholder, for the reason a trade
    with no sector is omitted from ``pnl_by_sector`` -- an absent breakdown
    beats a fictional one (ADR-0027 decision 8).

    A run with no registry pays one ``is not None`` comparison per position and
    nothing else, and gets an empty mapping -- which is what every run produced
    before v2.11.

    Every figure here aggregates market values across positions and is reported
    against the account's base currency, so this is a valuation in the sense the
    module docstring of :mod:`alphalab.portfolio.valuation` defines, and it
    refuses a mixed book. ``sector_exposure`` is the newest such aggregation in
    the tree and was blind from the day it was written: v2.11 bucketed signed
    market value by sector with no regard for what each position traded in, so a
    mixed book produced sector totals summed across currencies. The check folds
    into the pass this function already makes -- one string comparison per
    position, the same shape as the ``instruments is not None`` test beside it --
    and delegates the message to the one rule that owns it. See ADR-0028
    decision 7.

    Raises:
        MixedCurrencyValuationError: If the book holds positions or non-zero cash
            in any currency other than the account's base currency.
    """

    base_currency = portfolio.account.base_currency
    asset_exposure: dict[str, Decimal] = {}
    sector_exposure: dict[str, Decimal] = {}
    long_exposure = Decimal("0.00")
    short_exposure = Decimal("0.00")

    for asset_id, position in portfolio.positions.items():
        if position.currency != base_currency:
            # Always raises: a foreign position guarantees the predicate's first
            # condition fails. Delegated rather than raised here so that one
            # rule owns the message and the two cannot come to disagree.
            assert_single_currency_book(portfolio.cash, portfolio.positions, base_currency)
        value = position.market_value
        asset_exposure[asset_id] = value
        if value > 0:
            long_exposure += value
        elif value < 0:
            short_exposure += value
        if instruments is not None:
            sector = _sector_of(instruments, asset_id)
            if sector is not None:
                sector_exposure[sector] = sector_exposure.get(sector, Decimal("0.00")) + value

    return ExposureStatus(
        gross_exposure=long_exposure + abs(short_exposure),
        net_exposure=long_exposure + short_exposure,
        long_exposure=long_exposure,
        short_exposure=short_exposure,
        asset_exposure=asset_exposure,
        sector_exposure=sector_exposure,
    )
