"""End-to-end execution pipeline orchestration.

This module connects the existing pure subsystem engines without introducing
replacement domain models. Boundary conversions stay here so package ownership
remains explicit and the canonical core entities are preserved.
"""

from __future__ import annotations

import decimal
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_FLOOR, Decimal
from enum import Enum, auto
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.ceilings import adding_exposure
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.events import AllocationRejected, BudgetExceeded
from alphalab.allocation.sizing import FixedQuantitySizing, SizingModel
from alphalab.allocation.state import AllocationState
from alphalab.analytics.attribution import TradeRecord
from alphalab.analytics.engine import AnalyticsEngine, PortfolioSnapshot
from alphalab.analytics.state import AnalyticsState
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT, in_accounting_context
from alphalab.common.evolve import evolve
from alphalab.common.ids import IdStreamPosition, current_id_position, new_id
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.common.persistent_map import PersistentMap
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.conventions.tick import RoundingDirection, is_on_tick, round_to_tick
from alphalab.core.contribution import StrategyContribution, split_by_contribution
from alphalab.core.enums import AssetType
from alphalab.core.enums import Side as CoreSide
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.order_request import OrderRequest
from alphalab.core.trade import Trade as CoreTrade
from alphalab.data.calendar import MAX_SESSION_SEARCH_DAYS
from alphalab.data.feed import TradeAggressor
from alphalab.execution.algorithms import ChildOrder
from alphalab.execution.algorithms import _apportion as _largest_remainder
from alphalab.execution.auction import AuctionReopening
from alphalab.execution.engine import ExecutionEngine
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.policy import (
    FillDecision,
    FillPolicy,
    FillTiming,
    LiquidityContext,
    StaticFill,
)
from alphalab.execution.queue import QueueBook, QueueObservation, QueuePosition, QueueTurn
from alphalab.execution.report import ExecutionReport
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.state import ExecutionState
from alphalab.instrument.borrow import BorrowAvailability
from alphalab.instrument.controls import (
    AuctionPriority,
    AuctionTieBreak,
    BandAction,
    TradingControls,
    TradingHalt,
)
from alphalab.instrument.economics import economics_for
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.margin import (
    MarginBasis,
    MarginExposure,
    MarginModel,
    MarginTerms,
    OptionRisk,
    ScenarioMargin,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry
from alphalab.instrument.terms import Deliverable, FutureTerms, OptionTerms
from alphalab.market.bar import Bar, IntervalUnit, TimeFrame
from alphalab.market.engine import MarketEngine
from alphalab.market.events import (
    BarClosed,
    MarketEvent,
    QuoteReceived,
    TickReceived,
    TradeReceived,
)
from alphalab.market.exceptions import MarketValidationError, UnsupportedRecordError
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
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.expiration import SettlementStyle
from alphalab.portfolio.account import Account
from alphalab.portfolio.book import BookGroups, BookMarketValues, PositionBook
from alphalab.portfolio.corporate_actions import (
    Assignment,
    CashFlow,
    CashFlowKind,
    ContractSettlement,
    ExchangedHolding,
    ExchangeLeg,
    Exercise,
    HoldingExchange,
    OptionExpiry,
    Split,
)
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.events import (
    PortfolioEvent,
    PositionClosed,
    PositionExchanged,
    PositionReduced,
)
from alphalab.portfolio.exceptions import InvalidTransactionError
from alphalab.portfolio.financing import ACCRUAL_CLOSED, ACCRUES
from alphalab.portfolio.fx import (
    NO_RATES,
    FxConversion,
    FxRate,
    FxRates,
    MissingRateError,
    StaleRateError,
)
from alphalab.portfolio.money import to_money
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.position import Position
from alphalab.portfolio.settlement import DeliveryConvention, PaymentChange, payment_chain
from alphalab.portfolio.types import TransactionType
from alphalab.portfolio.valuation import (
    PortfolioValuation,
    PortfolioValuationSnapshot,
    assert_single_currency_book,
    book_totals_in,
    cash_in,
)
from alphalab.risk.decision import RiskDecision
from alphalab.risk.engine import RiskEngine
from alphalab.risk.events import BuyIn, MarginCall
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import RiskLimits
from alphalab.risk.margin import MarginStatus
from alphalab.risk.projection import NO_WORKING_ORDERS, BucketExposure, WorkingExposure
from alphalab.risk.state import RiskState
from alphalab.runtime.calendars import VenueCalendars, settles_at
from alphalab.runtime.context_views import (
    HistoryView,
    MarketView,
    OrderShare,
    OrderView,
    PortfolioView,
    RiskView,
    UniverseView,
    order_shares_by_strategy,
)
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_adapters import canonical_execution_from_report
from alphalab.runtime.retention import RetentionPolicy, trimmed
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import (
    AmendRefused,
    CancelRefused,
    ElectionMade,
    ExerciseRefused,
    FillEvent,
    HoldersRecorded,
    Intent,
    IntentKind,
    ObservationReceived,
    OrderEvent,
    SliceClosed,
    StrategyInboundEvent,
    TermsAnnounced,
    TimerEvent,
    UnitsElected,
)
from alphalab.strategy.protocol import defines_on_observation, defines_on_slice
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.state import StrategyStatus
from alphalab.strategy.subscription import Topic, market_topic

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

    #: The order is the parent an execution algorithm works in children, in a
    #: historical run (v4.1.1, ledger FUT-009). It is accepted and left working,
    #: holding its reservation and its contributions, and the pipeline never
    #: simulates it whole: each child is simulated by
    #: :meth:`ExecutionPipeline.work_child` against the run's own fill policy,
    #: simulator and cost model, and its fill settles on the parent. Terms an
    #: algorithm cannot work -- a stop, an auction, an immediate order -- are
    #: refused before an order exists. Backtest and replay only; see
    #: :mod:`alphalab.backtesting.algorithmic`.
    ALGORITHMIC = auto()


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
        currency: The **reporting settlement currency** of this pipeline --
            what starting cash is funded in, what risk and NAV are expressed in,
            and what a fill settles in unless the registry names another this
            pipeline also settles. It must equal ``account.base_currency``;
            :meth:`ExecutionPipeline.initialize` refuses a config where the two
            disagree. Left empty it **is** ``account.base_currency``: until
            v3.10 it defaulted to ``"USD"`` whatever the account said, so a
            euro account's config was refused unless it repeated the currency
            (ledger API-003).
        also_settles: Other currencies a fill may settle in. **Empty by
            default**, which is the single-currency pipeline every run had
            before v2.17 and is byte-identical to it: one permitted currency,
            the same two refusals, the same fast path.

            Naming a currency here does three things and no more. It lets
            :func:`_settlement_refusal` pass a request for an instrument that
            trades in it (ADR-0028 seam 1), lets
            :func:`_require_settlement_currency` book a venue fill denominated
            in it (seam 2), and makes the resulting book genuinely mixed -- so
            every valuation of it needs an
            :class:`~alphalab.portfolio.fx.FxRates` covering the pair, and
            refuses without one. It does **not** convert anything by itself, and
            it is not a licence to guess a rate: ADR-0035 keeps settlement in the
            currency traded and reporting in ``currency``, joined only by a rate
            a caller supplied.

            It is a ``frozenset`` because it is a membership test on the hot
            path and because a list would let the same currency be named twice.
        routing: Where an accepted order executes. Defaults to ``SIMULATED``,
            which is what every environment before v2.3 did.
        instruments: The registry the run's market data was resolved against, or
            ``None``. **Read-only, and read for declared facts only**: when a
            request is dropped for want of a price, whether the ``asset_id``
            names a registered instrument at all; since v2.11, the sector a
            fill's asset is classified as, which is frozen onto that fill's
            :class:`~alphalab.analytics.attribution.TradeRecord` (ADR-0027);
            since v2.12, the currency it trades in (ADR-0028); and since v3.11,
            the lot grid and minimum notional its
            :class:`~alphalab.instrument.economics.InstrumentEconomics` declare,
            which a target intent is rounded to and refused below (FEA-001).
            Every one is
            :meth:`~alphalab.instrument.registry.InstrumentRegistry.record_for`,
            which remains the only method ever called on it. The pipeline never
            resolves a provider symbol, never derives an ``asset_id``, never
            registers anything and never classifies anything: ADR-0016 gives
            resolution to the wire boundary and classification to the operator,
            and this takes neither back. Leaving it ``None`` is fully supported
            and changes nothing except how precisely an unpriced asset can be
            described and whether a run can report P&L by sector.
        fill_timing: When a simulated order fills: at the event that decided it
            (``SAME_EVENT``, optimistic, and what every run did before v3.10) or
            at its asset's next event (``NEXT_EVENT``). See
            :class:`~alphalab.execution.policy.FillTiming`. Ignored under
            ``EXTERNAL`` routing, where nothing is simulated. Recorded with the
            run, its snapshot and its results.
        calendars: The trading calendar of each listing venue, read for one
            question: when a simulated resting **day** order that states no
            ``expire_at`` expires. It is given the last close of its trading
            day by the calendar of its instrument's listing venue -- read
            through ``instruments`` -- or by ``calendars.default``, and is
            refused, naming the venue, when neither is declared. Empty by
            default, which refuses every such order, as v3.11 did. Ignored under
            ``EXTERNAL`` routing, where the venue works the order. Carried by
            the pipeline snapshot. See :mod:`alphalab.runtime.calendars`
            (ledger EXE-010).
        retention: How much of each derived history the run keeps -- market
            events (and a strategy's history window), per-record steps, audit
            logs and results -- applied between records by
            :meth:`~alphalab.runtime.run.RunEngine.advance`. Keeps everything by
            default, as every run before v3.12 did. Never touches positions,
            cash, orders or anything else a step computes from. Carried by the
            pipeline snapshot. See :mod:`alphalab.runtime.retention` (ledger
            PRF-004).
    """

    account: Account
    starting_cash: Decimal
    budget: CapitalBudget
    allocation_constraints: AllocationConstraints
    risk_limits: RiskLimits
    sizing_model: SizingModel = field(default_factory=FixedQuantitySizing)
    simulator: ExecutionSimulator = field(default_factory=ExecutionSimulator)
    venue: str = "SIM"
    currency: str = ""
    also_settles: frozenset[str] = frozenset()
    routing: ExecutionRouting = ExecutionRouting.SIMULATED
    instruments: InstrumentRegistry | None = None
    fill_timing: FillTiming = FillTiming.SAME_EVENT
    calendars: VenueCalendars = field(default_factory=VenueCalendars)
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)

    def __post_init__(self) -> None:
        if not self.currency:
            # The account names the currency; the config does not name a second.
            object.__setattr__(self, "currency", self.account.base_currency)

    @property
    def settlement_currencies(self) -> frozenset[str]:
        """Every currency this pipeline may settle a fill in.

        Always contains :attr:`currency`; a pipeline that could not settle its
        own reporting currency could not fund its own cash.
        """

        return frozenset({self.currency, *self.also_settles})

    @property
    def is_multi_currency(self) -> bool:
        """Whether this pipeline may produce a book holding two currencies."""

        return len(self.settlement_currencies) > 1


#: The empty budget-price map a single-currency pipeline passes. A module-level
#: constant so the per-event path allocates nothing for it.
_NO_BUDGET_PRICES: Mapping[str, Decimal] = MappingProxyType({})


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
            An event whose intents for the asset could not be sized into a
            request at all -- a sizing model that needs a price refuses one --
            counts once, as the netted request it would have been.
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
class ChildTurn:
    """What one execution-algorithm child did at one market event (v4.1.1, ledger FUT-009).

    Attributes:
        state: The pipeline after the child's fill, if any, settled on its parent.
        reports: The execution reports the fill produced -- each against the
            **parent** order, as a venue fill of a routed child is.
        fills: The canonical fills.
        trades: The canonical trades.
        filled: What the child filled at this event.
        outcome: ``None`` while the child works on; ``FILLED`` once it has filled
            in full; ``CANCELLED`` for a market child's unfilled remainder, which
            is withdrawn after its one attempt, as a market order's is;
            ``REJECTED`` or ``EXPIRED`` when the fill policy decided so.
    """

    state: ExecutionPipelineState
    reports: tuple[ExecutionReport, ...]
    fills: tuple[CoreFill, ...]
    trades: tuple[CoreTrade, ...]
    filled: Decimal
    outcome: OrderStatus | None


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


def retained(state: ExecutionPipelineState) -> ExecutionPipelineState:
    """``state`` with each derived history held to the configured retention (PRF-004).

    Trims only the logs :mod:`alphalab.runtime.retention` names, only once one
    has grown past its bound by the slack, and never anything a step computes
    from; with nothing bounded, or nothing grown that far, ``state`` itself is
    returned. Called between records, never during one.
    """

    policy = state.config.retention
    if policy.keeps_everything:
        return state
    changes: dict[str, object] = {}

    market = state.market
    history = trimmed(market.history, policy.market_history)
    events = trimmed(market.events, policy.market_history)
    if history is not market.history or events is not market.events:
        changes["market"] = evolve(market, history=history, events=events)

    audit = policy.audit_events
    for name in ("allocation", "risk", "oms", "execution"):
        part = getattr(state, name)
        history = trimmed(part.history, audit)
        events = trimmed(part.events, audit)
        if history is not part.history or events is not part.events:
            changes[name] = evolve(part, history=history, events=events)

    portfolio = state.portfolio
    portfolio_events = trimmed(portfolio.events, audit)
    transactions = trimmed(portfolio.ledger.transactions, _ledger_bound(portfolio, policy.results))
    if (
        portfolio_events is not portfolio.events
        or transactions is not portfolio.ledger.transactions
    ):
        changes["portfolio"] = evolve(
            portfolio,
            events=portfolio_events,
            ledger=evolve(portfolio.ledger, transactions=transactions),
        )

    for name in ("fills", "trades", "trade_records", "portfolio_snapshots"):
        log = getattr(state, name)
        kept = trimmed(log, policy.results)
        if kept is not log:
            changes[name] = kept
    return evolve(state, **changes) if changes else state


def _ledger_bound(portfolio: PortfolioState, bound: int | None) -> int | None:
    """The ledger's bound, widened so nothing a restore rebuilds from is trimmed.

    A restore rebuilds the fills not yet settled (FUT-015) from the ledger they
    travel in, and a period's posting sums the accruals it has not closed
    (FUT-023): trimming one before it settles or posts would lose an obligation
    (ACC-015). The bound grows to keep the oldest such transaction and
    everything after it -- a settlement cycle's fills, a period's days -- and
    only while one is open.
    """

    if bound is None:
        return bound
    transactions = portfolio.ledger.transactions
    pending = {entry.transaction_id for entry in portfolio.unsettled}
    closed: set[object] = set()
    needed = 0
    for depth, transaction in enumerate(reversed(transactions.to_tuple()), start=1):
        if transaction.transaction_id in pending:
            needed = depth
        elif transaction.type is TransactionType.ACCRUAL:
            key = transaction.metadata.get(ACCRUES)
            if transaction.metadata.get(ACCRUAL_CLOSED):
                closed.add(key)
            elif key not in closed:
                needed = depth
    return max(bound, needed)


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
    #: The run's queue book after the step, when one was given (v4.1.1, ledger
    #: FUT-011): each resting limit order's place at its price level. ``None``
    #: for a step taken without one.
    queue: QueueBook | None = None


#: No instrument has reopened by auction.
_NO_REOPENINGS: Final[Mapping[str, AuctionReopening]] = MappingProxyType({})


def _populate_context(
    context_factory: ContextFactory,
    *,
    portfolio: PortfolioState,
    risk: RiskState,
    market: MarketState,
    market_prices: Mapping[str, Decimal],
    shares: Mapping[str, tuple[OrderShare, ...]],
    instruments: InstrumentRegistry | None,
    as_of: float,
    window: int | None,
    reopened: Mapping[str, AuctionReopening] = _NO_REOPENINGS,
) -> ContextFactory:
    """Wrap a caller's factory so the pipeline owns what only it can know.

    The caller's :data:`ContextFactory` signature is unchanged and every
    existing factory keeps working: this calls it, then overlays the six fields
    the pipeline is authoritative for. ``clock``, ``logger`` and ``config`` are
    passed through exactly as supplied -- which is what keeps
    :mod:`alphalab.reinforcement_learning`'s ``_PendingDecision`` channel, which
    rides on ``config``, working untouched.

    ``history`` and ``universe`` joined the overlay in v2.15, completing the
    boundary ADR-0026 deferred them from. Both are references to state already in
    scope -- the market engine's event log and the config's registry -- so
    neither changes what this function costs.

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
    history_view = HistoryView(market, as_of, window)
    universe_view = UniverseView(instruments, as_of, reopened)

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
        return evolve(
            supplied,
            portfolio=portfolio_view,
            orders=OrderView(shares.get(strategy_id, ())),
            risk_view=risk_view,
            market=market_view,
            history=history_view,
            universe=universe_view,
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


def _budget_prices(
    state: ExecutionPipelineState,
    market_prices: Mapping[str, Decimal],
    rates: FxRates,
    as_of: float,
) -> Mapping[str, Decimal]:
    """Every observed price, expressed in the capital budget's currency.

    The seam that keeps ``alphalab.allocation`` free of exchange rates. That
    package sizes and nets in arithmetic and has never imported
    ``alphalab.portfolio``; giving it an ``FxRates`` would have ended that --
    measured, it pulled all eighteen portfolio modules into a package that
    previously imported none of them. So the conversion happens here, where the
    registry that names an instrument's currency and the rates that price it
    both already are, and allocation receives numbers.

    **A single-currency pipeline pays nothing.** It returns the empty mapping
    without reading a price, and ``AllocationEngine.allocate`` then falls back to
    ``market_prices`` exactly as it always has -- so the per-event allocation
    path is byte-for-byte what it was.

    A pair no rate covers raises rather than being summed anyway: an order whose
    cost against the budget nobody can state is not an order this pipeline will
    size.

    Raises:
        MissingRateError: If an asset trades in a currency the table cannot
            convert into the budget's.
        StaleRateError: If the only rate for such a pair is too old.
    """

    budget_currency = state.config.budget.currency
    registry = state.config.instruments
    if not state.config.is_multi_currency or registry is None or not budget_currency:
        return _NO_BUDGET_PRICES

    permitted = state.config.settlement_currencies
    converted: dict[str, Decimal] = {}
    for asset_id, price in market_prices.items():
        currency = _currency_of(registry, asset_id)
        if currency is None or currency == budget_currency:
            continue
        if currency not in permitted:
            # An instrument this pipeline cannot settle. Its request is dropped
            # by _settlement_refusal before an order exists, so pricing it
            # against the budget is work nobody uses -- and would demand a rate
            # for a pair the run has no reason to hold, turning a settlement
            # refusal into a MissingRateError that names the wrong problem.
            continue
        converted[asset_id] = rates.convert(price, currency, budget_currency, as_of).converted
    return converted


def _require_classifiable(config: ExecutionPipelineConfig) -> None:
    """Refuse classification limits a run could never apply (ledger OFE-001).

    A bucket is read from the instrument registry. A run that declares a limit
    on one and no registry would leave every instrument unclassified, and the
    limit would bind nothing -- silently. Refused here instead, where the
    configuration is judged, and on restore, which replays this.
    """

    limited = config.risk_limits.classification
    if limited and config.instruments is None:
        dimensions = sorted({limit.dimension for limit in limited})
        raise RuntimeValidationError(
            f"The risk limits bound {', '.join(dimensions)} buckets, and the run declares no "
            "instrument registry to read an instrument's classification from; configure one "
            "with ExecutionPipelineConfig.instruments."
        )


def _require_settleable_budget(config: ExecutionPipelineConfig) -> None:
    """Refuse a capital budget this pipeline cannot price.

    The third of the four blockers ADR-0033 decision 13 named: "allocation sizes
    against a capital budget in one currency". Two rules, and the asymmetry
    between them is the point.

    A **single-currency** pipeline accepts a budget that names no currency,
    because there is exactly one currency in play and the budget is therefore in
    it by determination rather than by assumption. Requiring the string there
    would break every existing caller to state something already known.

    A **multi-currency** pipeline refuses one. Its sizing compares a notional
    computed from an instrument's own price against the budget, and with two
    settlement currencies in play that comparison is meaningless unless the
    budget says which one it is in. An unstated budget is not "probably the
    reporting currency" -- it is a figure nobody can price, and sizing against
    it would produce orders whose size nothing could justify.

    Either way, a budget naming a currency the pipeline does not settle is
    refused. That catches a transposed configuration -- a EUR budget handed to a
    USD pipeline -- which a default would have silently accepted.

    Raises:
        RuntimeValidationError: On either failure. Deliberately the same class
            :func:`_require_one_account_currency` raises: one category of fault
            -- a call into the pipeline whose currencies do not agree -- gets one
            name.
    """

    budget = config.budget
    permitted = config.settlement_currencies

    if budget.states_currency:
        if budget.currency not in permitted:
            settles = ", ".join(repr(each) for each in sorted(permitted))
            raise RuntimeValidationError(
                f"CapitalBudget.currency is {budget.currency!r} and this pipeline "
                f"settles in {settles}. Allocation would size every order against a "
                "capital figure in a currency no fill can ever be denominated in, so "
                "no order it produced could be checked against it. State the budget "
                "in a currency this pipeline settles, or add "
                f"{budget.currency!r} to ExecutionPipelineConfig.also_settles."
            )
        return

    if config.is_multi_currency:
        settles = ", ".join(repr(each) for each in sorted(permitted))
        raise RuntimeValidationError(
            f"This pipeline settles in {settles} and its CapitalBudget names no "
            "currency. With one settlement currency a budget's currency is "
            "determined and need not be stated; with two it is not, and sizing an "
            "order against a capital figure in no currency is the defect ADR-0020 "
            "removed from valuation. Set CapitalBudget.currency -- "
            "budget.in_currency(...) relabels an existing one without converting it."
        )


class ExecutionPipeline:
    """Pure functional facade for the real AlphaLab execution path.

    Every entry point runs in
    :data:`~alphalab.common.arithmetic.ACCOUNTING_CONTEXT`, whatever the calling
    thread's decimal context (ledger ACC-004), and so does everything it calls
    -- the strategies it dispatches to included, so a run is a function of its
    inputs and never of the thread it is played on. A strategy that wants other
    arithmetic says so with its own ``decimal.localcontext``. Until v3.11 each
    engine was pinned but the pipeline's own arithmetic was not: a sale's signed
    quantity was negated in the caller's context, which rounds a ``Decimal``, so
    under a precision of five a sale of 185.295944 was booked as 185.30.
    """

    @staticmethod
    @in_accounting_context
    def initialize(
        config: ExecutionPipelineConfig,
        strategy_state: StrategyRuntimeState,
        timestamp: float,
    ) -> ExecutionPipelineState:
        """Create a fresh composite pipeline state.

        Raises:
            RuntimeValidationError: If ``config.currency`` and
                ``config.account.base_currency`` disagree, or the risk limits
                bound classification buckets with no instrument registry to
                read them from. The checks run before the portfolio exists, so
                nothing is funded against a refused configuration.
        """

        _require_one_account_currency(config)
        _require_settleable_budget(config)
        _require_classifiable(config)

        portfolio = _grouped_book(PortfolioState(account=config.account), config)
        portfolio = PortfolioEngine.apply_deposit(
            portfolio, config.starting_cash, config.currency, timestamp
        )
        # No rates here, deliberately: a freshly funded book holds one currency
        # -- the deposit above is in ``config.currency`` and there are no
        # positions -- so nothing is convertible and nothing needs converting.
        risk = _sync_risk_from_portfolio(
            RiskEngine.reset(config.risk_limits), portfolio, config.instruments, as_of=timestamp
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
    @in_accounting_context
    def fund(
        state: ExecutionPipelineState,
        amount: Decimal,
        currency: str,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> ExecutionPipelineState:
        """Deposit cash in a currency this pipeline settles, and resync risk.

        :meth:`initialize` funds ``config.currency`` with ``starting_cash`` and
        nothing else, which is complete for a single-currency pipeline and is
        not for one that settles two: a fill denominated in EUR debits the EUR
        balance, and a run with no EUR cash is refused by
        :class:`~alphalab.portfolio.exceptions.InsufficientFundsError`. That
        refusal is correct -- an account cannot spend money it does not hold --
        and this is how the money gets there.

        Deliberately **not** a config field. How much of each currency an account
        holds, and when, is an operational fact that changes during a run; a
        ``Mapping[str, Decimal]`` on ``ExecutionPipelineConfig`` would freeze one
        answer for the whole of it and would be wrong the first time a desk wired
        more in.

        ``rates`` is only read when the resulting book is mixed, which is when
        risk has to express two currencies as one NAV.

        Raises:
            RuntimeValidationError: If ``currency`` is not one this pipeline
                settles. Funding a currency no fill can be denominated in puts
                cash in the book that nothing can spend and every valuation must
                then convert.
        """

        permitted = state.config.settlement_currencies
        if currency not in permitted:
            settles = ", ".join(repr(each) for each in sorted(permitted))
            raise RuntimeValidationError(
                f"This pipeline settles in {settles} and cannot be funded in "
                f"{currency!r}. Cash in a currency no fill can be denominated in is "
                "capital nothing can spend, and every valuation of the book would "
                f"have to convert it. Add {currency!r} to "
                "ExecutionPipelineConfig.also_settles if this pipeline is meant to "
                "trade it."
            )

        portfolio = PortfolioEngine.apply_deposit(state.portfolio, amount, currency, timestamp)
        risk = _sync_risk_from_portfolio(
            state.risk, portfolio, state.config.instruments, rates, as_of=timestamp
        )
        return evolve(state, portfolio=portfolio, risk=risk)

    @staticmethod
    @in_accounting_context
    def convert_cash(
        state: ExecutionPipelineState,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        rates: FxRates,
        timestamp: float,
    ) -> tuple[ExecutionPipelineState, FxConversion]:
        """Fund one settlement currency out of another, at a supplied rate.

        The settlement-level counterpart to the valuation-level conversion v2.16
        added: this moves *actual cash* between balances rather than expressing
        one figure in another currency. Delegates to
        :meth:`~alphalab.portfolio.engine.PortfolioEngine.convert_cash`, which
        records the rate, its ``as_of`` and its source on a
        :class:`~alphalab.portfolio.events.CashConverted` event -- so the
        conversion stays attributable after the fact.

        **There is no implicit version of this.** No fill converts cash to cover
        itself, and no shortfall is silently financed: a run that settles a
        currency it has not funded is refused. Auto-conversion would mean
        applying a rate nobody asked for to money that already moved, which is
        exactly the "invented figure that looks authoritative" ADR-0020 refuses.

        Raises:
            RuntimeValidationError: If either currency is not one this pipeline
                settles.
            InvalidTransactionError: If the amount is not positive or the two
                currencies are the same.
            InsufficientFundsError: If ``from_currency`` cannot cover it.
            MissingRateError: If no rate covers the pair.
            StaleRateError: If the only rate for the pair is too old.
        """

        permitted = state.config.settlement_currencies
        unsettled = [c for c in (from_currency, to_currency) if c not in permitted]
        if unsettled:
            settles = ", ".join(repr(each) for each in sorted(permitted))
            raise RuntimeValidationError(
                f"This pipeline settles in {settles}, and a conversion between "
                f"{from_currency!r} and {to_currency!r} names {unsettled} which it "
                "does not. Both sides of a settlement conversion must be currencies "
                "this pipeline can hold and spend."
            )

        portfolio, conversion = PortfolioEngine.convert_cash(
            state.portfolio, amount, from_currency, to_currency, rates, timestamp
        )
        risk = _sync_risk_from_portfolio(
            state.risk, portfolio, state.config.instruments, rates, as_of=timestamp
        )
        return evolve(state, portfolio=portfolio, risk=risk), conversion

    @staticmethod
    @in_accounting_context
    def process_quote(
        state: ExecutionPipelineState,
        quote: Quote,
        context_factory: ContextFactory,
        fill_status: FillStatus = FillStatus.FULL_FILL,
        fill_quantity: Decimal | None = None,
        fill_policy: FillPolicy | None = None,
        rates: FxRates = NO_RATES,
    ) -> ExecutionPipelineResult:
        """Publish a quote and process the resulting market event."""

        market = MarketEngine.publish_quote(state.market, quote)
        event = market.events[-1]
        return ExecutionPipeline.process_market_event(
            evolve(state, market=market),
            event,
            context_factory,
            fill_status,
            fill_quantity,
            fill_policy,
            rates,
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
    @in_accounting_context
    def process_record(
        state: ExecutionPipelineState,
        record: MarketRecord,
        context_factory: ContextFactory,
        fill_policy: FillPolicy | None = None,
        rates: FxRates = NO_RATES,
        *,
        queue: QueueBook | None = None,
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
            evolve(state, market=market),
            market.events[-1],
            context_factory,
            fill_policy=fill_policy,
            rates=rates,
            queue=queue,
        )

    @staticmethod
    @in_accounting_context
    def process_market_event(
        state: ExecutionPipelineState,
        event: MarketEvent,
        context_factory: ContextFactory,
        fill_status: FillStatus = FillStatus.FULL_FILL,
        fill_quantity: Decimal | None = None,
        fill_policy: FillPolicy | None = None,
        rates: FxRates = NO_RATES,
        *,
        queue: QueueBook | None = None,
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

        ``rates`` is the FX table any conversion this step performs must use, and it
        defaults to the empty one -- so a single-currency run behaves exactly as
        it did and a multi-currency one refuses rather than guesses. It is a
        **parameter and not configuration** for the reason ADR-0033 decision 13
        gave: a quote is time-varying data, fixing one for a whole run would be
        wrong for a live session, and putting it on ``ExecutionPipelineConfig``
        would move ``PIPELINE_SNAPSHOT_SCHEMA``. It is not on
        ``ExecutionPipelineState`` either, which ADR-0030 fixes at sixteen
        fields because every one of them is paid eleven times per record.

        ``queue`` (v4.1.1, ledger FUT-011) is the run's
        :class:`~alphalab.execution.queue.QueueBook`, a parameter for the same
        reasons. With one, a simulated resting limit order working in the
        event's asset -- a limit order, or a triggered stop-limit, that is not
        immediate and not an auction order -- fills at this event only as far as
        its queue model allows, the fill policy deciding within that; an order
        the step leaves resting joins its queue at this event; and the book that
        comes back on the result holds only orders still working. Without one
        nothing changes.
        """

        refusal = price_refusal(state, _event_payload(event))
        if refusal is not None:
            raise MarketValidationError(refusal)
        update = _market_price(event)
        market_prices = _market_prices_with_event(state.market_prices, update)
        # One event moves at most one price, so the book re-marks that asset and
        # the positions a fill priced since, not every position it holds (PRF-001).
        portfolio = PortfolioEngine.mark_changed(
            state.portfolio,
            market_prices,
            event.timestamp,
            update[0] if update is not None else None,
        )
        risk = _sync_risk_from_portfolio(
            state.risk, portfolio, state.config.instruments, rates, as_of=event.timestamp
        )
        policy: FillPolicy = (
            fill_policy if fill_policy is not None else StaticFill(fill_status, fill_quantity)
        )
        current = evolve(state, market_prices=market_prices, portfolio=portfolio, risk=risk)

        # What the step starts from, kept for the feedback it delivers at its end:
        # who asked for each order (the ledger retires an order's entry when it
        # goes terminal) and where this step's trade records begin. Both are
        # persistent values, so keeping them costs a reference.
        contributions_before = current.allocation.contributions
        records_before = len(current.trade_records)

        # The orders an earlier event left working in this asset are worked now,
        # at this event's price, and before the strategy is dispatched -- so it
        # decides on a book that includes what they did (EXE-001). Under
        # NEXT_EVENT every simulated order is one; under SAME_EVENT only an order
        # that rests: one a strategy placed from a fill or an order event
        # (EXE-005), which fills at its asset's next event.
        earlier = _NO_ROUTING
        if (
            update is not None
            and state.config.routing is ExecutionRouting.SIMULATED
            and (current.oms.working_orders_for(update[0]) or _auctions_declared(current))
        ):
            # With auctions declared, every first event after one is recorded,
            # orders working or not (FUT-033).
            current, earlier, queue = _fill_working_orders(
                current, event, update[0], policy, rates, queue
            )
        # Every order still working in the asset now has had its turn; what the
        # step places from here on is appended after them (FUT-011).
        resting_before = (
            _working_count(current, update[0]) if queue is not None and update is not None else 0
        )

        # Assembled from the marked state above, after marking, after the risk
        # resync and after any working order filled, and before dispatch. The
        # order is the guarantee: reading ``state.portfolio`` here would show the
        # strategy a book marked at the previous event's prices while risk
        # evaluated its order against these.
        populated = _populate_context(
            context_factory,
            portfolio=current.portfolio,
            risk=current.risk,
            market=state.market,
            market_prices=market_prices,
            shares=order_shares_by_strategy(current.oms, current.allocation),
            instruments=state.config.instruments,
            reopened=current.execution.reopened,
            window=state.config.retention.market_history,
            # The look-ahead bound: this event's own timestamp, never a wall
            # clock. A strategy sees everything up to and including the event it
            # is being dispatched, and nothing after it.
            as_of=event.timestamp,
        )
        strategy, intents = StrategyEngine.process_event(
            state.strategy, event, populated, event.timestamp
        )
        current, ordered, amended = _instructions_requested(
            evolve(current, strategy=strategy),
            intents,
            event.timestamp,
            event=event,
            policy=policy,
            rates=rates,
        )
        allocation, requests = _allocate(current, ordered, market_prices, rates, event.timestamp)
        current = evolve(current, allocation=allocation)
        current, routed = _route_requests(current, event, ordered, requests, policy, rates)
        routed = earlier.then(amended).then(routed)

        # The step's feedback: every fill, and what became of every order and
        # request, to the strategies that asked for them (EXE-005).
        current, feedback_intents, feedback_requests, feedback = _deliver_feedback(
            current,
            event,
            context_factory,
            market_prices,
            rates,
            policy,
            routed=routed,
            requests=requests,
            contributions_before=contributions_before,
            records_before=records_before,
        )
        # A strategy's exercise instructions take effect at the end of the step
        # that asked for them, at its instant (FUT-014).
        current = _exercises_requested(
            current, (*intents, *feedback_intents), event.timestamp, rates
        )
        # A holder's elections are recorded for their actions (FUT-024).
        current = _elections_requested(current, (*intents, *feedback_intents), event.timestamp)
        # A recalled short past its buy-in deadline is covered (FUT-022), and
        # then the account's margin, after everything the step did (FUT-017).
        current, bought_in = _buy_ins(current, event, rates)
        current, liquidation = _margin_call(current, event, rates)
        routed = routed.then(bought_in).then(liquidation)
        if queue is not None:
            queue = _queue_after_step(queue, current, event, update, resting_before)
        return _step_result(
            current,
            event,
            (*intents, *feedback_intents),
            (*requests, *feedback_requests),
            routed.then(feedback),
            rates,
            queue,
        )

    @staticmethod
    @in_accounting_context
    def apply_execution_report(
        state: ExecutionPipelineState,
        order: OMSOrder,
        report: ExecutionReport,
        rates: FxRates = NO_RATES,
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
            _record_venue_execution(state, order, report), order, (report,), rates
        )
        return evolve(applied, id_position=current_id_position()), fills, trades

    @staticmethod
    @in_accounting_context
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

        terminated = evolve(
            state, oms=_terminate_order(state.oms, order, outcome, reason, timestamp)
        )
        released = _release_if_terminal(terminated, order.order_id, timestamp)
        return evolve(released, id_position=current_id_position())

    @staticmethod
    @in_accounting_context
    def work_child(
        state: ExecutionPipelineState,
        child: ChildOrder,
        working: Decimal,
        event: MarketEvent,
        policy: FillPolicy,
        rates: FxRates = NO_RATES,
        *,
        arriving: bool,
    ) -> ChildTurn:
        """One execution-algorithm child's turn at ``event``, its fill settled on the parent.

        The historical counterpart of routing a child to a venue (v4.1.1, ledger
        FUT-009). The parent is the OMS order the algorithm works under
        :attr:`ExecutionRouting.ALGORITHMIC`; the child is not an order of its
        own. The **fill policy** decides what the child's ``working`` quantity
        gets from the liquidity ``event`` showed -- so a liquidity cap applies to
        each child -- and the **simulator** prices it by the run's cost model.
        The fill is then applied to the parent by the path every simulated and
        venue fill takes: the OMS records a partial fill (or the fill that
        completes the parent), the portfolio books it, the allocation
        reservation is consumed, and the trade record carries the parent's
        contributions, so attribution splits by the strategies that asked.

        The child's terms are the order's: a ``MARKET`` child has one attempt at
        the event's price and what it does not fill is withdrawn (``CANCELLED``);
        a ``LIMIT`` child meets the market as a taker on its first turn
        (``arriving``) when the cost model prices taking it within its limit,
        and otherwise rests and fills at its limit, as a maker, when the market
        trades through it -- the rules :func:`_work_order` applies to an order.
        Nothing here reads a later event: ``event`` is the one the child is
        worked at, and the caller decides which events a child may see.

        Raises:
            RuntimeValidationError: If the run does not route algorithmically;
                ``event`` is not of the child's asset or carries no price; the
                parent is unknown, finished, or not the child's; or ``working``
                is not positive or exceeds what the parent has left unfilled --
                the over-execution the parent's quantity forbids.
        """

        if state.config.routing is not ExecutionRouting.ALGORITHMIC:
            raise RuntimeValidationError(
                f"Children are worked by the pipeline only under ALGORITHMIC routing; this run "
                f"routes {state.config.routing.name}."
            )
        priced = _market_price(event)
        if priced is None or priced[0] != child.asset_id:
            raise RuntimeValidationError(
                f"Child {child.child_id} trades {child.asset_id}; the event prices "
                f"{'nothing' if priced is None else priced[0]}."
            )
        parent_id = OrderId(UUID(child.parent_order_id))
        if not state.oms.orders.contains(parent_id):
            raise RuntimeValidationError(f"Child {child.child_id} names an unknown parent.")
        parent = state.oms.orders.find(parent_id)
        if not parent.is_open or parent.asset_id != child.asset_id or parent.side != child.side:
            raise RuntimeValidationError(
                f"Child {child.child_id} cannot work parent {child.parent_order_id}: it is "
                f"{parent.status} on {parent.asset_id} {parent.side}."
            )
        if working <= Decimal("0") or working > parent.remaining_quantity:
            raise RuntimeValidationError(
                f"Child {child.child_id} would work {working} with parent "
                f"{child.parent_order_id} {parent.remaining_quantity} short of filled."
            )

        # The child as the policy and the cost model see it: the parent's
        # identity, side and asset, the child's quantity, order type and limit.
        view = replace(
            parent,
            order_type=child.order_type,
            limit_price=child.limit_price,
            remaining_quantity=working,
            time_in_force=TimeInForce.GTC,
        )
        market = priced[1]
        price: Decimal | None = market
        passive = False
        if child.order_type is OrderType.LIMIT:
            if arriving and _within_limit(view, _taker_price(state, view, event, market)):
                price = market
            else:
                price = _resting_fill_price(view, event)
                passive = True
        if price is None:
            return ChildTurn(state, (), (), (), Decimal("0"), None)

        if _venue_blocks(state, parent.asset_id, event.timestamp, price):
            # Halted or outside the band (FUT-020): the child does not trade here,
            # and is withdrawn as an unfilled market child is, its limit child
            # resting on; the parent's schedule continues.
            unworked = OrderStatus.CANCELLED if child.order_type is OrderType.MARKET else None
            return ChildTurn(state, (), (), (), Decimal("0"), unworked)
        decision = _decide_fill(policy, view, event, price, _whole_units(state))
        if decision.status is FillStatus.REJECTED:
            return ChildTurn(state, (), (), (), Decimal("0"), OrderStatus.REJECTED)
        if decision.status is FillStatus.EXPIRED:
            return ChildTurn(state, (), (), (), Decimal("0"), OrderStatus.EXPIRED)
        unfilled = OrderStatus.CANCELLED if child.order_type is OrderType.MARKET else None
        if decision.status is FillStatus.NO_FILL:
            return ChildTurn(state, (), (), (), Decimal("0"), unfilled)

        quantity = working if decision.quantity is None else min(decision.quantity, working)
        # Status against the parent, as a venue fill's is: a fill completes the
        # parent only when it leaves nothing of the parent unfilled.
        status = (
            FillStatus.FULL_FILL
            if quantity >= parent.remaining_quantity
            else FillStatus.PARTIAL_FILL
        )
        current, reports = _execute_order(
            state, parent, FillDecision(status, quantity), event, price=price, passive=passive
        )
        current, fills, trades = _apply_reports(current, parent, reports, rates)
        outcome = OrderStatus.FILLED if quantity == working else unfilled
        return ChildTurn(
            evolve(current, id_position=current_id_position()),
            reports,
            fills,
            trades,
            quantity,
            outcome,
        )

    @staticmethod
    @in_accounting_context
    def process_timer(
        state: ExecutionPipelineState,
        timer: TimerEvent,
        context_factory: ContextFactory,
        rates: FxRates = NO_RATES,
    ) -> tuple[ExecutionPipelineState, tuple[Intent, ...], tuple[OMSOrder, ...]]:
        """Deliver a timer to the strategies subscribed to ``timers``, and place what they ask for.

        Until v3.11 the dispatcher routed :class:`~alphalab.strategy.events.TimerEvent`
        to ``on_timer`` and nothing on the execution path delivered one (ledger
        EXE-005). Who decides *when* a timer fires is the driver's business --
        :meth:`~alphalab.runtime.run.RunEngine.fire_timer` -- as it is for a
        record. A timer is an instant, not a market observation: the strategies
        see the book as it stands, and their orders **rest** until each asset's
        next event, exactly as a strategy stopped between events has its
        shutdown orders rest. What becomes of those orders is fed back to
        subscribers as for any step.

        Returns:
            The state, the intents the timer produced, and the orders placed.

        Raises:
            RuntimeValidationError: If the timer is before the last event the
                pipeline processed.
        """

        last = state.market.events[-1] if len(state.market.events) else None
        if last is not None and timer.timestamp < last.timestamp:
            raise RuntimeValidationError(
                f"A timer at {timer.timestamp!r} fires before the last event this pipeline "
                f"processed, at {last.timestamp!r}."
            )
        return _deliver_instant(state, timer, last, context_factory, rates)

    @staticmethod
    @in_accounting_context
    def process_observation(
        state: ExecutionPipelineState,
        event: ObservationReceived,
        context_factory: ContextFactory,
        rates: FxRates = NO_RATES,
    ) -> tuple[ExecutionPipelineState, tuple[Intent, ...], tuple[OMSOrder, ...]]:
        """Deliver a record that has become knowable, and place what it asks for (OFE-009).

        Strategies subscribed to ``observations`` -- or to the record's subject
        -- that define ``on_observation`` receive it, seeing the book as it
        stands at ``event.timestamp``. Like a timer it is an instant, not a
        price: what they ask for **rests** until each asset's next event, so an
        order decided on information known at ``t`` never fills at a price
        printed before ``t``. Which records are due, and in what order, is the
        driver's business (:meth:`~alphalab.runtime.run.RunEngine.deliver_observation`).

        Nothing happens -- the same state is returned -- when no running
        strategy both accepts the observation and defines the hook
        (:func:`wants_observation`).

        Returns:
            The state, the intents the observation produced, and the orders
            placed.

        Raises:
            RuntimeValidationError: If the observation is delivered before the
                last event the pipeline processed.
        """

        last = state.market.events[-1] if len(state.market.events) else None
        if last is not None and event.timestamp < last.timestamp:
            raise RuntimeValidationError(
                f"An observation known at {event.timestamp!r} is delivered after the last event "
                f"this pipeline processed, at {last.timestamp!r}: acting on it now would act on "
                "information as of an instant the market has already moved past."
            )
        if not wants_observation(state.strategy, event.subject):
            return state, (), ()
        return _deliver_instant(state, event, last, context_factory, rates)

    @staticmethod
    @in_accounting_context
    def close_slice(
        state: ExecutionPipelineState,
        context_factory: ContextFactory,
        rates: FxRates = NO_RATES,
    ) -> tuple[ExecutionPipelineState, tuple[Intent, ...], tuple[OMSOrder, ...]]:
        """Deliver the instant of the last event, complete, to ``slices`` subscribers.

        A strategy trading across instruments is dispatched once per record, so
        at any one record it sees an instant part-way through. The driver calls
        this once every record of an instant has been published -- a backtest
        when the next instant's first record arrives and when its data ends,
        :meth:`~alphalab.runtime.run.RunEngine.close_slice` for the rule -- and
        each running strategy subscribed to ``slices`` that defines ``on_slice``
        receives a :class:`~alphalab.strategy.events.SliceClosed` naming every
        asset the instant was about, with the book and every price as they stand
        (ledger EXE-004).

        What the strategies ask for **rests** until each asset's next event, as
        a timer's orders do: the instant's own events have been worked, and an
        order placed after them cannot have traded on them. A market order fills
        at its asset's next event's price; ``OrderTerms.at_open()`` takes the next
        daily bar's open instead.

        Nothing happens -- the same state is returned -- when no event has been
        published or when no running strategy both subscribes to slices and
        defines ``on_slice`` (:func:`wants_slices`). Which instants have been
        closed is the run's to remember, as its record cursor is:
        :meth:`~alphalab.runtime.run.RunEngine.close_slice` closes each once.

        Returns:
            The state, the intents the slice produced (feedback included), and
            the orders placed.
        """

        events = state.market.events
        if not len(events) or not wants_slices(state.strategy):
            return state, (), ()
        last = events[-1]
        at = last.timestamp
        # Derived, not drawn from the run's identifier stream: closing a slice
        # must not move every identifier minted after it.
        event = SliceClosed(f"SLICE-{at!r}", at, _instant_assets(events, at))
        return _deliver_instant(state, event, last, context_factory, rates)

    @staticmethod
    @in_accounting_context
    def stop_strategies(
        state: ExecutionPipelineState,
        context_factory: ContextFactory,
        timestamp: float,
        rates: FxRates = NO_RATES,
        strategy_ids: Iterable[str] | None = None,
    ) -> tuple[ExecutionPipelineState, tuple[Intent, ...], tuple[OMSOrder, ...]]:
        """Stop strategies: ``on_shutdown``, then ``on_stop``, then ``STOPPED`` (ledger EXE-005).

        ``strategy_ids`` names which; ``None`` stops every running or paused
        strategy. Each sees the book as it stands, as of ``timestamp`` -- the
        instant it is stopped, which is not before the last event -- and what
        its ``on_shutdown`` asks for goes through allocation, risk and the OMS
        like any intent. The orders **rest**: a simulated one fills at its
        asset's next event, and a live run routes it
        (:meth:`~alphalab.runtime.live.LiveSession.stop`). A backtest whose data
        has ended has no next event, so its shutdown orders are left working,
        and :attr:`~alphalab.runtime.run.RunState.working_orders` says so: a
        position is flattened at a price the run observes, never at one it
        invents.

        Returns:
            The state, the shutdown intents and the orders they placed.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event
                the pipeline processed: a strategy cannot be stopped in the past.
        """

        last = state.market.events[-1] if len(state.market.events) else None
        if last is not None and timestamp < last.timestamp:
            raise RuntimeValidationError(
                f"Strategies stopped at {timestamp!r} would be stopped before the last event "
                f"this pipeline processed, at {last.timestamp!r}."
            )
        populated = _populate_context(
            context_factory,
            portfolio=state.portfolio,
            risk=state.risk,
            market=state.market,
            market_prices=state.market_prices,
            shares=order_shares_by_strategy(state.oms, state.allocation),
            instruments=state.config.instruments,
            reopened=state.execution.reopened,
            window=state.config.retention.market_history,
            as_of=timestamp,
        )
        strategy, intents = StrategyEngine.stop(state.strategy, populated, timestamp, strategy_ids)
        current = _exercises_requested(evolve(state, strategy=strategy), intents, timestamp, rates)
        current, ordered, amended = _instructions_requested(
            current,
            intents,
            timestamp,
            event=last,
            policy=StaticFill(FillStatus.FULL_FILL, None),
            rates=rates,
            rest=True,
        )
        if not ordered or last is None:
            # With no event processed there is no price to size or judge an
            # order by; the intents are reported and nothing is placed.
            return evolve(current, id_position=current_id_position()), intents, amended.orders

        allocation, requests = _allocate(current, ordered, current.market_prices, rates, timestamp)
        current = evolve(current, allocation=allocation)
        current, routed = _route_requests(
            current,
            last,
            ordered,
            requests,
            StaticFill(FillStatus.FULL_FILL, None),
            rates,
            rest=True,
            at=timestamp,
        )
        return (
            evolve(current, id_position=current_id_position()),
            intents,
            (*amended.orders, *routed.orders),
        )

    @staticmethod
    @in_accounting_context
    def apply_cash_flow(
        state: ExecutionPipelineState,
        flow: CashFlow,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> ExecutionPipelineState:
        """Book a dividend, interest, a fee or a funding payment (ledger ACC-006).

        The portfolio books it --
        :meth:`~alphalab.portfolio.engine.PortfolioEngine.apply_cash_flow`: cash
        and realized P&L move by the signed amount -- the risk state is resynced
        from the book, and an equity point is recorded, so the equity curve
        shows the payment at the instant it was made. Which cash flows a run
        receives, and when, is the driver's to say: they are reference data, not
        market data.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event
                the pipeline processed, or the flow is in a currency this
                pipeline does not settle -- booking it would make the book one
                no valuation of it can express (ADR-0028).
        """

        _require_not_before_last_event(state, timestamp, "A cash flow")
        if flow.currency not in state.config.settlement_currencies:
            raise RuntimeValidationError(
                f"A {flow.kind.value} in {flow.currency!r} cannot be booked by a pipeline that "
                f"settles in {sorted(state.config.settlement_currencies)}; add it to "
                "ExecutionPipelineConfig.also_settles to hold it."
            )
        portfolio = PortfolioEngine.apply_cash_flow(state.portfolio, flow, timestamp)
        return _after_book_change(state, portfolio, timestamp, rates)

    @staticmethod
    @in_accounting_context
    def record_terms(
        state: ExecutionPipelineState, asset_id: str, action: str, version: int, timestamp: float
    ) -> ExecutionPipelineState:
        """Record that a corrected version of an action's terms is announced (FUT-038, phase 5).

        A :class:`~alphalab.strategy.events.TermsAnnounced` in the strategy
        runtime's log, at the instant the correction became known.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event.
        """

        _require_not_before_last_event(state, timestamp, "A correction's announcement")
        announced = TermsAnnounced(str(new_id()), timestamp, asset_id, action, version)
        strategy = state.strategy
        return evolve(
            state,
            strategy=strategy.evolved(strategy.strategies, (*strategy.events, announced), ()),
            id_position=current_id_position(),
        )

    @staticmethod
    @in_accounting_context
    def record_holders(
        state: ExecutionPipelineState, asset_id: str, action: str, timestamp: float
    ) -> ExecutionPipelineState:
        """Record who holds ``asset_id`` at an elective action's record instant (FUT-030, phase 4).

        Each strategy's signed share as it stands, as a
        :class:`~alphalab.strategy.events.HoldersRecorded` in the strategy
        runtime's log: what each may elect for when the action takes effect.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event.
        """

        _require_not_before_last_event(state, timestamp, "A record instant")
        holders = {
            sid: share
            for sid, share in AllocationEngine.shares_of(state.allocation, asset_id)
            if share
        }
        recorded = HoldersRecorded(str(new_id()), timestamp, asset_id, action, holders)
        strategy = state.strategy
        return evolve(
            state,
            strategy=strategy.evolved(strategy.strategies, (*strategy.events, recorded), ()),
            id_position=current_id_position(),
        )

    @staticmethod
    @in_accounting_context
    def amend_delivery(
        state: ExecutionPipelineState,
        holding: str,
        effective_at: float,
        asset_id: str,
        change: PaymentChange,
        timestamp: float,
        rates: FxRates = NO_RATES,
        *,
        delivers_at: float | None = None,
        units: Decimal | None = None,
        reference: str = "",
    ) -> ExecutionPipelineState:
        """Book a later word on units an exchange delivers after its instant (FUT-037, phase 5).

        See :meth:`~alphalab.portfolio.engine.PortfolioEngine.amend_delivery`;
        units written off are taken from each strategy by its part of the
        delivery, and the risk state is resynced as for any change to the book.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event, or
                the amendment does not hold against the book.
        """

        _require_not_before_last_event(state, timestamp, "A delivery amendment")
        try:
            portfolio, lost = PortfolioEngine.amend_delivery(
                state.portfolio, holding, effective_at, asset_id, change, timestamp,
                delivers_at=delivers_at, units=units, reference=reference,
            )  # fmt: skip
        except InvalidTransactionError as exc:
            raise RuntimeValidationError(str(exc)) from exc
        if portfolio is state.portfolio:
            return state  # the run was owed none of it
        allocation = state.allocation
        if lost:
            allocation = AllocationEngine.apply_lifecycle(allocation, asset_id, lost)
        return _after_book_change(evolve(state, allocation=allocation), portfolio, timestamp, rates)

    @staticmethod
    @in_accounting_context
    def amend_payment(
        state: ExecutionPipelineState,
        holding: str,
        effective_at: float,
        currency: str,
        change: PaymentChange,
        timestamp: float,
        rates: FxRates = NO_RATES,
        *,
        per_unit: Decimal | None = None,
        replacement_currency: str | None = None,
        payment_at: float | None = None,
        reference: str = "",
    ) -> ExecutionPipelineState:
        """Book an issuer's later word on a corporate action's payment (FUT-034, phase 4).

        The payment is ``currency``'s consideration of the exchange of
        ``holding`` effective at ``effective_at``; see
        :meth:`~alphalab.portfolio.engine.PortfolioEngine.amend_payment` for
        each :class:`~alphalab.portfolio.settlement.PaymentChange`. A correction
        or a replacement is ``per_unit`` for each unit the holding was when it
        was exchanged, as the exchange recorded it. The risk state is resynced
        and an equity point recorded, as for any cash the book moves: a payment
        that fails leaves buying power, settled cash and collateral as if it had
        never been owed, and a delayed one keeps it out of them until paid.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event, a
                currency is one this pipeline does not settle, or the amendment
                does not hold against the book.
        """

        _require_not_before_last_event(state, timestamp, "A payment amendment")
        for named in (currency, replacement_currency):
            if named is not None and named not in state.config.settlement_currencies:
                raise RuntimeValidationError(
                    f"A payment in {named!r} cannot be booked by a pipeline that settles in "
                    f"{sorted(state.config.settlement_currencies)}."
                )
        units = None
        if change in (PaymentChange.CORRECTED, PaymentChange.REPLACED):
            units = _units_exchanged(state.portfolio, holding, effective_at)
            ledger = state.portfolio.ledger.transactions
            if units is None and payment_chain(ledger, holding, effective_at, currency):
                raise RuntimeValidationError(
                    f"The run's events no longer record how many units of {holding} its "
                    f"exchange at {effective_at!r} exchanged -- a results bound trimmed them -- "
                    "so a payment per unit cannot be booked; keep the portfolio's events."
                )
        try:
            portfolio = PortfolioEngine.amend_payment(
                state.portfolio, holding, effective_at, currency, change, timestamp,
                units=units, per_unit=per_unit, replacement_currency=replacement_currency,
                pays_at=payment_at, reference=reference,
            )  # fmt: skip
        except InvalidTransactionError as exc:
            raise RuntimeValidationError(str(exc)) from exc
        if portfolio is state.portfolio:
            return state  # the run held none of it
        return _after_book_change(state, portfolio, timestamp, rates)

    @staticmethod
    @in_accounting_context
    def record_accrual(
        state: ExecutionPipelineState, key: str, amount: Decimal, currency: str, timestamp: float
    ) -> ExecutionPipelineState:
        """Record one day's accrued interest or fee under ``key``, moving no cash (FUT-023).

        See :meth:`~alphalab.portfolio.engine.PortfolioEngine.record_accrual`.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event, or
                ``currency`` is one this pipeline does not settle.
        """

        _require_not_before_last_event(state, timestamp, "An accrual")
        if currency not in state.config.settlement_currencies:
            raise RuntimeValidationError(
                f"An accrual in {currency!r} cannot be recorded by a pipeline that settles in "
                f"{sorted(state.config.settlement_currencies)}."
            )
        portfolio = PortfolioEngine.record_accrual(
            state.portfolio, key, amount, currency, timestamp
        )
        return evolve(state, portfolio=portfolio)

    @staticmethod
    @in_accounting_context
    def post_accrual(
        state: ExecutionPipelineState,
        key: str,
        kind: CashFlowKind,
        currency: str,
        asset_id: str,
        reference: str,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> ExecutionPipelineState:
        """Close ``key``'s accrual and post its sum, rounded once, as a cash flow (FUT-023).

        Nothing is posted for a sum that rounds to nothing at the currency's
        minor unit -- the accrual still closes -- and nothing at all for a key
        with nothing open, so a period posts once.
        """

        open_amount = PortfolioEngine.accrued(state.portfolio, key)
        if open_amount == 0:
            return state
        _require_not_before_last_event(state, timestamp, "An accrual's posting")
        portfolio = PortfolioEngine.close_accrual(
            state.portfolio, key, open_amount, currency, timestamp
        )
        current = evolve(state, portfolio=portfolio)
        posted = to_money(open_amount, currency, portfolio.account.currency_units)
        if posted == 0:
            return current
        flow = CashFlow(kind, open_amount, currency, asset_id, reference)
        return ExecutionPipeline.apply_cash_flow(current, flow, timestamp, rates)

    @staticmethod
    @in_accounting_context
    def apply_split(
        state: ExecutionPipelineState,
        split: Split,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> ExecutionPipelineState:
        """Apply a split, reverse split or stock dividend to the run (ledger ACC-006).

        Everything that counts the asset in units is restated, and nothing
        that values it moves:

        * the position -- quantity times the ratio, the same basis
          (:meth:`~alphalab.portfolio.engine.PortfolioEngine.apply_split`);
        * each strategy's own position, for the targets it measures against;
        * the price the run last observed, divided by the ratio, so the book is
          marked consistently until the asset's next event prices it.

        An order working in the asset is priced and sized in the old units.
        Under simulated routing it is cancelled, as a venue cancels open orders
        at a corporate action, and what it held is freed; a strategy that wants
        it back asks again in the new units. Under external routing the venue
        decides what becomes of its own orders, and says so in its reports, so
        a split is refused while any is working.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event
                the pipeline processed, or orders are working in the asset under
                external routing.
        """

        _require_not_before_last_event(state, timestamp, "A split")
        _require_adjusted_for(state, split, timestamp)
        working = state.oms.working_orders_for(split.asset_id)
        current = state
        if working:
            if state.config.routing is ExecutionRouting.EXTERNAL:
                raise RuntimeValidationError(
                    f"{len(working)} order(s) are working in {split.asset_id} at the venue. The "
                    "venue decides what becomes of them at a corporate action and reports it; "
                    "apply the split once they are resolved."
                )
            for order_id in working:
                current = _end_unfilled(current, current.oms.orders.find(order_id), timestamp)
        portfolio = current.portfolio
        if split.asset_id in portfolio.positions:
            portfolio = PortfolioEngine.apply_split(portfolio, split, timestamp)
        price = current.market_prices.get(split.asset_id)
        current = evolve(
            current,
            allocation=AllocationEngine.apply_split(
                current.allocation, split.asset_id, split.ratio
            ),
            market_prices=(
                current.market_prices
                if price is None
                else _market_prices_with_event(
                    current.market_prices, (split.asset_id, price / split.ratio)
                )
            ),
        )
        return _after_book_change(current, portfolio, timestamp, rates)

    @staticmethod
    @in_accounting_context
    def apply_exchange(
        state: ExecutionPipelineState,
        exchange: HoldingExchange,
        timestamp: float,
        rates: FxRates = NO_RATES,
        *,
        payment_at: float | None = None,
        part: Mapping[str, Decimal] | None = None,
        delivers_at: Mapping[str, float] | None = None,
        delivery: DeliveryConvention | None = None,
    ) -> ExecutionPipelineState:
        """Exchange a holding for other instruments and cash by its declared terms (FUT-021).

        ``payment_at`` is the issuer's payment instant for the cash part and any
        cash in lieu, when it follows the effective one (FUT-025, phase 3): the
        cash is then an entitlement until it, kept out of buying power and of
        settled cash, and paid at it. ``None`` pays it at ``timestamp``.

        ``part``, when given, is the part of the holding exchanged (FUT-030,
        phase 4): each strategy's signed units of it, at most its share, the
        basis apportioned by units; the rest stays held as it was, so the
        alternatives of an elective action are applied each to its electors'
        part. A part that is the whole holding is the holding exchanged whole.

        ``delivers_at`` names the legs delivered after ``timestamp`` (FUT-037,
        phase 5) and their delivery instants: each is its holders' entitlement
        from ``timestamp``, owed until then, under ``delivery`` -- by default
        neither sold nor counted as margin collateral until it arrives.

        Everything is planned before anything moves, and refused whole:

        * each leg's instrument is one the run's registry declares, when it has
          one, and the book does not hold its opposite side;
        * each leg's units are the holding's times its ratio -- the whole part
          the book can hold, whole lots or whole units where the run trades them,
          a fraction paid at the leg's declared in-lieu price or refused;
        * the cost basis is apportioned by the declared shares, converted at the
          rate in force into a leg's or the cash part's currency when it differs,
          and the cash part, and any cash in lieu, realizes its share against the
          cash it pays -- received by a long, paid by a short;
        * a contract delivering the exchanged instrument, held or working, must
          declare its adjusted terms effective at this instant, as for a split;
        * an order working in the exchanged instrument is cancelled under
          simulated routing, and refuses the exchange under external routing.

        Each strategy's share of the holding becomes its share of each leg. A
        received holding is marked at its last observed price, or at its basis
        per unit until its first one. The run holding none of the instrument
        moves no position, cash or share; the orders and contracts are still
        checked and handled.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event,
                or any check above fails.
        """

        _require_not_before_last_event(state, timestamp, "A holding exchange")
        if payment_at is not None and payment_at < timestamp:
            raise RuntimeValidationError(
                f"An exchange effective at {timestamp!r} cannot pay its cash at {payment_at!r}, "
                "before it: a payment follows the entitlement it settles."
            )
        _require_exchange_adjusted(state, exchange, timestamp)
        current = state
        working = state.oms.working_orders_for(exchange.asset_id)
        if working:
            if state.config.routing is ExecutionRouting.EXTERNAL:
                raise RuntimeValidationError(
                    f"{len(working)} order(s) are working in {exchange.asset_id} at the venue, "
                    "which decides what becomes of them at a corporate action; apply the "
                    "exchange once they are resolved."
                )
            for order_id in working:
                current = _end_unfilled(current, current.oms.orders.find(order_id), timestamp)
        held = current.portfolio.positions.get(exchange.asset_id)
        if held is None or held.quantity == 0:
            return _after_book_change(current, current.portfolio, timestamp, rates)
        shares = AllocationEngine.shares_of(current.allocation, exchange.asset_id)
        kept = None
        if part is not None:
            held, kept, shares = _exchanged_part(current, held, shares, part, timestamp)
        plan = _exchange_plan(current, exchange, held, timestamp, rates)
        leg_parts = {
            leg.asset_id: (
                _strategy_parts(given, holding.quantity)
                if (
                    given := tuple(
                        (sid, ACCOUNTING_CONTEXT.multiply(share, leg.ratio))
                        for sid, share in shares
                    )
                )
                else {}
            )
            for leg, holding in plan.legs
        }
        portfolio = PortfolioEngine.apply_exchange(
            current.portfolio,
            exchange.asset_id,
            plan.received,
            plan.retained_basis,
            plan.consideration,
            plan.realized,
            timestamp,
            exchange.reference,
            pays_at=payment_at,
            kept=kept,
            delivers=None
            if not delivers_at
            else {a: (when, leg_parts.get(a, {})) for a, when in delivers_at.items()},
            convention=delivery,
        )
        allocation = current.allocation
        closed = {} if exchange.retained else dict(shares)
        for index, (leg, holding) in enumerate(plan.legs):
            parts = leg_parts[leg.asset_id]
            unit_value = _in_budget_currency(
                current,
                ACCOUNTING_CONTEXT.multiply(holding.price, _multiplier(holding.economics)),
                holding.currency,
                rates,
                timestamp,
            )
            allocation = AllocationEngine.apply_lifecycle(
                allocation,
                exchange.asset_id,
                closed if index == 0 else {},
                leg.asset_id,
                parts,
                unit_value,
            )
        if not plan.legs and closed:
            allocation = AllocationEngine.apply_lifecycle(allocation, exchange.asset_id, closed)
        current = evolve(current, allocation=allocation)
        return _after_book_change(current, portfolio, timestamp, rates)

    @staticmethod
    @in_accounting_context
    def settle_contract(
        state: ExecutionPipelineState,
        settlement: ContractSettlement,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> ExecutionPipelineState:
        """Close every position in an expired contract at its final settlement price (FUT-012).

        The contract must be one the run's registry declares, with a last
        trading instant at or before ``timestamp``, and a future or an option
        -- what settles at expiry -- or a bond, redeemed at maturity at the
        price ``settlement`` states (v4.1.1, FUT-010). Then, as at a split, an order still working
        in it is cancelled and what it held freed (refused under external
        routing, where the venue reports what became of its orders); the
        position is closed at the settlement price with no commission, by the
        portfolio's one fill authority, so a future realizes its last variation
        and an option pays its intrinsic value times its multiplier; each
        strategy's share closes with it; a trade record attributes the realized
        P&L to the strategies that held it; the run is marked at the price; and
        risk is resynced and an equity point recorded.

        Settling a contract the run holds no position in only marks it.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event
                the pipeline processed; the contract is not declared, declares
                no last trading instant, has not expired by ``timestamp``, or is
                not a future or an option; the price is one its economics do not
                admit; or orders are working in it under external routing.
        """

        _require_not_before_last_event(state, timestamp, "A contract settlement")
        asset_id = settlement.asset_id
        registry = state.config.instruments
        record = None if registry is None else registry.record_for(asset_id)
        if record is None:
            raise RuntimeValidationError(
                f"{asset_id} is not declared in this run's instrument registry, so nothing "
                "says it expires or how it settles; a settlement for it is refused."
            )
        named = f"{record.symbol} on {record.exchange}"
        if record.last_trade_at is None:
            raise RuntimeValidationError(
                f"{named} declares no last trading instant: it does not expire, so it is not "
                "settled. Declare InstrumentRecord.last_trade_at for a contract that expires."
            )
        if timestamp < record.last_trade_at:
            raise RuntimeValidationError(
                f"{named} trades until {record.last_trade_at!r}; a settlement at {timestamp!r} "
                "would close positions while the contract still trades."
            )
        economics = economics_for(record.asset_type, record.economics, named)
        redeemed = record.asset_type is AssetType.BOND
        if economics.settlement not in _EXPIRING and not redeemed:
            raise RuntimeValidationError(
                f"{named} settles as {economics.settlement.value}: only a future or an option "
                "is settled at expiry, and a bond redeemed at maturity."
            )
        price = settlement.price
        if price < 0 and not economics.allows_negative_prices:
            raise RuntimeValidationError(
                f"{named} is settled at {price}, which its economics do not admit."
            )
        if isinstance(record.terms, OptionTerms):
            raise RuntimeValidationError(
                f"{named} declares its option terms, so its expiry is resolved from them: give "
                "an OptionExpiry with the underlying's settlement value, not a ContractSettlement "
                "at a price the caller computed."
            )
        delivery = (
            record.terms.deliverable
            if isinstance(record.terms, FutureTerms)
            and record.terms.settlement is SettlementStyle.PHYSICAL
            else None
        )

        working = state.oms.working_orders_for(asset_id)
        current = state
        if working:
            if state.config.routing is ExecutionRouting.EXTERNAL:
                raise RuntimeValidationError(
                    f"{len(working)} order(s) are working in {named} at the venue. The venue "
                    "reports what became of them at expiry; settle the contract once they are "
                    "resolved."
                )
            for order_id in working:
                current = _end_unfilled(current, current.oms.orders.find(order_id), timestamp)

        portfolio = current.portfolio
        position = portfolio.positions.get(asset_id)
        signed_shares = AllocationEngine.shares_of(current.allocation, asset_id)
        if delivery is not None and position is not None and position.quantity != 0:
            # Checked before anything moves: a delivery the book cannot take is
            # refused whole, not half-applied.
            _require_deliverable(
                current,
                record,
                delivery,
                delivery.units * position.quantity,
                price,
                economics.multiplier,
            )
        allocation, shares = AllocationEngine.settle_asset(current.allocation, asset_id)
        current = evolve(
            current,
            allocation=allocation,
            market_prices=_market_prices_with_event(current.market_prices, (asset_id, price)),
        )
        record_of_settlement: TradeRecord | None = None
        if position is not None and position.quantity != 0:
            before = len(portfolio.events)
            opened_at = position.opened_at
            portfolio = PortfolioEngine.apply_fill(
                portfolio,
                asset_id,
                -position.quantity,
                price,
                Decimal("0"),
                timestamp,
                position.currency,
                economics=economics,
                final_settlement=True,
                settles_at=_settles_at(current, asset_id, timestamp),
            )
            realized = Decimal("0.00")
            for event in portfolio.events[before:]:
                if isinstance(event, PositionReduced | PositionClosed):
                    realized = event.realized_pnl
                    break
            record_of_settlement = TradeRecord(
                # Derived, not drawn from the run's identifier stream.
                trade_id=f"SETTLEMENT-{asset_id}-{timestamp!r}",
                asset_id=asset_id,
                sector_id=_sector_for(current, asset_id),
                realized_pnl=realized,
                notional_value=ACCOUNTING_CONTEXT.multiply(position.quantity.copy_abs(), price),
                holding_period_seconds=None if opened_at is None else timestamp - opened_at,
                contributions=shares,
            )
        if record_of_settlement is not None:
            current = evolve(
                current, trade_records=current.trade_records.append(record_of_settlement)
            )
        if delivery is not None and position is not None and position.quantity != 0:
            # A physically settled future: the long takes the deliverable and
            # pays the final price for it, the short delivers and receives it.
            current = _deliver(
                evolve(current, portfolio=portfolio),
                record,
                delivery,
                {sid: delivery.units * held for sid, held in signed_shares},
                delivery.units * position.quantity,
                price,
                economics.multiplier,
                timestamp,
                rates,
                f"DELIVERY-{asset_id}-{timestamp!r}",
            )
            portfolio = current.portfolio
        return _after_book_change(current, portfolio, timestamp, rates)

    @staticmethod
    @in_accounting_context
    def exercise(
        state: ExecutionPipelineState,
        exercise: Exercise,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> ExecutionPipelineState:
        """Exercise ``exercise.contracts`` of a long option at ``timestamp`` (v4.1.1, FUT-013).

        The option's :class:`~alphalab.instrument.terms.OptionTerms` decide what
        happens: inside its exercise window -- any instant to expiry for an
        American contract, from the last trading instant to expiry for a European
        one -- a cash-settled contract pays its intrinsic value against the
        settlement value the instruction carries, and a physically settled one
        delivers its deliverable against the strike: a call takes it and pays
        ``strike x multiplier`` a contract, a put delivers it and receives that.
        Every movement goes through the portfolio's one fill authority, the
        exercised contracts closed at what they pay (nothing, when they deliver),
        and each strategy's share of the position closes and receives its share
        of the delivery. The engine carries out the instruction; whether
        exercising was wise is the holder's question.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event;
                the option is undeclared or declares no terms; the instant is
                outside its exercise window; the book is not long that many
                contracts; orders work in it; a cash-settled exercise carries no
                settlement value or a physical one carries one; or the delivery
                cannot be taken -- see :meth:`expire_option`.
        """

        record, terms, economics, named = _declared_option(state, exercise.asset_id, timestamp)
        _require_in_window(record, terms, timestamp, named, "exercised")
        _require_no_working_orders(state, exercise.asset_id, named, "exercised")
        held = _held(state, exercise.asset_id)
        if held < exercise.contracts:
            raise RuntimeValidationError(
                f"{exercise.contracts} contracts of {named} cannot be exercised: the book is "
                f"{held} of them, and only a long position is exercised."
            )
        return _resolve_option(
            state,
            record,
            terms,
            economics,
            exercise.contracts,
            exercise.underlying_value,
            timestamp,
            rates,
            f"EXERCISE-{exercise.asset_id}-{timestamp!r}-{exercise.reference}",
            instructed=True,
        )

    @staticmethod
    @in_accounting_context
    def assign(
        state: ExecutionPipelineState,
        assignment: Assignment,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> ExecutionPipelineState:
        """Assign ``assignment.contracts`` of a short option at ``timestamp`` (v4.1.1, FUT-013).

        The mirror of :meth:`exercise`, from the writer's side: a cash-settled
        contract pays out its intrinsic value; a physically settled call
        delivers the deliverable and receives the strike, a put takes it and
        pays the strike. Assignment is an input, never drawn -- see
        :class:`~alphalab.portfolio.corporate_actions.Assignment`.

        Raises:
            RuntimeValidationError: As :meth:`exercise`, with "short" for "long".
        """

        record, terms, economics, named = _declared_option(state, assignment.asset_id, timestamp)
        _require_in_window(record, terms, timestamp, named, "assigned")
        _require_no_working_orders(state, assignment.asset_id, named, "assigned")
        held = _held(state, assignment.asset_id)
        if -held < assignment.contracts:
            raise RuntimeValidationError(
                f"{assignment.contracts} contracts of {named} cannot be assigned: the book is "
                f"{held} of them, and only a short position is assigned."
            )
        return _resolve_option(
            state,
            record,
            terms,
            economics,
            -assignment.contracts,
            assignment.underlying_value,
            timestamp,
            rates,
            f"ASSIGNMENT-{assignment.asset_id}-{timestamp!r}-{assignment.reference}",
        )

    @staticmethod
    @in_accounting_context
    def expire_option(
        state: ExecutionPipelineState,
        expiry: OptionExpiry,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> ExecutionPipelineState:
        """Resolve every open position in an option at its expiry (v4.1.1, FUT-013).

        At or after the contract's ``expires_at``, against the underlying's
        settlement value: orders still working in it are cancelled, as at a
        settlement; then, if its automatic-exercise rule exercises at that value,
        a long position is exercised and a short one assigned -- in cash or by
        delivery, by its terms -- and otherwise the position is closed at
        nothing. A contract with no rule exercises nothing it was not instructed
        to. The option is marked at its intrinsic value.

        A delivery the book cannot take is refused rather than booked: a
        deliverable the registry does not declare, or one priced in another
        currency than the strike; a delivery leaving the book short when the
        run does not allow shorting; or a payment leaving the cash it is paid
        in below zero -- no financing of a delivery is modelled.

        Raises:
            RuntimeValidationError: If ``timestamp`` is before the last event or
                before the contract expires; the option is undeclared or declares
                no terms; orders work in it under external routing; or the
                delivery cannot be taken.
        """

        record, terms, economics, named = _declared_option(state, expiry.asset_id, timestamp)
        if timestamp < terms.expires_at:
            raise RuntimeValidationError(
                f"{named} expires at {terms.expires_at!r}; an expiry at {timestamp!r} would "
                "resolve it while it can still be exercised."
            )
        current = state
        working = state.oms.working_orders_for(expiry.asset_id)
        if working:
            if state.config.routing is ExecutionRouting.EXTERNAL:
                raise RuntimeValidationError(
                    f"{len(working)} order(s) are working in {named} at the venue; resolve them "
                    "before its expiry is."
                )
            for order_id in working:
                current = _end_unfilled(current, current.oms.orders.find(order_id), timestamp)
        packaged = bool(terms.basket) or terms.cash_per_contract != 0
        if packaged:
            if expiry.underlying_value is not None:
                raise RuntimeValidationError(
                    f"{named} expires against a package of {len(terms.deliverables)} "
                    "deliverables; one underlying value cannot state it, and its components "
                    "are valued at the prices the run has observed for them."
                )
            values, multipliers = _package_values(state, record, terms, timestamp)
            intrinsic = terms.basket_intrinsic(values, multipliers, economics.multiplier)
        else:
            if expiry.underlying_value is None:
                raise RuntimeValidationError(
                    f"{named} expires against its deliverable's settlement value, which the "
                    "expiry does not state."
                )
            intrinsic = terms.intrinsic(
                expiry.underlying_value,
                economics.multiplier,
                _deliverable_multiplier(state, record, terms.deliverable),
            )
        current = evolve(
            current,
            market_prices=_market_prices_with_event(
                current.market_prices, (expiry.asset_id, intrinsic)
            ),
        )
        held = _held(current, expiry.asset_id)
        if held == 0:
            return current
        rule = terms.automatic_exercise
        if rule is not None and rule.exercises(intrinsic):
            return _resolve_option(
                current,
                record,
                terms,
                economics,
                held,
                expiry.underlying_value
                if terms.settlement is SettlementStyle.CASH and not packaged
                else None,
                timestamp,
                rates,
                f"EXPIRY-{expiry.asset_id}-{timestamp!r}",
            )
        # Not exercised: the contract expires and is worth nothing.
        shares = _strategy_parts(
            AllocationEngine.shares_of(current.allocation, expiry.asset_id), held
        )
        current = evolve(
            current,
            allocation=AllocationEngine.apply_lifecycle(
                current.allocation, expiry.asset_id, shares
            ),
        )
        current = _lifecycle_fill(
            current,
            expiry.asset_id,
            -held,
            Decimal("0"),
            timestamp,
            record.currency,
            economics,
            shares,
            f"EXPIRY-{expiry.asset_id}-{timestamp!r}",
        )
        return _after_book_change(current, current.portfolio, timestamp, rates)

    @staticmethod
    @in_accounting_context
    def compile_analytics(
        state: ExecutionPipelineState,
        timestamp: float,
        years_elapsed: float | None = None,
        risk_free_rate: float = 0.0,
        periods_per_year: float | None = None,
    ) -> ExecutionPipelineState:
        """Compile analytics from portfolio snapshots and execution trade records.

        ``years_elapsed`` and ``periods_per_year`` are derived from the equity
        curve when ``None``; see
        :meth:`~alphalab.analytics.engine.AnalyticsEngine.compile_report`.
        """

        analytics = AnalyticsEngine.compile_report(
            state.analytics,
            state.portfolio_snapshots,
            state.trade_records,
            timestamp,
            years_elapsed,
            risk_free_rate,
            periods_per_year,
        )
        return evolve(state, analytics=analytics, id_position=current_id_position())


@dataclass(frozen=True, slots=True)
class _Routed:
    """What routing a batch of requests produced, accumulated across one step."""

    decisions: tuple[RiskDecision, ...] = ()
    orders: tuple[OMSOrder, ...] = ()
    reports: tuple[ExecutionReport, ...] = ()
    fills: tuple[CoreFill, ...] = ()
    trades: tuple[CoreTrade, ...] = ()
    unpriced: tuple[OrderRequest, ...] = ()
    refusals: tuple[SettlementRefusal, ...] = ()
    #: Every request that never became an order, and why.
    dropped: tuple[tuple[OrderRequest, str], ...] = ()

    def then(self, later: _Routed) -> _Routed:
        """This, followed by ``later``."""

        if later is _NO_ROUTING:
            return self
        if self is _NO_ROUTING:
            return later
        return _Routed(
            (*self.decisions, *later.decisions),
            (*self.orders, *later.orders),
            (*self.reports, *later.reports),
            (*self.fills, *later.fills),
            (*self.trades, *later.trades),
            (*self.unpriced, *later.unpriced),
            (*self.refusals, *later.refusals),
            (*self.dropped, *later.dropped),
        )


#: What a step carries when nothing was routed.
_NO_ROUTING: Final = _Routed()


def _route_requests(
    state: ExecutionPipelineState,
    event: MarketEvent,
    intents: tuple[Intent, ...],
    requests: tuple[OrderRequest, ...],
    policy: FillPolicy,
    rates: FxRates = NO_RATES,
    *,
    rest: bool = False,
    at: float | None = None,
) -> tuple[ExecutionPipelineState, _Routed]:
    """Take each request through price, settlement and risk checks to the OMS, and execute it.

    ``rest`` leaves every accepted simulated order working until its asset's next
    event, whatever the run's fill timing: the rule for an order placed from
    feedback, which must not fill in the step that reported the fill it reacts
    to -- a strategy answering each fill with an order would otherwise never
    let a step end. ``at`` is the instant the requests are judged and placed at,
    when it is not ``event``'s own -- a strategy stopped after the last event.
    """

    instant = event.timestamp if at is None else at

    decisions: list[RiskDecision] = []
    orders: list[OMSOrder] = []
    reports: list[ExecutionReport] = []
    fills: list[CoreFill] = []
    trades: list[CoreTrade] = []
    unpriced: list[OrderRequest] = []
    refusals: list[SettlementRefusal] = []
    dropped: list[tuple[OrderRequest, str]] = []
    current = state
    # An intent for an asset the run never priced can end before it is a
    # request: a sizing model that needs a price refuses it (ledger ALC-003), so
    # the loop below never sees it. Its asset is recorded here, once per event
    # as a netted request would be, so the run can still say why it did not
    # trade. Until v3.10 such an intent was sized to zero and left no trace.
    if any(intent.instrument not in current.market_prices for intent in intents):
        requested = {request.asset_id for request in requests}
        for asset_id in dict.fromkeys(
            intent.instrument
            for intent in intents
            if intent.instrument not in current.market_prices and intent.instrument not in requested
        ):
            current = _record_unpriced(current, asset_id, instant)

    breached: MarginPosition | bool | None = False  # not yet asked
    for request in requests:
        # An order that would increase a position while the account is below
        # its maintenance margin is refused: only reductions trade while a
        # margin call is open (FUT-017). Asked once, and only when one would.
        if _increases(current, request):
            if breached is False:
                margin = margin_position(current, rates, instant)
                breached = margin if margin is not None and margin.in_breach else None
            if isinstance(breached, MarginPosition):
                dropped.append(
                    (
                        request,
                        f"The account is below its maintenance margin -- equity "
                        f"{breached.equity} against {breached.maintenance} -- and only an "
                        "order reducing a position trades until the call is met.",
                    )
                )
                current = _retire_dropped_request(current, request.order_id, instant)
                continue
            # An order growing a margined position must leave the account's
            # equity at or above its initial requirement, counting what is
            # already working (FUT-017).
            short_of_margin = _initial_margin_refusal(current, request, rates, instant)
            if short_of_margin is not None:
                dropped.append((request, short_of_margin))
                current = _retire_dropped_request(current, request.order_id, instant)
                continue
            # A short is sold only against borrow a lender offers (FUT-018).
            unborrowable = _borrow_refusal(current, request, instant)
            if unborrowable is not None:
                dropped.append((request, unborrowable))
                current = _retire_dropped_request(current, request.order_id, instant)
                continue
        # A venue's band, at placement (FUT-020): a limit priced beyond it is
        # refused or capped, as the band declares.
        beyond, request = _band_terms(current, request, instant)
        if beyond is not None:
            dropped.append((request, beyond))
            current = _retire_dropped_request(current, request.order_id, instant)
            continue
        # The venue's price grid: a limit or stop between its ticks is refused,
        # as the venue would refuse it.
        off_grid = _tick_refusal(current, request)
        if off_grid is not None:
            dropped.append((request, off_grid))
            current = _retire_dropped_request(current, request.order_id, instant)
            continue
        # An order cannot be priced, executed or valued without a market price
        # for its asset -- allocation prices unknown assets at 0.00. Drop the
        # request here, deterministically and before it reaches the OMS, rather
        # than submitting an order the execution leg cannot price.
        if request.asset_id not in current.market_prices:
            unpriced.append(request)
            dropped.append(
                (request, f"No market price for {request.asset_id}; the order was not placed.")
            )
            current = _record_unpriced(current, request.asset_id, instant)
            current = _retire_dropped_request(current, request.order_id, instant)
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
        refusal = _settlement_refusal(current, request.asset_id, instant)
        if refusal is not None:
            refusals.append(refusal)
            dropped.append((request, refusal.detail))
            current = _retire_dropped_request(current, request.order_id, instant)
            continue
        # A book settling several currencies pays each fill from the cash of its
        # own: a purchase that cash cannot pay is refused here, not at the fill
        # (ACC-017; buying power is the base currency's sum of them all).
        unpayable = _currency_cash_refusal(current, request, instant)
        if unpayable is not None:
            dropped.append((request, unpayable))
            current = _retire_dropped_request(current, request.order_id, instant)
            continue
        # Units an exchange has not yet delivered are not sold (FUT-037).
        undelivered = _undelivered_refusal(current, request, instant)
        if undelivered is not None:
            dropped.append((request, undelivered))
            current = _retire_dropped_request(current, request.order_id, instant)
            continue
        unworkable = _terms_refusal(current, request, instant)
        if unworkable is not None:
            dropped.append((request, unworkable))
            current = _retire_dropped_request(current, request.order_id, instant)
            continue
        current, decision = _evaluate_risk(current, request, instant, rates)
        decisions.append(decision)
        if not decision.approved:
            # Allocation reserved this request's notional when it sized it.
            # Risk refused it, so it will never reach the OMS and never
            # execute: the capital it holds is freed here, at the point its
            # lifecycle ends, and exactly once.
            dropped.append((request, decision.reason))
            current = _retire_dropped_request(current, request.order_id, instant)
            continue
        current, order = _submit_and_accept_order(current, request, instant)
        if current.config.routing is not ExecutionRouting.SIMULATED:
            # The order is now working and belongs to whoever routes it -- the
            # venue, or the execution algorithm working it in children. No fill
            # is invented, the order is not closed out, and its reservation
            # stays held -- the capital is still committed.
            orders.append(order)
            continue
        if rest or current.config.fill_timing is FillTiming.NEXT_EVENT:
            # Working until its asset's next event, which fills it there -- not
            # at the price the strategy decided on (EXE-001), and not in the step
            # whose feedback placed it (EXE-005). Its reservation stays held
            # until then.
            orders.append(order)
            continue
        current, new_reports, new_fills, new_trades = _work_order(
            current, order, event, policy, rates, arriving=True
        )
        orders.append(order)
        reports.extend(new_reports)
        fills.extend(new_fills)
        trades.extend(new_trades)

    return current, _Routed(
        tuple(decisions),
        tuple(orders),
        tuple(reports),
        tuple(fills),
        tuple(trades),
        tuple(unpriced),
        tuple(refusals),
        tuple(dropped),
    )


def _step_result(
    state: ExecutionPipelineState,
    event: MarketEvent,
    intents: tuple[Intent, ...],
    requests: tuple[OrderRequest, ...],
    routed: _Routed,
    rates: FxRates,
    queue: QueueBook | None = None,
) -> ExecutionPipelineResult:
    """Close the step: value the book once, record the snapshot, and report."""

    # Valued once: the analytics snapshot and the result's valuation are the
    # same figures of the same book. Until v3.10 each was computed separately.
    current = state
    valuation = PortfolioValuation.snapshot(
        current.portfolio, event.timestamp, current.config.currency, rates
    )
    # The step boundary, and the only place the position is refreshed for an
    # event: every environment reaches here through process_record,
    # process_market_event or process_quote, and none of them refreshes it
    # earlier -- a position read halfway through a step would describe neither
    # the state before it nor the state after.
    current = evolve(
        current,
        portfolio_snapshots=current.portfolio_snapshots.append(
            _analytics_snapshot(valuation, event.timestamp)
        ),
        id_position=current_id_position(),
    )

    return ExecutionPipelineResult(
        current,
        event,
        intents,
        requests,
        routed.decisions,
        routed.orders,
        routed.reports,
        routed.fills,
        routed.trades,
        routed.unpriced,
        valuation,
        routed.refusals,
        queue,
    )


#: The unit a fill is divided among the strategies of a netted order in, for
#: :attr:`~alphalab.strategy.events.FillEvent.attributed_quantity`. Every part
#: but the last is rounded to it and the last takes the remainder, so the parts
#: sum to the fill exactly (:func:`~alphalab.core.contribution.split_by_contribution`).
_ATTRIBUTION_QUANTUM: Final = Decimal("1E-12")


def _allocate(
    state: ExecutionPipelineState,
    intents: tuple[Intent, ...],
    market_prices: Mapping[str, Decimal],
    rates: FxRates,
    timestamp: float,
) -> tuple[AllocationState, tuple[OrderRequest, ...]]:
    """Allocate intents with everything allocation reads and cannot see for itself.

    The one call every allocation on the path makes -- a market event's, a
    timer's, a stop's and the feedback round's -- so each hands allocation the
    same facts: prices in the budget's currency, the committed positions long-only
    judges, each target strategy's working share (FEA-001), and each asset's lot
    grid and minimum notional from its declared economics (ACC-005).
    """

    constraints = state.config.allocation_constraints
    lots, minimums, multipliers = _instrument_grid(state, intents)
    return AllocationEngine.allocate(
        state.allocation,
        intents,
        market_prices,
        state.config.sizing_model,
        constraints,
        timestamp,
        _budget_prices(state, market_prices, rates, timestamp),
        positions=_committed_positions(state, intents),
        working=_working_shares(state, intents),
        lots=lots,
        minimum_notionals=minimums,
        multipliers=multipliers,
    )


def _instrument_grid(
    state: ExecutionPipelineState, intents: tuple[Intent, ...]
) -> tuple[Mapping[str, LotSpecification], Mapping[str, Decimal], Mapping[str, Decimal]]:
    """The lot grid, minimum notional and multiplier each intent's asset declares.

    Only what is declared, and only for multipliers other than one: a run that
    declares nothing gets three empty mappings and allocation does exactly what
    it did before v3.11.
    """

    if state.config.instruments is None or not intents:
        return _NO_LOTS, _NO_MINIMUMS, _NO_MULTIPLIERS
    lots: dict[str, LotSpecification] = {}
    minimums: dict[str, Decimal] = {}
    multipliers: dict[str, Decimal] = {}
    for asset_id in {intent.instrument for intent in intents}:
        economics = _economics_of(state, asset_id)
        if economics is None:
            continue
        if economics.lot is not None:
            lots[asset_id] = economics.lot
        if economics.minimum_notional is not None:
            minimums[asset_id] = economics.minimum_notional
        if economics.multiplier != 1:
            multipliers[asset_id] = economics.multiplier
    return lots, minimums, multipliers


def _expired_at(state: ExecutionPipelineState, asset_id: str) -> float | None:
    """The last trading instant ``asset_id``'s record declares, or ``None`` (FUT-012).

    Read from the registry's index of expiring instruments, so a run whose
    registry declares none -- every run before v4.1.1 -- pays one emptiness test.
    """

    registry = state.config.instruments
    if registry is None or not registry.expiring:
        return None
    return registry.expiring.get(asset_id)


def held_past_expiry(state: ExecutionPipelineState, at: float) -> tuple[str, ...]:
    """Every contract the book still holds whose last trading instant is before ``at`` (FUT-012).

    What a :class:`~alphalab.portfolio.corporate_actions.ContractSettlement`
    should have closed. Sorted, so the answer does not depend on the order the
    positions were opened in.
    """

    return tuple(
        sorted(
            asset_id
            for asset_id, position in state.portfolio.positions.items()
            if position.quantity != 0
            and (expired := _expired_at(state, asset_id)) is not None
            and expired < at
        )
    )


def _declared(state: ExecutionPipelineState, asset_id: str) -> InstrumentRecord | None:
    """An instrument's declaration, for a lifecycle event: a contract or its deliverable.

    The one keyed read a lifecycle event takes, once per contract or deliverable
    it names -- never per record.
    """

    registry = state.config.instruments
    return None if registry is None else registry.record_for(asset_id)


def _declared_option(
    state: ExecutionPipelineState, asset_id: str, timestamp: float
) -> tuple[InstrumentRecord, OptionTerms, InstrumentEconomics, str]:
    """The option's record, terms and economics, or a refusal naming what is missing."""

    _require_not_before_last_event(state, timestamp, "An option's exercise, assignment or expiry")
    record = _declared(state, asset_id)
    if record is None:
        raise RuntimeValidationError(
            f"{asset_id} is not declared in this run's instrument registry, so nothing says "
            "what exercising it does."
        )
    named = f"{record.symbol} on {record.exchange}"
    terms = record.terms
    if not isinstance(terms, OptionTerms):
        raise RuntimeValidationError(
            f"{named} declares no OptionTerms, so nothing says what exercising it delivers or "
            "pays. Declare its terms, or settle it with a ContractSettlement at a stated price."
        )
    economics = economics_for(record.asset_type, record.economics, named)
    # The terms in force now: a declared adjustment -- a split's, a merger's --
    # applies from its own instant (FUT-016).
    return record, terms.effective(timestamp), economics, named


def _require_in_window(
    record: InstrumentRecord, terms: OptionTerms, timestamp: float, named: str, done: str
) -> None:
    assert record.last_trade_at is not None  # terms are declared only with one
    if terms.exercise_style is ExerciseStyle.BERMUDAN:
        if not terms.may_exercise(timestamp, record.last_trade_at):
            windows = ", ".join(f"{a!r} to {b!r}" for a, b in terms.exercise_windows)
            raise RuntimeValidationError(
                f"{named} is BERMUDAN: it may be {done} in its windows, {windows}; "
                f"{timestamp!r} is in none of them."
            )
        return
    start, end = terms.exercise_window(record.last_trade_at)
    if not start <= timestamp <= end:
        raise RuntimeValidationError(
            f"{named} is {terms.exercise_style.name}: it may be {done} from {start!r} to "
            f"{end!r}, and not at {timestamp!r}."
        )


def _require_no_working_orders(
    state: ExecutionPipelineState, asset_id: str, named: str, done: str
) -> None:
    working = state.oms.working_orders_for(asset_id)
    if working:
        raise RuntimeValidationError(
            f"{len(working)} order(s) are working in {named}; contracts are not {done} while "
            "orders may still trade the position. Resolve them first."
        )


def _held(state: ExecutionPipelineState, asset_id: str) -> Decimal:
    position = state.portfolio.positions.get(asset_id)
    return Decimal("0") if position is None else position.quantity


def _strategy_parts(
    shares: tuple[tuple[str, Decimal], ...], closing: Decimal
) -> dict[str, Decimal]:
    """Each strategy's part of ``closing`` contracts, from its own signed share.

    The whole position is each share as it stands. Part of it is apportioned in
    proportion to the shares -- whole contracts by largest remainder, ties to the
    earlier strategy, when every share is whole -- and is refused when the
    strategies hold opposite sides: which side a part of a netted book belongs to
    is not something the book can say.
    """

    if not shares:
        return {}
    total = sum((held for _, held in shares), Decimal("0"))
    if closing == total:
        return dict(shares)
    if any((held > 0) != (closing > 0) for _, held in shares):
        raise RuntimeValidationError(
            "Part of a position whose strategies hold opposite sides cannot be attributed to "
            "them; exercise or assign the whole position."
        )
    ctx = ACCOUNTING_CONTEXT
    if all(held == held.to_integral_value() for _, held in shares):
        # Whole contracts, by the one largest-remainder rule the algorithms
        # package apportions a schedule by.
        units = _largest_remainder(int(closing.copy_abs()), [held.copy_abs() for _, held in shares])
        sign = Decimal("1") if closing > 0 else Decimal("-1")
        return {sid: sign * Decimal(part) for (sid, _), part in zip(shares, units, strict=True)}
    parts = {sid: ctx.divide(ctx.multiply(closing, held), total) for sid, held in shares[:-1]}
    parts[shares[-1][0]] = ctx.subtract(closing, sum(parts.values(), Decimal("0")))
    return parts


def _contributions(parts: Mapping[str, Decimal]) -> tuple[StrategyContribution, ...]:
    return tuple(StrategyContribution(sid, part.copy_abs()) for sid, part in parts.items() if part)


def _lifecycle_fill(
    state: ExecutionPipelineState,
    asset_id: str,
    quantity: Decimal,
    price: Decimal,
    timestamp: float,
    currency: str,
    economics: InstrumentEconomics,
    parts: Mapping[str, Decimal],
    trade_id: str,
    financed: bool = False,
) -> ExecutionPipelineState:
    """Book ``quantity`` of ``asset_id`` at ``price`` through the portfolio's fill authority.

    No commission: a lifecycle event is not a trade the venue charges for as one.
    The trade record carries the realized P&L and the strategies it belongs to,
    and its identifier is derived, never drawn from the run's stream.
    """

    portfolio = state.portfolio
    before = len(portfolio.events)
    opened_at = _opened_at(portfolio, asset_id)
    portfolio = PortfolioEngine.apply_fill(
        portfolio,
        asset_id,
        quantity,
        price,
        Decimal("0"),
        timestamp,
        currency,
        economics=economics,
        final_settlement=True,
        settles_at=_settles_at(state, asset_id, timestamp),
        financed=financed,
    )
    realized = Decimal("0.00")
    for event in portfolio.events[before:]:
        if isinstance(event, PositionReduced | PositionClosed):
            realized = event.realized_pnl
            break
    record = TradeRecord(
        trade_id=trade_id,
        asset_id=asset_id,
        sector_id=_sector_for(state, asset_id),
        realized_pnl=realized,
        notional_value=ACCOUNTING_CONTEXT.multiply(quantity.copy_abs(), price),
        holding_period_seconds=None if opened_at is None else timestamp - opened_at,
        contributions=_contributions(parts),
    )
    return evolve(state, portfolio=portfolio, trade_records=state.trade_records.append(record))


def _deliverable_multiplier(
    state: ExecutionPipelineState, contract: InstrumentRecord, deliverable: Deliverable
) -> Decimal:
    """What one quoted unit of the deliverable is worth per unit of its price.

    Read from the deliverable's own declaration -- a cash-settled index option
    names its index as an instrument too -- so a contract on a future or on an
    instrument with a multiplier of its own is valued by it, not by an assumed one.
    """

    underlying = _declared(state, deliverable.asset_id)
    if underlying is None:
        raise RuntimeValidationError(
            f"{contract.symbol} on {contract.exchange} settles against "
            f"{deliverable.asset_id}, which the run's registry does not declare; what one unit "
            "of it is worth is that declaration's to say."
        )
    named = f"{underlying.symbol} on {underlying.exchange}"
    return economics_for(underlying.asset_type, underlying.economics, named).multiplier


def _require_deliverable(
    state: ExecutionPipelineState,
    contract: InstrumentRecord,
    deliverable: Deliverable,
    units: Decimal,
    price: Decimal,
    multiplier: Decimal,
) -> tuple[InstrumentRecord, InstrumentEconomics, Decimal, bool]:
    """The deliverable's record, its economics, the price per unit it moves at, and
    whether it is financed -- or a refusal.

    ``units`` is signed: positive is taken in, negative delivered away. ``price``
    is per unit of the contract's price, so ``price x multiplier`` is one
    contract's consideration, and the unit price is that over the deliverable
    units and the deliverable's own multiplier.

    A delivery taken in for more than the cash it is paid in is financed --
    booked as a debit -- only when the deliverable declares
    :class:`~alphalab.instrument.margin.MarginTerms`: it is then held on margin,
    and the account's margin, not its cash, decides what follows (FUT-017). A
    deliverable declaring none is refused, as it always was.
    """

    named = f"{contract.symbol} on {contract.exchange}"
    underlying = _declared(state, deliverable.asset_id)
    if underlying is None:
        raise RuntimeValidationError(
            f"{named} delivers {deliverable.asset_id}, which the run's registry does not "
            "declare; nothing can be delivered that the book cannot name."
        )
    if underlying.currency != contract.currency:
        raise RuntimeValidationError(
            f"{named} is paid for in {contract.currency} and delivers {underlying.symbol}, "
            f"priced in {underlying.currency}; a delivery whose consideration and deliverable "
            "are in two currencies is not modelled."
        )
    economics = economics_for(
        underlying.asset_type, underlying.economics, f"{underlying.symbol} on {underlying.exchange}"
    )
    ctx = ACCOUNTING_CONTEXT
    whole = _whole_part(state, economics, units.copy_abs())
    if whole != units.copy_abs():
        # Delivering it would leave the book holding a fraction of a unit it
        # cannot hold (FUT-016): that is the contract's cash in lieu to settle.
        raise RuntimeValidationError(
            f"{named} delivers {units.copy_abs()} {underlying.symbol}, of which "
            f"{ctx.subtract(units.copy_abs(), whole)} is less than a unit the book can hold, "
            "and declares no cash in lieu for it (OptionTerms.fractions)."
        )
    per_contract_units = deliverable.units
    unit_price = ctx.divide(
        ctx.multiply(price, multiplier), ctx.multiply(per_contract_units, economics.multiplier)
    )
    if not economics.admits_price(unit_price):
        raise RuntimeValidationError(
            f"{named} would deliver {underlying.symbol} at {unit_price}, which its economics do "
            "not admit."
        )
    held = _held(state, deliverable.asset_id)
    if (
        units < 0
        and ctx.add(held, units) < 0
        and not state.config.allocation_constraints.allow_shorting
    ):
        raise RuntimeValidationError(
            f"{named} must deliver {-units} {underlying.symbol} and the book holds {held}; the "
            "run does not allow a short position, and none is created to make the delivery."
        )
    financed = False
    if units > 0 and economics.settlement.pays_notional:
        cost = ctx.multiply(ctx.multiply(units, unit_price), economics.multiplier)
        cash = state.portfolio.cash.balance(underlying.currency)
        if cash < cost and underlying.margin is None:
            raise RuntimeValidationError(
                f"{named} takes delivery of {units} {underlying.symbol} for {cost} "
                f"{underlying.currency}, and the book holds {cash}; a delivery is financed only "
                "when its deliverable declares margin terms (InstrumentRecord.margin), and "
                f"{underlying.symbol} declares none."
            )
        financed = cash < cost
    return underlying, economics, unit_price, financed


def _deliver(
    state: ExecutionPipelineState,
    contract: InstrumentRecord,
    deliverable: Deliverable,
    parts: Mapping[str, Decimal],
    units: Decimal,
    price: Decimal,
    multiplier: Decimal,
    timestamp: float,
    rates: FxRates,
    trade_id: str,
) -> ExecutionPipelineState:
    """Move ``units`` of the deliverable against ``price`` per unit of the contract's price."""

    underlying, economics, unit_price, financed = _require_deliverable(
        state, contract, deliverable, units, price, multiplier
    )
    current = _lifecycle_fill(
        state,
        deliverable.asset_id,
        units,
        unit_price,
        timestamp,
        underlying.currency,
        economics,
        parts,
        trade_id,
        financed=financed,
    )
    budget_value = _in_budget_currency(
        current,
        ACCOUNTING_CONTEXT.multiply(unit_price, economics.multiplier),
        underlying.currency,
        rates,
        timestamp,
    )
    current = evolve(
        current,
        allocation=AllocationEngine.apply_lifecycle(
            current.allocation, deliverable.asset_id, {}, deliverable.asset_id, parts, budget_value
        ),
    )
    # The delivered position is worth the market's price for it, when the run has
    # one, not the price it was delivered at.
    if deliverable.asset_id in current.market_prices:
        current = evolve(
            current,
            portfolio=PortfolioEngine.mark_changed(
                current.portfolio, current.market_prices, timestamp, deliverable.asset_id
            ),
        )
    return current


@dataclass(frozen=True, slots=True)
class MarginHolding:
    """One margined position's part of the account's margin, in the base currency (FUT-017).

    Attributes:
        asset_id: The position.
        quantity: Its signed quantity.
        terms: What its instrument declares.
        initial: What it requires at the initial rate.
        maintenance: What it requires at maintenance.
        unit_initial: What one unit of it requires at the initial rate.
        unit_collateral: What selling one unit adds to equity: its haircut on a
            long, fully paid position, nothing otherwise.
        unit_value: One unit's market value in the base currency.
        unit_maintenance: What one unit requires at maintenance.
        haircut_rate: The haircut a long holding of it takes as collateral:
            its terms' haircut when it pays its notional, nothing otherwise.
    """

    asset_id: str
    quantity: Decimal
    terms: MarginTerms
    initial: Decimal
    maintenance: Decimal
    unit_initial: Decimal
    unit_collateral: Decimal
    unit_value: Decimal = Decimal("0")
    unit_maintenance: Decimal = Decimal("0")
    haircut_rate: Decimal = Decimal("0")
    #: An option the account's model reprices: its facts, ``units`` per
    #: contract held (FUT-026); ``None`` otherwise.
    option: OptionRisk | None = None

    def exposure(self, quantity: Decimal | None = None) -> MarginExposure:
        """This position as a margin model reads it, at ``quantity`` units (its own by default).

        At its own quantity the figures are the ones its terms gave; at another
        they are the per-unit figures scaled, which is what a model is asked
        when a liquidation or an order is being sized (FUT-019).
        """

        held = self.quantity if quantity is None else quantity
        option = (
            None
            if self.option is None
            else replace(self.option, units=ACCOUNTING_CONTEXT.multiply(held, self.option.units))
        )
        if quantity is None or quantity == self.quantity:
            sign = Decimal("1") if self.quantity > 0 else Decimal("-1")
            value = ACCOUNTING_CONTEXT.multiply(
                sign, ACCOUNTING_CONTEXT.multiply(self.quantity.copy_abs(), self.unit_value)
            )
            return MarginExposure(self.asset_id, value, self.initial, self.maintenance, option)
        units = quantity.copy_abs()
        return MarginExposure(
            self.asset_id,
            ACCOUNTING_CONTEXT.multiply(quantity, self.unit_value),
            ACCOUNTING_CONTEXT.multiply(units, self.unit_initial),
            ACCOUNTING_CONTEXT.multiply(units, self.unit_maintenance),
            option,
        )

    def haircut(self, quantity: Decimal) -> Decimal:
        """What a holding of ``quantity`` is not counted for as collateral."""

        if quantity <= 0 or not self.haircut_rate:
            return Decimal("0")
        return ACCOUNTING_CONTEXT.multiply(
            ACCOUNTING_CONTEXT.multiply(quantity, self.unit_value), self.haircut_rate
        )


@dataclass(frozen=True, slots=True)
class MarginPosition:
    """The account's margin at one instant, in the base currency (FUT-017).

    Attributes:
        equity: Net asset value less each long position's haircut.
        initial: Every margined position's initial requirement, summed.
        maintenance: Every margined position's maintenance requirement, summed.
        holdings: The margined positions, in the order a liquidation reduces them.
        model: The account's declared margin model, or ``None``: each
            position's own requirement, summed (FUT-019).
    """

    equity: Decimal
    initial: Decimal
    maintenance: Decimal
    holdings: tuple[MarginHolding, ...]
    model: MarginModel | None = None

    def figures(
        self, quantities: Mapping[str, Decimal], added: tuple[MarginHolding, ...] = ()
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Equity, initial and maintenance were the margined positions ``quantities``.

        A position ``quantities`` does not name keeps its own; ``added`` are
        positions not held yet, at their own quantities. Equity moves only by
        the haircuts the changed holdings take -- a trade at the mark moves no
        net asset value -- and the requirement is the declared model's, or each
        position's own summed (FUT-019).
        """

        ctx = ACCOUNTING_CONTEXT
        equity = self.equity
        exposures: list[MarginExposure] = []
        held_ids = {id(holding) for holding in self.holdings}
        for holding in (*self.holdings, *added):
            held = holding.quantity if id(holding) in held_ids else Decimal("0")
            after = quantities.get(holding.asset_id, holding.quantity)
            equity = ctx.add(equity, ctx.subtract(holding.haircut(held), holding.haircut(after)))
            if after:
                exposures.append(holding.exposure(after))
        exposed = tuple(exposures)
        if self.model is None:
            return (
                equity,
                sum((e.initial for e in exposed), Decimal("0")),
                sum((e.maintenance for e in exposed), Decimal("0")),
            )
        required = self.model.requirement(exposed)
        return equity, required.initial, required.maintenance

    @property
    def in_breach(self) -> bool:
        """Whether equity is below the maintenance requirement: a margin call."""

        return self.equity < self.maintenance

    @property
    def deficit(self) -> Decimal:
        """What a liquidation restores: the initial requirement less the equity."""

        return max(Decimal("0"), self.initial - self.equity)


def margin_position(
    state: ExecutionPipelineState, rates: FxRates, as_of: float
) -> MarginPosition | None:
    """The account's margin at ``as_of``, or ``None`` when no position held declares margin.

    Each position whose instrument declares :class:`~alphalab.instrument.margin.MarginTerms`
    requires its initial and maintenance amounts on its own -- a rate of its
    absolute market value, or an amount per contract -- converted into the base
    currency by ``rates`` at ``as_of``. Margin equity is the book's net asset
    value less each long, fully paid position's haircut on its market value. A
    book holding no margined position has no margin; only the instruments the
    registry's index says declare margin are read, so a run declaring none
    costs one emptiness check (FUT-017).
    """

    registry = state.config.instruments
    if registry is None or not registry.margined:
        return None
    portfolio = state.portfolio
    base = portfolio.account.base_currency
    units = portfolio.account.currency_units
    ctx = ACCOUNTING_CONTEXT

    def in_base(amount: Decimal, currency: str) -> Decimal:
        if currency == base or not amount:
            return amount
        return rates.convert(amount, currency, base, as_of, units=units).converted

    model = registry.margin_model
    repriced = model.repriced if isinstance(model, ScenarioMargin) else None
    holdings: list[MarginHolding] = []
    haircuts = Decimal("0")
    # Only what the registry's index says declares margin is read: a book's other
    # positions cost nothing, however many there are.
    for asset_id in registry.margined:
        position = portfolio.positions.get(asset_id)
        record = _declared(state, asset_id)
        terms = None if record is None else record.margin
        if position is None or terms is None or position.quantity == 0:
            continue
        value = in_base(
            ctx.multiply(
                ctx.multiply(position.quantity.copy_abs(), position.market_price),
                position.multiplier,
            ),
            position.currency,
        )
        unit_value = ctx.divide(value, position.quantity.copy_abs())
        if terms.basis is MarginBasis.VALUE_RATE:
            initial = terms.requirement(position.quantity, value, initial=True)
            maintenance = terms.requirement(position.quantity, value, initial=False)
            unit_initial = ctx.multiply(unit_value, terms.initial)
        else:
            initial = in_base(terms.requirement(position.quantity, value, True), position.currency)
            maintenance = in_base(
                terms.requirement(position.quantity, value, False), position.currency
            )
            unit_initial = in_base(terms.initial, position.currency)
        unit_maintenance = (
            ctx.multiply(unit_value, terms.maintenance)
            if terms.basis is MarginBasis.VALUE_RATE
            else in_base(terms.maintenance, position.currency)
        )
        haircut_rate = terms.haircut if position.pays_notional else Decimal("0")
        option = (
            _option_risk(state, record, position.currency, in_base, as_of)
            if repriced and asset_id in repriced
            else None
        )
        collateral = Decimal("0")
        if position.quantity > 0 and position.pays_notional and terms.haircut:
            haircuts += ctx.multiply(value, terms.haircut)
            collateral = ctx.multiply(unit_value, terms.haircut)
        holdings.append(
            MarginHolding(
                asset_id,
                position.quantity,
                terms,
                initial,
                maintenance,
                unit_initial,
                collateral,
                unit_value,
                unit_maintenance,
                haircut_rate,
                option,
            )
        )
    if not holdings:
        return None
    if portfolio.unsettled:
        # Units an exchange owes and has not delivered are the holder's, and
        # valued, but not collateral unless their convention says (FUT-037).
        for entry in portfolio.unsettled:
            if (
                entry.delivery is None
                or not entry.pending_at(as_of)
                or "collateral" in entry.delivery.split(",")
            ):
                continue
            owed = portfolio.positions.get(entry.asset_id)
            if owed is None or entry.quantity <= 0:
                continue
            haircuts += in_base(
                ctx.multiply(ctx.multiply(entry.quantity, owed.market_price), owed.multiplier),
                owed.currency,
            )
    nav = NAVCalculator.calculate(portfolio.cash, portfolio.positions, base, rates, as_of)
    holdings.sort(key=lambda h: (h.terms.priority, -h.initial, h.asset_id))
    if model is None:
        initial_total = sum((h.initial for h in holdings), Decimal("0"))
        maintenance_total = sum((h.maintenance for h in holdings), Decimal("0"))
    else:
        # The account's declared model reads every margined position at once
        # (FUT-019); each holding keeps its own figures for liquidation order.
        required = model.requirement(tuple(h.exposure() for h in holdings))
        initial_total, maintenance_total = required.initial, required.maintenance
    return MarginPosition(
        equity=ctx.subtract(nav, haircuts),
        initial=initial_total,
        maintenance=maintenance_total,
        holdings=tuple(holdings),
        model=model,
    )


def _option_risk(
    state: ExecutionPipelineState,
    record: InstrumentRecord | None,
    currency: str,
    in_base: Callable[[Decimal, str], Decimal],
    as_of: float,
) -> OptionRisk | None:
    """A repriced option's facts at ``as_of``, ``units`` per contract, or ``None`` (FUT-026).

    Its terms in effect now and its underlying's price now. Until the run has
    priced the underlying there is nothing to value the option from, and it is
    margined by its own terms, offsetting nothing -- never at a guessed price.
    """

    terms = None if record is None else record.terms
    assert record is not None and isinstance(terms, OptionTerms)
    effective = terms.effective(as_of)
    underlying = effective.deliverable.asset_id
    price = state.market_prices.get(underlying)
    if price is None:
        return None
    return OptionRisk(
        underlying,
        price,
        effective.strike,
        effective.option_type,
        effective.exercise_style,
        effective.expires_at,
        as_of,
        effective.deliverable.units,
        in_base(Decimal("1"), currency),
    )


def _increases(state: ExecutionPipelineState, request: OrderRequest) -> bool:
    """Whether ``request`` would open a position, increase one's size, or reverse it."""

    return _growth_of(state, request.asset_id, _signed(request)) > 0


def _signed(request: OrderRequest) -> Decimal:
    return request.quantity if request.side is CoreSide.BUY else -request.quantity


def _growth_of(state: ExecutionPipelineState, asset_id: str, signed: Decimal) -> Decimal:
    """Units of new exposure ``signed`` adds to the filled position: as the risk gate counts."""

    position = state.portfolio.positions.get(asset_id)
    held = Decimal("0") if position is None else position.quantity
    after = ACCOUNTING_CONTEXT.add(held, signed)
    if (held > 0 > after) or (held < 0 < after):
        return after.copy_abs()
    return max(Decimal("0"), ACCOUNTING_CONTEXT.subtract(after.copy_abs(), held.copy_abs()))


def _unit_margin(
    state: ExecutionPipelineState,
    asset_id: str,
    terms: MarginTerms,
    unit_value: Decimal,
    rates: FxRates,
    at: float,
) -> Decimal:
    """What one unit of ``asset_id`` requires at the initial rate, in the base currency.

    ``unit_value`` is one unit's value in the base currency, multiplier included.
    """

    if terms.basis is MarginBasis.VALUE_RATE:
        return ACCOUNTING_CONTEXT.multiply(unit_value.copy_abs(), terms.initial)
    record = _declared(state, asset_id)
    currency = state.portfolio.account.base_currency if record is None else record.currency
    base = state.portfolio.account.base_currency
    if currency == base:
        return terms.initial
    units = state.portfolio.account.currency_units
    return rates.convert(terms.initial, currency, base, at, units=units).converted


def _buying_power_charge(
    state: ExecutionPipelineState,
    asset_id: str,
    unit_value: Decimal,
    rates: FxRates,
    at: float,
    buying: bool = False,
) -> Decimal | None:
    """What a unit of growth in ``asset_id`` spends of buying power, when not its value (FUT-017).

    An instrument that pays no notional -- a future settled by variation --
    moves no cash when it trades: what opening it commits is the initial margin
    its record declares, not the notional, which the gate charged until v4.2.0.
    A fully paid instrument whose terms allow a purchase on margin
    (``MarginTerms.purchase_on_margin``, FUT-018) spends its initial requirement
    when ``buying``: what the cash does not cover is financed. ``None`` --
    charge the notional, as always -- otherwise: a short sale, a fully paid
    purchase, an instrument declaring no margin terms; a registry declaring none
    costs one emptiness check.
    """

    registry = state.config.instruments
    if registry is None or not registry.margined or asset_id not in registry.margined:
        return None
    record = _declared(state, asset_id)
    if record is None or record.margin is None:
        return None
    economics = _economics_of(state, asset_id)
    pays = economics is None or economics.settlement.pays_notional
    if pays and not (buying and record.margin.purchase_on_margin):
        return None
    if registry.margin_model is not None:
        # Under a declared margin model what such an order commits is the
        # model's to say, and the model's own checks gate it -- the pre-trade
        # initial requirement and the risk gate's margin figure -- so its own
        # requirement is not charged again here, offset-blind (FUT-019).
        return Decimal("0")
    return _unit_margin(state, asset_id, record.margin, unit_value, rates, at)


def _currency_cash_refusal(
    state: ExecutionPipelineState, request: OrderRequest, at: float
) -> str | None:
    """Why a purchase's own settlement currency cannot pay for it, or ``None`` (ACC-017).

    Read only in a book settling more than one currency -- in one, buying power
    is that currency's cash and the risk gate holds it -- and only for a purchase
    paying its notional in full: not one covering a short, which is financed past
    the cash (FUT-017), nor one bought on margin (FUT-018). Its cost at the
    request's price must be within the currency's cash less what is reserved,
    less proceeds not yet received, less every buy already working in it; cash in
    another currency is not converted to pay it (ADR-0035).
    """

    if request.side is not CoreSide.BUY or len(state.config.settlement_currencies) < 2:
        return None
    asset_id = request.asset_id
    economics = _economics_of(state, asset_id)
    if economics is not None and not economics.settlement.pays_notional:
        return None
    position = state.portfolio.positions.get(asset_id)
    if position is not None and position.quantity < 0:
        return None  # a cover, financed past the cash
    if _purchased_on_margin(state, asset_id, request.quantity):
        return None
    currency = _settlement_currency_for(state, asset_id)
    ctx = ACCOUNTING_CONTEXT
    multiplier = _multiplier(economics)
    cost = ctx.multiply(ctx.multiply(request.quantity, request.price), multiplier)
    cash = state.portfolio.cash
    available = ctx.subtract(cash.balance(currency), cash.reserved.get(currency, Decimal("0")))
    for entry in state.portfolio.unsettled:
        if entry.currency == currency and entry.cash > 0 and entry.pending_at(at):
            available = ctx.subtract(available, entry.cash)
    for order in _working_buys(state):
        if _settlement_currency_for(state, order.asset_id) != currency:
            continue
        unit = _multiplier(_economics_of(state, order.asset_id))
        price = order.limit_price or state.market_prices.get(order.asset_id, Decimal("0"))
        available = ctx.subtract(
            available, ctx.multiply(ctx.multiply(order.remaining_quantity, price), unit)
        )
    if cost <= available:
        return None
    return (
        f"The purchase costs {cost} {currency} and the book can pay {max(available, Decimal(0))} "
        f"{currency}: a fill settles in its own currency, and cash in another is not converted "
        "to pay it. Fund the currency first (Funding, RunEngine.fund)."
    )


def _working_buys(state: ExecutionPipelineState) -> tuple[OMSOrder, ...]:
    """Every buy order working, in the order the OMS keeps them."""

    out: list[OMSOrder] = []
    for order_id in state.oms.active_orders:
        order = state.oms.orders.find(order_id)
        if order.side is OMSSide.BUY:
            out.append(order)
    return tuple(out)


def _purchased_on_margin(state: ExecutionPipelineState, asset_id: str, quantity: Decimal) -> bool:
    """Whether a fill buying ``quantity`` is booked as a financed debit (FUT-018)."""

    if quantity <= 0:
        return False
    registry = state.config.instruments
    if registry is None or not registry.margined or asset_id not in registry.margined:
        return False
    record = _declared(state, asset_id)
    return record is not None and record.margin is not None and record.margin.purchase_on_margin


def _borrow_refusal(state: ExecutionPipelineState, request: OrderRequest, at: float) -> str | None:
    """Why ``request`` cannot be borrowed for, or ``None`` (FUT-018).

    Read only for an order that would grow a short in an instrument declaring
    borrow terms: the book's short after it -- filled, every working order in
    the instrument and this one -- must be within what the entry in effect at
    ``at`` makes available. Before the first entry nothing is available.
    """

    record = _declared(state, request.asset_id)
    if record is None or record.borrow is None:
        return None
    terms = record.borrow
    position = state.portfolio.positions.get(request.asset_id)
    committed = Decimal("0") if position is None else position.quantity
    for order_id in state.oms.working_orders_for(request.asset_id):
        order = state.oms.orders.find(order_id)
        remaining = order.remaining_quantity
        committed = ACCOUNTING_CONTEXT.add(
            committed, remaining if order.side is OMSSide.BUY else -remaining
        )
    after = ACCOUNTING_CONTEXT.add(committed, _signed(request))
    if after >= 0 or (after.copy_abs() <= committed.copy_abs() and committed < 0):
        return None
    entry = terms.in_effect(at)
    if entry is None:
        return (
            f"No borrow is in effect for {record.symbol} at {at!r} (its borrow terms start "
            f"at {terms.entries[0].effective_at!r}); nothing can be sold short."
        )
    if entry.available is not None and after.copy_abs() > entry.available:
        return (
            f"Selling short would leave the book short {after.copy_abs()} {record.symbol}, "
            f"and {entry.available} is available to borrow at {at!r}."
        )
    if entry.allocations:
        return _located_refusal(state, request, entry, record.symbol, at)
    return None


def _strategy_shorts(state: ExecutionPipelineState, asset_id: str) -> dict[str, Decimal]:
    """Each strategy's own holding of ``asset_id``: its share, and its share of what is working."""

    held = dict(AllocationEngine.shares_of(state.allocation, asset_id))
    for order_id in state.oms.working_orders_for(asset_id):
        order = state.oms.orders.find(order_id)
        contributions = state.allocation.contributions.get(str(order_id.value), ())
        signed = (
            order.remaining_quantity if order.side is OMSSide.BUY else -order.remaining_quantity
        )
        for strategy_id, part in split_by_contribution(signed, contributions, _ATTRIBUTION_QUANTUM):
            held[strategy_id] = ACCOUNTING_CONTEXT.add(held.get(strategy_id, Decimal("0")), part)
    return held


def _drawn(held: Mapping[str, Decimal], entry: BorrowAvailability) -> Decimal:
    """What the strategies short beyond their own allocations draw on the shared borrow."""

    return sum(
        (max(Decimal("0"), -h - entry.allocated(sid)) for sid, h in held.items() if h < 0),
        Decimal("0"),
    )


@dataclass(frozen=True, slots=True)
class BorrowClaim:
    """One strategy's claim on an instrument's borrow at one instant (FUT-035, phase 4).

    Attributes:
        strategy_id: The strategy.
        short: Its own short, filled and working, as a positive number of units.
        located: What the lender located for it; zero where none is.
        drawn: What it draws beyond that on the borrow the lender shares.
        share: Its fraction of the strategies' shorts -- what a fee on the
            book's borrow is attributed to it by.
    """

    strategy_id: str
    short: Decimal
    located: Decimal
    drawn: Decimal
    share: Decimal


def borrow_claims(
    state: ExecutionPipelineState, asset_id: str, at: float
) -> tuple[BorrowClaim, ...]:
    """Each short strategy's claim on ``asset_id``'s borrow at ``at`` (FUT-035, phase 4).

    By the borrow entry in effect then: under one locating per strategy, what is
    located for each and what each draws on what is shared; under the book's
    borrow, every short drawing on it. Ordered by strategy; empty for an
    instrument with no declared borrow or no short strategy.
    """

    record = _declared(state, asset_id)
    entry = None if record is None or record.borrow is None else record.borrow.in_effect(at)
    if record is None or record.borrow is None:
        return ()
    held = _strategy_shorts(state, asset_id)
    shorts = {sid: -h for sid, h in sorted(held.items()) if h < 0}
    total = sum(shorts.values(), Decimal("0"))
    ctx = ACCOUNTING_CONTEXT
    claims = []
    for sid, short in shorts.items():
        located = Decimal("0") if entry is None else entry.allocated(sid)
        claims.append(
            BorrowClaim(
                sid,
                short,
                located,
                max(Decimal("0"), ctx.subtract(short, located)),
                ctx.divide(short, total),
            )
        )
    return tuple(claims)


def _located_refusal(
    state: ExecutionPipelineState,
    request: OrderRequest,
    entry: BorrowAvailability,
    symbol: str,
    at: float,
) -> str | None:
    """Why a strategy's short would draw more than the shared borrow left, or ``None`` (FUT-035).

    Borrow located per strategy: each may be short its own allocation, and
    beyond it draws on what the lender left shared -- first come, so a short
    already held keeps its draw, and a unit is never claimed twice.
    """

    held = _strategy_shorts(state, request.asset_id)
    before = _drawn(held, entry)
    for contribution in request.contributions:
        held[contribution.strategy_id] = ACCOUNTING_CONTEXT.add(
            held.get(contribution.strategy_id, Decimal("0")), contribution.quantity
        )
    after = _drawn(held, entry)
    shared = entry.shared
    assert shared is not None  # allocations are of a stated availability
    if after <= before or after <= shared:
        return None
    growing = sorted(c.strategy_id for c in request.contributions if c.quantity < 0)
    return (
        f"Selling short would leave {', '.join(growing)} short beyond the {symbol} located for "
        f"it at {at!r}, drawing {after} on the {shared} the lender shares between strategies."
    )


def _initial_margin_refusal(
    state: ExecutionPipelineState, request: OrderRequest, rates: FxRates, at: float
) -> str | None:
    """Why ``request`` would leave equity below the initial requirement, or ``None`` (FUT-017).

    Read only for an order growing a position in an instrument that declares
    margin terms. The requirement counts the margined positions held, every
    working order growing one, and this order's growth, each at its current
    price; equity is the account's margin equity, less the haircut a long,
    fully paid purchase adds to it.
    """

    registry = state.config.instruments
    if registry is None or not registry.margined or request.asset_id not in registry.margined:
        return None
    record = _declared(state, request.asset_id)
    if record is None or record.margin is None:
        return None
    if registry.margin_model is not None:
        projected = _projected_margin(state, request, rates, at)
        if projected is None or projected[0] >= projected[1]:
            return None
        return (
            f"The order would leave the account below its initial margin -- equity "
            f"{projected[0]} against {projected[1]} required under the account's margin model "
            "-- and an order growing a margined position trades only within it."
        )
    ctx = ACCOUNTING_CONTEXT
    margin = margin_position(state, rates, at)
    if margin is None:
        portfolio = state.portfolio
        equity = NAVCalculator.calculate(
            portfolio.cash, portfolio.positions, portfolio.account.base_currency, rates, at
        )
        initial = Decimal("0")
    else:
        equity, initial = margin.equity, margin.initial

    def unit_value(asset_id: str) -> Decimal | None:
        price = state.market_prices.get(asset_id)
        if price is None:
            return None
        return _unit_value(
            state, asset_id, _price_in_base(state, asset_id, price, rates, at)
        ).copy_abs()

    committed = Decimal("0")
    for asset_id in registry.margined:
        margined = _declared(state, asset_id)
        terms = None if margined is None else margined.margin
        value = unit_value(asset_id)
        if terms is None or value is None:
            continue
        for order_id in state.oms.working_orders_for(asset_id):
            order = state.oms.orders.find(order_id)
            remaining = order.remaining_quantity
            growth = _growth_of(
                state, asset_id, remaining if order.side is OMSSide.BUY else -remaining
            )
            if growth:
                committed = ctx.add(
                    committed,
                    ctx.multiply(growth, _unit_margin(state, asset_id, terms, value, rates, at)),
                )
    value = unit_value(request.asset_id)
    if value is None:
        return None  # refused as unpriced, after this
    growth = _growth_of(state, request.asset_id, _signed(request))
    added = ctx.multiply(
        growth, _unit_margin(state, request.asset_id, record.margin, value, rates, at)
    )
    economics = _economics_of(state, request.asset_id)
    pays = economics is None or economics.settlement.pays_notional
    held = state.portfolio.positions.get(request.asset_id)
    after_long = request.side is CoreSide.BUY and (held is None or held.quantity >= 0)
    if pays and after_long and record.margin.haircut:
        equity = ctx.subtract(
            equity, ctx.multiply(ctx.multiply(growth, value), record.margin.haircut)
        )
    required = ctx.add(ctx.add(initial, committed), added)
    if equity >= required:
        return None
    return (
        f"The order would leave the account below its initial margin -- equity {equity} "
        f"against {required} required, {added} of it this order's -- and an order growing a "
        "margined position trades only within it."
    )


def _reducing_order_working(state: ExecutionPipelineState, margin: MarginPosition) -> bool:
    """Whether a market order reducing a margined position is working -- a liquidation in progress.

    Read off the OMS's working orders, which a snapshot keeps whatever retention
    trims, so a liquidation is never placed twice for one call: a strategy's own
    market order reducing the position, trading at its next turn, defers it the
    same way. A resting limit or stop is a price the strategy waits for and does
    not (ACC-016: until the phase 3 gap audit any reducing order deferred it).
    """

    for holding in margin.holdings:
        for order_id in state.oms.working_orders_for(holding.asset_id):
            order = state.oms.orders.find(order_id)
            if order.order_type is OrderType.MARKET and (order.side is OMSSide.SELL) == (
                holding.quantity > 0
            ):
                return True
    return False


def _margin_call(
    state: ExecutionPipelineState, event: MarketEvent, rates: FxRates
) -> tuple[ExecutionPipelineState, _Routed]:
    """Call margin and liquidate, when the account is below maintenance at ``event`` (FUT-017).

    A liquidation reduces the margined positions in their declared order --
    lowest priority first, then the largest requirement, then the asset -- by
    the least that restores equity to the initial requirement, whole lots of
    each where the run trades them, each holding strategy reducing its own share.
    It is placed as each strategy's own target through allocation, risk and the
    OMS, as a market order resting to its asset's next event, so it fills by the
    run's fill policy at a price the market then shows -- in part, or not at
    all, as the market allows. A call that finds a liquidation already working
    places nothing; one whose positions cannot be sold -- unpriced -- is
    recorded with no orders. Each round is a
    :class:`~alphalab.risk.events.MarginCall` on the risk log.
    """

    at = event.timestamp
    margin = margin_position(state, rates, at)
    if margin is None or not margin.in_breach or _reducing_order_working(state, margin):
        return state, _NO_ROUTING
    ctx = ACCOUNTING_CONTEXT
    reductions = (
        _per_position_reductions(state, margin)
        if margin.model is None
        else _model_reductions(state, margin)
    )
    intents: list[Intent] = []
    for holding, quantity in reductions:
        shares = _holding_shares(state, holding)
        sign = Decimal("1") if holding.quantity > 0 else Decimal("-1")
        parts = _strategy_parts(shares, ctx.multiply(sign, quantity))
        for sid, share in shares:
            part = parts.get(sid, Decimal("0"))
            if part:
                intents.append(
                    Intent(
                        sid,
                        holding.asset_id,
                        _forced_target(state, sid, holding.asset_id, share, -part),
                        timestamp=at,
                        kind=IntentKind.TARGET_QUANTITY,
                        metadata={"margin_call": repr(at)},
                    )
                )
    current = state
    routed = _NO_ROUTING
    if intents:
        batch = tuple(intents)
        allocation, requests = _allocate(current, batch, current.market_prices, rates, at)
        current = evolve(current, allocation=allocation)
        current, routed = _route_requests(
            current,
            event,
            batch,
            requests,
            StaticFill(FillStatus.FULL_FILL, None),
            rates,
            rest=True,
            at=at,
        )
    called = MarginCall(
        str(new_id()),
        at,
        equity=margin.equity,
        initial_requirement=margin.initial,
        maintenance_requirement=margin.maintenance,
        deficit=margin.deficit,
        orders=tuple(str(order.order_id.value) for order in routed.orders),
    )
    risk = current.risk
    return evolve(current, risk=evolve(risk, events=risk.events.append(called))), routed


def _buy_ins(
    state: ExecutionPipelineState, event: MarketEvent, rates: FxRates
) -> tuple[ExecutionPipelineState, _Routed]:
    """Cover each recalled short beyond what its lender makes available (FUT-022).

    Read only for the instruments whose borrow terms declare a recall, so a run
    declaring none costs one emptiness test. Once the entry in effect has passed
    its buy-in deadline, a short beyond its availability is covered -- the
    excess, rounded up to whole lots where declared or whole units where the
    run trades them, never more than is short -- each short strategy covering
    its own share, as its own target through allocation, risk and the OMS, a
    market order resting to the asset's next event. It fills as the market
    allows: in a halt or outside a band it rests, with too little liquidity it
    fills in part and what it leaves is bought in at the next step. A buy-in
    already working places nothing, so a recall is never covered twice. Each
    round is a :class:`~alphalab.risk.events.BuyIn` on the risk log.
    """

    registry = state.config.instruments
    if registry is None or not registry.recallable:
        return state, _NO_ROUTING
    at = event.timestamp
    ctx = ACCOUNTING_CONTEXT
    current, routed = state, _NO_ROUTING
    for asset_id in registry.recallable:
        position = current.portfolio.positions.get(asset_id)
        if position is None or position.quantity >= 0:
            continue
        record = _declared(current, asset_id)
        assert record is not None and record.borrow is not None
        entry = record.borrow.in_effect(at)
        if entry is None or entry.buy_in_at is None or entry.buy_in_at > at:
            continue
        assert entry.available is not None
        short = position.quantity.copy_abs()
        held = _strategy_shorts(current, asset_id) if entry.allocations else {}
        if entry.allocations:
            # Located per strategy (FUT-035): what the strategies short beyond
            # their own draw on the shared borrow beyond what it holds.
            shared = entry.shared
            assert shared is not None
            account_excess = max(Decimal("0"), ctx.subtract(_drawn(held, entry), shared))
        else:
            account_excess = ctx.subtract(short, entry.available)
        if account_excess <= 0 or _covering_order_working(current, asset_id):
            continue
        excess = account_excess
        step = _trading_step(current, asset_id, position.quantity)
        if step is not None:
            excess = ctx.multiply(
                ctx.divide(excess, step).to_integral_value(rounding=decimal.ROUND_CEILING), step
            )
        excess = min(excess, short)
        shares = tuple(
            (sid, share)
            for sid, share in AllocationEngine.shares_of(current.allocation, asset_id)
            if share < 0
        )
        placed: tuple[str, ...] = ()
        if shares and asset_id in current.market_prices:
            if entry.allocations:
                # Each strategy covers its own need beyond its allocation, in
                # proportion, never one within its own.
                needs = tuple(
                    (sid, -max(Decimal("0"), -h - entry.allocated(sid)))
                    for sid, h in sorted(held.items())
                    if h < 0 and -h > entry.allocated(sid)
                )
                parts = _strategy_parts(needs, -excess)
            else:
                parts = _strategy_parts(shares, -excess)
            batch = tuple(
                Intent(
                    sid,
                    asset_id,
                    _forced_target(current, sid, asset_id, share, -parts[sid]),
                    timestamp=at,
                    kind=IntentKind.TARGET_QUANTITY,
                    metadata={"buy_in": repr(at)},
                )
                for sid, share in shares
                if parts.get(sid)
            )
            allocation, requests = _allocate(current, batch, current.market_prices, rates, at)
            current = evolve(current, allocation=allocation)
            current, step_routed = _route_requests(
                current,
                event,
                batch,
                requests,
                StaticFill(FillStatus.FULL_FILL, None),
                rates,
                rest=True,
                at=at,
            )
            routed = routed.then(step_routed)
            placed = tuple(str(order.order_id.value) for order in step_routed.orders)
            if entry.buy_in_premium is not None or entry.buy_in_fee is not None:
                # The lender's charge is booked with each fill of the cover
                # (FUT-035): the order carries the buy-in it is.
                current = _marked_buy_ins(current, step_routed.orders, at)
        bought = BuyIn(
            str(new_id()),
            at,
            asset_id=asset_id,
            short=short,
            available=entry.available,
            excess=excess,
            orders=placed,
        )
        risk = current.risk
        current = evolve(current, risk=evolve(risk, events=risk.events.append(bought)))
    return current, routed


def _forced_target(
    state: ExecutionPipelineState, strategy_id: str, asset_id: str, share: Decimal, trade: Decimal
) -> Decimal:
    """The target that makes allocation trade exactly ``trade`` for a forced reduction.

    A liquidation and a buy-in move what the account requires, whatever the
    strategy has resting: allocation measures a target against the position and
    the strategy's working orders, so the target names both, and the order is
    ``trade`` -- never absorbed by a resting order, and never read through the
    run's sizing model, as a delta would be (ACC-016).
    """

    probe = Intent(strategy_id, asset_id, Decimal("0"), kind=IntentKind.TARGET_QUANTITY)
    working = _working_shares(state, (probe,)).get((strategy_id, asset_id), Decimal("0"))
    return ACCOUNTING_CONTEXT.add(ACCOUNTING_CONTEXT.add(share, working), trade)


#: The order metadata key naming the buy-in an order covers, by its instant (FUT-035).
BUY_IN: Final = "buy_in"


def _marked_buy_ins(
    state: ExecutionPipelineState, orders: tuple[OMSOrder, ...], at: float
) -> ExecutionPipelineState:
    """``state`` with each of ``orders`` marked as the buy-in at ``at`` it is."""

    oms = state.oms
    for placed in orders:
        order = oms.orders.find(placed.order_id)
        marked = replace(order, metadata={**order.metadata, BUY_IN: repr(at)})
        oms = evolve(oms, orders=oms.orders.replace(marked))
    return evolve(state, oms=oms)


def _buy_in_charges(
    state: ExecutionPipelineState, order: OMSOrder, report: ExecutionReport
) -> ExecutionPipelineState:
    """The lender's premium on one fill of a buy-in, and its fee on the first (FUT-035).

    By the borrow entry in effect at the buy-in's instant, in the instrument's
    currency, as ``FEE`` cash flows -- one for each strategy the cover was
    placed for, in proportion to its part, the reference naming it.
    """

    record = _declared(state, order.asset_id)
    assert record is not None and record.borrow is not None
    entry = record.borrow.in_effect(float(order.metadata[BUY_IN]))
    if entry is None:
        return state
    ctx = ACCOUNTING_CONTEXT
    value = _unit_value(
        state, order.asset_id, ctx.multiply(report.fill_quantity, report.fill_price)
    ).copy_abs()
    charge = Decimal("0")
    if entry.buy_in_premium is not None:
        charge = ctx.add(charge, ctx.multiply(value, entry.buy_in_premium))
    if entry.buy_in_fee is not None and order.filled_quantity == 0:
        charge = ctx.add(charge, entry.buy_in_fee)
    if charge == 0:
        return state
    contributions = state.allocation.contributions.get(str(order.order_id.value), ())
    parts = split_by_contribution(charge, contributions, Decimal("0.01")) or (
        (order.strategy_id, charge),
    )
    portfolio = state.portfolio
    for strategy_id, part in parts:
        if part == 0:
            continue
        flow = CashFlow(
            CashFlowKind.FEE, -part, report.currency, order.asset_id, f"buy-in:{strategy_id}"
        )
        portfolio = PortfolioEngine.apply_cash_flow(portfolio, flow, report.timestamp)
    return evolve(state, portfolio=portfolio)


def _covering_order_working(state: ExecutionPipelineState, asset_id: str) -> bool:
    """Whether a market buy is working in ``asset_id``: a buy-in, or a cover trading now.

    A market order only: a resting limit or stop buy is a price the strategy
    waits for, and does not stand in for a buy-in its lender requires now.
    """

    for order_id in state.oms.working_orders_for(asset_id):
        order = state.oms.orders.find(order_id)
        if order.side is OMSSide.BUY and order.order_type is OrderType.MARKET:
            return True
    return False


def _trading_step(
    state: ExecutionPipelineState, asset_id: str, quantity: Decimal
) -> Decimal | None:
    """What a forced trade moves in: whole lots where declared, whole units where the book is."""

    economics = _economics_of(state, asset_id)
    lot = None if economics is None else economics.lot
    whole = _whole_units(state) or quantity == quantity.to_integral_value()
    return lot.lot_size if lot is not None else (Decimal("1") if whole else None)


def _venue_blocks(state: ExecutionPipelineState, asset_id: str, at: float, price: Decimal) -> bool:
    """Whether the venue's declared controls stop a trade in ``asset_id`` at ``price`` (FUT-020).

    Halted -- the instrument, or its listing venue -- or ``price`` outside the
    band in force. A registry declaring no controls costs one test.
    """

    registry = state.config.instruments
    if registry is None or registry.controls is None:
        return False
    controls = registry.controls
    record = _declared(state, asset_id)
    venue = "" if record is None else record.exchange
    if controls.halt(asset_id, venue, at) is not None:
        return True
    if state.execution.reopened:
        # An auction extended because its price was outside the band: nothing
        # trades until it is held again (FUT-033).
        reopening = state.execution.reopened.get(asset_id)
        extended_to = None if reopening is None else reopening.extended_to
        if extended_to is not None and at < extended_to:
            return True
    # A band rule with no current reference: the venue's limits are unknown,
    # so nothing trades until one is published (FUT-028).
    if controls.unbanded(asset_id, at) is not None:
        return True
    band = controls.band(asset_id, at, None if record is None else record.tick)
    return band is not None and not band.admits(price)


def _band_terms(
    state: ExecutionPipelineState, request: OrderRequest, at: float
) -> tuple[str | None, OrderRequest]:
    """A limit priced beyond the band in force, refused or capped as it declares (FUT-020).

    Returns why the request is refused, or ``None`` and the request -- its limit
    moved to the band's edge when the band caps. A market order, and an order
    in an instrument with no band in force, is returned as it is.
    """

    registry = state.config.instruments
    if registry is None or registry.controls is None:
        return None, request
    limit = request.terms.limit_price
    if limit is None:
        return None, request
    declared = _declared(state, request.asset_id)
    band = registry.controls.band(request.asset_id, at, None if declared is None else declared.tick)
    if band is None or band.admits(limit):
        return None, request
    if band.beyond is BandAction.REFUSE:
        return (
            f"The limit {limit} is outside the venue's price band in force at {at!r} "
            f"({band.lower} to {band.upper}), and the venue refuses an order priced beyond it."
        ), request
    edge = band.upper if limit > band.upper else band.lower
    record = _declared(state, request.asset_id)
    if record is not None and record.tick is not None and edge >= 0:
        # A cap lands on the grid inside the band: a buy capped at the limit up
        # rounds down, a sell at the limit down rounds up.
        inward = RoundingDirection.DOWN if edge == band.upper else RoundingDirection.UP
        edge = round_to_tick(edge, record.tick.tick_size_at(edge), inward)
    return None, replace(request, terms=replace(request.terms, limit_price=edge))


def _tick_refusal(state: ExecutionPipelineState, request: OrderRequest) -> str | None:
    """Why ``request``'s limit or stop is off its venue's declared grid, or ``None``.

    Read only for a priced order in an instrument declaring a tick schedule; a
    negative price is refused, since no band of a published grid covers one.
    """

    terms = request.terms
    if terms.limit_price is None and terms.stop_price is None:
        return None
    record = _declared(state, request.asset_id)
    if record is None or record.tick is None:
        return None
    for price, what in ((terms.limit_price, "limit"), (terms.stop_price, "stop")):
        if price is None:
            continue
        if price < 0:
            return (
                f"The {what} {price} is negative, and {record.symbol}'s declared price grid "
                "covers no negative price."
            )
        tick = record.tick.tick_size_at(price)
        if not is_on_tick(price, tick):
            return (
                f"The {what} {price} is not on {record.symbol}'s price grid: the venue quotes "
                f"it in steps of {tick} there, and refuses a price between them."
            )
    return None


def _holding_shares(
    state: ExecutionPipelineState, holding: MarginHolding
) -> tuple[tuple[str, Decimal], ...]:
    """The strategies holding ``holding``'s side, and their shares: who a liquidation reduces."""

    return tuple(
        (sid, share)
        for sid, share in AllocationEngine.shares_of(state.allocation, holding.asset_id)
        if share and (share > 0) == (holding.quantity > 0)
    )


def _liquidation_step(state: ExecutionPipelineState, holding: MarginHolding) -> Decimal | None:
    """What a liquidation sells in: whole lots where declared, whole units where the book is."""

    return _trading_step(state, holding.asset_id, holding.quantity)


def _per_position_reductions(
    state: ExecutionPipelineState, margin: MarginPosition
) -> list[tuple[MarginHolding, Decimal]]:
    """How much of each position a liquidation sells when each is margined on its own (FUT-017).

    Each unit sold frees its initial requirement and the haircut it held back,
    so the least that restores the initial requirement is the deficit over
    that, rounded up to what the book trades in -- whole lots where the
    instrument declares them, whole units where the run trades them or the
    position is held in them.
    """

    ctx = ACCOUNTING_CONTEXT
    remaining = margin.deficit
    reductions: list[tuple[MarginHolding, Decimal]] = []
    for holding in margin.holdings:
        if remaining <= 0:
            break
        if holding.asset_id not in state.market_prices:
            continue
        improvement = ctx.add(holding.unit_initial, holding.unit_collateral)
        if improvement <= 0:
            continue
        needed = ctx.divide(remaining, improvement)
        step = _liquidation_step(state, holding)
        if step is not None:
            needed = ctx.multiply(
                ctx.divide(needed, step).to_integral_value(rounding=decimal.ROUND_CEILING), step
            )
        quantity = min(needed, holding.quantity.copy_abs())
        if quantity <= 0 or not _holding_shares(state, holding):
            continue
        reductions.append((holding, quantity))
        remaining = ctx.subtract(remaining, ctx.multiply(quantity, improvement))
    return reductions


#: The finest a liquidation divides a position held in fractions of a unit.
_FRACTIONAL_STEP: Final = Decimal("0.000001")


#: How many passes over the positions a liquidation under a model takes at most:
#: each pass only lowers the deficit, so it ends; a call still short calls again.
_LIQUIDATION_PASSES: Final = 4


def _model_reductions(
    state: ExecutionPipelineState, margin: MarginPosition
) -> list[tuple[MarginHolding, Decimal]]:
    """How much of each position a liquidation sells under the declared margin model (FUT-019).

    Coordinate descent over the positions in their liquidation order: each is
    reduced by the quantity that lowers the deficit most -- or, when some
    quantity restores the initial requirement, by the least that does -- in the
    units the book trades in, and left alone when no reduction lowers it, as a
    hedge leg whose sale alone would raise the requirement is. Under a scenario
    model the deficit is convex in one position's size, so the best quantity is
    found by ternary search and the least restoring one by bisection below it. A
    pass that lowers nothing ends the search; what it leaves short, the next
    step's call takes up from the book as it then is.
    """

    ctx = ACCOUNTING_CONTEXT
    quantities: dict[str, Decimal] = {}
    sold: dict[str, Decimal] = {}

    def deficit() -> Decimal:
        equity, initial, _ = margin.figures(quantities)
        return ctx.subtract(initial, equity)

    candidates = [
        holding
        for holding in margin.holdings
        if holding.asset_id in state.market_prices and _holding_shares(state, holding)
    ]
    for _ in range(_LIQUIDATION_PASSES):
        improved = False
        for holding in candidates:
            current = deficit()
            if current <= 0:
                break
            sign = Decimal("1") if holding.quantity > 0 else Decimal("-1")
            done = sold.get(holding.asset_id, Decimal("0"))
            left = ctx.subtract(holding.quantity.copy_abs(), done)
            if left <= 0:
                continue
            step = _liquidation_step(state, holding) or _FRACTIONAL_STEP
            steps = int(ctx.divide(left, step).to_integral_value(rounding=decimal.ROUND_CEILING))

            def at(
                n: int,
                holding: MarginHolding = holding,
                sign: Decimal = sign,
                done: Decimal = done,
                left: Decimal = left,
                step: Decimal = step,
            ) -> Decimal:
                more = min(ctx.multiply(Decimal(n), step), left)
                quantities[holding.asset_id] = ctx.subtract(
                    holding.quantity, ctx.multiply(sign, ctx.add(done, more))
                )
                return deficit()  # fmt: skip

            low, high = 0, steps
            while high - low > 2:
                first, second = low + (high - low) // 3, high - (high - low) // 3
                if at(first) <= at(second):
                    high = second
                else:
                    low = first
            best = min(range(low, high + 1), key=lambda n: (at(n), n))
            if at(best) >= current:
                at(0)  # no reduction of this position helps: leave it as it was
                continue
            if at(best) <= 0:
                bottom, top = 0, best
                while top - bottom > 1:
                    middle = (bottom + top) // 2
                    if at(middle) <= 0:
                        top = middle
                    else:
                        bottom = middle
                best = top
            at(best)
            sold[holding.asset_id] = ctx.add(done, min(ctx.multiply(Decimal(best), step), left))
            improved = True
        if not improved or deficit() <= 0:
            break
    by_asset = {holding.asset_id: holding for holding in candidates}
    return [(by_asset[asset_id], quantity) for asset_id, quantity in sold.items() if quantity > 0]


def _margin_unit(
    state: ExecutionPipelineState, asset_id: str, terms: MarginTerms, rates: FxRates, at: float
) -> MarginHolding | None:
    """A holding of none of ``asset_id``, with its per-unit figures: what an order would add."""

    price = state.market_prices.get(asset_id)
    if price is None:
        return None
    unit_value = _unit_value(
        state, asset_id, _price_in_base(state, asset_id, price, rates, at)
    ).copy_abs()
    unit_initial = _unit_margin(state, asset_id, terms, unit_value, rates, at)
    if terms.basis is MarginBasis.VALUE_RATE:
        unit_maintenance = ACCOUNTING_CONTEXT.multiply(unit_value, terms.maintenance)
    else:
        unit_maintenance = ACCOUNTING_CONTEXT.multiply(
            unit_initial, ACCOUNTING_CONTEXT.divide(terms.maintenance, terms.initial)
        )
    economics = _economics_of(state, asset_id)
    pays = economics is None or economics.settlement.pays_notional
    registry = state.config.instruments
    model = None if registry is None else registry.margin_model
    option = None
    if isinstance(model, ScenarioMargin) and asset_id in model.repriced:
        record = _declared(state, asset_id)
        assert record is not None
        base = state.portfolio.account.base_currency
        units = state.portfolio.account.currency_units

        def in_base(amount: Decimal, currency: str) -> Decimal:
            if currency == base:
                return amount
            return rates.convert(amount, currency, base, at, units=units).converted

        option = _option_risk(state, record, record.currency, in_base, at)
    return MarginHolding(
        asset_id,
        Decimal("0"),
        terms,
        Decimal("0"),
        Decimal("0"),
        unit_initial,
        Decimal("0"),
        unit_value,
        unit_maintenance,
        terms.haircut if pays else Decimal("0"),
        option,
    )


def _projected_margin(
    state: ExecutionPipelineState, request: OrderRequest, rates: FxRates, at: float
) -> tuple[Decimal, Decimal] | None:
    """Margin equity and the initial requirement, were ``request`` to fill (FUT-019).

    The declared model is asked for the book as it would be: every margined
    position held, every working order that grows one, and this order -- so an
    order that offsets a held position is credited only as the model credits
    it, and one in an instrument it does not cover only by its own terms.
    ``None`` when the order's instrument declares no margin terms or no price.
    """

    registry = state.config.instruments
    if registry is None:
        return None
    record = _declared(state, request.asset_id)
    if record is None or record.margin is None:
        return None
    margin = margin_position(state, rates, at)
    if margin is None:
        portfolio = state.portfolio
        nav = NAVCalculator.calculate(
            portfolio.cash, portfolio.positions, portfolio.account.base_currency, rates, at
        )
        margin = MarginPosition(nav, Decimal("0"), Decimal("0"), (), registry.margin_model)
    ctx = ACCOUNTING_CONTEXT
    held = {holding.asset_id: holding for holding in margin.holdings}
    added: dict[str, MarginHolding] = {}
    quantities: dict[str, Decimal] = {}

    def grow(asset_id: str, terms: MarginTerms, signed: Decimal) -> bool:
        if asset_id not in held and asset_id not in added:
            unit = _margin_unit(state, asset_id, terms, rates, at)
            if unit is None:
                return False
            added[asset_id] = unit
        base = held[asset_id].quantity if asset_id in held else Decimal("0")
        quantities[asset_id] = ctx.add(quantities.get(asset_id, base), signed)
        return True

    for asset_id in registry.margined or ():
        margined = _declared(state, asset_id)
        if margined is None or margined.margin is None:
            continue
        for order_id in state.oms.working_orders_for(asset_id):
            order = state.oms.orders.find(order_id)
            remaining = order.remaining_quantity
            signed = remaining if order.side is OMSSide.BUY else -remaining
            if _growth_of(state, asset_id, signed):
                grow(asset_id, margined.margin, signed)
    if not grow(request.asset_id, record.margin, _signed(request)):
        return None
    equity, initial, _ = margin.figures(quantities, tuple(added.values()))
    return equity, initial


def _multiplier(economics: InstrumentEconomics | None) -> Decimal:
    return Decimal("1") if economics is None else economics.multiplier


@dataclass(frozen=True, slots=True)
class _ExchangePlan:
    """What a holding exchange moves, planned before anything does (FUT-021)."""

    legs: tuple[tuple[ExchangeLeg, ExchangedHolding], ...]
    retained_basis: Decimal | None
    consideration: Mapping[str, Decimal]
    realized: Decimal

    @property
    def received(self) -> tuple[ExchangedHolding, ...]:
        return tuple(holding for _, holding in self.legs)


def _exchange_plan(
    state: ExecutionPipelineState,
    exchange: HoldingExchange,
    held: Position,
    at: float,
    rates: FxRates,
) -> _ExchangePlan:
    """Apportion a holding's basis and units across an exchange's legs and cash, or refuse it."""

    ctx = ACCOUNTING_CONTEXT
    sign = Decimal("1") if held.quantity > 0 else Decimal("-1")
    units = held.quantity.copy_abs()
    basis = held.basis
    source = held.currency
    account_units = state.portfolio.account.currency_units

    def convert(amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
        if from_currency == to_currency or not amount:
            return amount
        try:
            return rates.convert(
                amount, from_currency, to_currency, at, units=account_units
            ).converted
        except (MissingRateError, StaleRateError) as error:
            raise RuntimeValidationError(
                f"The exchange of {exchange.asset_id} moves {from_currency} into {to_currency} at "
                f"{at!r}, and needs a rate the run holds then: {error}"
            ) from None

    legs: list[tuple[ExchangeLeg, ExchangedHolding]] = []
    consideration: dict[str, Decimal] = {}
    realized = Decimal("0")
    for leg in exchange.legs:
        record = _declared(state, leg.asset_id)
        if state.config.instruments is not None and record is None:
            raise RuntimeValidationError(
                f"The exchange of {exchange.asset_id} delivers {leg.asset_id}, which the run's "
                "registry does not declare; nothing can be delivered that the book cannot name."
            )
        currency = source if record is None else record.currency
        economics = None if record is None else record.economics
        booked = (
            economics_for(record.asset_type, economics, record.symbol)
            if record is not None
            else None
        )
        received = ctx.multiply(units, leg.ratio)
        whole = received if booked is None else _whole_part(state, booked, received)
        fraction = ctx.subtract(received, whole)
        carried = ctx.multiply(basis, leg.basis_share)
        if fraction:
            if leg.in_lieu_price is None:
                raise RuntimeValidationError(
                    f"The exchange of {exchange.asset_id} delivers {received} {leg.asset_id}, of "
                    f"which {fraction} is less than a unit the book can hold, and declares no "
                    "in-lieu price for it (ExchangeLeg.in_lieu_price)."
                )
            fraction_basis = ctx.divide(ctx.multiply(carried, fraction), received)
            carried = ctx.subtract(carried, fraction_basis)
            paid = ctx.multiply(ctx.multiply(fraction, leg.in_lieu_price), _multiplier(economics))
            consideration[currency] = ctx.add(
                consideration.get(currency, Decimal("0")), ctx.multiply(sign, paid)
            )
            realized = ctx.add(
                realized,
                ctx.multiply(sign, ctx.subtract(convert(paid, currency, source), fraction_basis)),
            )
        if not whole:
            continue
        leg_basis = convert(carried, source, currency)
        price = state.market_prices.get(leg.asset_id)
        if price is None:
            price = ctx.divide(leg_basis, ctx.multiply(whole, _multiplier(economics)))
        legs.append(
            (
                leg,
                ExchangedHolding(
                    leg.asset_id, ctx.multiply(sign, whole), leg_basis, price, currency, economics
                ),
            )
        )
    if exchange.cash_per_unit:
        currency = exchange.cash_currency or source
        if currency not in _settlement_currencies(state):
            raise RuntimeValidationError(
                f"The exchange of {exchange.asset_id} pays cash in {currency}, which the run does "
                "not settle: declare it in ExecutionPipelineConfig.also_settles."
            )
        paid = ctx.multiply(units, exchange.cash_per_unit)
        consideration[currency] = ctx.add(
            consideration.get(currency, Decimal("0")), ctx.multiply(sign, paid)
        )
        share = ctx.multiply(basis, exchange.cash_basis_share)
        realized = ctx.add(
            realized, ctx.multiply(sign, ctx.subtract(convert(paid, currency, source), share))
        )
    retained = (
        None
        if exchange.retained_basis_share is None
        else ctx.multiply(basis, exchange.retained_basis_share)
    )
    return _ExchangePlan(tuple(legs), retained, consideration, realized)


def _settlement_currencies(state: ExecutionPipelineState) -> frozenset[str]:
    config = state.config
    return frozenset({config.currency, *config.also_settles})


def _require_exchange_adjusted(
    state: ExecutionPipelineState, exchange: HoldingExchange, timestamp: float
) -> None:
    """Refuse an exchange of an instrument a held or working contract delivers, unless adjusted.

    A contract on the exchanged instrument keeps its economics only if its terms
    say what it delivers from the exchange's instant: it must declare an
    adjustment effective then -- an exchange's is the issuer's and the clearing
    house's published one, declared, not derived here. A future delivering it is
    refused: futures' terms are not adjusted (FUT-021).
    """

    assets = dict.fromkeys(
        (
            *state.portfolio.positions,
            *(o for o in state.oms.working_by_asset if state.oms.working_orders_for(o)),
        )
    )
    for asset_id in assets:
        if asset_id == exchange.asset_id:
            continue
        record = _declared(state, asset_id)
        terms = None if record is None else record.terms
        if terms is None:
            continue
        named = f"{record.symbol} on {record.exchange}"  # type: ignore[union-attr]
        if isinstance(terms, FutureTerms):
            if terms.deliverable is not None and terms.deliverable.asset_id == exchange.asset_id:
                raise RuntimeValidationError(
                    f"{named} delivers {exchange.asset_id}, which is exchanged at {timestamp!r}; "
                    "a future's delivery is not adjusted for an exchange. Close it before."
                )
            continue
        before = replace(
            terms, adjustments=tuple(a for a in terms.adjustments if a.effective_at < timestamp)
        ).effective(timestamp)
        if exchange.asset_id not in {d.asset_id for d in before.deliverables}:
            continue
        if not any(a.effective_at == timestamp for a in terms.adjustments):
            raise RuntimeValidationError(
                f"{named} delivers {exchange.asset_id}, which is exchanged at {timestamp!r}, and "
                "declares no adjustment effective then: it would deliver an instrument that no "
                "longer trades as it was. Declare its adjusted terms (OptionTerms.adjustments)."
            )


def _require_adjusted_for(state: ExecutionPipelineState, split: Split, timestamp: float) -> None:
    """Refuse a split of an instrument a held or working contract delivers, unless it is adjusted.

    A contract on the split instrument keeps its economics only if its terms say
    what it delivers afterwards: an option declaring, effective at the split's
    instant, the adjustment :meth:`~alphalab.instrument.terms.OptionTerms.split_adjusted`
    implies -- the split instrument's units times the ratio, everything else the
    same. Anything else would deliver the pre-split units at the pre-split strike,
    half the value of a two-for-one split's, so it is refused, naming the
    contract (FUT-016). Contracts are read by the keyed lookup alone, for the
    assets the book holds or works.
    """

    assets = dict.fromkeys(
        (
            *state.portfolio.positions,
            *(o for o in state.oms.working_by_asset if state.oms.working_orders_for(o)),
        )
    )
    for asset_id in assets:
        if asset_id == split.asset_id:
            continue
        record = _declared(state, asset_id)
        if record is None or record.terms is None:
            continue
        terms = record.terms
        named = f"{record.symbol} on {record.exchange}"
        if isinstance(terms, FutureTerms):
            if terms.deliverable is not None and terms.deliverable.asset_id == split.asset_id:
                raise RuntimeValidationError(
                    f"{named} delivers {split.asset_id}, which splits at {timestamp!r}; a "
                    "future's delivery is not adjusted for a split. Close it before the split."
                )
            continue
        before = replace(
            terms, adjustments=tuple(a for a in terms.adjustments if a.effective_at < timestamp)
        ).effective(timestamp)
        if split.asset_id not in {d.asset_id for d in before.deliverables}:
            continue
        declared = [a for a in terms.adjustments if a.effective_at == timestamp]
        expected = before.split_adjusted(split.asset_id, split.ratio, timestamp).adjustments[-1]
        if not declared:
            raise RuntimeValidationError(
                f"{named} delivers {split.asset_id}, which splits {split.ratio} for one at "
                f"{timestamp!r}, and declares no adjustment effective then: it would deliver "
                "the units of before the split at the strike of before it. Declare its "
                "adjusted terms (OptionTerms.split_adjusted)."
            )
        (adjustment,) = declared
        if (
            adjustment.strike,
            adjustment.deliverable,
            adjustment.basket,
            adjustment.cash_per_contract,
        ) != (
            expected.strike,
            expected.deliverable,
            expected.basket,
            expected.cash_per_contract,
        ):
            raise RuntimeValidationError(
                f"{named} declares an adjustment at {timestamp!r} that does not preserve its "
                f"economics under a {split.ratio} split of {split.asset_id}: it delivers "
                f"{adjustment.deliverable}, {adjustment.basket} at {adjustment.strike}, and the "
                f"split implies {expected.deliverable}, {expected.basket} at {expected.strike}."
            )


def _observed(state: ExecutionPipelineState, asset_id: str, named: str, why: str) -> Decimal:
    """The last price the run observed for ``asset_id``, or a refusal saying why it was needed."""

    price = state.market_prices.get(asset_id)
    if price is None:
        raise RuntimeValidationError(
            f"{named} {why} at the price the run has observed for {asset_id}, and it has "
            "observed none."
        )
    return price


def _package_values(
    state: ExecutionPipelineState,
    record: InstrumentRecord,
    terms: OptionTerms,
    timestamp: float,
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Each deliverable's observed value and multiplier, by asset (FUT-016)."""

    named = f"{record.symbol} on {record.exchange}"
    values = {
        d.asset_id: _observed(state, d.asset_id, named, "values its package")
        for d in terms.deliverables
    }
    multipliers = {
        d.asset_id: _deliverable_multiplier(state, record, d) for d in terms.deliverables
    }
    return values, multipliers


def _whole_part(
    state: ExecutionPipelineState, economics: InstrumentEconomics, units: Decimal
) -> Decimal:
    """The part of ``units`` a book can hold: whole lots, or whole units if the run trades them."""

    lot = economics.lot
    if lot is not None:
        steps = ACCOUNTING_CONTEXT.divide(units, lot.lot_size).to_integral_value(
            rounding=decimal.ROUND_DOWN
        )
        return ACCOUNTING_CONTEXT.multiply(steps, lot.lot_size)
    if state.config.allocation_constraints.enforce_integer_quantities:
        return units.to_integral_value(rounding=decimal.ROUND_DOWN)
    return units


@dataclass(frozen=True, slots=True)
class _Component:
    """One deliverable of a package, as one exercise moves it."""

    deliverable: Deliverable
    record: InstrumentRecord
    economics: InstrumentEconomics
    #: Whole units moved, signed: positive taken in by the book.
    units: Decimal
    #: The price per unit it is booked at, its share of the consideration.
    unit_price: Decimal


@dataclass(frozen=True, slots=True)
class _PackagePlan:
    """Everything a package delivery moves, checked before anything does (FUT-016)."""

    components: tuple[_Component, ...]
    #: The deliverables' currency, in which they are booked.
    currency: str
    #: The consideration's currency: the strike and the cash component.
    consideration_currency: str
    #: Converted before the components move: (amount, from, to), or ``None``.
    convert_before: tuple[Decimal, str, str] | None
    #: Converted after: the proceeds back into the consideration's currency.
    convert_after: tuple[Decimal, str, str] | None
    #: The rates both conversions use: the run's, or a quanto contract's fixed one.
    rates: FxRates
    #: Whether the consideration is more than the cash it is paid in, and is
    #: financed: every component taken in declares margin terms (FUT-017).
    financed: bool = False


def _package_plan(
    state: ExecutionPipelineState,
    record: InstrumentRecord,
    terms: OptionTerms,
    economics: InstrumentEconomics,
    closing: Decimal,
    timestamp: float,
    rates: FxRates,
) -> _PackagePlan:
    """Plan a physical delivery of a package -- or refuse it before anything moves (FUT-016).

    The side taking the package in (a call's holder, a put's writer) pays the
    consideration, ``strike x multiplier`` a contract less the cash component it
    receives, in the consideration currency, converted into the deliverables'
    currency at ``timestamp`` by ``rates`` when the two differ. A fraction of a
    unit the book cannot hold is settled in cash in lieu, by the contract's
    declared rule. What is left of the consideration is the whole components'
    cost, apportioned by their observed market values -- the one component's,
    when there is one -- so each is booked through the portfolio's fill
    authority at a price, and the fills move exactly the net cash.
    """

    ctx = ACCOUNTING_CONTEXT
    named = f"{record.symbol} on {record.exchange}"
    direction = Decimal("1") if terms.option_type is OptionType.CALL else Decimal("-1")
    sign = direction if closing > 0 else -direction
    contracts = closing.copy_abs()
    found: list[tuple[Deliverable, InstrumentRecord, InstrumentEconomics, Decimal, Decimal]] = []
    currency: str | None = None
    lieu = Decimal("0")
    for d in terms.deliverables:
        underlying = _declared(state, d.asset_id)
        if underlying is None:
            raise RuntimeValidationError(
                f"{named} delivers {d.asset_id}, which the run's registry does not declare; "
                "nothing can be delivered that the book cannot name."
            )
        if currency is None:
            currency = underlying.currency
        elif underlying.currency != currency:
            raise RuntimeValidationError(
                f"{named} delivers a package priced in {currency} and {underlying.currency}; "
                "one consideration cannot be apportioned across two currencies' components."
            )
        component_economics = economics_for(
            underlying.asset_type,
            underlying.economics,
            f"{underlying.symbol} on {underlying.exchange}",
        )
        units = ctx.multiply(d.units, contracts)
        whole = _whole_part(state, component_economics, units)
        fraction = ctx.subtract(units, whole)
        if fraction:
            rule = terms.fractions
            if rule is None:
                raise RuntimeValidationError(
                    f"{named} delivers {units} {underlying.symbol}, of which {fraction} is less "
                    "than a unit the book can hold, and declares no cash in lieu for it."
                )
            price = (
                rule.price
                if rule.price is not None
                else _observed(state, d.asset_id, named, "settles a fraction in cash in lieu")
            )
            lieu = ctx.add(
                lieu, ctx.multiply(ctx.multiply(fraction, price), component_economics.multiplier)
            )
        found.append((d, underlying, component_economics, whole, units))
    assert currency is not None  # a package has a deliverable
    consideration_currency = terms.consideration_currency or currency
    units_of_account = state.portfolio.account.currency_units
    net = to_money(
        ctx.subtract(
            ctx.multiply(ctx.multiply(terms.strike, economics.multiplier), contracts),
            ctx.multiply(terms.cash_per_contract, contracts),
        ),
        consideration_currency,
        units_of_account,
    )
    if net <= 0:
        raise RuntimeValidationError(
            f"{named}'s cash component covers its strike: the side taking the package in "
            "would be paid to, which no fill can book."
        )
    conversion_rates = rates
    if terms.consideration_rate is not None and consideration_currency != currency:
        # A quanto contract converts at the rate it fixes, not the market's.
        fixed = FxRate(
            consideration_currency,
            currency,
            terms.consideration_rate,
            timestamp,
            f"{named}'s fixed rate",
        )
        conversion_rates = FxRates.of(
            (
                fixed,
                replace(
                    fixed,
                    base=currency,
                    quote=consideration_currency,
                    rate=ACCOUNTING_CONTEXT.divide(Decimal("1"), terms.consideration_rate),
                ),
            ),
            currency_units=rates.currency_units,
        )
    if consideration_currency == currency:
        net_in_currency = net
    else:
        try:
            net_in_currency = conversion_rates.convert(
                net, consideration_currency, currency, timestamp, units=units_of_account
            ).converted
        except (MissingRateError, StaleRateError) as refused:
            raise RuntimeValidationError(
                f"{named} is paid for in {consideration_currency} and delivers instruments "
                f"priced in {currency}; converting the consideration at {timestamp!r} needs a "
                f"rate the run holds: {refused}"
            ) from refused
    cost = ctx.subtract(net_in_currency, lieu)
    whole_components = [entry for entry in found if entry[3] > 0]
    if not whole_components:
        raise RuntimeValidationError(
            f"{named} would deliver no whole unit of anything; a delivery that is all cash in "
            "lieu is not booked by a fill."
        )
    if cost <= 0:
        raise RuntimeValidationError(
            f"{named}'s cash in lieu ({lieu} {currency}) covers its consideration; the "
            "components' cost would not be positive."
        )
    if len(whole_components) == 1:
        shares = [cost]
    else:
        worth = [
            ctx.multiply(
                ctx.multiply(
                    whole, _observed(state, d.asset_id, named, "apportions its consideration")
                ),
                component_economics.multiplier,
            )
            for d, _, component_economics, whole, _ in whole_components
        ]
        total = sum(worth, Decimal("0"))
        if total <= 0:
            raise RuntimeValidationError(
                f"{named}'s components are observed worth nothing; its consideration cannot be "
                "apportioned by their values."
            )
        shares = [ctx.divide(ctx.multiply(cost, w), total) for w in worth[:-1]]
        shares.append(ctx.subtract(cost, sum(shares, Decimal("0"))))
    components = []
    for (d, underlying, component_economics, whole, _), share in zip(
        whole_components, shares, strict=True
    ):
        unit_price = ctx.divide(share, ctx.multiply(whole, component_economics.multiplier))
        if not component_economics.admits_price(unit_price):
            raise RuntimeValidationError(
                f"{named} would deliver {underlying.symbol} at {unit_price}, which its "
                "economics do not admit."
            )
        moved = ctx.multiply(sign, whole)
        held = _held(state, d.asset_id)
        if (
            moved < 0
            and ctx.add(held, moved) < 0
            and not state.config.allocation_constraints.allow_shorting
        ):
            raise RuntimeValidationError(
                f"{named} must deliver {whole} {underlying.symbol} and the book holds {held}; "
                "the run does not allow a short position, and none is created to make the "
                "delivery."
            )
        components.append(_Component(d, underlying, component_economics, moved, unit_price))
    convert_before = None
    convert_after = None
    paying = sign > 0
    payer_currency = consideration_currency if paying else currency
    owed = net if paying else Decimal("0")
    financed = False
    if paying:
        cash = state.portfolio.cash.balance(consideration_currency)
        unmargined = [c.record.symbol for c in components if c.record.margin is None]
        if cash < owed and unmargined:
            raise RuntimeValidationError(
                f"{named} takes delivery for {owed} {payer_currency}, and the book holds "
                f"{cash}; a delivery is financed only when every deliverable declares margin "
                f"terms (InstrumentRecord.margin), and {', '.join(unmargined)} declares none."
            )
        financed = cash < owed
        if consideration_currency != currency:
            convert_before = (net, consideration_currency, currency)
    elif consideration_currency != currency:
        convert_after = (cost, currency, consideration_currency)
    return _PackagePlan(
        tuple(components),
        currency,
        consideration_currency,
        convert_before,
        convert_after,
        conversion_rates,
        financed,
    )


def _book_package(
    state: ExecutionPipelineState,
    record: InstrumentRecord,
    plan: _PackagePlan,
    parts: Mapping[str, Decimal],
    timestamp: float,
    rates: FxRates,
    trade_id: str,
) -> ExecutionPipelineState:
    """Move a planned package through the portfolio's fill authority, attributed (FUT-016)."""

    ctx = ACCOUNTING_CONTEXT
    current = state
    if plan.convert_before is not None:
        amount, source, target = plan.convert_before
        portfolio, _ = PortfolioEngine.convert_cash(
            current.portfolio, amount, source, target, plan.rates, timestamp, financed=plan.financed
        )
        current = evolve(current, portfolio=portfolio)
    total = sum(parts.values(), Decimal("0"))
    for component in plan.components:
        asset_id = component.deliverable.asset_id
        shares = tuple(
            (sid, ctx.divide(ctx.multiply(component.units, part), total))
            for sid, part in parts.items()
        )
        component_parts = _strategy_parts(shares, component.units)
        current = _lifecycle_fill(
            current,
            asset_id,
            component.units,
            component.unit_price,
            timestamp,
            component.record.currency,
            component.economics,
            component_parts,
            f"DELIVERY-{trade_id}-{asset_id}",
            financed=plan.financed and component.units > 0,
        )
        budget_value = _in_budget_currency(
            current,
            ctx.multiply(component.unit_price, component.economics.multiplier),
            component.record.currency,
            rates,
            timestamp,
        )
        current = evolve(
            current,
            allocation=AllocationEngine.apply_lifecycle(
                current.allocation, asset_id, {}, asset_id, component_parts, budget_value
            ),
        )
        if asset_id in current.market_prices:
            current = evolve(
                current,
                portfolio=PortfolioEngine.mark_changed(
                    current.portfolio, current.market_prices, timestamp, asset_id
                ),
            )
    if plan.convert_after is not None:
        amount, source, target = plan.convert_after
        portfolio, _ = PortfolioEngine.convert_cash(
            current.portfolio, amount, source, target, plan.rates, timestamp
        )
        current = evolve(current, portfolio=portfolio)
    return current


_NOT_ORDERS: Final = (IntentKind.EXERCISE, IntentKind.CANCEL, IntentKind.ELECT, IntentKind.AMEND)


def _cancels_requested(
    state: ExecutionPipelineState, intents: tuple[Intent, ...], timestamp: float
) -> ExecutionPipelineState:
    """Withdraw what each cancel instruction names, before the batch's orders are placed.

    A cancel withdraws every order working in its instrument that the asking
    strategy alone contributed to -- read off the OMS's working orders and the
    allocation's contributions -- and frees what each held. An order netted
    with another strategy's, one an execution plan works, and one held at a
    venue are left working, each recorded as a
    :class:`~alphalab.strategy.events.CancelRefused`. A run whose strategies
    ask for no cancel takes one test per intent.
    """

    if not any(intent.kind is IntentKind.CANCEL for intent in intents):
        return state
    current = state
    for intent in intents:
        if intent.kind is not IntentKind.CANCEL:
            continue
        sid, asset_id = intent.strategy_id, intent.instrument
        for order_id in tuple(current.oms.working_orders_for(asset_id)):
            contributions = AllocationEngine.contributions_for(
                current.allocation, str(order_id.value)
            )
            ours = bool(contributions) and all(c.strategy_id == sid for c in contributions)
            if not ours:
                if any(c.strategy_id == sid for c in contributions):
                    current = _refuse_cancel(
                        current, intent, order_id, timestamp,
                        "The order is netted with another strategy's, and withdrawing it "
                        "would withdraw theirs.",
                    )  # fmt: skip
                continue
            routing = current.config.routing
            if routing is ExecutionRouting.EXTERNAL:
                current = _refuse_cancel(
                    current, intent, order_id, timestamp,
                    "The order is held at the venue; a cancel there is a venue request "
                    "(LiveSession.cancel).",
                )  # fmt: skip
                continue
            if routing is ExecutionRouting.ALGORITHMIC:
                current = _refuse_cancel(
                    current, intent, order_id, timestamp,
                    "The order is a parent an execution plan works; withdrawing it is not "
                    "modelled.",
                )  # fmt: skip
                continue
            current = _end_unfilled(current, current.oms.orders.find(order_id), timestamp)
    return current


def _instructions_requested(
    state: ExecutionPipelineState,
    intents: tuple[Intent, ...],
    timestamp: float,
    *,
    event: MarketEvent | None,
    policy: FillPolicy,
    rates: FxRates,
    rest: bool = False,
) -> tuple[ExecutionPipelineState, tuple[Intent, ...], _Routed]:
    """Carry out the batch's cancels and amends: the intents left to size, and the amends' routing.

    Cancels first, then amends -- each replacement placed atomically, before
    the batch's own orders (FUT-031).
    """

    current = _cancels_requested(state, intents, timestamp)
    current, routed = _amends_requested(
        current, intents, timestamp, event=event, policy=policy, rates=rates, rest=rest
    )
    return current, _for_orders(intents), routed


def _amends_requested(
    state: ExecutionPipelineState,
    intents: tuple[Intent, ...],
    timestamp: float,
    *,
    event: MarketEvent | None,
    policy: FillPolicy,
    rates: FxRates,
    rest: bool,
) -> tuple[ExecutionPipelineState, _Routed]:
    """Amend what each amend instruction names (FUT-029), each atomically (FUT-031).

    An amend applies to the one order working in its instrument that the asking
    strategy alone contributed to, under simulated routing; it keeps the order's
    type and time in force. A reduction at the same prices is made in place --
    the OMS replaces the order's quantity, its contribution and its reservations
    shrink, and it keeps its id and so its queue priority. Any other amend --
    more quantity, another limit or stop -- is prepared and committed: its
    replacement is allocated and routed against the book with the order
    withdrawn, through allocation, risk and the OMS like any order, at the back
    of its queue, and only if the replacement is placed is that book kept. If
    allocation, risk or routing refuses the replacement, the book is as it was
    -- the order working, its reservations held -- and the amend is recorded as
    an :class:`~alphalab.strategy.events.AmendRefused` naming why. With no market
    event yet to route at, a replacement is refused the same way.
    """

    if not any(intent.kind is IntentKind.AMEND for intent in intents):
        return state, _NO_ROUTING
    current, routed = state, _NO_ROUTING
    for intent in intents:
        if intent.kind is not IntentKind.AMEND:
            continue
        refusal, order = _amendable(current, intent)
        if refusal is not None or order is None:
            current = _refuse_amend(current, intent, timestamp, refusal or "")
            continue
        terms = intent.terms
        reduced = intent.target < order.remaining_quantity
        same_prices = (
            terms.limit_price == order.limit_price and terms.stop_price == order.stop_price
        )
        if same_prices and intent.target == order.remaining_quantity:
            continue  # nothing to change
        if same_prices and reduced:
            current = _reduce_in_place(current, order, intent.target, timestamp)
            continue
        if event is None:
            current = _refuse_amend(
                current, intent, timestamp,
                "No market event has priced the instrument yet, so the replacement cannot be "
                "judged; the order is kept working.",
            )  # fmt: skip
            continue
        current, placed = _replace_atomically(
            current, intent, order, event, policy, rates, timestamp, rest=rest
        )
        routed = routed.then(placed)
    return current, routed


def _replace_atomically(
    state: ExecutionPipelineState,
    intent: Intent,
    order: OMSOrder,
    event: MarketEvent,
    policy: FillPolicy,
    rates: FxRates,
    timestamp: float,
    *,
    rest: bool,
) -> tuple[ExecutionPipelineState, _Routed]:
    """Prepare ``order``'s replacement against the book without it; commit only if placed."""

    trial = _end_unfilled(state, order, timestamp)
    mark = len(trial.allocation.events)
    sign = Decimal("1") if order.side is OMSSide.BUY else Decimal("-1")
    share = AllocationEngine.strategy_position(trial.allocation, intent.strategy_id, order.asset_id)
    replacement = Intent(
        intent.strategy_id,
        intent.instrument,
        # A target naming the position and the strategy's other working orders,
        # so the order is exactly the quantity asked, never read through the
        # run's sizing model (as ACC-016's forced trades).
        _forced_target(
            trial,
            intent.strategy_id,
            order.asset_id,
            share,
            ACCOUNTING_CONTEXT.multiply(sign, intent.target),
        ),
        timestamp=timestamp,
        kind=IntentKind.TARGET_QUANTITY,
        terms=intent.terms,
        metadata={"amends": str(order.order_id.value)},
    )
    allocation, requests = _allocate(trial, (replacement,), trial.market_prices, rates, timestamp)
    trial = evolve(trial, allocation=allocation)
    trial, placed = _route_requests(
        trial, event, (replacement,), requests, policy, rates, rest=rest,
        at=timestamp if rest else None,
    )  # fmt: skip
    if placed.orders:
        return trial, placed
    judged = allocation.events[mark:]
    why = "; ".join(
        (
            *(e.reason for e in judged if isinstance(e, (AllocationRejected, BudgetExceeded))),
            *(reason for _, reason in placed.dropped),
        )
    )
    # The book is as it was. What judged the replacement stays on the record --
    # allocation's and risk's decisions, an unpriced asset -- and the strategy
    # hears of a refused request as of any.
    kept = evolve(
        state,
        allocation=evolve(state.allocation, events=state.allocation.events.extend(judged)),
        risk=trial.risk,
        unpriced_assets=trial.unpriced_assets,
    )
    refused = _Routed(
        decisions=placed.decisions,
        unpriced=placed.unpriced,
        refusals=placed.refusals,
        dropped=placed.dropped,
    )
    return (
        _refuse_amend(
            kept, intent, timestamp,
            f"The replacement was refused ({why or 'allocation sized no order from it'}); "
            "the order is kept working as it was.",
        ),
        refused,
    )  # fmt: skip


def _amendable(state: ExecutionPipelineState, intent: Intent) -> tuple[str | None, OMSOrder | None]:
    """The one order ``intent`` amends, or why it cannot be amended."""

    if state.config.routing is ExecutionRouting.EXTERNAL:
        return (
            "The order is held at the venue; an amend there is a venue request "
            "(LiveSession.amend).",
            None,
        )
    if state.config.routing is ExecutionRouting.ALGORITHMIC:
        return "The order is a parent an execution plan works; amending it is not modelled.", None
    own: list[OMSOrder] = []
    for order_id in state.oms.working_orders_for(intent.instrument):
        contributions = AllocationEngine.contributions_for(state.allocation, str(order_id.value))
        if not any(c.strategy_id == intent.strategy_id for c in contributions):
            continue
        if not all(c.strategy_id == intent.strategy_id for c in contributions):
            return (
                "The order is netted with another strategy's, and amending it would amend theirs.",
                None,
            )
        own.append(state.oms.orders.find(order_id))
    if len(own) != 1:
        return (
            f"An amend names the one order the strategy has working in the instrument, and it "
            f"has {len(own)}.",
            None,
        )
    (order,) = own
    terms = intent.terms
    if terms.order_type is not order.order_type or terms.time_in_force is not order.time_in_force:
        return (
            f"An amend keeps the order's type and time in force ({order.order_type.name}, "
            f"{order.time_in_force.name}); place a new order for other terms.",
            None,
        )
    if order.order_type is OrderType.MARKET:
        return "A market order has no price to amend; it trades at its next turn.", None
    return None, order


def _reduce_in_place(
    state: ExecutionPipelineState, order: OMSOrder, remaining: Decimal, timestamp: float
) -> ExecutionPipelineState:
    """``order`` with ``remaining`` left working, its id and its queue place kept (FUT-029).

    What it holds shrinks with what it may still add (FUT-036): the account's
    reservation by the exposure the order adds to the account's committed
    position, and a ceilinged strategy's by what it adds to the strategy's --
    each the share of what it held that the smaller order still adds, so one
    that only reduces a position holds nothing, and the reservations are the
    ones its allocation would have made for the smaller order. Nothing is
    reserved anew, and nothing is released twice: what is released is freed
    now, and what is kept is released at the order's end as before.
    """

    ctx = ACCOUNTING_CONTEXT
    order_id = str(order.order_id.value)
    total = ctx.add(order.filled_quantity, remaining)
    oms = OMSEngine.replace(state.oms, order.order_id, total, timestamp)
    allocation = state.allocation
    (contribution,) = AllocationEngine.contributions_for(allocation, order_id)
    sign = Decimal("1") if contribution.quantity > 0 else Decimal("-1")
    contributions = allocation.contributions.set(
        order_id, (replace(contribution, quantity=ctx.multiply(sign, total)),)
    )
    was = ctx.multiply(sign, order.remaining_quantity)
    now = ctx.multiply(sign, remaining)
    reserved = allocation.reservations.get(order_id)
    if reserved is not None and order.remaining_quantity:
        # The account's committed position without this order, as allocation
        # judged it: filled, and every other order working.
        committed = _committed_positions(state, (_probe(order),)).get(order.asset_id, Decimal("0"))
        kept = _still_added(reserved, ctx.subtract(committed, was), was, now, _adds_to_account)
        allocation = evolve(
            allocation,
            reservations=allocation.reservations.set(order_id, kept),
            notional_allocated=ctx.subtract(
                allocation.notional_allocated, ctx.subtract(reserved, kept)
            ),
        )
    strategy_id = contribution.strategy_id
    capital = allocation.strategy_capital.get(strategy_id)
    held_back = None if capital is None else capital.reserved.get(order_id)
    if capital is not None and held_back is not None:
        # The strategy's position and its share of its other working orders.
        own = AllocationEngine.strategy_position(allocation, strategy_id, order.asset_id)
        working = _working_shares(state, (_probe(order, strategy_id),)).get(
            (strategy_id, order.asset_id), Decimal("0")
        )
        before = ctx.subtract(ctx.add(own, working), was)
        kept = _still_added(held_back, before, was, now, adding_exposure)
        allocation = evolve(
            allocation,
            strategy_capital=allocation.strategy_capital.set(
                strategy_id, capital.reduce(order_id, kept)
            ),
        )
    return evolve(state, oms=oms, allocation=evolve(allocation, contributions=contributions))


def _probe(order: OMSOrder, strategy_id: str = "") -> Intent:
    """A target intent naming ``order``'s asset: what the position readers are asked by."""

    return Intent(strategy_id, order.asset_id, Decimal("0"), kind=IntentKind.TARGET_QUANTITY)


def _adds_to_account(held: Decimal, delta: Decimal) -> Decimal:
    """What ``delta`` adds to the account's committed position ``held``, as allocation reads it."""

    after = ACCOUNTING_CONTEXT.add(held, delta)
    return max(Decimal("0"), ACCOUNTING_CONTEXT.subtract(abs(after), abs(held)))


def _still_added(
    reserved: Decimal,
    before: Decimal,
    was: Decimal,
    now: Decimal,
    adds: Callable[[Decimal, Decimal], Decimal],
) -> Decimal:
    """The share of ``reserved`` an order cut from ``was`` to ``now`` still adds to ``before``."""

    ctx = ACCOUNTING_CONTEXT
    added = adds(before, was)
    if added == 0:
        # It adds nothing to the book as it stands, though it held something
        # when allocated: the book may yet change back, so it keeps the same
        # share of it as of its quantity -- never less than it may need.
        return ctx.divide(ctx.multiply(reserved, abs(now)), abs(was))
    return ctx.divide(ctx.multiply(reserved, adds(before, now)), added)


def _refuse_amend(
    state: ExecutionPipelineState, intent: Intent, timestamp: float, reason: str
) -> ExecutionPipelineState:
    """Record that ``intent`` could not be made, in the strategy runtime's log."""

    refused = AmendRefused(str(new_id()), timestamp, intent.strategy_id, intent.instrument, reason)
    strategy = state.strategy
    return evolve(
        state, strategy=strategy.evolved(strategy.strategies, (*strategy.events, refused), ())
    )


def _refuse_cancel(
    state: ExecutionPipelineState,
    intent: Intent,
    order_id: OrderId,
    timestamp: float,
    reason: str,
) -> ExecutionPipelineState:
    """Record that ``intent`` left ``order_id`` working, in the strategy runtime's log."""

    refused = CancelRefused(
        str(new_id()), timestamp, intent.strategy_id, intent.instrument, str(order_id.value), reason
    )
    strategy = state.strategy
    return evolve(
        state, strategy=strategy.evolved(strategy.strategies, (*strategy.events, refused), ())
    )


def _for_orders(intents: tuple[Intent, ...]) -> tuple[Intent, ...]:
    """The intents allocation sizes into orders: every one but an exercise instruction (FUT-014)."""

    if not any(intent.kind in _NOT_ORDERS for intent in intents):
        return intents
    return tuple(intent for intent in intents if intent.kind not in _NOT_ORDERS)


def _exercises_requested(
    state: ExecutionPipelineState,
    intents: tuple[Intent, ...],
    timestamp: float,
    rates: FxRates,
) -> ExecutionPipelineState:
    """Carry out the exercise instructions among ``intents``, in the order asked (FUT-014).

    Each is the strategy's own: it exercises contracts of the strategy's own
    long position, through the checks a scheduled exercise passes, effective at
    ``timestamp``. One the run cannot carry out is recorded as an
    :class:`~alphalab.strategy.events.ExerciseRefused` and moves nothing; the
    strategy keeps running.
    """

    current = state
    number = 0
    for intent in intents:
        if intent.kind is not IntentKind.EXERCISE:
            continue
        try:
            current = _strategy_exercise(current, intent, timestamp, rates, number)
        except RuntimeValidationError as refused:
            current = _refuse_exercise(current, intent, timestamp, str(refused))
        number += 1
    return current


def _elections_requested(
    state: ExecutionPipelineState, intents: tuple[Intent, ...], timestamp: float
) -> ExecutionPipelineState:
    """Record each election among ``intents`` in the strategy runtime's log (FUT-024).

    Kept as made: which counts is the action's to decide when it takes effect,
    by its announcement, its deadline and its alternatives.
    """

    made = tuple(
        ElectionMade(
            event_id=str(new_id()),
            timestamp=timestamp,
            strategy_id=intent.strategy_id,
            asset_id=intent.instrument,
            action=str(intent.metadata["action"]),
            alternative=int(intent.target),
        )
        if "units" not in intent.metadata
        # Part of the holding (FUT-030): the units the election names.
        else UnitsElected(
            event_id=str(new_id()),
            timestamp=timestamp,
            strategy_id=intent.strategy_id,
            asset_id=intent.instrument,
            action=str(intent.metadata["action"]),
            alternative=int(intent.target),
            units=Decimal(str(intent.metadata["units"])),
        )
        for intent in intents
        if intent.kind is IntentKind.ELECT
    )
    if not made:
        return state
    strategy = state.strategy
    return evolve(
        state, strategy=strategy.evolved(strategy.strategies, (*strategy.events, *made), ())
    )


def _strategy_exercise(
    state: ExecutionPipelineState,
    intent: Intent,
    timestamp: float,
    rates: FxRates,
    number: int,
) -> ExecutionPipelineState:
    """Exercise ``intent.target`` contracts of the asking strategy's own position."""

    asset_id = intent.instrument
    strategy_id = intent.strategy_id
    contracts = intent.target
    record, terms, economics, named = _declared_option(state, asset_id, timestamp)
    _require_in_window(record, terms, timestamp, named, "exercised")
    _require_no_working_orders(state, asset_id, named, "exercised")
    own = dict(AllocationEngine.shares_of(state.allocation, asset_id)).get(strategy_id, Decimal(0))
    if own < contracts:
        raise RuntimeValidationError(
            f"{strategy_id} asked to exercise {contracts} contracts of {named} and holds "
            f"{own}: a strategy exercises its own long position, and only that."
        )
    value: Decimal | None = None
    if terms.settlement is SettlementStyle.CASH and (terms.basket or terms.cash_per_contract):
        pass  # a package is valued at its components' observed prices (FUT-016)
    elif terms.settlement is SettlementStyle.CASH:
        underlying = terms.deliverable.asset_id
        value = state.market_prices.get(underlying)
        if value is None:
            raise RuntimeValidationError(
                f"{named} is cash-settled against {underlying}, and the run has observed no "
                f"price for it by {timestamp!r}: an exercise a strategy asks for settles "
                "against what the run has seen, never a value it supplies."
            )
    return _resolve_option(
        state,
        record,
        terms,
        economics,
        contracts,
        value,
        timestamp,
        rates,
        f"EXERCISE-{asset_id}-{timestamp!r}-{strategy_id}-{number}",
        parts={strategy_id: contracts},
        instructed=True,
    )


def _refuse_exercise(
    state: ExecutionPipelineState, intent: Intent, timestamp: float, reason: str
) -> ExecutionPipelineState:
    """Record that ``intent`` was refused, in the strategy runtime's event log."""

    strategy = state.strategy
    refused = ExerciseRefused(
        event_id=str(new_id()),
        timestamp=timestamp,
        strategy_id=intent.strategy_id,
        asset_id=intent.instrument,
        contracts=intent.target,
        reason=reason,
    )
    return evolve(
        state, strategy=strategy.evolved(strategy.strategies, (*strategy.events, refused), ())
    )


def _resolve_option(
    state: ExecutionPipelineState,
    record: InstrumentRecord,
    terms: OptionTerms,
    economics: InstrumentEconomics,
    closing: Decimal,
    underlying_value: Decimal | None,
    timestamp: float,
    rates: FxRates,
    trade_id: str,
    parts: Mapping[str, Decimal] | None = None,
    *,
    instructed: bool = False,
) -> ExecutionPipelineState:
    """Exercise (``closing`` > 0) or assign (< 0) contracts by the option's terms.

    ``parts`` is whose contracts they are, when that is named -- a strategy
    exercising its own (FUT-014) -- rather than apportioned across every
    strategy holding the position.

    ``instructed`` is an exercise someone asked for -- scheduled, or a
    strategy's -- rather than an obligation: an assignment, or an expiry's
    automatic exercise. A delivery financed on margin (FUT-017) is booked for an
    obligation whatever it leaves, and the account's next margin check calls
    it; an instructed one is refused unless the account still meets its initial
    margin requirement afterwards -- financing extends no further than the
    declared terms do.
    """

    named = f"{record.symbol} on {record.exchange}"
    asset_id = record.asset_id
    parts = (
        _strategy_parts(AllocationEngine.shares_of(state.allocation, asset_id), closing)
        if parts is None
        else dict(parts)
    )
    cash_settled = terms.settlement is SettlementStyle.CASH
    packaged = bool(terms.basket) or terms.cash_per_contract != 0
    if cash_settled and packaged:
        if underlying_value is not None:
            raise RuntimeValidationError(
                f"{named} settles against a package of {len(terms.deliverables)} deliverables"
                + (" and cash" if terms.cash_per_contract else "")
                + ": one underlying value cannot state it. Its components are valued at the "
                "prices the run has observed for them."
            )
        plan = None
    elif cash_settled and underlying_value is None:
        raise RuntimeValidationError(
            f"{named} is cash-settled: its exercise pays the intrinsic value against the "
            "underlying's settlement value, which was not supplied."
        )
    if not cash_settled and underlying_value is not None:
        raise RuntimeValidationError(
            f"{named} is physically settled: the deliverable moves against the strike, and a "
            "settlement value would be read by nothing."
        )
    direction = Decimal("1") if terms.option_type is OptionType.CALL else Decimal("-1")
    delivered_units = ACCOUNTING_CONTEXT.multiply(
        ACCOUNTING_CONTEXT.multiply(direction, terms.deliverable.units), closing
    )
    plan = None
    financed = False
    if not cash_settled and not terms.is_single:
        # Refused before anything moves (FUT-016).
        plan = _package_plan(state, record, terms, economics, closing, timestamp, rates)
        financed = plan.financed
    elif not cash_settled:
        # Refused before anything moves.
        financed = _require_deliverable(
            state, record, terms.deliverable, delivered_units, terms.strike, economics.multiplier
        )[3]
    current = evolve(
        state, allocation=AllocationEngine.apply_lifecycle(state.allocation, asset_id, parts)
    )
    if cash_settled:
        if packaged:
            values, multipliers = _package_values(current, record, terms, timestamp)
            paid = terms.basket_intrinsic(values, multipliers, economics.multiplier)
        else:
            assert underlying_value is not None
            paid = terms.intrinsic(
                underlying_value,
                economics.multiplier,
                _deliverable_multiplier(current, record, terms.deliverable),
            )
        current = _lifecycle_fill(
            current,
            asset_id,
            -closing,
            paid,
            timestamp,
            record.currency,
            economics,
            parts,
            trade_id,
        )
    else:
        # The contracts are surrendered for nothing; the deliverable moves
        # against the strike.
        current = _lifecycle_fill(
            current,
            asset_id,
            -closing,
            Decimal("0"),
            timestamp,
            record.currency,
            economics,
            parts,
            trade_id,
        )
        if plan is not None:
            current = _book_package(current, record, plan, parts, timestamp, rates, trade_id)
            if financed and instructed:
                _require_initial_margin(current, record, timestamp, rates)
            return _after_book_change(current, current.portfolio, timestamp, rates)
        current = _deliver(
            current,
            record,
            terms.deliverable,
            {
                sid: ACCOUNTING_CONTEXT.multiply(
                    ACCOUNTING_CONTEXT.multiply(direction, terms.deliverable.units), part
                )
                for sid, part in parts.items()
            },
            delivered_units,
            terms.strike,
            economics.multiplier,
            timestamp,
            rates,
            f"DELIVERY-{trade_id}",
        )
        if financed and instructed:
            _require_initial_margin(current, record, timestamp, rates)
    return _after_book_change(current, current.portfolio, timestamp, rates)


def _require_initial_margin(
    state: ExecutionPipelineState, contract: InstrumentRecord, timestamp: float, rates: FxRates
) -> None:
    """Refuse an instructed exercise whose financed delivery leaves equity below initial margin.

    Read on the state the delivery produced, which is discarded with the refusal:
    nothing moves (FUT-017).
    """

    margin = margin_position(state, rates, timestamp)
    if margin is not None and margin.equity >= margin.initial:
        return
    named = f"{contract.symbol} on {contract.exchange}"
    detail = (
        "no position declares margin"
        if margin is None
        else f"equity {margin.equity} against an initial requirement of {margin.initial}"
    )
    raise RuntimeValidationError(
        f"Exercising {named} takes a delivery its cash does not cover, financed on margin, and "
        f"would leave {detail}: an exercise is financed only within the initial requirement."
    )


def _economics_of(state: ExecutionPipelineState, asset_id: str) -> InstrumentEconomics | None:
    """The economics ``asset_id`` is booked by: what its record declares (ledger ACC-005).

    ``None`` is a fully paid unit with a multiplier of one -- what every
    instrument was booked as before v3.11, and still is when the run configures
    no registry to read economics from, when the registry does not hold the
    asset, or when the record declares nothing for an asset type that is fully
    paid (:func:`~alphalab.instrument.economics.economics_for`). A future or an
    option that declares nothing never reaches a book: :func:`_settlement_refusal`
    refuses its request before an order exists. One keyed lookup, the only one
    on the registry for economics.
    """

    registry = state.config.instruments
    if registry is None:
        return None
    record = registry.record_for(asset_id)
    economics = None if record is None else record.economics
    return None if economics is None or economics.is_cash_equity else economics


def _unit_value(state: ExecutionPipelineState, asset_id: str, price: Decimal) -> Decimal:
    """What one unit of ``asset_id`` is worth at ``price``: the price times its multiplier.

    The figure a notional limit, a working-order commitment and a budget
    compare -- a contract on 50 units of an index is worth 50 times its quoted
    price. ``price`` itself for every instrument whose multiplier is one. Signed
    as the price is; each caller that compares a commitment takes its
    magnitude, since a contract priced below zero commits as much as one priced
    as far above it (ACC-007).
    """

    economics = _economics_of(state, asset_id)
    if economics is None or economics.multiplier == 1:
        return price
    return ACCOUNTING_CONTEXT.multiply(price, economics.multiplier)


def _working_shares(
    state: ExecutionPipelineState, intents: tuple[Intent, ...]
) -> Mapping[tuple[str, str], Decimal]:
    """Each target intent's strategy's signed share of what is still working in its asset.

    What a target is measured against beside the strategy's own position: an
    order already working toward it must not be asked for twice. Each working
    order's remaining quantity is divided among the strategies that asked for it
    by contribution, as its fills are. Computed only when a target is asked for
    -- or, when the budget enforces per-strategy ceilings, for every intent of a
    ceilinged strategy, whose order commits only the exposure it adds to that
    same position (OFE-003).
    """

    budget = state.allocation.budget
    wanted = {
        (intent.strategy_id, intent.instrument)
        for intent in intents
        if intent.kind is not IntentKind.DELTA
        or budget.strategy_ceiling(intent.strategy_id) is not None
    }
    if not wanted:
        return _NO_WORKING
    shares: dict[tuple[str, str], Decimal] = {}
    for asset_id in {asset for _, asset in wanted}:
        for order_id in state.oms.working_orders_for(asset_id):
            order = state.oms.orders.find(order_id)
            contributions = state.allocation.contributions.get(str(order_id.value), ())
            remaining = order.remaining_quantity
            signed = remaining if order.side is OMSSide.BUY else -remaining
            for strategy_id, share in split_by_contribution(
                signed, contributions, _ATTRIBUTION_QUANTUM
            ):
                key = (strategy_id, asset_id)
                if key in wanted:
                    shares[key] = ACCOUNTING_CONTEXT.add(shares.get(key, Decimal("0")), share)
    return shares


#: The settlement models of what expires: futures and options (FUT-012).
_EXPIRING: Final = frozenset({SettlementModel.FUTURES_VARIATION, SettlementModel.OPTION_PREMIUM})

_NO_LOTS: Mapping[str, LotSpecification] = MappingProxyType({})
_NO_MINIMUMS: Mapping[str, Decimal] = MappingProxyType({})
_NO_MULTIPLIERS: Mapping[str, Decimal] = MappingProxyType({})
_NO_WORKING: Mapping[tuple[str, str], Decimal] = MappingProxyType({})


def _deliver_instant(
    state: ExecutionPipelineState,
    event: TimerEvent | SliceClosed | ObservationReceived,
    last: MarketEvent | None,
    context_factory: ContextFactory,
    rates: FxRates,
) -> tuple[ExecutionPipelineState, tuple[Intent, ...], tuple[OMSOrder, ...]]:
    """Dispatch an instant that is not a market observation, and place what it asks for.

    A timer, a slice and an observation alike: the strategies see the book as it stands at
    ``event``'s instant, their orders rest until each asset's next event, and
    what becomes of the orders is fed back as for any step. With no event
    published there is no price to size or judge an order by; the intents are
    reported and nothing is placed.
    """

    at = event.timestamp
    populated = _populate_context(
        context_factory,
        portfolio=state.portfolio,
        risk=state.risk,
        market=state.market,
        market_prices=state.market_prices,
        shares=order_shares_by_strategy(state.oms, state.allocation),
        instruments=state.config.instruments,
        reopened=state.execution.reopened,
        window=state.config.retention.market_history,
        as_of=at,
    )
    strategy, intents = StrategyEngine.process_event(state.strategy, event, populated, at)
    current, ordered, amended = _instructions_requested(
        evolve(state, strategy=strategy),
        intents,
        at,
        event=last,
        policy=StaticFill(FillStatus.FULL_FILL, None),
        rates=rates,
        rest=True,
    )
    if not ordered or last is None:
        current = _exercises_requested(current, intents, at, rates)
        return evolve(current, id_position=current_id_position()), intents, ()

    contributions_before = current.allocation.contributions
    records_before = len(current.trade_records)
    allocation, requests = _allocate(current, ordered, current.market_prices, rates, at)
    current = evolve(current, allocation=allocation)
    policy = StaticFill(FillStatus.FULL_FILL, None)
    current, routed = _route_requests(
        current, last, ordered, requests, policy, rates, rest=True, at=at
    )
    routed = amended.then(routed)
    current, feedback_intents, _, feedback = _deliver_feedback(
        current,
        last,
        context_factory,
        current.market_prices,
        rates,
        policy,
        routed=routed,
        requests=requests,
        contributions_before=contributions_before,
        records_before=records_before,
        at=at,
    )
    current = _exercises_requested(current, (*intents, *feedback_intents), at, rates)
    return (
        evolve(current, id_position=current_id_position()),
        (*intents, *feedback_intents),
        routed.then(feedback).orders,
    )


def wants_observation(strategies: StrategyRuntimeState, subject: str) -> bool:
    """Whether any running strategy accepts an observation about ``subject`` and defines
    ``on_observation`` (ledger OFE-009).

    What decides whether delivering one does anything at all: a run none of
    whose strategies would act on it builds no context and dispatches nothing.
    """

    held = strategies.strategies
    return any(
        held[strategy_id].status is StrategyStatus.RUNNING
        and defines_on_observation(held[strategy_id].instance)
        for strategy_id in strategies.reach.reaching(Topic.OBSERVATIONS, subject)
    )


def wants_slices(strategies: StrategyRuntimeState) -> bool:
    """Whether any running strategy subscribed to slices defines ``on_slice`` (EXE-004).

    What decides whether closing a slice does anything at all: a run none of
    whose strategies would receive one builds no context, dispatches nothing and
    records nothing, and is exactly the run it was before slices existed.
    """

    held = strategies.strategies
    return any(
        held[strategy_id].status is StrategyStatus.RUNNING
        and defines_on_slice(held[strategy_id].instance)
        for strategy_id in strategies.reach.reaching(Topic.SLICES)
    )


def _instant_assets(events: AppendOnlyLog[MarketEvent], at: float) -> tuple[str, ...]:
    """The assets the events at instant ``at`` were about, sorted.

    Read backwards from the newest event by index, so the cost is the
    instant's own events and never the run's history -- iterating the log in
    reverse would copy all of it.
    """

    assets: set[str] = set()
    index = len(events) - 1
    while index >= 0 and events[index].timestamp == at:
        topic = market_topic(events[index])
        if topic is not None:
            assets.add(topic[1])
        index -= 1
    return tuple(sorted(assets))


def _wants_feedback(strategies: StrategyRuntimeState) -> bool:
    """Whether any running strategy subscribed to fills or to orders."""

    held = strategies.strategies
    reach = strategies.reach
    return any(
        held[strategy_id].status is StrategyStatus.RUNNING
        for topic in (Topic.FILLS, Topic.ORDERS)
        for strategy_id in reach.reaching(topic)
    )


def _contributors(
    order_id: str,
    before: Mapping[str, tuple[StrategyContribution, ...]],
    state: ExecutionPipelineState,
    requests: Mapping[str, OrderRequest],
) -> tuple[StrategyContribution, ...]:
    """Who asked for ``order_id``: the ledger as the step found it, as it left it, or the request.

    An order the step took terminal has had its ledger entry retired, so the
    ledger *before* the step answers for an order placed earlier, and the
    request itself for one placed and finished within the step.
    """

    found = before.get(order_id)
    if found:
        return found
    found = state.allocation.contributions.get(order_id)
    if found:
        return found
    request = requests.get(order_id)
    return request.contributions if request is not None else ()


def _feedback_events(
    state: ExecutionPipelineState,
    instant: float,
    routed: _Routed,
    requests: tuple[OrderRequest, ...],
    contributions_before: Mapping[str, tuple[StrategyContribution, ...]],
    records_before: int,
) -> tuple[tuple[str, StrategyInboundEvent], ...]:
    """Every fill and order event the step owes, addressed, in a fixed order.

    Per order the step touched, in the order it touched them: each of its fills
    -- to every strategy that asked for the order, with that strategy's share --
    and then one :class:`~alphalab.strategy.events.OrderEvent` with the status
    the order ended the step in. Then one ``"rejected"`` order event for each
    request that never became an order: refused by risk, unpriced, or in a
    currency this pipeline does not settle.

    Identities are derived from what they describe, never drawn from the run's
    identifier stream, so delivering feedback moves no other identifier.
    """

    by_request = {request.order_id: request for request in requests}
    fill_contributors = {
        record.trade_id: record.contributions for record in state.trade_records[records_before:]
    }
    reports_by_order: dict[str, list[ExecutionReport]] = {}
    for report in routed.reports:
        reports_by_order.setdefault(report.order_id, []).append(report)

    deliveries: list[tuple[str, StrategyInboundEvent]] = []
    seen: set[str] = set()
    for touched in routed.orders:
        order_id = str(touched.order_id.value)
        if order_id in seen:
            continue
        seen.add(order_id)
        order = state.oms.orders.find(touched.order_id)
        contributions = _contributors(order_id, contributions_before, state, by_request)
        for report in reports_by_order.get(order_id, ()):
            signed = report.fill_quantity if order.side is OMSSide.BUY else -report.fill_quantity
            shares = split_by_contribution(
                signed,
                fill_contributors.get(report.execution_id) or contributions,
                _ATTRIBUTION_QUANTUM,
            )
            for strategy_id, share in shares:
                deliveries.append(
                    (
                        strategy_id,
                        FillEvent(
                            f"fill:{report.execution_id}:{strategy_id}",
                            report.timestamp,
                            order_id,
                            report.asset_id,
                            report.fill_quantity,
                            report.fill_price,
                            side=order.side.value,
                            attributed_quantity=share,
                            execution_id=report.execution_id,
                        ),
                    )
                )
        for contribution in contributions:
            deliveries.append(
                (
                    contribution.strategy_id,
                    OrderEvent(
                        f"order:{order_id}:{instant!r}:{contribution.strategy_id}",
                        instant,
                        order_id,
                        order.asset_id,
                        order.status.value,
                        quantity=order.quantity,
                        filled_quantity=order.filled_quantity,
                    ),
                )
            )

    for request, reason in routed.dropped:
        for contribution in request.contributions:
            deliveries.append(
                (
                    contribution.strategy_id,
                    OrderEvent(
                        f"order:{request.order_id}:{instant!r}:{contribution.strategy_id}",
                        instant,
                        request.order_id,
                        request.asset_id,
                        OrderStatus.REJECTED.value,
                        reason,
                    ),
                )
            )
    return tuple(deliveries)


def _deliver_feedback(
    state: ExecutionPipelineState,
    event: MarketEvent,
    context_factory: ContextFactory,
    market_prices: Mapping[str, Decimal],
    rates: FxRates,
    policy: FillPolicy,
    *,
    routed: _Routed,
    requests: tuple[OrderRequest, ...],
    contributions_before: Mapping[str, tuple[StrategyContribution, ...]],
    records_before: int,
    at: float | None = None,
) -> tuple[ExecutionPipelineState, tuple[Intent, ...], tuple[OrderRequest, ...], _Routed]:
    """Deliver the step's fills and order events, and route what the strategies answer.

    The strategies see the book as the step leaves it -- after every fill -- and
    what they ask for goes through allocation, risk and the OMS like any intent,
    as orders that rest until their asset's next event (see
    :func:`_route_requests`). Nothing is built when no running strategy
    subscribed to fills or orders, or the step touched no order.
    """

    instant = event.timestamp if at is None else at
    if not (routed.orders or routed.dropped):
        return state, (), (), _NO_ROUTING
    if not _wants_feedback(state.strategy):
        return state, (), (), _NO_ROUTING
    deliveries = _feedback_events(
        state, instant, routed, requests, contributions_before, records_before
    )
    if not deliveries:
        return state, (), (), _NO_ROUTING

    populated = _populate_context(
        context_factory,
        portfolio=state.portfolio,
        risk=state.risk,
        market=state.market,
        market_prices=market_prices,
        shares=order_shares_by_strategy(state.oms, state.allocation),
        instruments=state.config.instruments,
        reopened=state.execution.reopened,
        window=state.config.retention.market_history,
        as_of=instant,
    )
    strategy, intents = StrategyEngine.deliver(state.strategy, deliveries, populated, instant)
    current, ordered, amended = _instructions_requested(
        evolve(state, strategy=strategy),
        intents,
        instant,
        event=event,
        policy=policy,
        rates=rates,
        rest=True,
    )
    if not ordered:
        # Exercise instructions, if any, are the caller's to apply (FUT-014).
        return current, intents, (), amended

    allocation, feedback_requests = _allocate(current, ordered, market_prices, rates, instant)
    current = evolve(current, allocation=allocation)
    current, feedback = _route_requests(
        current, event, ordered, feedback_requests, policy, rates, rest=True, at=instant
    )
    return current, intents, feedback_requests, amended.then(feedback)


def _simulate_fill(
    state: ExecutionPipelineState,
    order: OMSOrder,
    event: MarketEvent,
    policy: FillPolicy,
    rates: FxRates,
    price: Decimal | None = None,
) -> tuple[
    ExecutionPipelineState,
    tuple[ExecutionReport, ...],
    tuple[CoreFill, ...],
    tuple[CoreTrade, ...],
]:
    """Give a simulated order its one attempt at ``event``, and settle what it did.

    ``price`` is the price it executes against when that is not the event's
    market price -- a stop order triggered by a bar that gapped through it.
    """

    base = state.market_prices[order.asset_id] if price is None else price
    if _venue_blocks(state, order.asset_id, event.timestamp, base):
        # A stop reached at a price beyond the band trades nothing there (FUT-020).
        return state, (), (), ()
    decision = _decide_fill(policy, order, event, base, _whole_units(state))
    if _kills(order, decision):
        return _end_unfilled(state, order, event.timestamp), (), (), ()
    current, reports = _execute_order(state, order, decision, event, price=base)
    # A rejected, expired or unfilled execution produces no report. The order
    # never trades, so close it out of the OMS instead of leaving it open
    # forever awaiting a fill, and retire both ledgers it holds. The order is
    # terminal by the time _release_if_terminal is asked, which is what lets
    # that one function serve every terminal transition.
    if not reports and decision.status in _NON_TRADING_STATUSES:
        current = evolve(
            current,
            oms=_close_unfilled_order(current.oms, order, decision.status, event.timestamp),
        )
        current = _release_if_terminal(current, order.order_id, event.timestamp)

    current, fills, trades = _apply_reports(current, order, reports, rates)
    current = _withdraw_partial_remainder(current, order, event.timestamp)
    return current, reports, fills, trades


#: The daily-or-longer interval units: a bar of one of these opens and closes a
#: session, so its open and close are auction prices.
_SESSION_UNITS: Final = frozenset({IntervalUnit.DAY, IntervalUnit.WEEK, IntervalUnit.MONTH})


def _session_bar(event: MarketEvent, asset_id: str) -> Bar | None:
    """The event's bar, when it is a daily-or-longer bar for ``asset_id``."""

    if (
        isinstance(event, BarClosed)
        and event.bar.asset_id == asset_id
        and event.bar.timeframe.unit in _SESSION_UNITS
    ):
        return event.bar
    return None


def _has_session_bars(market: MarketState, asset_id: str) -> bool:
    """Whether the run has published a daily-or-longer bar for ``asset_id``."""

    return any(
        f"{asset_id}_{interval.code}" in market.latest_bars
        for interval in (TimeFrame.D1, TimeFrame.W1, TimeFrame.MN1)
    )


def _settlement_calendar(
    state: ExecutionPipelineState, asset_id: str
) -> tuple[InstrumentRecord, Any] | None:
    """The record and calendar a fill of ``asset_id`` settles by; ``None``: it settles at once.

    ``None`` when the run configures no registry, the asset is undeclared, or
    its record declares no settlement rule (FUT-015). The calendar is ``None``
    when the rule is declared and its listing venue's calendar is not.
    """

    if state.config.instruments is None:
        return None
    record = _declared(state, asset_id)
    if record is None or record.settlement is None:
        return None
    return record, state.config.calendars.calendar_for(record.exchange)


def _settlement_calendar_refusal(state: ExecutionPipelineState, asset_id: str) -> str | None:
    """Why an order in ``asset_id`` cannot settle, or ``None`` (FUT-015)."""

    found = _settlement_calendar(state, asset_id)
    if found is None or found[1] is not None:
        return None
    record = found[0]
    return (
        f"{record.symbol} on {record.exchange} settles T+n by its venue's calendar, and no "
        f"calendar is declared for {record.exchange}: declare it on "
        "ExecutionPipelineConfig.calendars."
    )


def _settles_at(state: ExecutionPipelineState, asset_id: str, timestamp: float) -> float | None:
    """When a fill of ``asset_id`` at ``timestamp`` settles, or ``None``: at once (FUT-015).

    By the instrument's declared rule over its listing venue's declared calendar:
    the trade date is the session the fill belongs to -- the local date, when it
    falls outside one -- and the instant is the settlement date's first open.
    """

    found = _settlement_calendar(state, asset_id)
    if found is None:
        return None
    record, calendar = found
    if calendar is None:
        raise RuntimeValidationError(str(_settlement_calendar_refusal(state, asset_id)))
    assert record.settlement is not None  # _settlement_calendar returned it
    day = calendar.trading_day_of(timestamp) or calendar.local_datetime(timestamp).date()
    return settles_at(calendar, record.settlement, day)


def _terms_refusal(
    state: ExecutionPipelineState, request: OrderRequest, instant: float
) -> str | None:
    """Why an order on these terms cannot be placed here, or ``None`` when it can.

    An order already past its expiry is refused on either routing. Two more are
    refused only in simulation, where the pipeline itself must work the order:
    a resting day order that states no close when no calendar is declared for
    its venue -- the pipeline will not guess a session (EXE-010) -- and an
    auction order for an asset the run has no daily bars for, since an auction
    price is a daily bar's open or close.
    """

    terms = request.terms
    if terms.expire_at is not None and instant >= terms.expire_at:
        return (
            f"The order would be placed at {instant!r}, and its terms expire it at "
            f"{terms.expire_at!r}."
        )
    unsettleable = _settlement_calendar_refusal(state, request.asset_id)
    if unsettleable is not None:
        return unsettleable
    if state.config.routing is ExecutionRouting.EXTERNAL:
        return None
    if state.config.routing is ExecutionRouting.ALGORITHMIC:
        unworkable = _algorithmic_terms_refusal(terms)
        if unworkable is not None:
            return unworkable
    if _needs_session_close(terms):
        _, refusal = _day_order_close(state, request.asset_id, instant)
        if refusal is not None:
            return refusal
    if terms.is_auction and not _has_session_bars(state.market, request.asset_id):
        return (
            f"An auction order fills at a daily bar's open or close, and this run has published "
            f"no daily bar for {request.asset_id}."
        )
    return None


def _algorithmic_terms_refusal(terms: OrderTerms) -> str | None:
    """Why an execution algorithm cannot work a parent on these terms, or ``None``.

    An algorithm's children are market or limit orders
    (:class:`~alphalab.execution.algorithms.AlgorithmTerms`), sent over a window:
    a stop has no trigger to slice, an auction is one price at one instant, and an
    immediate order ends at its first attempt, which a window contradicts.
    Refused before an order exists, so nothing is held and nothing is filled
    whole in its place.
    """

    if terms.order_type not in (OrderType.MARKET, OrderType.LIMIT):
        return (
            f"An execution algorithm works MARKET or LIMIT parents in children, not "
            f"{terms.order_type}; the order was not placed."
        )
    if terms.is_auction or terms.is_immediate:
        return (
            f"An execution algorithm works a parent over a window, and a "
            f"{terms.time_in_force} order is one attempt; the order was not placed."
        )
    return None


def _needs_session_close(terms: OrderTerms) -> bool:
    """Whether a simulated order on these terms expires at a session close nobody stated."""

    return terms.rests and terms.time_in_force is TimeInForce.DAY and terms.expire_at is None


def _listing_exchange(state: ExecutionPipelineState, asset_id: str) -> str | None:
    """The venue ``asset_id`` is listed on, when the run's registry holds it."""

    instruments = state.config.instruments
    record = None if instruments is None else instruments.record_for(asset_id)
    return None if record is None else record.exchange


def _day_order_close(
    state: ExecutionPipelineState, asset_id: str, instant: float
) -> tuple[float | None, str | None]:
    """When a simulated day order in ``asset_id`` placed at ``instant`` expires, or why
    it cannot be said: ``(close, None)`` or ``(None, refusal)`` (EXE-010).

    The close is the last of the trading day the order is good for, by the
    declared calendar of the asset's listing venue
    (:meth:`~alphalab.data.calendar.MarketCalendar.day_order_expiry`).
    """

    exchange = _listing_exchange(state, asset_id)
    calendar = state.config.calendars.calendar_for(exchange)
    if calendar is None:
        venue = (
            "its listing venue is not known"
            if exchange is None
            else f"its listing venue {exchange} has no calendar declared"
        )
        return None, (
            f"A day order rests until its trading day ends, and {venue}: declare the "
            "calendar on ExecutionPipelineConfig.calendars, state the close as expire_at, "
            "or use GTC or GTD."
        )
    close = calendar.day_order_expiry(instant)
    if close is None:
        return None, (
            f"A day order rests until its trading day ends, and calendar "
            f"{calendar.calendar_id} declares no session within "
            f"{MAX_SESSION_SEARCH_DAYS} days of {instant!r}."
        )
    return close, None


def _kills(order: OMSOrder, decision: FillDecision) -> bool:
    """Whether a fill-or-kill order must be killed rather than filled as decided."""

    if order.time_in_force is not TimeInForce.FOK:
        return False
    if decision.status not in (FillStatus.FULL_FILL, FillStatus.PARTIAL_FILL):
        return True
    quantity = decision.quantity if decision.quantity is not None else order.remaining_quantity
    return quantity < order.remaining_quantity


def _end_unfilled(
    state: ExecutionPipelineState, order: OMSOrder, timestamp: float
) -> ExecutionPipelineState:
    """Cancel an order that will not fill, and free what it held."""

    cancelled = evolve(state, oms=OMSEngine.cancel(state.oms, order.order_id, timestamp))
    return _release_if_terminal(cancelled, order.order_id, timestamp)


def _expire(
    state: ExecutionPipelineState, order: OMSOrder, timestamp: float
) -> ExecutionPipelineState:
    """Expire an order whose lifetime ended, and free what it held."""

    expired = evolve(state, oms=OMSEngine.expire(state.oms, order.order_id, timestamp))
    return _release_if_terminal(expired, order.order_id, timestamp)


def _stop_price_reached(
    order: OMSOrder, event: MarketEvent, market_price: Decimal, *, arriving: bool
) -> Decimal | None:
    """The price a stop order executes against at ``event``, or ``None`` if not reached.

    A buy stop is reached when the market trades at or above its stop, a sell
    stop at or below. An order meeting the market at the event it was placed
    on sees only that event's price; a resting order sees everything the event
    shows -- a bar's whole range -- and a bar that opened beyond the stop fills
    the stop at the open, where the market actually was.
    """

    stop = order.stop_price
    assert stop is not None  # a stop order names its stop
    buy = order.side is OMSSide.BUY
    if not arriving and isinstance(event, BarClosed):
        bar = event.bar
        if buy and bar.high >= stop:
            return max(bar.open, stop)
        if not buy and bar.low <= stop:
            return min(bar.open, stop)
        return None
    if isinstance(event, QuoteReceived):
        # The side a stop order would take once triggered.
        observed = event.quote.ask if buy else event.quote.bid
    else:
        observed = market_price
    reached = observed >= stop if buy else observed <= stop
    return market_price if reached else None


def _resting_fill_price(order: OMSOrder, event: MarketEvent) -> Decimal | None:
    """The price a resting limit order fills at, at ``event``, or ``None`` if not reached.

    It fills at its own limit when the market trades through it -- the resting
    order was the liquidity there -- and at a bar's open when the bar opened
    beyond it, which is a better price the market genuinely offered.
    """

    limit = order.limit_price
    assert limit is not None  # a limit order names its limit
    buy = order.side is OMSSide.BUY
    if isinstance(event, QuoteReceived):
        quote = event.quote
        crossed = quote.ask <= limit if buy else quote.bid >= limit
        return limit if crossed else None
    if isinstance(event, BarClosed):
        bar = event.bar
        if buy:
            return min(bar.open, limit) if bar.low <= limit else None
        return max(bar.open, limit) if bar.high >= limit else None
    if isinstance(event, TickReceived | TradeReceived):
        price = event.tick.price
        crossed = price <= limit if buy else price >= limit
        return limit if crossed else None
    return None


def _within_limit(order: OMSOrder, price: Decimal) -> bool:
    limit = order.limit_price
    assert limit is not None
    return price <= limit if order.side is OMSSide.BUY else price >= limit


def _taker_price(
    state: ExecutionPipelineState, order: OMSOrder, event: MarketEvent, base: Decimal
) -> Decimal:
    """What taking the market at ``event`` would cost per unit, by the run's cost model."""

    simulator = state.config.simulator
    bid, ask = _available_quote(event)
    context = simulator.context(
        _instruction(order, state),
        order.remaining_quantity,
        base,
        event.timestamp,
        bid,
        ask,
        _available_quantity(event, order.side),
    )
    return simulator.costs.fill_price(context, simulator.costs.quote(context))


def _work_resting(
    state: ExecutionPipelineState,
    order: OMSOrder,
    event: MarketEvent,
    policy: FillPolicy,
    rates: FxRates,
    *,
    price: Decimal,
    passive: bool,
    one_shot: bool,
    cap: Decimal | None = None,
) -> tuple[
    ExecutionPipelineState,
    tuple[ExecutionReport, ...],
    tuple[CoreFill, ...],
    tuple[CoreTrade, ...],
]:
    """Fill what the event offers a limit or auction order at ``price``.

    What does not fill goes on resting, unless the order is ``one_shot`` -- an
    immediate-or-cancel, fill-or-kill or auction order -- when it is cancelled.
    A decision to fill nothing is not a refusal of the order: a resting order
    that found no liquidity this event rests. ``cap`` is what a queue model
    allows to fill, when less than the queue cleared (FUT-011).
    """

    if _venue_blocks(state, order.asset_id, event.timestamp, price):
        # The price it would fill at is outside the band in force (FUT-020).
        current = _end_unfilled(state, order, event.timestamp) if one_shot else state
        return current, (), (), ()
    decision = _decide_fill(policy, order, event, price, _whole_units(state))
    if cap is not None:
        decision = _within_queue(decision, order, cap, _whole_units(state))
    if _kills(order, decision):
        return _end_unfilled(state, order, event.timestamp), (), (), ()
    if decision.status is FillStatus.NO_FILL:
        current = _end_unfilled(state, order, event.timestamp) if one_shot else state
        return current, (), (), ()
    current, reports = _execute_order(state, order, decision, event, price=price, passive=passive)
    if not reports and decision.status in _NON_TRADING_STATUSES:
        current = evolve(
            current,
            oms=_close_unfilled_order(current.oms, order, decision.status, event.timestamp),
        )
        current = _release_if_terminal(current, order.order_id, event.timestamp)
    current, fills, trades = _apply_reports(current, order, reports, rates)
    if one_shot:
        current = _withdraw_partial_remainder(current, order, event.timestamp)
    return current, reports, fills, trades


def _work_order(
    state: ExecutionPipelineState,
    order: OMSOrder,
    event: MarketEvent,
    policy: FillPolicy,
    rates: FxRates,
    *,
    arriving: bool,
    queue_turn: QueueTurn | None = None,
) -> tuple[
    ExecutionPipelineState,
    tuple[ExecutionReport, ...],
    tuple[CoreFill, ...],
    tuple[CoreTrade, ...],
]:
    """One simulated order's turn at ``event`` (ledger EXE-003).

    ``arriving`` is the order's first turn, at the event it was placed on; it
    sees only that event's price, since everything else the event shows -- a
    bar's range -- happened before the order existed. The rules:

    * **Expired** -- a good-til-date or day order at or past ``expire_at`` --
      expires before anything else is considered.
    * **Market** orders have their one attempt, as every order did before v3.11;
      what does not fill is withdrawn, and fill-or-kill fills all or nothing.
    * **Auction** orders wait for their asset's next daily bar and fill at its
      open (``OPG``) or close (``CLS``) -- a limit-on-open or -close only within
      its limit -- or expire unfilled. Not at the bar they were placed on: its
      auctions are over.
    * **Stop** orders wait until the market reaches the stop, then have one
      market attempt at the price that reached it. A **stop-limit** order is
      marked triggered and works from then as a limit order.
    * **Limit** orders meeting the market take it if the run's cost model
      prices taking it within the limit; otherwise, and afterwards, they rest,
      and fill at their limit -- or at a bar's better open -- when the market
      trades through it, as a maker: no spread, slippage or impact. What does
      not fill rests on; an immediate-or-cancel or fill-or-kill order is
      cancelled after its first turn instead. Under a queue model
      (``queue_turn``, FUT-011) a resting turn fills only what the model says
      has reached the front of the order's price level.
    """

    timestamp = event.timestamp
    terms = order.terms
    if order.expire_at is not None and timestamp >= order.expire_at:
        return _expire(state, order, timestamp), (), (), ()
    market_price = state.market_prices[order.asset_id]
    one_shot = terms.is_immediate
    if _venue_blocks(state, order.asset_id, timestamp, market_price):
        # Halted, or the market outside the venue's band (FUT-020): nothing
        # trades at this event. The order rests -- a market order too, to its
        # next turn after -- unless it was to trade now or never.
        return (_end_unfilled(state, order, timestamp) if one_shot else state), (), (), ()

    if terms.is_auction:
        bar = None if arriving else _session_bar(event, order.asset_id)
        if bar is None:
            return state, (), (), ()
        price = bar.open if order.time_in_force is TimeInForce.OPG else bar.close
        if order.order_type is OrderType.LIMIT and not _within_limit(order, price):
            return _expire(state, order, timestamp), (), (), ()
        return _work_resting(
            state, order, event, policy, rates, price=price, passive=True, one_shot=True
        )

    if order.order_type is OrderType.MARKET:
        return _simulate_fill(state, order, event, policy, rates)

    current = state
    execution_price: Decimal | None = None
    if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and order.triggered_at is None:
        reached = _stop_price_reached(order, event, market_price, arriving=arriving)
        if reached is None:
            current = _end_unfilled(current, order, timestamp) if one_shot else current
            return current, (), (), ()
        if order.order_type is OrderType.STOP:
            return _simulate_fill(current, order, event, policy, rates, price=reached)
        current = evolve(current, oms=OMSEngine.trigger(current.oms, order.order_id, timestamp))
        order = current.oms.orders.find(order.order_id)
        # Triggered in this event: it meets the market as a limit order now.
        arriving, execution_price = True, reached

    if arriving:
        base = market_price if execution_price is None else execution_price
        if _within_limit(order, _taker_price(current, order, event, base)):
            return _work_resting(
                current, order, event, policy, rates, price=base, passive=False, one_shot=one_shot
            )
        current = _end_unfilled(current, order, timestamp) if one_shot else current
        return current, (), (), ()

    resting_price = _resting_fill_price(order, event)
    if queue_turn is not None and not queue_turn.may_fill:
        resting_price = None
    if resting_price is None:
        current = _end_unfilled(current, order, timestamp) if one_shot else current
        return current, (), (), ()
    return _work_resting(
        current,
        order,
        event,
        policy,
        rates,
        price=resting_price,
        passive=True,
        one_shot=one_shot,
        cap=None if queue_turn is None else queue_turn.eligible,
    )


def _auctions_declared(state: ExecutionPipelineState) -> bool:
    """Whether any of the run's venues reopens a halt by auction (FUT-027)."""

    controls = _controls(state)
    return controls is not None and controls.auctions


def _controls(state: ExecutionPipelineState) -> TradingControls | None:
    """The trading controls the run's venues declare, or ``None``: one read of the registry."""

    registry = state.config.instruments
    return None if registry is None else registry.controls


def _auction_due(
    state: ExecutionPipelineState, event: MarketEvent, asset_id: str
) -> TradingHalt | None:
    """The halt whose reopening auction is held at ``event``, or ``None``.

    A halt declaring an auction reopening (FUT-027) has ended by ``event``, and
    the run has not yet held its auction -- or held it and extended it to an
    instant ``event`` has reached (FUT-033). Read from the reopening the run
    records for the instrument, never from the market history it keeps, so
    however short that history is, a later event is never taken for the first.
    """

    controls = _controls(state)
    if controls is None:
        return None
    record = _declared(state, asset_id)
    venue = "" if record is None else record.exchange
    halt = controls.reopened_by_auction(asset_id, venue, event.timestamp)
    if halt is None or halt.end is None:
        return None
    held = state.execution.reopened.get(asset_id)
    if held is None or held.halt_end < halt.end:
        return halt
    if held.extended_to is not None and event.timestamp >= held.extended_to:
        return halt
    return None


def _reopen(
    state: ExecutionPipelineState,
    event: MarketEvent,
    asset_id: str,
    halt: TradingHalt,
    policy: FillPolicy,
    rates: FxRates,
    queue: QueueBook | None,
) -> tuple[ExecutionPipelineState, _Routed, QueueBook | None]:
    """Hold ``halt``'s reopening auction at ``event``, and record it (FUT-027, FUT-033)."""

    assert halt.end is not None  # only an ended halt reopens
    earlier = state.execution.reopened.get(asset_id)
    extended = (
        earlier is not None and earlier.halt_end == halt.end and earlier.extended_to is not None
    )
    # The orders that waited for this auction: placed before the halt ended, or
    # before the extension it was held over to.
    ended = earlier.extended_to if extended and earlier is not None else halt.end
    assert ended is not None
    if halt.uncross is None:
        price = event.bar.open if isinstance(event, BarClosed) else None
        if price is None:
            priced = _market_price(event)
            price = None if priced is None else priced[1]
        held = AuctionReopening(halt.end, event.timestamp, price)
        current = _held_auction(state, asset_id, held)
        if price is None:
            return current, _NO_ROUTING, queue
        return _uncross(current, event, asset_id, price, ended, None, policy, rates, queue)
    outcome = _computed_uncross(state, event, asset_id, halt, ended)
    price, matched, imbalance, allotted, reason = outcome
    if price is not None and _venue_blocks(state, asset_id, event.timestamp, price):
        extension = halt.uncross.extension
        if extension is not None and not extended:
            # Outside the band: the venue extends the auction once, and nothing
            # trades in the instrument until it is held again.
            held = AuctionReopening(
                halt.end,
                event.timestamp,
                None,
                matched,
                imbalance,
                event.timestamp + extension,
                f"The uncross, {price}, was outside the band in force; extended.",
            )
            return (
                _held_auction(state, asset_id, held),
                _NO_ROUTING,
                queue,
            )
        price, reason = None, f"The uncross, {price}, was outside the band in force."
    held = AuctionReopening(halt.end, event.timestamp, price, matched, imbalance, None, reason)
    current = _held_auction(state, asset_id, held)
    if price is None:
        # Nothing crossed: every order rests on into continuous trading, from
        # the next event, as it would after a halt with no auction.
        return current, _NO_ROUTING, queue
    return _uncross(current, event, asset_id, price, ended, allotted, policy, rates, queue)


def _held_auction(
    state: ExecutionPipelineState, asset_id: str, held: AuctionReopening
) -> ExecutionPipelineState:
    """``state`` recording ``held`` as ``asset_id``'s last reopening auction."""

    execution = state.execution
    return evolve(
        state, execution=evolve(execution, reopened=execution.reopened.set(asset_id, held))
    )


def _computed_uncross(
    state: ExecutionPipelineState,
    event: MarketEvent,
    asset_id: str,
    halt: TradingHalt,
    ended: float,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, dict[str, Decimal], str]:
    """The auction's price, matched volume, imbalance and the run's allotments (FUT-033).

    From the book the venue published for this halt -- its interest at each
    price and at market -- and the run's own orders that waited for the
    auction: each market order, and each limit order. The price is the one, of
    every price stated, that matches the most volume; then the smallest
    imbalance; then the declared tie-break. Nothing crosses with no book
    published, with no volume matched, or with a tie the declared rule cannot
    break. Only the run's own orders are allotted fills: by price, then -- at
    the marginal price on the side with more interest -- by the declared
    priority against the published interest there, in the instrument's trading
    step, the remainder unfilled.
    """

    ctx = ACCOUNTING_CONTEXT
    controls = _controls(state)
    assert controls is not None and halt.uncross is not None
    book = controls.auction_book(asset_id, halt.start, event.timestamp)
    if book is None:
        return None, None, None, {}, "The venue published no auction book for the halt."
    own = [
        order
        for order in (state.oms.orders.find(i) for i in state.oms.working_orders_for(asset_id))
        if order.created_at < ended and order.order_type in (OrderType.MARKET, OrderType.LIMIT)
    ]
    zero = Decimal("0")

    def interest(price: Decimal) -> tuple[Decimal, Decimal]:
        demand = book.market_buy + sum((q for p, q in book.bids if p >= price), zero)
        supply = book.market_sell + sum((q for p, q in book.offers if p <= price), zero)
        for order in own:
            reaches = order.order_type is OrderType.MARKET or _within_limit(order, price)
            if not reaches:
                continue
            if order.side is OMSSide.BUY:
                demand += order.remaining_quantity
            else:
                supply += order.remaining_quantity
        return demand, supply

    prices = sorted({*book.prices, *(o.limit_price for o in own if o.limit_price is not None)})
    if not prices:
        return None, None, None, {}, "No price is stated in the auction, so it has none."
    ranked = []
    for price in prices:
        demand, supply = interest(price)
        ranked.append((min(demand, supply), abs(demand - supply), price, demand - supply))
    most = max(matched for matched, *_ in ranked)
    if most <= 0:
        return None, zero, None, {}, "The auction's interest does not cross."
    least = min(gap for matched, gap, *_ in ranked if matched == most)
    tied = [
        (price, signed) for matched, gap, price, signed in ranked if (matched, gap) == (most, least)
    ]
    rule = halt.uncross.tie_break
    if rule is AuctionTieBreak.HIGHEST:
        price, signed = tied[-1]
    elif rule is AuctionTieBreak.LOWEST:
        price, signed = tied[0]
    else:
        reference = controls.reference(asset_id, event.timestamp)
        if reference is None:
            return None, most, None, {}, "No reference price is in effect to break the tie by."
        price, signed = min(tied, key=lambda t: (abs(t[0] - reference.price), t[0]))
    allotted: dict[str, Decimal] = {}
    for side in (OMSSide.BUY, OMSSide.SELL):
        buying = side is OMSSide.BUY
        published = sorted(book.bids if buying else book.offers, reverse=buying)
        mine = [o for o in own if o.side is side]
        # Tiers by price priority: at market first, then each limit from the
        # best, down to the clearing price.
        tiers: list[tuple[Decimal, list[OMSOrder]]] = [
            (book.market_buy if buying else book.market_sell,
             [o for o in mine if o.order_type is OrderType.MARKET]),
        ]  # fmt: skip
        limits = sorted(
            {o.limit_price for o in mine if o.limit_price is not None and _within_limit(o, price)}
            | {p for p, _ in published if (p >= price if buying else p <= price)},
            reverse=buying,
        )
        for level in limits:
            tiers.append(
                (
                    sum((q for p, q in published if p == level), zero),
                    [o for o in mine if o.order_type is OrderType.LIMIT and o.limit_price == level],
                )
            )
        left = most
        for published_quantity, orders in tiers:
            wanted = sum((o.remaining_quantity for o in orders), zero)
            standing = published_quantity + wanted
            if left >= standing:
                allotted.update((str(o.order_id.value), o.remaining_quantity) for o in orders)
                left -= standing
                continue
            if halt.uncross.priority is AuctionPriority.PUBLISHED_FIRST:
                share = max(zero, left - published_quantity)
                for order in orders:  # by time: the OMS keeps them in placement order
                    take = min(order.remaining_quantity, share)
                    take = _in_steps(state, asset_id, take)
                    allotted[str(order.order_id.value)] = take
                    share -= take
            else:
                for order in orders:
                    take = ctx.divide(ctx.multiply(order.remaining_quantity, left), standing)
                    allotted[str(order.order_id.value)] = _in_steps(state, asset_id, take)
            left = zero
            break
    return price, most, signed, allotted, ""


def _in_steps(state: ExecutionPipelineState, asset_id: str, quantity: Decimal) -> Decimal:
    """``quantity`` rounded down to what the instrument trades in: whole lots, or whole units."""

    step = _trading_step(state, asset_id, quantity)
    if step is None or quantity <= 0:
        return max(Decimal("0"), quantity)
    return ACCOUNTING_CONTEXT.multiply((quantity / step).to_integral_value(ROUND_FLOOR), step)


def _uncross(
    state: ExecutionPipelineState,
    event: MarketEvent,
    asset_id: str,
    price: Decimal,
    ended: float,
    allotted: Mapping[str, Decimal] | None,
    policy: FillPolicy,
    rates: FxRates,
    queue: QueueBook | None,
) -> tuple[ExecutionPipelineState, _Routed, QueueBook | None]:
    """The reopening auction: every order that waited for it crosses at ``price``.

    In the order they were placed, each market order, and each limit order whose
    limit the price reaches, fills at the one price as a maker: under a computed
    uncross (FUT-033) exactly what it was ``allotted``; read from the print
    (FUT-027), as far as the run's fill policy gives it at this event. A market
    order's remainder is withdrawn, a limit order's rests on into continuous
    trading, and an order the price does not reach -- a limit beyond it, a stop
    not reached -- takes no part. Outside a band in force the auction does not
    cross at this event: every order rests, as through the halt (FUT-020,
    FUT-028). A queue model does not order an auction: it crosses by time
    (FUT-011).
    """

    if _venue_blocks(state, asset_id, event.timestamp, price):
        return state, _NO_ROUTING, queue
    current = state
    orders: list[OMSOrder] = []
    reports: list[ExecutionReport] = []
    fills: list[CoreFill] = []
    trades: list[CoreTrade] = []
    for order_id in state.oms.working_orders_for(asset_id):
        order = current.oms.orders.find(order_id)
        if order.created_at >= ended:
            # Placed after the halt ended, so it did not rest through it: its
            # turn is continuous trading's, as at any other event.
            current, new_reports, new_fills, new_trades = _work_order(
                current, order, event, policy, rates, arriving=False
            )
            orders.append(current.oms.orders.find(order_id))
            reports.extend(new_reports)
            fills.extend(new_fills)
            trades.extend(new_trades)
            continue
        market = order.order_type is OrderType.MARKET
        if not market and not (order.order_type is OrderType.LIMIT and _within_limit(order, price)):
            continue
        given = policy
        if allotted is not None:
            allot = min(allotted.get(str(order_id.value), Decimal("0")), order.remaining_quantity)
            if allot <= 0:
                if market:
                    current = _end_unfilled(current, order, event.timestamp)
                    orders.append(current.oms.orders.find(order_id))
                continue
            whole = allot == order.remaining_quantity
            given = StaticFill(FillStatus.FULL_FILL if whole else FillStatus.PARTIAL_FILL, allot)
        current, new_reports, new_fills, new_trades = _work_resting(
            current, order, event, given, rates, price=price, passive=True, one_shot=market
        )
        after = current.oms.orders.find(order_id)
        if queue is not None and not after.is_open:
            queue = queue.without(str(order_id.value))
        orders.append(after)
        reports.extend(new_reports)
        fills.extend(new_fills)
        trades.extend(new_trades)
    routed = _Routed(
        orders=tuple(orders),
        reports=tuple(reports),
        fills=tuple(fills),
        trades=tuple(trades),
    )
    return current, routed, queue


def _fill_working_orders(
    state: ExecutionPipelineState,
    event: MarketEvent,
    asset_id: str,
    policy: FillPolicy,
    rates: FxRates,
    queue: QueueBook | None = None,
) -> tuple[ExecutionPipelineState, _Routed, QueueBook | None]:
    """Work the simulated orders left working in ``asset_id``, at ``event``.

    In the order they were placed, each gets its turn at what this event showed
    -- see :func:`_work_order`: a market order its one attempt, a resting order
    the fill the event's prices give it, or nothing. Under a queue model each
    resting limit order's position is advanced by the event first, from where
    it stood -- or, for an order that has not joined, from an unknown queue --
    and its turn fills only what that allows (FUT-011).
    """

    halt = _auction_due(state, event, asset_id) if _auctions_declared(state) else None
    if halt is not None:
        return _reopen(state, event, asset_id, halt, policy, rates, queue)
    current = state
    observation = None if queue is None else _queue_observation(event)
    orders: list[OMSOrder] = []
    reports: list[ExecutionReport] = []
    fills: list[CoreFill] = []
    trades: list[CoreTrade] = []
    for order_id in state.oms.working_orders_for(asset_id):
        order = current.oms.orders.find(order_id)
        turn = None
        key = ""
        if queue is not None and observation is not None:
            key = str(order_id.value)
            if _queued(order):
                held = queue.position_of(key)
                limit = order.limit_price
                assert limit is not None  # a queued order is a limit order
                position = held
                if position is None or position.limit_price != limit:
                    position = QueuePosition(limit, None)
                turn = queue.model.advance(position, order.side, observation)
                if turn.position != held:
                    queue = queue.placed(key, turn.position)
        current, new_reports, new_fills, new_trades = _work_order(
            current, order, event, policy, rates, arriving=False, queue_turn=turn
        )
        after = current.oms.orders.find(order_id)
        if queue is not None and observation is not None:
            if not after.is_open:
                # Filled, expired or withdrawn: it leaves the queue with the book.
                queue = queue.without(key)
            elif turn is None and _queued(after):
                # A stop-limit triggered at this event rests from here: it joins now.
                limit = after.limit_price
                assert limit is not None
                queue = queue.placed(key, queue.model.join(after.side, limit, observation))
        orders.append(after)
        reports.extend(new_reports)
        fills.extend(new_fills)
        trades.extend(new_trades)
    routed = _Routed(
        orders=tuple(orders), reports=tuple(reports), fills=tuple(fills), trades=tuple(trades)
    )
    return current, routed, queue


def _queued(order: OMSOrder) -> bool:
    """Whether a queue model governs ``order``: a limit order resting in continuous trading.

    A limit order, or a stop-limit once triggered, that is neither immediate --
    it never rests -- nor an auction order, which an auction fills at one price
    rather than a queue (FUT-011).
    """

    if order.time_in_force in _NOT_QUEUED:
        return False
    if order.order_type is OrderType.LIMIT:
        return True
    return order.order_type is OrderType.STOP_LIMIT and order.triggered_at is not None


#: Times in force whose orders never rest in a queue: they end at their first
#: attempt (``IOC``, ``FOK``) or are filled by an auction (``OPG``, ``CLS``) --
#: what :attr:`OrderTerms.is_immediate` and :attr:`OrderTerms.is_auction` name,
#: read off the order without building its terms on every turn.
_NOT_QUEUED: Final = frozenset({TimeInForce.IOC, TimeInForce.FOK, TimeInForce.OPG, TimeInForce.CLS})


_AGGRESSOR: Final = {TradeAggressor.BUYER: CoreSide.BUY, TradeAggressor.SELLER: CoreSide.SELL}


def _queue_observation(event: MarketEvent) -> QueueObservation:
    """What ``event`` showed that a queue model reads -- see :class:`QueueObservation`."""

    if isinstance(event, QuoteReceived):
        quote = event.quote
        return QueueObservation(
            event.timestamp,
            bid=quote.bid,
            bid_size=quote.bid_size,
            ask=quote.ask,
            ask_size=quote.ask_size,
        )
    if isinstance(event, TickReceived | TradeReceived):
        tick = event.tick
        return QueueObservation(
            event.timestamp,
            trade_price=tick.price,
            trade_quantity=tick.quantity,
            aggressor=None if tick.aggressor is None else _AGGRESSOR[tick.aggressor],
        )
    if isinstance(event, BarClosed):
        return QueueObservation(event.timestamp, low=event.bar.low, high=event.bar.high)
    return QueueObservation(event.timestamp)


def _queue_after_step(
    queue: QueueBook,
    state: ExecutionPipelineState,
    event: MarketEvent,
    update: tuple[str, Decimal] | None,
    resting_before: int,
) -> QueueBook:
    """``queue`` after a step: the orders it left resting in the event's asset joined.

    Each joins at this event -- the one it was placed at -- from what the
    event showed, which the strategy had seen before placing it. They are the
    asset's working orders after the ``resting_before`` that were working when
    the step began placing orders: the working orders of an asset are kept in
    the order they were placed. An order placed outside a market event of its
    asset -- by a timer, a slice or an observation -- has no event of its own
    to join at, and starts its first turn from an unknown queue instead.

    Orders that end at their turn leave the book there
    (:func:`_fill_working_orders`); one ended otherwise -- a settlement, a
    split, an outcome a caller reports -- is never worked again, and leaves it
    when the driver next keeps only working orders (at a pause, and at the end).
    """

    if state.config.routing is not ExecutionRouting.SIMULATED or update is None:
        return queue
    if _working_count(state, update[0]) == resting_before:
        return queue  # the step placed nothing that rests in this asset
    placed = state.oms.working_orders_for(update[0])[resting_before:]
    observation: QueueObservation | None = None
    for order_id in placed:
        key = str(order_id.value)
        if queue.position_of(key) is not None:
            continue
        order = state.oms.orders.find(order_id)
        if not _queued(order):
            continue
        limit = order.limit_price
        assert limit is not None  # a queued order is a limit order
        if observation is None:
            observation = _queue_observation(event)
        queue = queue.placed(key, queue.model.join(order.side, limit, observation))
    return queue


def _working_count(state: ExecutionPipelineState, asset_id: str) -> int:
    """How many orders are working in ``asset_id``, without listing them."""

    working = state.oms.working_by_asset.get(asset_id)
    return 0 if working is None else len(working)


def _within_queue(
    decision: FillDecision, order: OMSOrder, cap: Decimal, whole_units: bool
) -> FillDecision:
    """``decision`` held to what a queue model allows, in whole units when the run trades them."""

    if decision.status not in (FillStatus.FULL_FILL, FillStatus.PARTIAL_FILL):
        return decision
    quantity = order.remaining_quantity if decision.quantity is None else decision.quantity
    if quantity <= cap:
        return decision
    allowed = cap.to_integral_value(rounding=decimal.ROUND_DOWN) if whole_units else cap
    if allowed <= Decimal("0"):
        return FillDecision(FillStatus.NO_FILL, None)
    return FillDecision(FillStatus.PARTIAL_FILL, allowed)


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


def _committed_positions(
    state: ExecutionPipelineState, intents: tuple[Intent, ...]
) -> Mapping[str, Decimal]:
    """Each intended asset's filled position plus its working orders, signed, in units.

    What long-only allocation is judged against (ledger ALC-001): a sale that
    closes a long passes, and one that would leave a short -- counting sales
    already working -- does not. And, since v3.11, what the budget is judged
    against: an order commits only the exposure it adds to this position, so a
    sale that reduces it commits nothing (ledger ALC-007). Plain numbers, so
    ``alphalab.allocation`` goes on knowing nothing of the portfolio or the
    OMS. Read only for the assets the strategies named, through the OMS's
    per-asset working index rather than every working order (PRF-001).
    """

    committed: dict[str, Decimal] = {}
    if not intents:
        return committed
    oms = state.oms
    for asset_id in {intent.instrument for intent in intents}:
        held = state.portfolio.positions.get(asset_id)
        total = Decimal("0") if held is None else held.quantity
        for order_id in oms.working_orders_for(asset_id):
            order = oms.orders.find(order_id)
            remaining = order.remaining_quantity
            total = ACCOUNTING_CONTEXT.add(
                total, remaining if order.side is CoreSide.BUY else -remaining
            )
        if held is not None or total != 0:
            committed[asset_id] = total
    return committed


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
        entry = evolve(existing, last_timestamp=timestamp, occurrences=existing.occurrences + 1)
    return evolve(state, unpriced_assets=state.unpriced_assets.set(asset_id, entry))


def _evaluate_risk(
    state: ExecutionPipelineState,
    request: OrderRequest,
    timestamp: float,
    rates: FxRates = NO_RATES,
) -> tuple[ExecutionPipelineState, RiskDecision]:
    """Judge ``request`` on the book it would leave, counting working orders.

    The gate is given what it needs to project the order (see
    :mod:`alphalab.risk.projection`): the asset's filled position, the order's
    price in the account's base currency -- converted with the rate in force at
    ``timestamp`` and *not* rounded to a minor unit, because it is a price --
    and every asset's working orders, read from the OMS's active orders.
    """

    position = state.portfolio.positions.get(request.asset_id)
    # One unit's value, not its price: a notional limit reads a contract on
    # fifty units of an index as fifty times its quote (ACC-005).
    price = _unit_value(
        state,
        request.asset_id,
        _price_in_base(state, request.asset_id, request.price, rates, timestamp),
    ).copy_abs()
    working = _working_exposure(state, rates, timestamp)
    risk, decision = RiskEngine.evaluate(
        state.risk,
        request,
        timestamp,
        position=position.quantity if position is not None else Decimal("0"),
        price=price,
        working=working,
        buckets=_bucket_exposures(state, request, price, working),
        charge=_buying_power_charge(
            state, request.asset_id, price, rates, timestamp, request.side is CoreSide.BUY
        ),
        margin=_gate_margin(state, request, rates, timestamp),
    )
    return evolve(state, risk=risk), decision


def _gate_margin(
    state: ExecutionPipelineState, request: OrderRequest, rates: FxRates, at: float
) -> tuple[Decimal, Decimal] | None:
    """The risk gate's margin figure, when the account declares a margin model (FUT-019).

    The model's initial requirement and margin equity were the order to fill,
    for an order in an instrument declaring margin terms; ``None`` -- the gate
    reads gross exposure over net asset value, as always -- otherwise, at one
    test for a registry declaring no model.
    """

    registry = state.config.instruments
    if registry is None or registry.margin_model is None:
        return None
    projected = _projected_margin(state, request, rates, at)
    if projected is None:
        return None
    equity, initial = projected
    return initial, equity


def _bucket_exposures(
    state: ExecutionPipelineState,
    request: OrderRequest,
    price: Decimal,
    working: Mapping[str, WorkingExposure],
) -> tuple[BucketExposure, ...]:
    """The bucket the order's instrument is in along each limited dimension (OFE-001).

    A bucket's gross exposure is the sum over its members -- the registry's
    index of every instrument carrying the label -- each at its filled value
    plus what its working orders commit, as the projection values the book. It
    is paid only when a classification limit is declared; a run that declares
    none takes one empty-tuple test.

    v3.12 reads the filled part from the gross the book keeps per bucket (see
    :func:`_grouped_book`) and adds what the assets with working orders commit,
    so an order costs the working orders rather than the bucket: summing the
    bucket made a 10,000-name rebalance under sector limits cost the square of
    the book over the number of sectors. Where the book keeps no such total --
    a mixed-currency book, a restored state not yet re-marked -- the members are
    summed as before, and both give the same figure, written the same way.
    """

    limits = state.risk.active_limits.classification
    if not limits:
        return ()
    instruments = state.config.instruments
    values = state.risk.exposure.asset_exposure
    base = state.portfolio.account.base_currency
    ctx = ACCOUNTING_CONTEXT
    signed = request.quantity if request.side is CoreSide.BUY else -request.quantity
    order_value = ctx.multiply(signed, price)
    buckets: list[BucketExposure] = []
    for dimension in sorted({limit.dimension for limit in limits}):
        label = None if instruments is None else instruments.label_of(request.asset_id, dimension)
        if label is None or instruments is None:
            buckets.append(BucketExposure(dimension, None, Decimal("0"), Decimal("0")))
            continue
        committed = _kept_bucket_gross(values, working, instruments, dimension, label, base)
        if committed is None:
            committed = Decimal("0")
            for member in instruments.bucket_members(dimension, label):
                committed = ctx.add(committed, abs(_committed_value(values, working, member)))
        here = _committed_value(values, working, request.asset_id)
        projected = ctx.add(ctx.subtract(committed, abs(here)), abs(ctx.add(here, order_value)))
        buckets.append(BucketExposure(dimension, label, committed, projected))
    return tuple(buckets)


#: Sums kept exactly or not at all: a bucket's gross read from the book is used
#: only when it is the exact figure the sum over its members gives.
_EXACT_SUM = ACCOUNTING_CONTEXT.copy()
_EXACT_SUM.traps[decimal.Inexact] = True


def _book_groups(config: ExecutionPipelineConfig) -> BookGroups | None:
    """The buckets the run's classification limits bound, as groups for the book to total.

    ``None`` -- the book keeps nothing -- when no limit bounds a bucket.
    """

    instruments = config.instruments
    dimensions = frozenset(limit.dimension for limit in config.risk_limits.classification)
    index = None if instruments is None else instruments.members
    if not dimensions or instruments is None or index is None:
        return None
    keys: dict[str, list[tuple[str, str]]] = {}
    for dimension in sorted(dimensions):
        labels = index.get(dimension)
        if labels is None:
            continue
        for label, members in labels.items():
            for asset_id in members:
                keys.setdefault(asset_id, []).append((dimension, label))
    return BookGroups(
        dimensions, {asset_id: tuple(found) for asset_id, found in keys.items()}, instruments
    )


def _grouped_book(portfolio: PortfolioState, config: ExecutionPipelineConfig) -> PortfolioState:
    """``portfolio`` keeping the gross of every bucket the run's classification limits bound.

    Installed where a pipeline state comes into being -- :meth:`ExecutionPipeline.initialize`
    and a snapshot's restore -- and carried through every change the portfolio
    engine makes. The grouping is not part of the portfolio's value: equality,
    snapshots and digests never see it. A run with no classification limit
    keeps no groups and pays nothing.
    """

    groups = _book_groups(config)
    if groups is None:
        return portfolio
    return evolve(portfolio, positions=portfolio.book.grouped(groups))


def _kept_bucket_gross(
    values: Mapping[str, Decimal],
    working: Mapping[str, WorkingExposure],
    instruments: InstrumentRegistry,
    dimension: str,
    label: str,
    base: str,
) -> Decimal | None:
    """The bucket's committed gross from the book's kept total, or ``None`` to sum its members.

    The book's gross is the sum of the members' absolute filled values; each
    member with working orders is then counted at its filled value plus what
    they commit instead. Read only from the book the exposure was computed from,
    grouped by this registry, every figure in the base currency; every step
    exact, so the result is the figure the member sum gives -- and ``None``
    whenever it could differ.
    """

    if not isinstance(values, BookMarketValues):
        return None
    groups = values.groups
    if groups is None or groups.source is not instruments:
        return None
    kept = values.group_gross(dimension, label)
    if kept is None or any(currency != base for currency in kept):
        return None
    total = kept.get(base, Decimal("0"))
    try:
        for asset_id in working:
            if instruments.label_of(asset_id, dimension) != label:
                continue
            filled = abs(values.get(asset_id, Decimal("0")))
            total = _EXACT_SUM.add(
                _EXACT_SUM.subtract(total, filled),
                abs(_committed_value(values, working, asset_id)),
            )
    except decimal.Inexact:
        return None
    return total


def _committed_value(
    values: Mapping[str, Decimal], working: Mapping[str, WorkingExposure], asset_id: str
) -> Decimal:
    """An asset's filled value plus what its working orders commit, in the base currency."""

    filled = values.get(asset_id, Decimal("0"))
    pending = working.get(asset_id)
    if pending is None:
        return filled
    return ACCOUNTING_CONTEXT.add(
        filled, ACCOUNTING_CONTEXT.multiply(pending.quantity, pending.price)
    )


def _price_in_base(
    state: ExecutionPipelineState, asset_id: str, price: Decimal, rates: FxRates, as_of: float
) -> Decimal:
    """``price`` -- in the currency ``asset_id`` settles in -- in the base currency."""

    currency = _settlement_currency_for(state, asset_id)
    base = state.portfolio.account.base_currency
    if currency == base:
        return price
    return ACCOUNTING_CONTEXT.multiply(price, rates.rate_at(currency, base, as_of).rate)


def _working_exposure(
    state: ExecutionPipelineState, rates: FxRates, as_of: float
) -> Mapping[str, WorkingExposure]:
    """What the OMS's working orders commit, per asset, in the base currency.

    Read from the active-order set, so the cost follows the orders that are
    working rather than every order ever placed. An order that has not filled
    commits its remaining quantity; each asset's is valued at its current market
    price, or at the order's reference price when the asset has not been priced
    since.
    """

    oms = state.oms
    if not oms.active_orders:
        return NO_WORKING_ORDERS
    quantities: dict[str, Decimal] = {}
    reference: dict[str, Decimal] = {}
    for order_id in oms.active_orders:
        order = oms.orders.find(order_id)
        remaining = order.remaining_quantity
        signed = remaining if order.side is CoreSide.BUY else -remaining
        quantities[order.asset_id] = ACCOUNTING_CONTEXT.add(
            quantities.get(order.asset_id, Decimal("0")), signed
        )
        quoted = order.metadata.get("reference_price")
        if quoted is not None:
            reference.setdefault(order.asset_id, Decimal(str(quoted)))
    working: dict[str, WorkingExposure] = {}
    for asset_id, quantity in quantities.items():
        mark = state.market_prices.get(asset_id, reference.get(asset_id))
        if mark is None:
            continue
        held = state.portfolio.positions.get(asset_id)
        unit = _unit_value(
            state, asset_id, _price_in_base(state, asset_id, mark, rates, as_of)
        ).copy_abs()
        working[asset_id] = WorkingExposure(
            quantity=quantity,
            price=unit,
            position=held.quantity if held is not None else Decimal("0"),
            charge=_buying_power_charge(state, asset_id, unit, rates, as_of, quantity > 0),
        )
    return working


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
    return evolve(
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
    return evolve(
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
    simulated fill arrives through :func:`_route_requests` and a venue fill
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
    return evolve(
        released,
        allocation=AllocationEngine.retire_contributions(released.allocation, str(order_id.value)),
    )


def _whole_units(state: ExecutionPipelineState) -> bool:
    """Whether this run trades whole units: its allocation sizes every order in them."""

    return state.config.allocation_constraints.enforce_integer_quantities


def _decide_fill(
    policy: FillPolicy, order: OMSOrder, event: MarketEvent, price: Decimal, whole_units: bool
) -> FillDecision:
    """Ask the policy what the venue does with this order at this event.

    In a run that trades whole units (``enforce_integer_quantities``), a partial
    fill is floored to whole units, and one that floors to nothing is no fill
    (v4.0, ledger EXE-011). A fill policy sizes a partial fill from the
    liquidity an event showed -- ``LiquidityCappedFill`` takes a share of it --
    and until v4.0 that share reached the book as it was: a 2.5-share fill of a
    whole-share order, a book holding half a share of an equity, and a target
    strategy that then could not reach its target in whole units. The order's
    remainder stays whole and works on as before.
    """

    decision = policy.decide(
        LiquidityContext(
            asset_id=order.asset_id,
            side=order.side,
            requested_quantity=order.remaining_quantity,
            price=price,
            available_quantity=_available_quantity(event, order.side),
            timestamp=event.timestamp,
        )
    )
    if not whole_units or decision.status is not FillStatus.PARTIAL_FILL:
        return decision
    if decision.quantity is None:
        return decision
    whole = decision.quantity.to_integral_value(rounding=decimal.ROUND_DOWN)
    if whole <= Decimal("0"):
        return FillDecision(FillStatus.NO_FILL, None)
    return FillDecision(FillStatus.PARTIAL_FILL, whole)


def _available_quote(event: MarketEvent) -> tuple[Decimal | None, Decimal | None]:
    """The two sides of the quote the event carried, or ``(None, None)``.

    Only a :class:`~alphalab.market.events.QuoteReceived` carries both. A bar, a
    tick and a trade each print a price without a spread around it, so no spread
    was observed and none is supplied -- ``QuotedHalfSpread`` refuses on those
    feeds rather than inventing one, which is the correct outcome and the reason
    this returns a pair of ``None`` instead of deriving something from the last
    trade.
    """

    if isinstance(event, QuoteReceived):
        return event.quote.bid, event.quote.ask
    return None, None


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
    expire_at = request.terms.expire_at
    if state.config.routing is not ExecutionRouting.EXTERNAL and _needs_session_close(
        request.terms
    ):
        # _terms_refusal already refused the order when this has no answer.
        expire_at, _ = _day_order_close(state, request.asset_id, timestamp)
    submitted_order = _oms_order(request, expire_at)
    oms = OMSEngine.submit(state.oms, submitted_order, timestamp)
    oms = OMSEngine.accept(oms, submitted_order.order_id, timestamp)
    accepted = oms.orders.find(submitted_order.order_id)
    return evolve(state, oms=oms), accepted


def _execute_order(
    state: ExecutionPipelineState,
    order: OMSOrder,
    decision: FillDecision,
    event: MarketEvent | None = None,
    *,
    price: Decimal | None = None,
    passive: bool = False,
) -> tuple[ExecutionPipelineState, tuple[ExecutionReport, ...]]:
    """Simulate the decided fill, priced against what the event actually showed.

    ``event`` is what :func:`_decide_fill` read to size the fill, handed on so
    the cost model prices it against the same observation. ``None`` means the
    caller has no event -- the cost model then sees no quote and no depth, and
    any role needing one refuses rather than inventing it. ``price`` is the
    price the fill starts from when it is not the market's; ``passive`` a
    resting order filled at it (see
    :meth:`~alphalab.execution.simulator.ExecutionSimulator.simulate_fill`).
    """

    before = len(state.execution.history)
    instruction = _instruction(order, state)
    quantity = decision.quantity if decision.quantity is not None else order.remaining_quantity
    bid, ask = _available_quote(event) if event is not None else (None, None)
    execution = ExecutionEngine.simulate(
        state.execution,
        state.config.simulator,
        instruction,
        quantity,
        instruction.price if price is None else price,
        # The instant it executes: the event's own. For a same-event order that
        # is the instant it was accepted; a next-event order was accepted at the
        # event before.
        event.timestamp if event is not None else order.updated_at,
        decision.status,
        bid=bid,
        ask=ask,
        available_liquidity=None if event is None else _available_quantity(event, order.side),
        passive=passive,
    )
    return evolve(state, execution=execution), execution.history[before:]


def _apply_reports(
    state: ExecutionPipelineState,
    order: OMSOrder,
    reports: tuple[ExecutionReport, ...],
    rates: FxRates = NO_RATES,
) -> tuple[ExecutionPipelineState, tuple[CoreFill, ...], tuple[CoreTrade, ...]]:
    current = state
    fills: list[CoreFill] = []
    trades: list[CoreTrade] = []

    for report in reports:
        before = current.oms.orders.find(order.order_id)
        current = _apply_report_to_oms(current, order.order_id, report)
        fill, trade = _canonical_execution(report, order.side)
        current = _apply_report_to_portfolio(current, report, order.side, rates)
        if BUY_IN in before.metadata:
            current = _buy_in_charges(current, before, report)
        # Reconcile allocation budgets with executed notional, expressed in the
        # budget's currency like the reservation it consumes. Converting here
        # rather than inside AllocationEngine is what keeps that package free of
        # exchange rates -- see _budget_prices.
        executed_notional = _in_budget_currency(
            current,
            _unit_value(
                current,
                report.asset_id,
                ACCOUNTING_CONTEXT.multiply(report.fill_quantity, report.fill_price),
            ).copy_abs(),
            report.currency,
            rates,
            report.timestamp,
        )
        # Each contributing strategy's own position (FEA-001), read while the
        # order's contributions are still on the ledger -- and, for a ceilinged
        # strategy, what its share deployed (OFE-003).
        signed = report.fill_quantity if order.side is OMSSide.BUY else -report.fill_quantity
        current = evolve(
            current,
            allocation=AllocationEngine.record_fill(
                current.allocation, report.order_id, report.asset_id, signed, executed_notional
            ),
        )
        allocation_state = AllocationEngine.apply_execution(
            current.allocation, report.order_id, executed_notional, report.timestamp
        )
        current = evolve(current, allocation=allocation_state)
        # If this report took the order terminal, whatever the reference price
        # reserved but the execution price did not consume is capital committed
        # to nothing. Free it here -- see _release_if_terminal.
        current = _release_if_terminal(current, order.order_id, report.timestamp)
        fills.append(fill)
        trades.append(trade)

    return (
        evolve(
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
    return evolve(state, execution=execution)


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
    return evolve(state, oms=oms)


def _require_settlement_currency(state: ExecutionPipelineState, report: ExecutionReport) -> None:
    """Refuse a report denominated in something this pipeline does not settle.

    Seam 2 of ADR-0028, and the counterpart to :func:`_settlement_refusal`. They
    answer different questions: Seam 1 asks whether this run may *trade* an
    instrument, and needs the registry to answer; this asks whether a report is
    denominated in what the pipeline *settles*, and needs nothing but the
    configuration. Neither subsumes the other -- with only this check a foreign
    instrument would still slip through, because its report carries the
    settlement currency; with only Seam 1 a venue fill would still slip through,
    because it never passes through :func:`_route_requests`.

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

    permitted = state.config.settlement_currencies
    if report.currency in permitted:
        return

    settles = ", ".join(repr(each) for each in sorted(permitted))
    raise RuntimeValidationError(
        f"Execution report {report.execution_id} for asset {report.asset_id} is "
        f"denominated in {report.currency!r}, and this pipeline settles in "
        f"{settles}. Applying it would book a position and move cash in "
        f"{report.currency!r}, leaving a book holding a currency no valuation of it "
        "could express -- the next portfolio snapshot would raise. Nothing has been "
        "applied and the state you passed in is unchanged. A venue fill reaches this "
        "path through RoutingConfig.currency, which defaults to 'USD' and is not the "
        "pipeline's settlement currency unless it is set to it. Add "
        f"{report.currency!r} to ExecutionPipelineConfig.also_settles if this "
        "pipeline is meant to settle it."
    )


def _apply_report_to_portfolio(
    state: ExecutionPipelineState,
    report: ExecutionReport,
    side: OMSSide,
    rates: FxRates = NO_RATES,
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
        economics=_economics_of(state, report.asset_id),
        settles_at=_settles_at(state, report.asset_id, report.timestamp),
        financed=_purchased_on_margin(state, report.asset_id, signed_quantity),
    )
    risk = _sync_risk_from_portfolio(
        state.risk, portfolio, state.config.instruments, rates, as_of=report.timestamp
    )
    record = _trade_record(report, portfolio.events[before:], opened_at, contributions, sector)
    return evolve(
        state,
        portfolio=portfolio,
        risk=risk,
        trade_records=state.trade_records.append(record),
    )


def _withdraw_partial_remainder(
    state: ExecutionPipelineState,
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

    withdrawn = evolve(state, oms=OMSEngine.cancel(state.oms, order.order_id, timestamp))
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


def _oms_order(request: OrderRequest, expire_at: float | None) -> OMSOrder:
    """The OMS order a request becomes, on the terms it was asked with (EXE-003).

    Until v3.11 every order was built ``MARKET`` here whatever was asked; the
    request had nowhere to say anything else. ``expire_at`` is the terms' own,
    or -- for a simulated day order that stated none -- its session close by the
    declared calendar (EXE-010), so the order records when it will expire.
    """

    terms = request.terms
    return OMSOrder(
        OrderId(UUID(request.order_id)),
        request.strategy_id,
        request.asset_id,
        request.side,
        terms.order_type,
        OrderStatus.NEW,
        request.quantity,
        Decimal("0"),
        request.quantity,
        terms.limit_price,
        terms.stop_price,
        Decimal("0"),
        request.timestamp,
        request.timestamp,
        {"reference_price": str(request.price)},
        time_in_force=terms.time_in_force,
        expire_at=expire_at,
    )


def _instruction(order: OMSOrder, state: ExecutionPipelineState) -> OrderInstruction:
    currency = _settlement_currency_for(state, order.asset_id)
    return OrderInstruction(
        str(order.order_id.value),
        order.strategy_id,
        order.asset_id,
        order.remaining_quantity,
        state.market_prices[order.asset_id],
        order.side,
        state.config.venue,
        currency,
        # The account's unit for the settlement currency, so a simulated fill's
        # cash costs are rounded where the portfolio will book them.
        minor_units=state.config.account.currency_units.minor_units(currency),
    )


def _settlement_currency_for(state: ExecutionPipelineState, asset_id: str) -> str:
    """What a fill in ``asset_id`` settles in.

    The instrument's own currency when this pipeline has a registry that names
    one *and* is configured to settle it; otherwise the pipeline's reporting
    currency.

    That ordering is what makes settlement-level multi-currency real rather than
    nominal. Before v2.17 every instruction was stamped ``config.currency``
    regardless of what the instrument traded in, and :func:`_settlement_refusal`
    dropped anything that disagreed -- so the stamp was always right because
    nothing else could reach it. Now that a foreign instrument *can* reach it,
    stamping the pipeline's currency would book a EUR trade as USD at the EUR
    price: a silent relabelling, which is the exact failure ADR-0019 exists to
    prevent.

    A single-currency pipeline is unaffected. Its
    :attr:`~ExecutionPipelineConfig.settlement_currencies` holds one member, so
    the only currency this can return is the one it always returned.
    """

    registry = state.config.instruments
    if registry is None:
        return state.config.currency

    currency = _currency_of(registry, asset_id)
    if currency is None or currency not in state.config.settlement_currencies:
        # Unregistered, or registered and not settled here -- the second of
        # which _settlement_refusal already dropped before an order existed.
        return state.config.currency
    return currency


def _in_budget_currency(
    state: ExecutionPipelineState,
    amount: Decimal,
    currency: str,
    rates: FxRates,
    as_of: float,
) -> Decimal:
    """``amount``, settled in ``currency``, expressed in the budget's currency.

    The counterpart to :func:`_budget_prices` on the fill path. Nothing is
    converted when the budget states no currency (a single-currency pipeline,
    where there is only one currency for the amount to be in) or when it is
    already the same one.
    """

    budget_currency = state.config.budget.currency
    if not budget_currency or currency == budget_currency:
        return amount
    return rates.convert(amount, currency, budget_currency, as_of).converted


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
    * the instrument settles here, and declares economics the book can be kept
      by -- or needs none, being fully paid (ledger ACC-005).

    The one ``record_for`` answers both questions and names the instrument in a
    refusal, rather than only its identifier.
    """

    registry = state.config.instruments
    if registry is None:
        return None

    settlement = state.config.currency
    permitted = state.config.settlement_currencies
    record = registry.record_for(asset_id)
    if record is None:
        return None
    currency = record.currency
    named = f"{record.symbol} on {record.exchange}"
    if record.asset_type is AssetType.INDEX:
        # v4.1.1 (FUT-013): an index is a value contracts settle against.
        return SettlementRefusal(
            asset_id=asset_id,
            instrument_currency=currency,
            settlement_currency=settlement,
            detail=(
                f"{named} is an index: a reference value, not something held. Trade a "
                "contract on it; the request was dropped before it reached the OMS."
            ),
            timestamp=timestamp,
        )
    if record.last_trade_at is not None and timestamp > record.last_trade_at:
        # v4.1.1 (FUT-012): an expired contract trades no more, here or anywhere.
        return SettlementRefusal(
            asset_id=asset_id,
            instrument_currency=currency,
            settlement_currency=settlement,
            detail=(
                f"{named} stopped trading at {record.last_trade_at!r} and the request is at "
                f"{timestamp!r}: an expired contract is not traded. The request was dropped "
                "before it reached the OMS; a position still held is closed by its "
                "ContractSettlement."
            ),
            timestamp=timestamp,
        )
    if currency in permitted:
        # Since v3.11 a request must also be one the book can be kept by: a
        # future or an option declaring no economics has a multiplier and a
        # settlement nothing can supply (ACC-005), and is refused here, before
        # an order exists, rather than booked as a share.
        try:
            economics_for(record.asset_type, record.economics, named)
        except InstrumentInputError as exc:
            return SettlementRefusal(
                asset_id=asset_id,
                instrument_currency=currency,
                settlement_currency=settlement,
                detail=f"{exc} The request was dropped before it reached the OMS.",
                timestamp=timestamp,
            )
        return None

    settles = ", ".join(repr(each) for each in sorted(permitted))
    return SettlementRefusal(
        asset_id=asset_id,
        instrument_currency=currency,
        settlement_currency=settlement,
        detail=(
            f"{named} trades in {currency!r} and this pipeline settles in "
            f"{settles}, so it cannot be traded here. The request was dropped "
            "before it reached the OMS and the capital it held was released. "
            f"Add {currency!r} to ExecutionPipelineConfig.also_settles to trade it "
            "here -- which makes this book multi-currency, so every valuation of it "
            "then needs an FxRates table covering the pair -- or trade an instrument "
            f"that settles in one of {settles}. This is a settlement boundary, not a "
            "missing FX rate."
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
        notional_value=ACCOUNTING_CONTEXT.multiply(report.fill_quantity, report.fill_price),
        holding_period_seconds=holding_period,
        contributions=contributions,
    )


def _market_prices_with_event(
    prices: Mapping[str, Decimal], update: tuple[str, Decimal] | None
) -> Mapping[str, Decimal]:
    """``prices`` with the event's price set, sharing structure with ``prices``.

    Until v3.10 every event copied the whole map to change one entry (PRF-001).
    A map read back from a snapshot is a plain ``dict``, and becomes persistent
    at its first event.
    """

    if update is None:
        return prices
    asset_id, price = update
    persistent = prices if isinstance(prices, PersistentMap) else PersistentMap(prices)
    return persistent.set(asset_id, price)


def _event_payload(event: MarketEvent) -> object:
    if isinstance(event, QuoteReceived):
        return event.quote
    if isinstance(event, BarClosed):
        return event.bar
    if isinstance(event, TickReceived):
        return event.tick
    return None


def price_refusal(state: ExecutionPipelineState, payload: object) -> str | None:
    """Why the prices ``payload`` carries cannot be used for its instrument, or ``None``.

    The price gate (ledger ACC-007). Market data no longer refuses a negative
    print -- a price is data -- so the question of whether one is usable moves
    here, where the registry that declares each instrument's economics is. Every
    instrument's mark must be positive and no price it shows negative, unless
    its economics allow negative prices, and then every price need only be
    finite. A quote's zero bid is still data -- no bids -- and is accepted.

    Costs nothing for ordinary data: the registry is read only when a price is
    not positive. :meth:`~alphalab.runtime.run.RunEngine.advance` asks before a
    record is published and records a refused one as skipped; the pipeline
    refuses one reaching it directly.
    """

    if isinstance(payload, Quote):
        asset_id = payload.asset_id
        mark = ACCOUNTING_CONTEXT.divide(
            ACCOUNTING_CONTEXT.add(payload.bid, payload.ask), Decimal("2")
        )
        shown: tuple[Decimal, ...] = (payload.bid, payload.ask)
    elif isinstance(payload, Bar):
        asset_id = payload.asset_id
        mark = payload.close
        shown = (payload.open, payload.high, payload.low, payload.close)
    elif isinstance(payload, Tick):
        asset_id, mark, shown = payload.asset_id, payload.price, (payload.price,)
    else:
        return None
    expired = _expired_at(state, asset_id)
    if expired is not None and payload.timestamp > expired:
        return (
            f"{asset_id} stopped trading at {expired!r} and this record is at "
            f"{payload.timestamp!r}: an expired contract has no price to mark or trade on "
            "(FUT-012), so the record is refused."
        )
    if mark > 0 and all(price >= 0 for price in shown):
        return None
    economics = _economics_of(state, asset_id)
    if economics is not None and economics.allows_negative_prices:
        return None
    return (
        f"{asset_id} is priced at {mark} (showing {', '.join(str(p) for p in shown)}): its "
        "prices must be positive unless its declared economics allow negative prices "
        "(ACC-007), so the record is refused rather than marked or traded on."
    )


def _market_price(event: MarketEvent) -> tuple[str, Decimal] | None:
    if isinstance(event, QuoteReceived):
        quote = event.quote
        # The midpoint, in the pinned context: a price the whole run reads must
        # not depend on the caller's decimal precision (ACC-004).
        mid = ACCOUNTING_CONTEXT.divide(ACCOUNTING_CONTEXT.add(quote.bid, quote.ask), Decimal("2"))
        return quote.asset_id, mid
    if isinstance(event, BarClosed):
        return event.bar.asset_id, event.bar.close
    if isinstance(event, TickReceived):
        return event.tick.asset_id, event.tick.price
    return None


def _require_not_before_last_event(
    state: ExecutionPipelineState, timestamp: float, what: str
) -> None:
    last = state.market.events[-1] if len(state.market.events) else None
    if last is not None and timestamp < last.timestamp:
        raise RuntimeValidationError(
            f"{what} at {timestamp!r} would be booked before the last event this pipeline "
            f"processed, at {last.timestamp!r}."
        )


def _exchanged_part(
    state: ExecutionPipelineState,
    held: Position,
    shares: tuple[tuple[str, Decimal], ...],
    part: Mapping[str, Decimal],
    timestamp: float,
) -> tuple[Position, tuple[Decimal, Decimal] | None, tuple[tuple[str, Decimal], ...]]:
    """The part of ``held`` an exchange takes, what it leaves, and whose it is (FUT-030).

    Raises:
        RuntimeValidationError: If a strategy's part is not of its own share --
            another side, or more than it holds.
    """

    ctx = ACCOUNTING_CONTEXT
    own = dict(shares)
    for strategy_id, units in part.items():
        share = own.get(strategy_id, Decimal("0"))
        if units != 0 and ((units > 0) != (share > 0) or units.copy_abs() > share.copy_abs()):
            raise RuntimeValidationError(
                f"{strategy_id}'s part of {held.asset_id}, {units}, is not of its holding of "
                f"{share}."
            )
    taken = tuple((sid, units) for sid, units in sorted(part.items()) if units != 0)
    units = sum((u for _, u in taken), Decimal("0"))
    if units == held.quantity:
        return held, None, taken
    basis = to_money(
        ctx.divide(ctx.multiply(held.basis, units.copy_abs()), held.quantity.copy_abs()),
        held.currency,
        state.portfolio.account.currency_units,
    )
    exchanged = held._rebased(units, basis, held.realized_pnl, held.market_price, timestamp)
    return exchanged, (ctx.subtract(held.quantity, units), ctx.subtract(held.basis, basis)), taken


def _undelivered(state: ExecutionPipelineState, asset_id: str, at: float, use: str) -> Decimal:
    """Units of ``asset_id`` an exchange owes, undelivered at ``at`` and not usable for ``use``.

    The pending deliveries whose convention does not allow ``use`` -- "sellable"
    or "collateral" (FUT-037).
    """

    total = Decimal("0")
    for entry in state.portfolio.unsettled:
        if (
            entry.delivery is not None
            and entry.asset_id == asset_id
            and entry.pending_at(at)
            and use not in entry.delivery.split(",")
        ):
            total = ACCOUNTING_CONTEXT.add(total, entry.quantity)
    return total


def _undelivered_refusal(
    state: ExecutionPipelineState, request: OrderRequest, at: float
) -> str | None:
    """Why ``request`` would sell units an exchange has not yet delivered, or ``None`` (FUT-037).

    Read only for a sale in an instrument with a delivery pending: the holding
    after it -- filled, every working order and this one -- must keep the units
    owed and not yet delivered, unless their convention lets them be sold.
    """

    if request.side is not CoreSide.SELL or not state.portfolio.unsettled:
        return None
    owed = _undelivered(state, request.asset_id, at, "sellable")
    if owed <= 0:
        return None
    position = state.portfolio.positions.get(request.asset_id)
    held = Decimal("0") if position is None else position.quantity
    for order_id in state.oms.working_orders_for(request.asset_id):
        order = state.oms.orders.find(order_id)
        if order.side is OMSSide.SELL:
            held = ACCOUNTING_CONTEXT.subtract(held, order.remaining_quantity)
    after = ACCOUNTING_CONTEXT.subtract(held, request.quantity)
    if after >= owed:
        return None
    return (
        f"Selling {request.quantity} {request.asset_id} would sell units an exchange owes and "
        f"has not delivered: {owed} are pending delivery, and are not sold before they arrive."
    )


def _units_exchanged(
    portfolio: PortfolioState, holding: str, effective_at: float
) -> Decimal | None:
    """How many units of ``holding`` its exchange at ``effective_at`` exchanged, signed (FUT-034).

    As the exchange's :class:`~alphalab.portfolio.events.PositionExchanged`
    recorded them; ``None`` when the run's events hold none.
    """

    for event in reversed(portfolio.events.to_tuple()):
        if (
            isinstance(event, PositionExchanged)
            and event.asset_id == holding
            and event.timestamp == effective_at
        ):
            return event.quantity
    return None


def _after_book_change(
    state: ExecutionPipelineState,
    portfolio: PortfolioState,
    timestamp: float,
    rates: FxRates,
) -> ExecutionPipelineState:
    """``state`` with ``portfolio`` booked, risk resynced and an equity point recorded."""

    risk = _sync_risk_from_portfolio(
        state.risk, portfolio, state.config.instruments, rates, as_of=timestamp
    )
    snapshot = _portfolio_snapshot(portfolio, state.config.currency, timestamp, rates)
    return evolve(
        state,
        portfolio=portfolio,
        risk=risk,
        portfolio_snapshots=state.portfolio_snapshots.append(snapshot),
        id_position=current_id_position(),
    )


def _portfolio_snapshot(
    portfolio: PortfolioState,
    currency: str,
    timestamp: float,
    rates: FxRates = NO_RATES,
) -> PortfolioSnapshot:
    """Project the canonical portfolio valuation into the analytics snapshot.

    ``rates`` is threaded rather than defaulted at the call sites, because a
    multi-currency book cannot be valued without one and a snapshot that
    silently dropped a currency is the v2.7 defect ADR-0020 removed. A
    single-currency book converts nothing and never reads the table.
    """

    return _analytics_snapshot(
        PortfolioValuation.snapshot(portfolio, timestamp, currency, rates), timestamp
    )


def _analytics_snapshot(
    valuation: PortfolioValuationSnapshot, timestamp: float
) -> PortfolioSnapshot:
    """The analytics projection of one valuation."""

    return PortfolioSnapshot(
        timestamp,
        valuation.equity,
        valuation.cash,
        valuation.long_value,
        valuation.short_value,
    )


def _sync_risk_from_portfolio(
    risk: RiskState,
    portfolio: PortfolioState,
    instruments: InstrumentRegistry | None,
    rates: FxRates = NO_RATES,
    *,
    as_of: float,
) -> RiskState:
    """Refresh the risk state from a marked book.

    ``instruments`` is threaded rather than defaulted so that no call site can
    silently stop reporting sector exposure by forgetting it. It is read only by
    :func:`_risk_exposure`, and only to classify; nothing here resolves,
    registers or classifies anything.

    ``rates`` is threaded for a sharper reason, and it closes the fourth blocker
    ADR-0033 decision 13 named. **Risk limits are stated in one currency**:
    ``buying_power``, ``current_nav``, ``peak_nav`` and every limit checked
    against them are figures in ``account.base_currency``. Reading cash with
    ``cash.balance(base)`` on a book that also holds JPY silently *dropped* the
    JPY -- so a run holding most of its capital abroad would have reported
    almost no buying power and refused every order, with nothing saying why.
    ``cash_in`` includes every balance and converts what is not already in the
    base currency; a book it cannot convert refuses rather than under-reports.

    A single-currency book takes the same addition it always did and touches no
    rate, which is what keeps the per-event risk resync at the cost ADR-0028
    decision 7 measured.
    """

    base = portfolio.account.base_currency
    # ``as_of`` is the instant the book is being marked at, and every conversion
    # here is checked against it: a rate from the future or one older than the
    # table tolerates is refused, as it is on every other conversion path. Until
    # v3.10 this resync converted with no instant, so the guards never ran on the
    # figures every pre-trade check reads (ledger EXE-008).
    cash, _ = cash_in(portfolio.cash, base, rates, as_of, portfolio.account.currency_units)
    nav = NAVCalculator.calculate(portfolio.cash, portfolio.positions, base, rates, as_of)
    exposure = _risk_exposure(portfolio, instruments, rates, as_of)

    # Exposure and margin through the RiskEngine, so risk events are produced
    # consistently with other codepaths; then the engine marks NAV and cash,
    # which is where the high-water mark and the daily loss are maintained.
    # Margin is full-notional: gross exposure against net asset value.
    risk_with_exposure = RiskEngine.update_exposure(risk, exposure, as_of)
    margin = MarginStatus(available_margin=nav, margin_used=exposure.gross_exposure)
    risk_with_margin = RiskEngine.update_margin(risk_with_exposure, margin, as_of)
    marked = RiskEngine.mark(risk_with_margin, nav=nav, cash=cash, timestamp=as_of)
    if not portfolio.unsettled:
        return marked
    # Proceeds traded and not yet received are not cash a run may spend
    # (FUT-015): buying power is trade-date cash less them, in the base currency.
    credits = Decimal("0")
    for entry in portfolio.unsettled:
        if entry.cash <= 0 or not entry.pending_at(as_of):
            continue
        credits += (
            entry.cash
            if entry.currency == base
            else rates.convert(
                entry.cash, entry.currency, base, as_of, units=portfolio.account.currency_units
            ).converted
        )
    if not credits:
        return marked
    return evolve(marked, buying_power=max(Decimal("0.00"), marked.cash - credits))


class _ValuesInBase(Mapping[str, Decimal]):
    """Each position's market value in the base currency, converted when read.

    The per-asset figures of a mixed book. Converting all of them on every event
    cost a conversion per position; the risk gate reads one -- the asset an
    order is for -- so each converts when it is read, at the rate and instant
    the event fixed, and is kept (PRF-001).
    """

    __slots__ = ("_as_of", "_base", "_book", "_rates", "_read")

    def __init__(self, book: PositionBook, base: str, rates: FxRates, as_of: float | None) -> None:
        self._book = book
        self._base = base
        self._rates = rates
        self._as_of = as_of
        self._read: dict[str, Decimal] = {}

    def __getitem__(self, asset_id: str) -> Decimal:
        value = self._read.get(asset_id)
        if value is None:
            currency = self._book[asset_id].currency
            value = self._book.market_value(asset_id)
            if currency != self._base:
                value = self._rates.convert(value, currency, self._base, self._as_of).converted
            self._read[asset_id] = value
        return value

    def __contains__(self, asset_id: object) -> bool:
        return asset_id in self._book

    def __iter__(self) -> Iterator[str]:
        return iter(self._book)

    def __len__(self) -> int:
        return len(self._book)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return repr(dict(self.items()))

    def __serializable__(self) -> dict[str, Decimal]:
        return dict(self.items())

    def __reduce__(self) -> tuple[Any, ...]:
        return (dict, (dict(self.items()),))


class _SectorExposure(Mapping[str, Decimal]):
    """Signed market value by sector, summed when first read (PRF-001).

    Produced on every event and fill, and read by no risk check: only a
    snapshot, or a caller inspecting the risk state, reads it. The sum over
    every position is therefore taken at the first read, from the immutable
    market values and registry the event produced, and kept. The figures are
    those the eager sum gave; only when the work is done changes.
    """

    __slots__ = ("_instruments", "_sums", "_values")

    def __init__(self, values: Mapping[str, Decimal], instruments: InstrumentRegistry) -> None:
        self._values = values
        self._instruments = instruments
        self._sums: dict[str, Decimal] | None = None

    def _summed(self) -> dict[str, Decimal]:
        if self._sums is None:
            sums: dict[str, Decimal] = {}
            for asset_id, value in self._values.items():
                sector = _sector_of(self._instruments, asset_id)
                if sector is not None:
                    sums[sector] = sums.get(sector, Decimal("0.00")) + value
            self._sums = sums
        return self._sums

    def __getitem__(self, sector: str) -> Decimal:
        return self._summed()[sector]

    def __iter__(self) -> Iterator[str]:
        return iter(self._summed())

    def __len__(self) -> int:
        return len(self._summed())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return self._summed() == dict(other.items())
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return repr(self._summed())

    def __serializable__(self) -> dict[str, Decimal]:
        return dict(self._summed())

    def __reduce__(self) -> tuple[Any, ...]:
        return (dict, (dict(self._summed()),))


def _risk_exposure(
    portfolio: PortfolioState,
    instruments: InstrumentRegistry | None,
    rates: FxRates = NO_RATES,
    as_of: float | None = None,
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
    refuses a book it cannot express. ``sector_exposure`` is the newest such
    aggregation in the tree and was blind from the day it was written: v2.11
    bucketed signed market value by sector with no regard for what each position
    traded in, so a mixed book produced sector totals summed across currencies.
    The check folds into the pass this function already makes -- one string
    comparison per position, the same shape as the ``instruments is not None``
    test beside it -- and delegates the message to the one rule that owns it.
    See ADR-0028 decision 7.

    **v2.17 converts where v2.16 could only refuse.** A position in a currency
    ``rates`` covers is expressed in the base currency and bucketed there, so a
    multi-currency book produces exposure figures that are genuinely one number.
    One that no rate covers is still refused, through the same rule and with the
    same message. A homogeneous book takes the identical path it always did and
    touches no rate, which is what keeps the per-event resync at the cost
    ADR-0028 decision 7 measured.

    Raises:
        MixedCurrencyValuationError: If the book holds positions or non-zero cash
            in a currency the account's base currency cannot express and no rate
            converts.
    """

    base_currency = portfolio.account.base_currency
    book = portfolio.book
    if book.currencies in ((), (base_currency,)):
        # Homogeneous: the book's own totals, kept as it changes, are the
        # figures -- no pass over the positions for them (PRF-001). The sums
        # start from 0.00 as the pass below does, so they are written the same.
        totals = book.totals(base_currency)
        long_total = Decimal("0.00") + totals.long_value
        short_total = Decimal("0.00") + totals.short_value
        values = book.market_values
        return ExposureStatus(
            gross_exposure=long_total + abs(short_total),
            net_exposure=long_total + short_total,
            long_exposure=long_total,
            short_exposure=short_total,
            asset_exposure=values,
            sector_exposure=(
                _SectorExposure(values, instruments) if instruments is not None else {}
            ),
        )

    # Mixed: refused if a currency has no rate into the base, through the one
    # rule that owns the message; otherwise each currency's totals convert
    # once, and the per-asset figures convert when read (PRF-001).
    assert_single_currency_book(portfolio.cash, portfolio.positions, base_currency, rates)
    long_value, short_value, _, _, _ = book_totals_in(book, base_currency, rates, as_of)
    long_total = Decimal("0.00") + long_value
    short_total = Decimal("0.00") + short_value
    converted = _ValuesInBase(book, base_currency, rates, as_of)
    return ExposureStatus(
        gross_exposure=long_total + abs(short_total),
        net_exposure=long_total + short_total,
        long_exposure=long_total,
        short_exposure=short_total,
        asset_exposure=converted,
        sector_exposure=(
            _SectorExposure(converted, instruments) if instruments is not None else {}
        ),
    )
