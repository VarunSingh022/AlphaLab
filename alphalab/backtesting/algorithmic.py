"""Execution algorithms on the historical drivers: a parent worked in children over market events.

A strategy decides *what* to own; :mod:`alphalab.execution.algorithms` decides
how a large order is traded -- evenly over a window, with the market's expected
volume, a share of what trades, in slices, a tranche at a time. Until v4.1.1 an
algorithm worked children only on the live path, through
:func:`~alphalab.runtime.broker_routing.route_child_order`; a backtest filled
every order whole, so how a strategy's orders were executed could not be
measured against history (ledger FUT-009).

An :class:`ExecutionPlan` given to
:meth:`BacktestEngine.run <alphalab.backtesting.engine.BacktestEngine.run>` or
:meth:`ReplayBacktest.run <alphalab.backtesting.replay.ReplayBacktest.run>`
changes that, and changes nothing else about the run.

The path
--------
::

    strategy intent -> allocation -> risk -> OMS: the parent, accepted and held
      -> the algorithm (start_algorithm, release_children) at the asset's events
      -> capability check of each child, when the plan declares one
      -> ExecutionPipeline.work_child: the run's fill policy and cost model
      -> the fill settled on the parent: OMS, portfolio, reservation, attribution
      -> record_child_execution / record_child_outcome
      -> the parent ended when the algorithm is: filled, expired, cancelled

The run routes
:attr:`~alphalab.runtime.execution_pipeline.ExecutionRouting.ALGORITHMIC`, so
the pipeline accepts every order and never simulates one whole: risk judges the
whole parent when it is placed, allocation reserves its notional, and the OMS
holds it. Each child is simulated by the same fill policy, simulator and cost
model a whole order would be -- so a liquidity cap applies to each child, and
spread, slippage, impact, commission and fees are charged on each fill -- and
its fill reaches the books through the path every fill takes, so the parent is
one OMS order however many pieces it traded in, and the reservation is consumed
by its fills and released once, when it ends.

When a child may trade
----------------------
Everything happens at a market event of the parent's asset, in the order the run
reaches them. At each one, after the run's own step:

1. a parent placed since the asset's last event is **adopted**: its terms are
   the plan's window from the instant it was placed (clipped to its own
   expiry), its increment, and its own order type and limit -- a ``MARKET``
   parent's children are market orders, a ``LIMIT`` parent's are limit orders
   at its limit. A parent with no assignment, or one the algorithm refuses (a
   quantity not a whole number of increments, a VWAP profile that cannot
   plan), is **rejected**, never filled whole;
2. children past their slice -- or the window -- **expire**;
3. working children have their **turn** at the event: a child released at an
   earlier instant, or -- under the optimistic
   :attr:`~alphalab.execution.policy.FillTiming.SAME_EVENT` -- at this one;
4. the algorithm **releases** what its rule says is due at the event's instant,
   :class:`~alphalab.execution.algorithms.Participation` sizing from the volume
   the asset's events have printed so far (a bar's volume, a trade's quantity;
   a quote shows depth, not volume, and counts as nothing observed). Under
   ``SAME_EVENT`` a child released now has its turn now;
5. a parent whose algorithm has finished is **ended** through
   :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.apply_terminal_outcome`
   -- expired, cancelled, or rejected if it never filled -- which retires its
   reservation and contributions.

Nothing reads a later event: a child sees the event it is worked at, and the
algorithm sees the instant and the volume printed by it. A parent the run itself
ends -- a split or a contract settlement cancels working orders -- has its
algorithm cancelled. A parent still working when the data ends stays working, as
any resting order does, and is listed by :attr:`AlgorithmBook.incomplete`; a run
continued with more data continues it.

What is not done
----------------
A child's fill reaches its strategy through the portfolio its next context
shows, not through ``on_fill`` -- as a venue fill does on the live path.
:class:`~alphalab.execution.algorithms.Participation` with
:attr:`~alphalab.execution.algorithms.IncompletePolicy.COMPLETE_AT_END` is
refused: it sends a child after the window has closed, and here no child
outlives its window. Each child is judged by the capability declaration, if
the plan states one; risk judged the parent. Paper and live runs route children
to their venue instead.

Persistence
-----------
An :class:`AlgorithmBook` is a value. :func:`capture_book` writes it as plain
primitives and :func:`book_from_primitives` reads them back, given the plan it
was produced under -- refused, by :attr:`ExecutionPlan.plan_id`, if it is
another -- and every algorithm run is rebuilt by
:func:`~alphalab.execution.algorithms.start_algorithm` and checked against its
recorded ``algorithm_id``, so an altered payload is refused rather than
continued. A paused replay carries its book in its payload.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from alphalab.allocation.engine import AllocationEngine
from alphalab.backtesting.exceptions import BacktestError
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.arithmetic import canonical_text
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.common.persistent_map import PersistentMap, PersistentSet
from alphalab.core.capabilities import (
    Capability,
    CapabilityDeclaration,
    Compatibility,
    Support,
    check_compatibility,
    order_requirements,
)
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution.algorithms import (
    TWAP,
    VWAP,
    AlgorithmState,
    AlgorithmStatus,
    AlgorithmTerms,
    ChildOrder,
    ChildProgress,
    ExecutionAlgorithm,
    Iceberg,
    IncompletePolicy,
    IntervalVolume,
    MissingVolumePolicy,
    Participation,
    Slicing,
    TrancheRandomization,
    Urgency,
    VolumeProfile,
    algorithm_configuration_id,
    cancel_algorithm,
    record_child_execution,
    record_child_outcome,
    release_children,
    start_algorithm,
)
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.policy import FillPolicy, FillTiming
from alphalab.market.events import (
    BarClosed,
    MarketEvent,
    QuoteReceived,
    TickReceived,
    TradeReceived,
)
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order as OMSOrder
from alphalab.portfolio.fx import NO_RATES, FxRates
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.runtime.run import ExecutionMode, RunConfig, RunState

__all__ = [
    "ALGORITHM_BOOK_SCHEMA",
    "EXECUTION_PLAN_SCHEME",
    "AlgorithmAssignment",
    "AlgorithmBook",
    "ChildFill",
    "ExecutionPlan",
    "ExecutionPlanError",
    "FinishedParent",
    "VenueCheck",
    "algorithmic_run",
    "book_from_primitives",
    "capture_book",
    "finish",
    "work",
]

#: Scheme tag of an execution plan's identity.
EXECUTION_PLAN_SCHEME: Final = "alphalab.execution_plan.v1"

#: The version of the primitives :func:`capture_book` writes.
ALGORITHM_BOOK_SCHEMA: Final = 1

_ZERO = Decimal("0")


class ExecutionPlanError(BacktestError):
    """A plan, or a book written under one, that cannot be used as given."""


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AlgorithmAssignment:
    """How the parents of one asset -- or of every asset -- are worked.

    Attributes:
        algorithm: The configuration, as :mod:`alphalab.execution.algorithms`
            states it.
        window_seconds: How long each parent is worked, from the instant it was
            placed. A parent with an earlier expiry is worked until then.
        quantity_increment: The smallest unit a child trades in -- a share, a
            contract, a lot. No default: it is a market convention. A parent
            that is not a whole number of them is rejected.

    Raises:
        ExecutionPlanError: If the window is not a positive finite number of
            seconds, the increment is not positive, or the algorithm sends
            children after its window (``Participation`` with
            ``COMPLETE_AT_END``), which a historical run does not model.
    """

    algorithm: ExecutionAlgorithm
    window_seconds: float
    quantity_increment: Decimal

    def __post_init__(self) -> None:
        window = self.window_seconds
        if isinstance(window, bool) or not isinstance(window, int | float):
            raise ExecutionPlanError(f"window_seconds must be a number, got {window!r}.")
        if not math.isfinite(window) or window <= 0:
            raise ExecutionPlanError(f"A window of {window!r} seconds contains no time to trade.")
        if not isinstance(self.quantity_increment, Decimal) or not self.quantity_increment > _ZERO:
            raise ExecutionPlanError(
                f"A quantity increment of {self.quantity_increment!r} is not a unit."
            )
        algorithm = self.algorithm
        if not isinstance(algorithm, TWAP | VWAP | Participation | Slicing | Iceberg):
            raise ExecutionPlanError(f"{algorithm!r} is not an execution algorithm.")
        if (
            isinstance(algorithm, Participation)
            and algorithm.at_end is IncompletePolicy.COMPLETE_AT_END
        ):
            raise ExecutionPlanError(
                "Participation with COMPLETE_AT_END sends a child after its window closes; a "
                "historical run works no child beyond its window. Use LEAVE_UNFILLED, and the "
                "quantity not filled is reported unfilled."
            )

    def _render(self) -> list[str]:
        return [
            f"algorithm={algorithm_configuration_id(self.algorithm)}",
            f"window={self.window_seconds!r}",
            f"increment={canonical_text(self.quantity_increment)}",
        ]


@dataclass(frozen=True, slots=True)
class VenueCheck:
    """What every child is checked against before it trades: the venue's declaration.

    Each child is checked as a ``DAY`` order of its own type, side and quantity,
    on the asset class and listing exchange its instrument record declares,
    short selling derived from the position held before it -- the check
    :func:`~alphalab.runtime.broker_routing.route_child_order` makes on the live
    path. A child the venue does not declare it can take is rejected, which
    stops the algorithm.

    Attributes:
        declaration: The venue's declared capabilities.
        account_id: The account the children are sent under.
        features: Venue-level capabilities the children depend on.
    """

    declaration: CapabilityDeclaration
    account_id: str
    features: frozenset[Capability] = frozenset()

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ExecutionPlanError("A capability check names no account.")


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Which algorithm works which parents, in a historical run.

    Attributes:
        default: The assignment for every asset ``by_asset`` does not name, or
            ``None`` -- a parent in an asset with no assignment is rejected.
        by_asset: ``(asset_id, assignment)`` pairs, one per asset; held sorted.
        capabilities: The venue declaration each child is checked against, or
            ``None`` for no check.

    Raises:
        ExecutionPlanError: If the plan assigns nothing, or names an asset twice.
    """

    default: AlgorithmAssignment | None = None
    by_asset: tuple[tuple[str, AlgorithmAssignment], ...] = ()
    capabilities: VenueCheck | None = None
    _assignments: Mapping[str, AlgorithmAssignment] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        pairs = tuple(sorted(self.by_asset, key=lambda pair: pair[0]))
        assets = [asset for asset, _ in pairs]
        if len(set(assets)) != len(assets):
            raise ExecutionPlanError(f"An execution plan names {assets} with repeats.")
        if self.default is None and not pairs:
            raise ExecutionPlanError(
                "An execution plan assigns no algorithm, so it would work no parent."
            )
        object.__setattr__(self, "by_asset", pairs)
        object.__setattr__(self, "_assignments", dict(pairs))

    def assignment_for(self, asset_id: str) -> AlgorithmAssignment | None:
        """The assignment working ``asset_id``'s parents, or ``None``."""

        return self._assignments.get(asset_id, self.default)

    @property
    def plan_id(self) -> str:
        """SHA-256 over every assignment and the capability check."""

        lines = [EXECUTION_PLAN_SCHEME]
        if self.default is not None:
            lines.extend(f"default.{line}" for line in self.default._render())
        for asset, assignment in self.by_asset:
            lines.extend(f"asset.{asset!r}.{line}" for line in assignment._render())
        check = self.capabilities
        if check is not None:
            lines.append(f"capabilities={check.declaration.declaration_id}")
            lines.append(f"account={check.account_id!r}")
            lines.append(f"features={sorted(str(f) for f in check.features)}")
        return _digest(lines)


# --------------------------------------------------------------------------- #
# The book
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ChildFill:
    """One child's fill, as the parent's books received it.

    Attributes:
        child_id: The child, ``"<parent>/<sequence>"``.
        parent_order_id: The parent OMS order the fill settled on.
        execution_id: The execution report's id -- the one the parent's OMS,
            portfolio and trade record carry.
        quantity: What filled.
        price: At what price, costs included.
        commission: The cash charges the report carried.
        timestamp: The event it filled at.
    """

    child_id: str
    parent_order_id: str
    execution_id: str
    quantity: Decimal
    price: Decimal
    commission: Decimal
    timestamp: float


@dataclass(frozen=True, slots=True)
class FinishedParent:
    """A parent the book no longer works, and why.

    Attributes:
        order_id: The parent OMS order.
        status: Its final OMS status.
        at: When it ended.
        reason: Why -- the algorithm's, or the book's own when it never worked it.
        algorithm: The algorithm's final state, or ``None`` for a parent rejected
            before an algorithm started.
    """

    order_id: str
    status: OrderStatus
    at: float
    reason: str
    algorithm: AlgorithmState | None


@dataclass(frozen=True, slots=True)
class AlgorithmBook:
    """Every parent a run's execution plan has worked, works, or refused. A value.

    Attributes:
        plan: The plan.
        working: The algorithm working each open parent, by parent order id, in
            the order the parents were adopted.
        finished: Every parent ended, by parent order id, in the order they ended.
        fills: Every child fill, in the order the books received them.
    """

    plan: ExecutionPlan
    working: PersistentMap[str, AlgorithmState] = field(default_factory=PersistentMap)
    finished: PersistentMap[str, FinishedParent] = field(default_factory=PersistentMap)
    fills: AppendOnlyLog[ChildFill] = field(default_factory=AppendOnlyLog)

    @property
    def incomplete(self) -> tuple[AlgorithmState, ...]:
        """Algorithms still working a parent: what a run that ended now left unexecuted."""

        return tuple(self.working.values())

    def algorithm_for(self, parent_order_id: str) -> AlgorithmState | None:
        """The algorithm that worked, or works, a parent; ``None`` if none did."""

        working = self.working.get(parent_order_id)
        if working is not None:
            return working
        finished = self.finished.get(parent_order_id)
        return None if finished is None else finished.algorithm

    def fills_of(self, parent_order_id: str) -> tuple[ChildFill, ...]:
        """A parent's child fills, in order."""

        return tuple(f for f in self.fills if f.parent_order_id == parent_order_id)


# --------------------------------------------------------------------------- #
# Working the book at a market event
# --------------------------------------------------------------------------- #


def _event_asset(event: MarketEvent) -> str | None:
    if isinstance(event, BarClosed):
        return event.bar.asset_id
    if isinstance(event, QuoteReceived):
        return event.quote.asset_id
    if isinstance(event, TickReceived | TradeReceived):
        return event.tick.asset_id
    return None


def _printed_volume(event: MarketEvent) -> Decimal | None:
    """What the event says traded: a bar's volume, a print's quantity; a quote, nothing."""

    if isinstance(event, BarClosed):
        return event.bar.volume
    if isinstance(event, TickReceived | TradeReceived):
        return event.tick.quantity
    return None


def _request_of(pipeline: ExecutionPipelineState, parent: OMSOrder) -> OrderRequest:
    """The parent as the algorithm reads it: the request the OMS order was placed from."""

    order_id = str(parent.order_id.value)
    return OrderRequest(
        order_id=order_id,
        strategy_id=parent.strategy_id,
        asset_id=parent.asset_id,
        side=parent.side,
        quantity=parent.quantity,
        price=Decimal(parent.metadata["reference_price"]),
        timestamp=parent.created_at,
        contributions=AllocationEngine.contributions_for(pipeline.allocation, order_id),
        terms=parent.terms,
    )


def _end(
    book: AlgorithmBook,
    pipeline: ExecutionPipelineState,
    parent: OMSOrder,
    status: OrderStatus,
    at: float,
    reason: str,
    algorithm: AlgorithmState | None,
) -> tuple[AlgorithmBook, ExecutionPipelineState]:
    """End a parent, once: through the pipeline when it is still open, then in the book."""

    order_id = str(parent.order_id.value)
    final = status
    if parent.is_open:
        if status is OrderStatus.REJECTED and parent.filled_quantity > _ZERO:
            # The OMS does not reject an order that has traded; what is left of
            # it is cancelled instead, and the reason says why.
            final = OrderStatus.CANCELLED
        pipeline = ExecutionPipeline.apply_terminal_outcome(pipeline, parent, final, at, reason)
    else:
        final = parent.status
    return (
        replace(
            book,
            working=book.working.delete(order_id) if order_id in book.working else book.working,
            finished=book.finished.set(
                order_id, FinishedParent(order_id, final, at, reason, algorithm)
            ),
        ),
        pipeline,
    )


def _adopt(
    book: AlgorithmBook, pipeline: ExecutionPipelineState, asset_id: str, at: float
) -> tuple[AlgorithmBook, ExecutionPipelineState]:
    """Start an algorithm on every parent placed in ``asset_id`` since the book last looked."""

    for oms_id in pipeline.oms.working_orders_for(asset_id):
        order_id = str(oms_id.value)
        if order_id in book.working or order_id in book.finished:
            continue
        parent = pipeline.oms.orders.find(oms_id)
        assignment = book.plan.assignment_for(asset_id)
        if assignment is None:
            book, pipeline = _end(
                book,
                pipeline,
                parent,
                OrderStatus.REJECTED,
                at,
                f"No execution algorithm is assigned to {asset_id}; a run that works its "
                "orders in children does not fill one whole.",
                None,
            )
            continue
        start = parent.created_at
        end = start + assignment.window_seconds
        if parent.expire_at is not None:
            end = min(end, parent.expire_at)
        try:
            algorithm = start_algorithm(
                _request_of(pipeline, parent),
                assignment.algorithm,
                AlgorithmTerms(
                    start=start,
                    end=end,
                    quantity_increment=assignment.quantity_increment,
                    order_type=parent.order_type,
                    limit_price=parent.limit_price,
                ),
            )
        except ExecutionValidationError as refused:
            book, pipeline = _end(
                book,
                pipeline,
                parent,
                OrderStatus.REJECTED,
                at,
                f"The execution algorithm cannot work this parent: {refused}",
                None,
            )
            continue
        book = replace(book, working=book.working.set(order_id, algorithm))
    return book, pipeline


def _capability_refusal(
    check: VenueCheck | None, pipeline: ExecutionPipelineState, child: ChildOrder
) -> str | None:
    if check is None:
        return None
    registry = pipeline.config.instruments
    record = None if registry is None else registry.record_for(child.asset_id)
    if record is None:
        return (
            f"Child {child.child_id} cannot be checked against the venue's declaration: no "
            f"instrument record declares {child.asset_id}'s asset class and listing exchange."
        )
    held = pipeline.portfolio.positions.get(child.asset_id)
    report = check_compatibility(
        order_requirements(
            asset_class=record.asset_type,
            listing_venue=record.exchange,
            account_id=check.account_id,
            order_type=child.order_type,
            time_in_force=TimeInForce.DAY,
            side=child.side,
            quantity=child.quantity,
            position_before=_ZERO if held is None else held.quantity,
            extended_hours=False,
            bracket_orders=False,
            margin=False,
            features=check.features,
        ),
        check.declaration,
    )
    if report.status is Compatibility.COMPATIBLE:
        return None
    failing = [c.requirement for c in report.checks if c.support is not Support.SUPPORTED]
    return (
        f"Child {child.child_id} is {report.status} with the venue's declaration: "
        f"{', '.join(failing)}."
    )


def _turn(
    book: AlgorithmBook,
    pipeline: ExecutionPipelineState,
    algorithm: AlgorithmState,
    child: ChildOrder,
    event: MarketEvent,
    rates: FxRates,
    fill_policy: FillPolicy,
    *,
    arriving: bool,
) -> tuple[AlgorithmBook, ExecutionPipelineState, AlgorithmState]:
    """One child's turn at ``event``: its fill settled on the parent, its outcome booked."""

    progress = algorithm.children[child.child_id]
    turn = ExecutionPipeline.work_child(
        pipeline, child, progress.working, event, fill_policy, rates, arriving=arriving
    )
    fills = book.fills
    for report in turn.reports:
        algorithm = record_child_execution(
            algorithm, child.child_id, report.execution_id, report.fill_quantity, report.timestamp
        )
        fills = fills.append(
            ChildFill(
                child.child_id,
                child.parent_order_id,
                report.execution_id,
                report.fill_quantity,
                report.fill_price,
                report.commission,
                report.timestamp,
            )
        )
    if turn.outcome is not None and turn.outcome is not OrderStatus.FILLED:
        algorithm = record_child_outcome(algorithm, child.child_id, turn.outcome, event.timestamp)
    return replace(book, fills=fills), turn.state, algorithm


def _work_parent(
    book: AlgorithmBook,
    pipeline: ExecutionPipelineState,
    order_id: str,
    event: MarketEvent,
    rates: FxRates,
    fill_policy: FillPolicy,
) -> tuple[AlgorithmBook, ExecutionPipelineState]:
    at = event.timestamp
    algorithm = book.working[order_id]
    parent = pipeline.oms.orders.find(_oms_id(order_id))
    same_event = pipeline.config.fill_timing is FillTiming.SAME_EVENT

    if not parent.is_open:
        # The run ended it -- a split, a settlement -- so the algorithm stops.
        algorithm = _cancel_working(algorithm, at, f"The run ended the parent: {parent.status}.")
        return _end(book, pipeline, parent, parent.status, at, algorithm.reason, algorithm)

    # Children past their slice, or the window, expire before anything trades.
    for child in algorithm.working_children:
        if at >= algorithm.terms.end or (child.expires_at is not None and at >= child.expires_at):
            algorithm = record_child_outcome(algorithm, child.child_id, OrderStatus.EXPIRED, at)

    # Children released before this event -- or at it, under SAME_EVENT -- trade.
    for child in algorithm.working_children:
        if child.released_at < at or (same_event and child.released_at == at):
            book, pipeline, algorithm = _turn(
                book, pipeline, algorithm, child, event, rates, fill_policy, arriving=False
            )

    if not algorithm.is_terminal:
        volumes: tuple[IntervalVolume, ...] = ()
        if isinstance(algorithm.algorithm, Participation):
            since = (
                algorithm.terms.start
                if algorithm.observed_until is None
                else algorithm.observed_until
            )
            until = min(at, algorithm.terms.end)
            if until > since:
                volumes = (IntervalVolume(since, until, _printed_volume(event)),)
        algorithm, released = release_children(algorithm, at, volumes)
        for child in released:
            refusal = _capability_refusal(book.plan.capabilities, pipeline, child)
            if refusal is not None:
                algorithm = record_child_outcome(
                    algorithm, child.child_id, OrderStatus.REJECTED, at
                )
                algorithm = replace(algorithm, reason=refusal)
                continue
            if same_event:
                book, pipeline, algorithm = _turn(
                    book, pipeline, algorithm, child, event, rates, fill_policy, arriving=True
                )

    if algorithm.is_terminal:
        parent = pipeline.oms.orders.find(parent.order_id)
        if algorithm.working_children:
            algorithm = _cancel_working(algorithm, at, algorithm.reason)
        status = {
            AlgorithmStatus.COMPLETED: OrderStatus.FILLED,
            AlgorithmStatus.EXPIRED: OrderStatus.EXPIRED,
            AlgorithmStatus.CANCELLED: OrderStatus.CANCELLED,
            AlgorithmStatus.FAILED: OrderStatus.REJECTED,
        }[algorithm.status]
        return _end(book, pipeline, parent, status, at, algorithm.reason, algorithm)
    return replace(book, working=book.working.set(order_id, algorithm)), pipeline


def _oms_id(order_id: str) -> OrderId:
    """The OMS key of a parent the book names by its text id."""

    return OrderId(UUID(order_id))


def _cancel_working(algorithm: AlgorithmState, at: float, reason: str) -> AlgorithmState:
    """Stop an algorithm, with every child still working booked cancelled."""

    working = algorithm.working_children
    if not algorithm.is_terminal:
        algorithm, working = cancel_algorithm(algorithm, at, reason)
    for child in working:
        algorithm = record_child_outcome(algorithm, child.child_id, OrderStatus.CANCELLED, at)
    return algorithm


def algorithmic_run(
    config: RunConfig, plan: ExecutionPlan | None, mode: ExecutionMode
) -> tuple[RunConfig, AlgorithmBook | None]:
    """The configuration a driver runs in ``mode`` under a plan, and the book it starts with.

    A plan routes the run ``ALGORITHMIC``; no plan leaves the routing to the
    mode. A configuration that routes ``ALGORITHMIC`` with no plan is refused: every
    order would be held, and none worked.

    Raises:
        ExecutionPlanError: If the configuration routes ``ALGORITHMIC`` and no
            plan is given.
    """

    if plan is None:
        if config.pipeline.routing is ExecutionRouting.ALGORITHMIC:
            raise ExecutionPlanError(
                "The run routes ALGORITHMIC and no execution plan was given, so every order "
                "would be held and none worked. Pass execution=ExecutionPlan(...)."
            )
        return replace(config, mode=mode), None
    pipeline = replace(config.pipeline, routing=ExecutionRouting.ALGORITHMIC)
    return replace(config, mode=mode, pipeline=pipeline), AlgorithmBook(plan)


def work(
    book: AlgorithmBook,
    state: RunState,
    event: MarketEvent,
    rates: FxRates = NO_RATES,
) -> tuple[AlgorithmBook, RunState]:
    """Work every parent in ``event``'s asset at ``event``, after the run's own step at it.

    Call it with the event a record became -- the
    :attr:`~alphalab.runtime.execution_pipeline.ExecutionPipelineResult.market_event`
    of :meth:`~alphalab.runtime.run.RunEngine.advance` -- and not for a record
    the run skipped. See the module docstring for what happens, in order.

    Raises:
        ExecutionPlanError: If the run does not route ``ALGORITHMIC``.
    """

    pipeline = state.pipeline
    if pipeline.config.routing is not ExecutionRouting.ALGORITHMIC:
        raise ExecutionPlanError(
            f"An execution plan works a run that routes ALGORITHMIC; this one routes "
            f"{pipeline.config.routing.name}."
        )
    asset_id = _event_asset(event)
    if asset_id is None:
        return book, state
    book, pipeline = _adopt(book, pipeline, asset_id, event.timestamp)
    for order_id, algorithm in tuple(book.working.items()):
        if algorithm.parent.asset_id != asset_id:
            continue
        book, pipeline = _work_parent(
            book, pipeline, order_id, event, rates, state.config.fill_policy
        )
    return book, replace(state, pipeline=pipeline)


def finish(book: AlgorithmBook, state: RunState) -> tuple[AlgorithmBook, RunState]:
    """Bring the book up to the run's end: what the run did after each asset's last event.

    A parent placed after its asset's last event -- on the data's last slice, by
    a timer -- is adopted, and starts no child, since no event follows. A
    contract settled after its last record, or a split applied to an asset that
    printed nothing after it, cancels working parents with no event of their
    asset to notice it at; their algorithms are cancelled. A parent still open
    stays working: :attr:`AlgorithmBook.incomplete`.
    """

    pipeline = state.pipeline
    at = state.current_timestamp
    for asset_id in dict.fromkeys(order.asset_id for order in state.working_orders):
        book, pipeline = _adopt(book, pipeline, asset_id, at)
    for order_id, algorithm in tuple(book.working.items()):
        parent = pipeline.oms.orders.find(_oms_id(order_id))
        if parent.is_open:
            continue
        algorithm = _cancel_working(algorithm, at, f"The run ended the parent: {parent.status}.")
        book, pipeline = _end(
            book, pipeline, parent, parent.status, at, algorithm.reason, algorithm
        )
    return book, replace(state, pipeline=pipeline)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _read_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(value)


def _algorithm_out(algorithm: ExecutionAlgorithm) -> dict[str, Any]:
    if isinstance(algorithm, TWAP):
        return {"kind": "twap", "slices": algorithm.slices, "urgency": str(algorithm.urgency.kappa)}
    if isinstance(algorithm, VWAP):
        return {
            "kind": "vwap",
            "source": algorithm.profile.source,
            "intervals": [
                [i.start, i.end, _decimal(i.volume)] for i in algorithm.profile.intervals
            ],
            "missing_volume": algorithm.missing_volume.value,
            "urgency": str(algorithm.urgency.kappa),
        }
    if isinstance(algorithm, Participation):
        return {
            "kind": "participation",
            "rate": str(algorithm.rate),
            "min": _decimal(algorithm.min_child_quantity),
            "max": _decimal(algorithm.max_child_quantity),
            "at_end": algorithm.at_end.value,
        }
    if isinstance(algorithm, Slicing):
        return {
            "kind": "slicing",
            "slice_quantity": _decimal(algorithm.slice_quantity),
            "slice_count": algorithm.slice_count,
        }
    randomization = algorithm.randomization
    return {
        "kind": "iceberg",
        "display": str(algorithm.display_quantity),
        "randomization": None
        if randomization is None
        else {"spread": str(randomization.spread), "seed": randomization.seed},
    }


def _algorithm_in(payload: Mapping[str, Any]) -> ExecutionAlgorithm:
    kind = payload["kind"]
    if kind == "twap":
        return TWAP(int(payload["slices"]), Urgency(Decimal(payload["urgency"])))
    if kind == "vwap":
        return VWAP(
            VolumeProfile(
                tuple(
                    IntervalVolume(float(s), float(e), _read_decimal(v))
                    for s, e, v in payload["intervals"]
                ),
                payload["source"],
            ),
            MissingVolumePolicy(payload["missing_volume"]),
            Urgency(Decimal(payload["urgency"])),
        )
    if kind == "participation":
        return Participation(
            Decimal(payload["rate"]),
            _read_decimal(payload["min"]),
            _read_decimal(payload["max"]),
            IncompletePolicy(payload["at_end"]),
        )
    if kind == "slicing":
        count = payload["slice_count"]
        return Slicing(
            _read_decimal(payload["slice_quantity"]), None if count is None else int(count)
        )
    if kind == "iceberg":
        drawn = payload["randomization"]
        return Iceberg(
            Decimal(payload["display"]),
            None
            if drawn is None
            else TrancheRandomization(Decimal(drawn["spread"]), int(drawn["seed"])),
        )
    raise ExecutionPlanError(f"{kind!r} is not an execution algorithm this book can read.")


def _terms_out(terms: OrderTerms) -> dict[str, Any]:
    return {
        "order_type": terms.order_type.name,
        "limit_price": _decimal(terms.limit_price),
        "stop_price": _decimal(terms.stop_price),
        "time_in_force": terms.time_in_force.name,
        "expire_at": terms.expire_at,
    }


def _terms_in(payload: Mapping[str, Any]) -> OrderTerms:
    return OrderTerms(
        order_type=OrderType[payload["order_type"]],
        limit_price=_read_decimal(payload["limit_price"]),
        stop_price=_read_decimal(payload["stop_price"]),
        time_in_force=TimeInForce[payload["time_in_force"]],
        expire_at=None if payload["expire_at"] is None else float(payload["expire_at"]),
    )


def _request_out(request: OrderRequest) -> dict[str, Any]:
    return {
        "order_id": request.order_id,
        "strategy_id": request.strategy_id,
        "asset_id": request.asset_id,
        "side": request.side.name,
        "quantity": str(request.quantity),
        "price": str(request.price),
        "timestamp": request.timestamp,
        "contributions": [[c.strategy_id, str(c.quantity)] for c in request.contributions],
        "terms": _terms_out(request.terms),
    }


def _request_in(payload: Mapping[str, Any]) -> OrderRequest:
    return OrderRequest(
        order_id=payload["order_id"],
        strategy_id=payload["strategy_id"],
        asset_id=payload["asset_id"],
        side=Side[payload["side"]],
        quantity=Decimal(payload["quantity"]),
        price=Decimal(payload["price"]),
        timestamp=float(payload["timestamp"]),
        contributions=tuple(
            StrategyContribution(sid, Decimal(quantity))
            for sid, quantity in payload["contributions"]
        ),
        terms=_terms_in(payload["terms"]),
    )


def _state_out(state: AlgorithmState) -> dict[str, Any]:
    terms = state.terms
    return {
        "parent": _request_out(state.parent),
        "algorithm": _algorithm_out(state.algorithm),
        "terms": {
            "start": terms.start,
            "end": terms.end,
            "increment": str(terms.quantity_increment),
            "order_type": terms.order_type.name,
            "limit_price": _decimal(terms.limit_price),
        },
        "algorithm_id": state.algorithm_id,
        "children": [
            {
                "sequence": p.child.sequence,
                "quantity": str(p.child.quantity),
                "released_at": p.child.released_at,
                "expires_at": p.child.expires_at,
                "filled": str(p.filled),
                "outcome": None if p.outcome is None else p.outcome.name,
            }
            for p in state.children.values()
        ],
        "released": str(state.released),
        "filled": str(state.filled),
        "returned": str(state.returned),
        "executions": list(state.executions),
        "observed_volume": str(state.observed_volume),
        "observed_until": state.observed_until,
        "status": state.status.value,
        "reason": state.reason,
        "notes": list(state.notes),
    }


def _state_in(payload: Mapping[str, Any]) -> AlgorithmState:
    terms = payload["terms"]
    started = start_algorithm(
        _request_in(payload["parent"]),
        _algorithm_in(payload["algorithm"]),
        AlgorithmTerms(
            start=float(terms["start"]),
            end=float(terms["end"]),
            quantity_increment=Decimal(terms["increment"]),
            order_type=OrderType[terms["order_type"]],
            limit_price=_read_decimal(terms["limit_price"]),
        ),
    )
    if started.algorithm_id != payload["algorithm_id"]:
        raise ExecutionPlanError(
            f"An algorithm run recorded as {payload['algorithm_id']} rebuilds as "
            f"{started.algorithm_id}: the payload was altered, and continuing it would work "
            "another order than the one recorded."
        )
    children: PersistentMap[str, ChildProgress] = PersistentMap()
    for entry in payload["children"]:
        sequence = int(entry["sequence"])
        child = ChildOrder(
            child_id=f"{started.parent.order_id}/{sequence}",
            parent_order_id=started.parent.order_id,
            sequence=sequence,
            asset_id=started.parent.asset_id,
            side=started.parent.side,
            quantity=Decimal(entry["quantity"]),
            order_type=started.terms.order_type,
            limit_price=started.terms.limit_price,
            released_at=float(entry["released_at"]),
            expires_at=None if entry["expires_at"] is None else float(entry["expires_at"]),
            strategy_id=started.parent.strategy_id,
            contributions=started.parent.contributions,
            algorithm_id=started.algorithm_id,
        )
        outcome = entry["outcome"]
        children = children.set(
            child.child_id,
            ChildProgress(
                child, Decimal(entry["filled"]), None if outcome is None else OrderStatus[outcome]
            ),
        )
    executions: PersistentSet[str] = PersistentSet()
    for execution_id in payload["executions"]:
        executions = executions.add(execution_id)
    until = payload["observed_until"]
    return replace(
        started,
        children=children,
        released=Decimal(payload["released"]),
        filled=Decimal(payload["filled"]),
        returned=Decimal(payload["returned"]),
        executions=executions,
        observed_volume=Decimal(payload["observed_volume"]),
        observed_until=None if until is None else float(until),
        status=AlgorithmStatus(payload["status"]),
        reason=payload["reason"],
        notes=tuple(payload["notes"]),
    )


def capture_book(book: AlgorithmBook) -> dict[str, Any]:
    """The book as plain primitives, for a payload. Read back by :func:`book_from_primitives`."""

    return {
        "schema_version": ALGORITHM_BOOK_SCHEMA,
        "plan_id": book.plan.plan_id,
        "working": [_state_out(state) for state in book.working.values()],
        "finished": [
            {
                "order_id": f.order_id,
                "status": f.status.name,
                "at": f.at,
                "reason": f.reason,
                "algorithm": None if f.algorithm is None else _state_out(f.algorithm),
            }
            for f in book.finished.values()
        ],
        "fills": [
            [
                f.child_id,
                f.parent_order_id,
                f.execution_id,
                str(f.quantity),
                str(f.price),
                str(f.commission),
                f.timestamp,
            ]
            for f in book.fills
        ],
    }


def book_from_primitives(payload: Mapping[str, Any], plan: ExecutionPlan) -> AlgorithmBook:
    """Read a book :func:`capture_book` wrote, under the plan it was produced by.

    Raises:
        ExecutionPlanError: If the schema version is not this build's, ``plan``
            is not the plan the book was produced under, or an algorithm run
            does not rebuild to its recorded identity.
    """

    version = payload.get("schema_version")
    if version != ALGORITHM_BOOK_SCHEMA:
        raise ExecutionPlanError(
            f"An algorithm book of schema {version!r}; this build reads {ALGORITHM_BOOK_SCHEMA}."
        )
    if payload["plan_id"] != plan.plan_id:
        raise ExecutionPlanError(
            f"The book was produced under plan {payload['plan_id']} and plan {plan.plan_id} "
            "was supplied; continuing it would work its parents another way."
        )
    working: PersistentMap[str, AlgorithmState] = PersistentMap()
    for entry in payload["working"]:
        state = _state_in(entry)
        working = working.set(state.parent.order_id, state)
    finished: PersistentMap[str, FinishedParent] = PersistentMap()
    for entry in payload["finished"]:
        recorded = entry["algorithm"]
        finished = finished.set(
            entry["order_id"],
            FinishedParent(
                entry["order_id"],
                OrderStatus[entry["status"]],
                float(entry["at"]),
                entry["reason"],
                None if recorded is None else _state_in(recorded),
            ),
        )
    fills: AppendOnlyLog[ChildFill] = AppendOnlyLog()
    for child_id, parent_id, execution_id, quantity, price, commission, at in payload["fills"]:
        fills = fills.append(
            ChildFill(
                child_id,
                parent_id,
                execution_id,
                Decimal(quantity),
                Decimal(price),
                Decimal(commission),
                float(at),
            )
        )
    return AlgorithmBook(plan, working, finished, fills)
