"""The live driver: the loop that routes an order and settles the fill it returns.

ADR-0030 named this module before it existed. Its Tier-3 table lists
``TradingSession``, ``BacktestEngine``, ``ReplayBacktest`` and then
"``(later) StreamingDriver, LiveDriver``", and its consequences section records
the gap plainly: "no live driver exists".

Every half of the live path was already here and tested. What nothing owned was
the **cycle**:

.. code-block:: text

    venue fills ---> settle ---> RunEngine.advance ---> route working orders
         ^                                                      |
         +------------------------------------------------------+

:mod:`alphalab.runtime.session` said so against itself -- "a live session driven
by this module still produces working orders and stops" -- and left the caller
to decide when to route, which orders were already routed, where
:class:`~alphalab.broker.state.BrokerState` and
:class:`~alphalab.broker.reconciliation.ExternalOrderMap` lived across steps,
and how to apply what came back. Four decisions, each of which is a way to
duplicate an order at a venue if it is got wrong.

What this module is, and is not
-------------------------------

**It is a driver, on ADR-0030's definition, and it holds no state.** It decides
which record comes next and what clock reading judges it, exactly as
``TradingSession`` does. Every method is a ``@staticmethod`` over an immutable
value, and the value it transforms is an *aggregate*, not a new authority:

============================  ==================================================
:attr:`LiveRunState.run`      the canonical ``RunState`` -- ``RunEngine``'s
:attr:`LiveRunState.broker`   the canonical ``BrokerState`` -- the adapter's
:attr:`LiveRunState.mapping`  the canonical ``ExternalOrderMap`` -- reconciliation's
============================  ==================================================

**There is no second cursor, no second accounting and no second order book.**
``processed``, ``steps``, ``skipped`` and the whole execution state stay on
``RunState``; cash, positions and fills stay on ``ExecutionPipelineState``; the
venue's view stays on ``BrokerState``. This module adds the two facts that
genuinely belong to neither -- what each routing attempt decided, and what each
returning fill turned out to be -- and nothing else.

Broker fields were deliberately **not** added to ``RunState``. ADR-0030 decision
2 fixes it at eight fields, four of which are the continuation, and moving it
would move ``RUN_SNAPSHOT_SCHEMA`` and reshape the envelope a backtest shares.
Venue-side durability has its own envelope; see
:mod:`alphalab.runtime.live_snapshot`.

The order of a step, and why
---------------------------

:meth:`LiveSession.advance` does three things in a fixed order:

1. **Settle** the fills that arrived since the last step. A fill the venue has
   already given us must reach the portfolio *before* the strategy is dispatched,
   or the strategy reads a book that does not know about a position it already
   holds. This is the same rule ``ExecutionPipeline.process_market_event``
   follows when it marks before it dispatches.
2. **Advance** the run over the record, through the canonical step. Unchanged,
   and this module does not reimplement one line of it.
3. **Route** every working order the venue does not already hold. After the
   step, because that is when the orders exist.

How fills arrive is not this module's business
----------------------------------------------

A pull-based adapter's ``poll_executions`` (the reference REST adapter in the
test suite has one) is deliberately not part of
:class:`~alphalab.broker.protocol.BrokerProtocol` -- "how fills
arrive is a venue's business", and a push-based venue delivers the same
:class:`~alphalab.broker.execution.BrokerExecution` values with no polling at
all. So :meth:`LiveSession.advance` *takes* the executions that arrived, and the
caller gets them from a poll, a socket or a callback. Inventing a feed
abstraction here would add a second answer to a question the broker layer has
already answered.

Idempotence, twice over
-----------------------

A venue redelivers fills after a reconnect. Both layers refuse a repeat
independently: :func:`~alphalab.broker.reconciliation.classify_execution` sees
the ``execution_id`` in ``BrokerState.executions`` and returns ``DUPLICATE``,
and :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.apply_execution_report`
is itself idempotent in that id. Nothing here counts on which one fires first.

Determinism
-----------

Nothing in this module reads a wall clock. ``now`` is a parameter, as ADR-0030
decision 4 requires, and every timestamp written comes from a record, a report
or a caller-supplied reading. A live run draws identifiers from the same
``id_scope`` a backtest does, so :mod:`alphalab.runtime.run_snapshot` continues
it unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from uuid import UUID

from alphalab.broker.events import BrokerEvent
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.protocol import BrokerProtocol
from alphalab.broker.reconciliation import (
    ExecutionDecision,
    ExternalOrderMap,
    ReconciliationLog,
    apply_execution,
)
from alphalab.broker.requests import (
    CancelRequest,
    ModifyRequest,
    RequestDecision,
    RequestLedger,
    issue_cancel,
    issue_modify,
)
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.arithmetic import in_accounting_context
from alphalab.core.capabilities import CompatibilityReport
from alphalab.core.fill import Fill as CoreFill
from alphalab.execution.algorithms import ChildOrder
from alphalab.market.record import MarketRecord
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order as OMSOrder
from alphalab.portfolio.corporate_actions import CashFlow, Split
from alphalab.portfolio.fx import NO_RATES, FxRates
from alphalab.runtime.broker_routing import (
    ChildOrderBindings,
    ChildRoutingResult,
    RoutingConfig,
    RoutingDecision,
    RoutingRefusal,
    apply_broker_execution,
    route_child_order,
    route_order,
)
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ContextFactory, ExecutionPipelineResult
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine, RunState
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "LiveRunState",
    "LiveSession",
    "LiveStep",
    "RoutedOrder",
    "SettledExecution",
]


@dataclass(frozen=True, slots=True)
class RoutedOrder:
    """One routing attempt, and what the venue boundary decided about it.

    Kept because a refusal is information: ``DISCONNECTED`` means the run held an
    order rather than losing it, and an operator needs to see how many.
    """

    oms_order_id: str
    broker_order_id: str | None
    decision: RoutingDecision

    @property
    def routed(self) -> bool:
        """Whether the order reached the venue."""

        return self.decision.routed


@dataclass(frozen=True, slots=True)
class SettledExecution:
    """One returning fill, what the venue-side ledger made of it, and what the
    portfolio did with it.

    The two are independent, which is why both are recorded.
    :attr:`decision` is the *broker* layer's answer -- ``APPLIED`` the first time
    that ledger sees a fill, ``DUPLICATE`` afterwards, or a break.
    :attr:`fills` is what reached the *portfolio*, which is empty for a break,
    for a fill naming no order this run holds, and for a report the pipeline had
    already applied. See :meth:`LiveSession.settle` for why a ``DUPLICATE`` at
    one layer says nothing about the other.
    """

    execution: BrokerExecution
    decision: ExecutionDecision
    fills: tuple[CoreFill, ...] = ()

    @property
    def booked(self) -> bool:
        """Whether this fill moved the portfolio.

        The question a caller almost always means. ``decision.applied`` is the
        venue-side ledger's answer and is a different one.
        """

        return bool(self.fills)

    @property
    def is_break(self) -> bool:
        """Whether AlphaLab and the venue disagree about this fill."""

        return self.decision.is_break


@dataclass(frozen=True, slots=True)
class LiveStep:
    """What one live step produced, beyond the run step itself."""

    settled: tuple[SettledExecution, ...] = ()
    routed: tuple[RoutedOrder, ...] = ()
    result: ExecutionPipelineResult | None = None
    events: tuple[BrokerEvent, ...] = ()

    @property
    def breaks(self) -> tuple[SettledExecution, ...]:
        """Fills AlphaLab and the venue disagree about.

        A duplicate is not a break -- redelivery is expected. Everything else
        that was refused is.
        """

        return tuple(settled for settled in self.settled if settled.is_break)


@dataclass(frozen=True, slots=True)
class LiveRunState:
    """A live run: the run, the venue connection, and the binding between them.

    An **aggregate of three existing authorities**, not a fourth one. See the
    module docstring for why no field of it duplicates a field of ``RunState``.

    Attributes:
        run: The canonical run. ``RunEngine`` owns every field of it.
        broker: The venue connection as AlphaLab believes it. The adapter owns it.
        routing: How this session addresses and denominates orders at the venue.
            Required since v3.10: it defaulted to a venue called ``"LIVE"``
            denominated in ``"USD"`` (ledger API-003).
        mapping: ``oms_order_id`` <-> ``broker_order_id``. Reconciliation owns it,
            and it is the single answer to "has this order already been sent?"
        reconciliation: Every fill the broker layer refused, kept so none is lost.
        routed: Every routing attempt this run has made, in order.
        settled: Every returning fill this run has seen, in order.
        children: Which venue handles are an execution algorithm's children, and
            of which OMS order (:meth:`LiveSession.route_child`). Held here since
            v3.11 rather than by the caller, and rebuilt exactly from the mirror
            on restore: every child handle names its parent.
        requests: Every cancel and amendment this run has issued
            (:meth:`LiveSession.cancel`, :meth:`LiveSession.amend`), so a retry
            is recognised as one -- across a restart too, since v3.11 persists
            it with the live envelope.
        held: The OMS orders an execution algorithm works (:meth:`LiveSession.hold`):
            never routed whole, only ever through their children. Since v3.11,
            and persisted: a restarted run that forgot a hold would send the
            parent whole on its next step.
    """

    run: RunState
    broker: BrokerState
    routing: RoutingConfig
    mapping: ExternalOrderMap = field(default_factory=ExternalOrderMap)
    reconciliation: ReconciliationLog = field(default_factory=ReconciliationLog)
    routed: AppendOnlyLog[RoutedOrder] = field(default_factory=AppendOnlyLog)
    settled: AppendOnlyLog[SettledExecution] = field(default_factory=AppendOnlyLog)
    children: ChildOrderBindings = field(default_factory=ChildOrderBindings)
    requests: RequestLedger = field(default_factory=RequestLedger)
    held: frozenset[str] = frozenset()

    @property
    def connected(self) -> bool:
        """Whether the venue connection can currently carry an order."""

        return self.broker.connection_status.can_trade

    def _at_venue(self, order: OMSOrder) -> bool:
        order_id = str(order.order_id.value)
        return self.mapping.broker_id_for(order_id) is not None or bool(
            self.children.children_of(order_id)
        )

    @property
    def unrouted_orders(self) -> tuple[OMSOrder, ...]:
        """Working orders to be sent whole that the venue does not hold yet.

        Read from the OMS, the mapping and the algorithm bindings rather than
        stored again, so the run and its venue binding cannot disagree about
        what is outstanding. A parent an execution algorithm is working in
        children is at the venue: until v3.11 the bindings were the caller's,
        this property could not see them, and :meth:`LiveSession.advance` would
        have sent the whole parent as well. A parent :attr:`held` for an
        algorithm is not to be sent whole at all, children or none (ledger
        LIV-001).
        """

        return tuple(
            order
            for order in self.run.working_orders
            if not self._at_venue(order) and str(order.order_id.value) not in self.held
        )

    @property
    def held_orders(self) -> tuple[OMSOrder, ...]:
        """Working orders held for an execution algorithm, whether or not a child is out yet."""

        return tuple(
            order for order in self.run.working_orders if str(order.order_id.value) in self.held
        )

    @property
    def orders_at_venue(self) -> tuple[OMSOrder, ...]:
        """Working orders the venue holds: bound to a handle, or worked in children."""

        return tuple(order for order in self.run.working_orders if self._at_venue(order))

    @property
    def open_breaks(self) -> tuple[ExecutionDecision, ...]:
        """Fills that need a human or a reconcile to resolve."""

        return self.reconciliation.breaks.to_tuple()


def _oms_order_for(state: LiveRunState, execution: BrokerExecution) -> OMSOrder | None:
    """The OMS order a venue fill belongs to, or ``None`` when it names none.

    The mapping is the only authority for which order a fill belongs to.
    Matching on anything else -- asset, quantity, timestamp -- is guessing, and
    a wrong guess books one order's fill against another.

    The book is then read by **key**, not scanned.
    :class:`~alphalab.oms.book.OrderBook` indexes by ``OrderId`` and v2.2 made
    that index a :class:`~alphalab.common.persistent_map.PersistentMap` for
    exactly this reason; walking ``orders()`` to find one would put a linear
    term back on the fill path that the order book has not had since.

    ``None`` covers both absences and they are deliberately not distinguished:
    a handle the mapping does not know and a handle it knows for an order this
    run no longer holds are the same thing to a caller, which is a
    reconciliation break either way.
    """

    oms_order_id = state.mapping.oms_id_for(execution.broker_order_id)
    if oms_order_id is None:
        return None
    try:
        order_id = OrderId(UUID(oms_order_id))
    except ValueError:
        # A binding whose OMS side is not a UUID did not come from this run;
        # `broker_order_id_for` derives the handle from an OrderId.
        return None
    book = state.run.pipeline.oms.orders
    return book.find(order_id) if book.contains(order_id) else None


class LiveSession:
    """Drives a live run: settle, advance, route.

    Stateless. Every method takes a :class:`LiveRunState` and returns a new one,
    the way :class:`~alphalab.runtime.session.TradingSession` does with a
    :class:`~alphalab.runtime.run.RunState`.
    """

    @staticmethod
    @in_accounting_context
    def initialize(
        config: RunConfig,
        strategy_state: StrategyRuntimeState,
        broker_state: BrokerState,
        routing: RoutingConfig,
    ) -> LiveRunState:
        """Fund the portfolio and build the state a live run starts from.

        Raises:
            RuntimeValidationError: If ``config.mode`` is not
                :attr:`~alphalab.runtime.run.ExecutionMode.LIVE`. A run whose
                mode routes to the simulator would have its orders filled twice
                -- once by ``ExecutionSimulator`` and once by the venue -- and
                that is the one mistake this driver exists to make impossible.
        """

        if config.mode is not ExecutionMode.LIVE:
            raise RuntimeValidationError(
                f"A live session requires ExecutionMode.LIVE and this run declares "
                f"{config.mode.name}. In any other mode an accepted order is filled by "
                "ExecutionSimulator, so routing it to a venue as well would book the "
                "same order twice. Use TradingSession for a paper run."
            )

        return LiveRunState(
            run=RunEngine.initialize(config, strategy_state),
            broker=broker_state,
            routing=routing,
        )

    @staticmethod
    @in_accounting_context
    def connect(
        state: LiveRunState, broker: BrokerProtocol, timestamp: float
    ) -> tuple[LiveRunState, tuple[BrokerEvent, ...]]:
        """Open the venue connection. Nothing routes until this succeeds."""

        broker_state, events = broker.connect(state.broker, timestamp)
        return replace(state, broker=broker_state), events

    @staticmethod
    @in_accounting_context
    def disconnect(
        state: LiveRunState, broker: BrokerProtocol, reason: str, timestamp: float
    ) -> tuple[LiveRunState, tuple[BrokerEvent, ...]]:
        """Close the venue connection.

        Orders already at the venue stay there. Disconnecting is not cancelling,
        and this driver will not silently decide otherwise: an operator who
        wants the book flat cancels it, and the venue confirms.
        """

        broker_state, events = broker.disconnect(state.broker, reason, timestamp)
        return replace(state, broker=broker_state), events

    @staticmethod
    @in_accounting_context
    def settle(
        state: LiveRunState,
        executions: Iterable[BrokerExecution],
        rates: FxRates = NO_RATES,
    ) -> tuple[LiveRunState, tuple[SettledExecution, ...]]:
        """Apply the fills the venue has reported, through the canonical path.

        ``rates`` reaches the portfolio with each fill, exactly as
        :meth:`~alphalab.runtime.run.RunEngine.advance` passes it to a simulated
        one, and is read only when the book holds more than one currency. Until
        v3.11 it was not taken here, so a multi-currency live run could not
        settle a fill denominated in a foreign currency (ledger EXE-009).

        **Two ledgers, two questions, and they are not the same question.**
        :attr:`~alphalab.broker.state.BrokerState.executions` answers "has the
        venue-side bookkeeping recorded this fill?";
        :attr:`~alphalab.execution.state.ExecutionState.reports` answers "has the
        *portfolio* booked it?". Both are keyed by ``execution_id`` and both
        refuse their own repeat, and a fill can be in one and not the other --
        which is the normal case, not an edge one:
        an adapter's poll -- the reference REST adapter's ``poll_executions``,
        for one -- applies every fill it fetches to the broker state before
        returning it, so by the
        time a caller hands it here the venue-side ledger already holds it and
        the portfolio has never seen it.

        So a ``DUPLICATE`` from
        :func:`~alphalab.broker.reconciliation.classify_execution` is **not** a
        reason to stop: it is an answer about the other ledger. What does stop a
        fill is a *break* -- ``UNKNOWN_ORDER``, ``TERMINAL_ORDER``, ``OVERFILL``,
        ``INVALID`` -- because each of those means AlphaLab and the venue
        disagree about something the portfolio must not be moved on.
        :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.apply_execution_report`
        then answers the portfolio's own question and returns no fills for a
        report it has already applied, so a genuine redelivery books nothing
        twice however it arrives.

        A fill naming an order this run does not hold is recorded and changes
        nothing. It is not an error to raise on -- a venue reporting a fill for
        an order AlphaLab has forgotten is precisely what reconciliation is for,
        and raising here would stop a live run over a bookkeeping disagreement.
        """

        current = state
        outcomes: list[SettledExecution] = []

        for execution in executions:
            broker_state, decision, log = apply_execution(
                current.broker, execution, current.reconciliation
            )
            current = replace(current, broker=broker_state, reconciliation=log)

            if decision.is_break:
                outcomes.append(SettledExecution(execution, decision))
                continue

            oms_order = _oms_order_for(current, execution)
            if oms_order is None:
                # The venue-side ledger accepted it, but the run holds no order
                # under that handle. Recorded, not raised: see the docstring.
                outcomes.append(SettledExecution(execution, decision))
                continue

            pipeline, fills, _trades = apply_broker_execution(
                current.run.pipeline, oms_order, execution, current.routing, rates
            )
            current = replace(current, run=replace(current.run, pipeline=pipeline))
            outcomes.append(SettledExecution(execution, decision, fills))

        settled = tuple(outcomes)
        return replace(current, settled=current.settled.extend(settled)), settled

    @staticmethod
    @in_accounting_context
    def route_working_orders(
        state: LiveRunState, broker: BrokerProtocol, timestamp: float
    ) -> tuple[LiveRunState, tuple[RoutedOrder, ...], tuple[BrokerEvent, ...]]:
        """Send every working order the venue does not already hold.

        Orders already bound are skipped rather than refused: they are at the
        venue and re-sending is what ``DUPLICATE_SUBMISSION`` exists to prevent.
        ``route_order`` refuses them too -- this is the same answer reached one
        step earlier, so a disconnected session does not fill its log with a
        refusal per already-routed order per record.
        """

        current = state
        attempts: list[RoutedOrder] = []
        events: list[BrokerEvent] = []

        for order in state.unrouted_orders:
            result = route_order(
                current.broker,
                broker,
                order,
                timestamp,
                current.mapping,
                config=current.routing,
            )
            current = replace(current, broker=result.broker_state, mapping=result.mapping)
            events.extend(result.events)
            attempts.append(
                RoutedOrder(
                    oms_order_id=str(order.order_id.value),
                    broker_order_id=(
                        result.order.broker_order_id if result.order is not None else None
                    ),
                    decision=result.decision,
                )
            )

        routed = tuple(attempts)
        return (
            replace(current, routed=current.routed.extend(routed)),
            routed,
            tuple(events),
        )

    @staticmethod
    @in_accounting_context
    def advance(
        state: LiveRunState,
        record: MarketRecord,
        context_factory: ContextFactory,
        broker: BrokerProtocol,
        now: float | None = None,
        executions: Sequence[BrokerExecution] = (),
        rates: FxRates = NO_RATES,
        *,
        route: bool = True,
    ) -> tuple[LiveRunState, LiveStep]:
        """One whole live step: settle what came back, advance, route what is new.

        ``executions`` are the fills that arrived since the last step, from
        wherever the venue delivers them. ``now`` is the session's clock, used
        by the staleness gate and as the timestamp routing is recorded at; it
        defaults to the record's own timestamp. ``rates`` is the FX table the
        step's fills and marks are converted at -- the settled venue fills and
        the run step alike.

        ``route=False`` stops the step before routing, so the caller can decide
        about the orders it created first -- :meth:`hold` the ones an execution
        algorithm is to work, then :meth:`route_working_orders` for the rest.
        Until v3.11 there was no such pause: the step that created an order sent
        it whole, before any algorithm could be bound to it (ledger LIV-001).

        The order is the contract, and it is the same one the execution step
        keeps: what is already known is applied before anything is dispatched.
        See the module docstring.
        """

        clock = now if now is not None else record.timestamp

        current, settled = LiveSession.settle(state, executions, rates)
        run_state, result = RunEngine.advance(current.run, record, context_factory, now, rates)
        current = replace(current, run=run_state)
        if not route:
            return current, LiveStep(settled=settled, routed=(), result=result, events=())
        current, routed, events = LiveSession.route_working_orders(current, broker, clock)

        return current, LiveStep(settled=settled, routed=routed, result=result, events=events)

    @staticmethod
    @in_accounting_context
    def stop(
        state: LiveRunState,
        broker: BrokerProtocol,
        context_factory: ContextFactory,
        now: float,
        rates: FxRates = NO_RATES,
        strategy_ids: Iterable[str] | None = None,
        *,
        route: bool = True,
    ) -> tuple[LiveRunState, tuple[RoutedOrder, ...], tuple[BrokerEvent, ...]]:
        """Stop strategies and send what their ``on_shutdown`` asked for.

        :meth:`~alphalab.runtime.run.RunEngine.stop` at ``now``, then
        :meth:`route_working_orders`, so a strategy flattening itself as it
        stops has its orders at the venue in the same call. Nothing else is
        cancelled: orders already at the venue stay there, as they do on
        :meth:`disconnect`. ``route=False`` leaves the routing to the caller, as
        for :meth:`advance`.
        """

        current = replace(
            state, run=RunEngine.stop(state.run, context_factory, now, rates, strategy_ids)
        )
        if not route:
            return current, (), ()
        return LiveSession.route_working_orders(current, broker, now)

    @staticmethod
    @in_accounting_context
    def close_slice(
        state: LiveRunState,
        broker: BrokerProtocol,
        context_factory: ContextFactory,
        now: float,
        rates: FxRates = NO_RATES,
        *,
        route: bool = True,
    ) -> tuple[LiveRunState, tuple[RoutedOrder, ...], tuple[BrokerEvent, ...]]:
        """Close the instant of the last record, and send what the strategies ask for.

        :meth:`~alphalab.runtime.run.RunEngine.close_slice`, then
        :meth:`route_working_orders` at ``now``. A live session cannot know an
        instant is complete by looking ahead, so the caller says when -- at a
        bar's close, say, once every instrument's bar is in (ledger EXE-004). A
        slice is closed once, so a session restarted inside an instant does not
        decide on it twice. ``route=False`` leaves the routing to the caller, as
        for :meth:`advance`.
        """

        current = replace(state, run=RunEngine.close_slice(state.run, context_factory, rates))
        if not route:
            return current, (), ()
        return LiveSession.route_working_orders(current, broker, now)

    @staticmethod
    @in_accounting_context
    def apply_cash_flow(
        state: LiveRunState,
        flow: CashFlow,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> LiveRunState:
        """Book a cash flow the venue paid or charged (ledger ACC-006).

        :meth:`~alphalab.runtime.run.RunEngine.apply_cash_flow` on the run. The
        venue's own record of it is the broker mirror's, which its adapter keeps.
        """

        return replace(state, run=RunEngine.apply_cash_flow(state.run, flow, timestamp, rates))

    @staticmethod
    @in_accounting_context
    def apply_split(
        state: LiveRunState,
        split: Split,
        timestamp: float,
        rates: FxRates = NO_RATES,
    ) -> LiveRunState:
        """Apply a corporate action the venue has applied (ledger ACC-006).

        :meth:`~alphalab.runtime.run.RunEngine.apply_split` on the run. Refused
        while orders in the asset are working at the venue: the venue decides
        what becomes of them and reports it.
        """

        return replace(state, run=RunEngine.apply_split(state.run, split, timestamp, rates))

    @staticmethod
    @in_accounting_context
    def hold(state: LiveRunState, order_ids: Iterable[str]) -> LiveRunState:
        """Mark working orders as an execution algorithm's to work: never sent whole.

        A held order reaches the venue only as the children
        :meth:`route_child` sends for it; :meth:`route_working_orders` passes
        over it, now and after a restart, since the hold is persisted. Call it
        between an :meth:`advance` with ``route=False`` and the routing of the
        rest (ledger LIV-001).

        Raises:
            RuntimeValidationError: If an id names no working order of this run,
                or an order the venue already holds whole -- a hold cannot
                recall what has been sent.
        """

        working = {str(order.order_id.value): order for order in state.run.working_orders}
        held = set(state.held)
        for order_id in order_ids:
            order = working.get(order_id)
            if order is None:
                raise RuntimeValidationError(
                    f"Order {order_id!r} is not a working order of this run, so there is "
                    "nothing for an execution algorithm to work."
                )
            if state.mapping.broker_id_for(order_id) is not None:
                raise RuntimeValidationError(
                    f"Order {order_id!r} is already at the venue whole; holding it now "
                    "would not recall it, and working it in children as well would "
                    "double it."
                )
            held.add(order_id)
        return replace(state, held=frozenset(held))

    @staticmethod
    @in_accounting_context
    def route_child(
        state: LiveRunState,
        broker: BrokerProtocol,
        parent: OMSOrder,
        child: ChildOrder,
        timestamp: float,
        *,
        capability: CompatibilityReport | None,
    ) -> tuple[LiveRunState, ChildRoutingResult]:
        """Send one execution-algorithm child for ``parent``, through the run's own bindings.

        :func:`~alphalab.runtime.broker_routing.route_child_order` with the run's
        mapping, bindings and routing, so a child bound here is one a restored
        run knows about. Every gate is that function's; a refusal changes nothing.
        """

        result = route_child_order(
            state.broker,
            broker,
            parent,
            child,
            timestamp,
            mapping=state.mapping,
            children=state.children,
            config=state.routing,
            capability=capability,
        )
        return replace(state, broker=result.broker_state, children=result.children), result

    @staticmethod
    @in_accounting_context
    def cancel(
        state: LiveRunState, broker: BrokerProtocol, request: CancelRequest
    ) -> tuple[LiveRunState, RequestDecision, tuple[BrokerEvent, ...]]:
        """Ask the venue to cancel an order -- once, however often this is retried.

        :func:`~alphalab.broker.requests.issue_cancel` decides against the run's
        request ledger, and only a ``NEW`` request reaches the adapter's
        ``cancel_order``. A retry of the same attempt is a ``DUPLICATE`` and
        sends nothing, before and after a restart alike.
        """

        broker_state, ledger, decision = issue_cancel(state.broker, state.requests, request)
        events: tuple[BrokerEvent, ...] = ()
        if decision.send:
            broker_state, events = broker.cancel_order(
                broker_state, request.broker_order_id, request.requested_at
            )
        return replace(state, broker=broker_state, requests=ledger), decision, events

    @staticmethod
    @in_accounting_context
    def amend(
        state: LiveRunState, broker: BrokerProtocol, request: ModifyRequest
    ) -> tuple[LiveRunState, RequestDecision, tuple[BrokerEvent, ...]]:
        """Ask the venue to amend an order's quantity and price -- once per revision.

        :func:`~alphalab.broker.requests.issue_modify` decides against the run's
        request ledger, and only a ``NEW`` request reaches the adapter's
        ``replace_order``. Whether the venue can amend in place at all is the
        caller's capability check, as ``issue_modify`` documents.
        """

        broker_state, ledger, decision = issue_modify(state.broker, state.requests, request)
        events: tuple[BrokerEvent, ...] = ()
        if decision.send:
            broker_state, events = broker.replace_order(
                broker_state,
                request.broker_order_id,
                request.quantity,
                request.price,
                request.requested_at,
            )
        return replace(state, broker=broker_state, requests=ledger), decision, events

    @staticmethod
    def resume(state: LiveRunState) -> object:
        """The scope a restored live run continues in.

        Delegates to :meth:`~alphalab.runtime.run.RunEngine.resume`, which is the
        only implementation of this contract in AlphaLab. A live run's
        identifier stream is the run's, and restoring the venue binding does not
        touch it.
        """

        return RunEngine.resume(state.run)

    @staticmethod
    @in_accounting_context
    def finalize(state: LiveRunState) -> LiveRunState:
        """Close the run out. The venue binding is left exactly as it is.

        Finalizing does not cancel, disconnect or reconcile. Every one of those
        is an operator decision with a consequence at the venue, and a function
        called ``finalize`` is not where they belong.
        """

        return replace(state, run=RunEngine.finalize(state.run))


def live_health(state: LiveRunState) -> tuple[str, ...]:
    """Everything wrong with this live run right now, in plain sentences.

    Empty means healthy. This is the one place that answers "should a human look
    at this?", and it reads existing state rather than recording anything of its
    own.
    """

    problems: list[str] = []

    if state.broker.connection_status is not ConnectionStatus.CONNECTED:
        problems.append(
            f"The venue connection is {state.broker.connection_status.name}, so no order "
            "can be routed."
        )
    if state.open_breaks:
        problems.append(
            f"{len(state.open_breaks)} reported fill(s) could not be applied and need reconciling."
        )
    held = tuple(
        attempt
        for attempt in state.routed
        if attempt.decision.refusal is RoutingRefusal.DISCONNECTED
    )
    if held:
        problems.append(f"{len(held)} order(s) were held because the venue was unreachable.")
    if state.unrouted_orders and state.connected:
        problems.append(f"{len(state.unrouted_orders)} working order(s) are not at the venue.")
    return tuple(problems)
