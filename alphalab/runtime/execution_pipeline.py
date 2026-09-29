"""End-to-end execution pipeline orchestration.

This module connects the existing pure subsystem engines without introducing
replacement domain models. Boundary conversions stay here so package ownership
remains explicit and the canonical core entities are preserved.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from types import MappingProxyType
from typing import Any, Final
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
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT, in_accounting_context
from alphalab.common.evolve import evolve
from alphalab.common.ids import IdStreamPosition, current_id_position
from alphalab.common.order_terms import TimeInForce
from alphalab.common.persistent_map import PersistentMap
from alphalab.conventions.economics import InstrumentEconomics
from alphalab.conventions.lot import LotSpecification
from alphalab.core.contribution import StrategyContribution, split_by_contribution
from alphalab.core.enums import Side as CoreSide
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.order_request import OrderRequest
from alphalab.core.trade import Trade as CoreTrade
from alphalab.execution.engine import ExecutionEngine
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.policy import (
    FillDecision,
    FillPolicy,
    FillTiming,
    LiquidityContext,
    StaticFill,
)
from alphalab.execution.report import ExecutionReport
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.state import ExecutionState
from alphalab.instrument.economics import economics_for
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.registry import InstrumentRegistry
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
from alphalab.portfolio.account import Account
from alphalab.portfolio.book import PositionBook
from alphalab.portfolio.corporate_actions import CashFlow, Split
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.events import PortfolioEvent, PositionClosed, PositionReduced
from alphalab.portfolio.fx import NO_RATES, FxConversion, FxRates
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.valuation import (
    PortfolioValuation,
    PortfolioValuationSnapshot,
    assert_single_currency_book,
    book_totals_in,
    cash_in,
)
from alphalab.risk.decision import RiskDecision
from alphalab.risk.engine import RiskEngine
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import RiskLimits
from alphalab.risk.margin import MarginStatus
from alphalab.risk.projection import NO_WORKING_ORDERS, WorkingExposure
from alphalab.risk.state import RiskState
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
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import (
    FillEvent,
    Intent,
    IntentKind,
    OrderEvent,
    SliceClosed,
    StrategyInboundEvent,
    TimerEvent,
)
from alphalab.strategy.protocol import defines_on_slice
from alphalab.strategy.state import LifecycleState
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
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
    instruments: InstrumentRegistry | None,
    as_of: float,
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
    history_view = HistoryView(market, as_of)
    universe_view = UniverseView(instruments)

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
                ``config.account.base_currency`` disagree. The check runs before
                the portfolio exists, so nothing is funded against a refused
                configuration.
        """

        _require_one_account_currency(config)
        _require_settleable_budget(config)

        portfolio = PortfolioState(account=config.account)
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
            and current.oms.working_orders_for(update[0])
        ):
            current, earlier = _fill_working_orders(current, event, update[0], policy, rates)

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
            # The look-ahead bound: this event's own timestamp, never a wall
            # clock. A strategy sees everything up to and including the event it
            # is being dispatched, and nothing after it.
            as_of=event.timestamp,
        )
        strategy, intents = StrategyEngine.process_event(
            state.strategy, event, populated, event.timestamp
        )
        allocation, requests = _allocate(current, intents, market_prices, rates, event.timestamp)
        current = evolve(current, strategy=strategy, allocation=allocation)
        current, routed = _route_requests(current, event, intents, requests, policy, rates)
        routed = earlier.then(routed)

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
        return _step_result(
            current,
            event,
            (*intents, *feedback_intents),
            (*requests, *feedback_requests),
            routed.then(feedback),
            rates,
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
            as_of=timestamp,
        )
        strategy, intents = StrategyEngine.stop(state.strategy, populated, timestamp, strategy_ids)
        current = evolve(state, strategy=strategy)
        if not intents or last is None:
            # With no event processed there is no price to size or judge an
            # order by; the intents are reported and nothing is placed.
            return evolve(current, id_position=current_id_position()), intents, ()

        allocation, requests = _allocate(current, intents, current.market_prices, rates, timestamp)
        current = evolve(current, allocation=allocation)
        current, routed = _route_requests(
            current,
            last,
            intents,
            requests,
            StaticFill(FillStatus.FULL_FILL, None),
            rates,
            rest=True,
            at=timestamp,
        )
        return evolve(current, id_position=current_id_position()), intents, routed.orders

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

    for request in requests:
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
        if current.config.routing is ExecutionRouting.EXTERNAL:
            # The order is now working and belongs to whoever routes it. No
            # fill is invented, the order is not closed out, and its
            # reservation stays held -- the capital is still committed.
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
    by contribution, as its fills are. Computed only when a target is asked for.
    """

    wanted = {
        (intent.strategy_id, intent.instrument)
        for intent in intents
        if intent.kind is not IntentKind.DELTA
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


_NO_LOTS: Mapping[str, LotSpecification] = MappingProxyType({})
_NO_MINIMUMS: Mapping[str, Decimal] = MappingProxyType({})
_NO_MULTIPLIERS: Mapping[str, Decimal] = MappingProxyType({})
_NO_WORKING: Mapping[tuple[str, str], Decimal] = MappingProxyType({})


def _deliver_instant(
    state: ExecutionPipelineState,
    event: TimerEvent | SliceClosed,
    last: MarketEvent | None,
    context_factory: ContextFactory,
    rates: FxRates,
) -> tuple[ExecutionPipelineState, tuple[Intent, ...], tuple[OMSOrder, ...]]:
    """Dispatch an instant that is not a market observation, and place what it asks for.

    A timer and a slice alike: the strategies see the book as it stands at
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
        as_of=at,
    )
    strategy, intents = StrategyEngine.process_event(state.strategy, event, populated, at)
    current = evolve(state, strategy=strategy)
    if not intents or last is None:
        return evolve(current, id_position=current_id_position()), intents, ()

    contributions_before = current.allocation.contributions
    records_before = len(current.trade_records)
    allocation, requests = _allocate(current, intents, current.market_prices, rates, at)
    current = evolve(current, allocation=allocation)
    policy = StaticFill(FillStatus.FULL_FILL, None)
    current, routed = _route_requests(
        current, last, intents, requests, policy, rates, rest=True, at=at
    )
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
    return (
        evolve(current, id_position=current_id_position()),
        (*intents, *feedback_intents),
        routed.then(feedback).orders,
    )


def wants_slices(strategies: StrategyRuntimeState) -> bool:
    """Whether any running strategy subscribed to slices defines ``on_slice`` (EXE-004).

    What decides whether closing a slice does anything at all: a run none of
    whose strategies would receive one builds no context, dispatches nothing and
    records nothing, and is exactly the run it was before slices existed.
    """

    return any(
        entry.status is LifecycleState.RUNNING
        and entry.routing.accepts(Topic.SLICES)
        and defines_on_slice(entry.instance)
        for entry in strategies.strategies.values()
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

    return any(
        entry.status is LifecycleState.RUNNING
        and (entry.routing.accepts(Topic.FILLS) or entry.routing.accepts(Topic.ORDERS))
        for entry in strategies.strategies.values()
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
        as_of=instant,
    )
    strategy, intents = StrategyEngine.deliver(state.strategy, deliveries, populated, instant)
    current = evolve(state, strategy=strategy)
    if not intents:
        return current, (), (), _NO_ROUTING

    allocation, feedback_requests = _allocate(current, intents, market_prices, rates, instant)
    current = evolve(current, allocation=allocation)
    current, feedback = _route_requests(
        current, event, intents, feedback_requests, policy, rates, rest=True, at=instant
    )
    return current, intents, feedback_requests, feedback


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
    decision = _decide_fill(policy, order, event, base)
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


def _terms_refusal(
    state: ExecutionPipelineState, request: OrderRequest, instant: float
) -> str | None:
    """Why an order on these terms cannot be placed here, or ``None`` when it can.

    An order already past its expiry is refused on either routing. Two more are
    refused only in simulation, where the pipeline itself must work the order:
    a resting day order with no session close -- the pipeline holds no
    calendar and will not guess one -- and an auction order for an asset the
    run has no daily bars for, since an auction price is a daily bar's open or
    close.
    """

    terms = request.terms
    if terms.expire_at is not None and instant >= terms.expire_at:
        return (
            f"The order would be placed at {instant!r}, and its terms expire it at "
            f"{terms.expire_at!r}."
        )
    if state.config.routing is not ExecutionRouting.SIMULATED:
        return None
    if terms.rests and terms.time_in_force is TimeInForce.DAY and terms.expire_at is None:
        return (
            "A day order rests until its session closes, and a simulated run holds no "
            "calendar: state the close as expire_at (MarketCalendar.next_close gives it), or "
            "use GTC or GTD."
        )
    if terms.is_auction and not _has_session_bars(state.market, request.asset_id):
        return (
            f"An auction order fills at a daily bar's open or close, and this run has published "
            f"no daily bar for {request.asset_id}."
        )
    return None


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
    that found no liquidity this event rests.
    """

    decision = _decide_fill(policy, order, event, price)
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
      cancelled after its first turn instead.
    """

    timestamp = event.timestamp
    terms = order.terms
    if order.expire_at is not None and timestamp >= order.expire_at:
        return _expire(state, order, timestamp), (), (), ()
    market_price = state.market_prices[order.asset_id]
    one_shot = terms.is_immediate

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
    if resting_price is None:
        current = _end_unfilled(current, order, timestamp) if one_shot else current
        return current, (), (), ()
    return _work_resting(
        current, order, event, policy, rates, price=resting_price, passive=True, one_shot=one_shot
    )


def _fill_working_orders(
    state: ExecutionPipelineState,
    event: MarketEvent,
    asset_id: str,
    policy: FillPolicy,
    rates: FxRates,
) -> tuple[ExecutionPipelineState, _Routed]:
    """Work the simulated orders left working in ``asset_id``, at ``event``.

    In the order they were placed, each gets its turn at what this event showed
    -- see :func:`_work_order`: a market order its one attempt, a resting order
    the fill the event's prices give it, or nothing.
    """

    current = state
    orders: list[OMSOrder] = []
    reports: list[ExecutionReport] = []
    fills: list[CoreFill] = []
    trades: list[CoreTrade] = []
    for order_id in state.oms.working_orders_for(asset_id):
        order = current.oms.orders.find(order_id)
        current, new_reports, new_fills, new_trades = _work_order(
            current, order, event, policy, rates, arriving=False
        )
        orders.append(current.oms.orders.find(order_id))
        reports.extend(new_reports)
        fills.extend(new_fills)
        trades.extend(new_trades)
    return current, _Routed(
        orders=tuple(orders), reports=tuple(reports), fills=tuple(fills), trades=tuple(trades)
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
    risk, decision = RiskEngine.evaluate(
        state.risk,
        request,
        timestamp,
        position=position.quantity if position is not None else Decimal("0"),
        # One unit's value, not its price: a notional limit reads a contract on
        # fifty units of an index as fifty times its quote (ACC-005).
        price=_unit_value(
            state,
            request.asset_id,
            _price_in_base(state, request.asset_id, request.price, rates, timestamp),
        ).copy_abs(),
        working=_working_exposure(state, rates, timestamp),
    )
    return evolve(state, risk=risk), decision


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
        working[asset_id] = WorkingExposure(
            quantity=quantity,
            price=_unit_value(
                state, asset_id, _price_in_base(state, asset_id, mark, rates, as_of)
            ).copy_abs(),
            position=held.quantity if held is not None else Decimal("0"),
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
    submitted_order = _oms_order(request)
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
        current = _apply_report_to_oms(current, order.order_id, report)
        fill, trade = _canonical_execution(report, order.side)
        current = _apply_report_to_portfolio(current, report, order.side, rates)
        # Each contributing strategy's own position (FEA-001), read while the
        # order's contributions are still on the ledger.
        signed = report.fill_quantity if order.side is OMSSide.BUY else -report.fill_quantity
        current = evolve(
            current,
            allocation=AllocationEngine.record_fill(
                current.allocation, report.order_id, report.asset_id, signed
            ),
        )
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


def _oms_order(request: OrderRequest) -> OMSOrder:
    """The OMS order a request becomes, on the terms it was asked with (EXE-003).

    Until v3.11 every order was built ``MARKET`` here whatever was asked; the
    request had nowhere to say anything else.
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
        expire_at=terms.expire_at,
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
    return RiskEngine.mark(risk_with_margin, nav=nav, cash=cash, timestamp=as_of)


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
