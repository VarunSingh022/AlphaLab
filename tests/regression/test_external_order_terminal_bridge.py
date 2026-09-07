"""A working external order the venue has ended can be ended here too.

Under :attr:`~alphalab.runtime.execution_pipeline.ExecutionRouting.EXTERNAL` an
accepted order is left working for a broker adapter to route, and its allocation
reservation and contribution stay held -- correctly, because the order is live and
that capital is committed (ADR-0021 decision 6). What ADR-0021 also recorded, as
a known boundary rather than an oversight inside it, is that nothing could *end*
such an order. A venue fill had a route home through ``apply_execution_report``;
a venue rejection, cancellation or expiry had none, so the whole public surface of
the pipeline was seven methods and not one of them terminated an order. Measured
on v2.8.0, six externally routed events left six open orders holding six
reservations, six contributions and 3,000 of committed notional with no way to
retire any of it.

``apply_terminal_outcome`` is that route. The caller supplies the outcome -- the
venue is the authority for what happened and the pipeline never infers it -- and
the pipeline applies the transition that follows: ``OMSEngine`` moves the order
and emits its event, and ``_release_if_terminal`` retires both ledgers, exactly
as they do for every other terminal transition. No venue is contacted, no
``BrokerState`` is built, and ``broker.reconciliation`` is not consulted.

One thing to read carefully, because it is easy to expect otherwise:
``Order.cancel`` does not zero ``remaining_quantity``, and never has. A cancelled
partially filled order reports ``quantity=10, filled=4, remaining=6`` on the
*simulated* withdrawal path too (v2.5, ADR-0014 decision 3). What makes the order
finished is its status and ``is_open``, not its remaining quantity, and these
tests assert the invariant where the repository actually keeps it rather than
introducing a second convention here. See ADR-0024.
"""

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.oms.events import OrderRejected
from alphalab.oms.exceptions import InvalidTransitionError, UnknownOrderError
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order as OMSOrder
from alphalab.oms.status import OrderStatus, OrderType, Side
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())

TERMINAL_OUTCOMES = [OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED]


def _working(quantity: str = "10") -> tuple[ExecutionPipelineState, OMSOrder]:
    """One order left working by EXTERNAL routing, holding both ledgers.

    This is the state a restored live session is in: the order was accepted, no
    fill was invented for it, and its capital is still committed.
    """

    config = replace(pipeline_config(STRATEGY_ID), routing=ExecutionRouting.EXTERNAL)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, {2.0: Decimal(quantity)})
        ),
        1.0,
    )
    state = ExecutionPipeline.process_quote(
        state, sized_quote(ASSET_ID, 2.0, Decimal("100"), Decimal("100")), context_factory
    ).state
    return state, next(iter(state.oms.orders.open_orders()))


def _venue_fill(order: OMSOrder, quantity: str) -> ExecutionReport:
    return ExecutionReport(
        execution_id=str(uuid.uuid4()),
        order_id=str(order.order_id.value),
        asset_id=ASSET_ID,
        strategy_id="",
        timestamp=3.0,
        fill_price=Decimal("100"),
        fill_quantity=Decimal(quantity),
        commission=Decimal("0"),
        slippage=Decimal("0"),
        liquidity_flag="",
        venue="LIVE",
        currency="USD",
        status=FillStatus.PARTIAL_FILL,
    )


def _current(state: ExecutionPipelineState, order: OMSOrder) -> OMSOrder:
    return state.oms.orders.find(order.order_id)


# ---------------------------------------------------------------------------
# The motivating case: a working order holds both ledgers with no way out
# ---------------------------------------------------------------------------


def test_a_working_external_order_holds_both_ledgers() -> None:
    """The state D3 exists for. Without a bridge this is held indefinitely."""

    state, order = _working()

    assert _current(state, order).status is OrderStatus.ACCEPTED
    assert _current(state, order).is_open
    assert len(state.oms.active_orders) == 1
    assert len(state.allocation.reservations) == 1
    assert len(state.allocation.contributions) == 1
    assert state.allocation.notional_allocated == Decimal("1000.000000")


@pytest.mark.parametrize("outcome", TERMINAL_OUTCOMES)
def test_a_terminal_outcome_ends_the_order_and_retires_both_ledgers(
    outcome: OrderStatus,
) -> None:
    state, order = _working()

    terminated = ExecutionPipeline.apply_terminal_outcome(
        state, order, outcome, 5.0, reason="Venue refused the order"
    )
    current = _current(terminated, order)

    assert current.status is outcome
    assert not current.is_open
    assert order.order_id not in set(terminated.oms.active_orders)
    assert order.order_id in set(terminated.oms.completed_orders)
    assert dict(terminated.allocation.reservations) == {}
    assert dict(terminated.allocation.contributions) == {}
    assert terminated.allocation.notional_allocated == Decimal("0.000000")


@pytest.mark.parametrize("outcome", TERMINAL_OUTCOMES)
def test_terminalization_moves_no_money_and_creates_no_fill(outcome: OrderStatus) -> None:
    """Ending an order is not an implicit fill."""

    state, order = _working()

    terminated = ExecutionPipeline.apply_terminal_outcome(state, order, outcome, 5.0)

    assert terminated.portfolio.cash.balance("USD") == state.portfolio.cash.balance("USD")
    assert ASSET_ID not in terminated.portfolio.positions
    assert len(terminated.fills) == len(state.fills) == 0
    assert len(terminated.trades) == len(state.trades) == 0
    assert len(terminated.execution.reports) == len(state.execution.reports) == 0


# ---------------------------------------------------------------------------
# The lifecycle event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "event_type"),
    [
        (OrderStatus.CANCELLED, "OrderCancelled"),
        (OrderStatus.REJECTED, "OrderRejected"),
        (OrderStatus.EXPIRED, "OrderExpired"),
    ],
)
def test_exactly_one_terminal_event_is_emitted(outcome: OrderStatus, event_type: str) -> None:
    state, order = _working()
    before = [type(event).__name__ for event in state.oms.events]

    terminated = ExecutionPipeline.apply_terminal_outcome(state, order, outcome, 5.0)
    after = [type(event).__name__ for event in terminated.oms.events]

    assert after == [*before, event_type]
    assert after.count(event_type) == 1


def test_the_event_carries_the_timestamp_it_was_given() -> None:
    state, order = _working()

    terminated = ExecutionPipeline.apply_terminal_outcome(state, order, OrderStatus.CANCELLED, 42.0)

    assert terminated.oms.events[-1].timestamp == 42.0
    assert _current(terminated, order).updated_at == 42.0


def test_a_rejection_records_the_reason() -> None:
    state, order = _working()

    terminated = ExecutionPipeline.apply_terminal_outcome(
        state, order, OrderStatus.REJECTED, 5.0, reason="Insufficient margin at venue"
    )
    event = terminated.oms.events[-1]

    assert isinstance(event, OrderRejected)
    assert event.reason == "Insufficient margin at venue"


def test_an_absent_reason_is_recorded_as_absent_not_invented() -> None:
    state, order = _working()

    terminated = ExecutionPipeline.apply_terminal_outcome(state, order, OrderStatus.REJECTED, 5.0)
    event = terminated.oms.events[-1]

    assert isinstance(event, OrderRejected)
    assert event.reason == ""


@pytest.mark.parametrize("outcome", [OrderStatus.CANCELLED, OrderStatus.EXPIRED])
def test_the_events_without_a_reason_field_do_not_gain_one(outcome: OrderStatus) -> None:
    """A reason that cannot be recorded is dropped rather than the payload changed.

    ``OrderCancelled`` and ``OrderExpired`` carry no ``reason``, and OMS events
    are part of the snapshot payload whose schema v2.9 has just declared -- so
    giving them one would move a persisted shape one step after fixing it.
    """

    from dataclasses import fields

    from alphalab.oms.events import OrderCancelled, OrderExpired

    assert "reason" not in {field.name for field in fields(OrderCancelled)}
    assert "reason" not in {field.name for field in fields(OrderExpired)}

    state, order = _working()
    terminated = ExecutionPipeline.apply_terminal_outcome(
        state, order, outcome, 5.0, reason="ignored, and deliberately so"
    )

    assert not hasattr(terminated.oms.events[-1], "reason")
    assert _current(terminated, order).status is outcome


# ---------------------------------------------------------------------------
# A partially filled working order
# ---------------------------------------------------------------------------


def test_cancelling_a_partially_filled_order_preserves_the_fill() -> None:
    """filled 4 of 10, then the venue cancels the rest."""

    state, order = _working("10")
    filled, fills, _ = ExecutionPipeline.apply_execution_report(
        state, order, _venue_fill(order, "4")
    )
    partially = _current(filled, order)

    assert len(fills) == 1
    assert partially.status is OrderStatus.PARTIALLY_FILLED
    assert partially.filled_quantity == Decimal("4")
    assert partially.remaining_quantity == Decimal("6")

    terminated = ExecutionPipeline.apply_terminal_outcome(
        filled, partially, OrderStatus.CANCELLED, 5.0
    )
    current = _current(terminated, order)

    # The fill that happened is untouched.
    assert current.filled_quantity == Decimal("4")
    assert current.average_fill_price == Decimal("100")
    assert terminated.portfolio.positions[ASSET_ID].quantity == Decimal("4")
    assert terminated.portfolio.cash.balance("USD") == filled.portfolio.cash.balance("USD")

    # No second fill is created, and cancellation is not an implicit fill.
    assert len(terminated.fills) == len(filled.fills) == 1
    assert len(terminated.trades) == len(filled.trades) == 1
    assert len(terminated.execution.reports) == len(filled.execution.reports) == 1

    # Only the working exposure is closed.
    assert current.status is OrderStatus.CANCELLED
    assert not current.is_open
    assert dict(terminated.allocation.reservations) == {}
    assert dict(terminated.allocation.contributions) == {}
    assert terminated.allocation.notional_allocated == Decimal("0.000000")


def test_a_cancelled_order_keeps_the_quantities_that_describe_what_happened() -> None:
    """``Order.cancel`` does not zero ``remaining_quantity``, and never has.

    The pre-existing simulated withdrawal path (v2.5) reports the same
    ``quantity=10, filled=4, remaining=6`` for a cancelled partial fill, so the
    bridge matches it rather than introducing a second convention. What makes
    the order finished is ``status`` and ``is_open``: nothing can fill against
    it again, which ``test_a_terminated_order_cannot_be_filled_again`` holds.
    """

    state, order = _working("10")
    filled, _, _ = ExecutionPipeline.apply_execution_report(state, order, _venue_fill(order, "4"))
    terminated = ExecutionPipeline.apply_terminal_outcome(
        filled, _current(filled, order), OrderStatus.CANCELLED, 5.0
    )
    current = _current(terminated, order)

    assert (current.quantity, current.filled_quantity, current.remaining_quantity) == (
        Decimal("10.000000"),
        Decimal("4"),
        Decimal("6.000000"),
    )
    assert not current.is_open


def test_a_terminated_order_cannot_be_filled_again() -> None:
    """The invariant that matters: the order is finished, whatever it still reports."""

    state, order = _working("10")
    terminated = ExecutionPipeline.apply_terminal_outcome(state, order, OrderStatus.CANCELLED, 5.0)

    with pytest.raises(InvalidTransitionError):
        ExecutionPipeline.apply_execution_report(
            terminated, _current(terminated, order), _venue_fill(order, "4")
        )


# ---------------------------------------------------------------------------
# Negative cases: the existing lifecycle rules are preserved, not relaxed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", TERMINAL_OUTCOMES)
def test_terminalizing_an_already_terminal_order_is_refused(outcome: OrderStatus) -> None:
    """The OMS refuses it, and the bridge does not soften that."""

    state, order = _working()
    terminated = ExecutionPipeline.apply_terminal_outcome(state, order, OrderStatus.CANCELLED, 5.0)

    with pytest.raises(InvalidTransitionError):
        ExecutionPipeline.apply_terminal_outcome(
            terminated, _current(terminated, order), outcome, 6.0
        )


def test_a_refused_repeat_does_not_double_release_the_allocation_ledgers() -> None:
    state, order = _working()
    terminated = ExecutionPipeline.apply_terminal_outcome(state, order, OrderStatus.CANCELLED, 5.0)

    with pytest.raises(InvalidTransitionError):
        ExecutionPipeline.apply_terminal_outcome(
            terminated, _current(terminated, order), OrderStatus.CANCELLED, 6.0
        )

    assert dict(terminated.allocation.reservations) == {}
    assert dict(terminated.allocation.contributions) == {}
    assert terminated.allocation.notional_allocated == Decimal("0.000000")


def test_rejecting_a_partially_filled_order_is_refused_by_the_oms() -> None:
    """An order that has already traded cannot be rejected -- ``Order.reject``'s rule."""

    state, order = _working("10")
    filled, _, _ = ExecutionPipeline.apply_execution_report(state, order, _venue_fill(order, "4"))

    with pytest.raises(InvalidTransitionError, match="Cannot reject order in status"):
        ExecutionPipeline.apply_terminal_outcome(
            filled, _current(filled, order), OrderStatus.REJECTED, 5.0
        )


@pytest.mark.parametrize(
    "outcome",
    [
        OrderStatus.NEW,
        OrderStatus.PENDING,
        OrderStatus.ACCEPTED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
    ],
)
def test_a_non_terminal_outcome_is_refused(outcome: OrderStatus) -> None:
    """A status reached by trading is applied from a report, never asserted."""

    state, order = _working()

    with pytest.raises(RuntimeValidationError, match="not a terminal outcome"):
        ExecutionPipeline.apply_terminal_outcome(state, order, outcome, 5.0)


def test_an_unsupported_outcome_does_not_silently_become_another_status() -> None:
    state, order = _working()

    with pytest.raises(RuntimeValidationError):
        ExecutionPipeline.apply_terminal_outcome(state, order, OrderStatus.FILLED, 5.0)

    assert _current(state, order).status is OrderStatus.ACCEPTED
    assert len(state.allocation.reservations) == 1


def test_an_order_the_book_does_not_hold_is_refused() -> None:
    state, _ = _working()
    stranger = OMSOrder(
        order_id=OrderId(uuid.uuid4()),
        strategy_id=STRATEGY_ID,
        asset_id=ASSET_ID,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        status=OrderStatus.ACCEPTED,
        quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("1"),
        limit_price=None,
        stop_price=None,
        average_fill_price=Decimal("0"),
        created_at=1.0,
        updated_at=1.0,
        metadata={},
    )

    with pytest.raises(UnknownOrderError):
        ExecutionPipeline.apply_terminal_outcome(state, stranger, OrderStatus.CANCELLED, 5.0)


# ---------------------------------------------------------------------------
# Boundaries the bridge does not cross
# ---------------------------------------------------------------------------


def test_only_the_named_order_is_retired() -> None:
    """Two working orders; ending one leaves the other's ledgers alone."""

    config = replace(pipeline_config(STRATEGY_ID), routing=ExecutionRouting.EXTERNAL)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            STRATEGY_ID,
            ScriptedStrategy(STRATEGY_ID, ASSET_ID, {2.0: Decimal("5"), 3.0: Decimal("5")}),
        ),
        1.0,
    )
    for index in range(2):
        state = ExecutionPipeline.process_quote(
            state,
            sized_quote(ASSET_ID, 2.0 + index, Decimal("100"), Decimal("100")),
            context_factory,
        ).state

    first, second = tuple(state.oms.orders.open_orders())
    assert len(state.allocation.reservations) == 2

    terminated = ExecutionPipeline.apply_terminal_outcome(state, first, OrderStatus.CANCELLED, 9.0)

    assert set(terminated.allocation.reservations) == {str(second.order_id.value)}
    assert set(terminated.allocation.contributions) == {str(second.order_id.value)}
    assert _current(terminated, second).is_open
    assert set(terminated.oms.active_orders) == {second.order_id}


def test_the_bridge_works_under_simulated_routing_too() -> None:
    """It is a state transition, not a routing feature, and checks no routing mode."""

    state = ExecutionPipeline.initialize(
        pipeline_config(STRATEGY_ID),
        running_strategy_state(
            STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, {2.0: Decimal("5")})
        ),
        1.0,
    )
    order = ExecutionPipeline.process_quote(
        state, sized_quote(ASSET_ID, 2.0, Decimal("100"), Decimal("100")), context_factory
    )
    # A simulated fill already terminates the order, so it is refused -- proving
    # the bridge consults the order's lifecycle and not its routing.
    with pytest.raises(InvalidTransitionError):
        ExecutionPipeline.apply_terminal_outcome(
            order.state,
            next(iter(order.state.oms.orders.orders())),
            OrderStatus.CANCELLED,
            5.0,
        )


def test_the_bridge_touches_no_venue_state() -> None:
    """No BrokerState, no reconciliation, no transport."""

    import ast
    import inspect

    from alphalab.runtime import execution_pipeline

    tree = ast.parse(inspect.getsource(execution_pipeline))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported
    assert not [name for name in imported if name.startswith("alphalab.broker")]
