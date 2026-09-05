"""Regression guard for the risk-rejection reservation leak fixed in v2.2.

``AllocationEngine.allocate`` reserves each request's notional against the
capital budget. Before v2.2 the pipeline released that reservation only when an
execution came back non-trading; a request that risk *refused* was skipped with
a bare ``continue``, and a request dropped for having no market price was
skipped earlier still. Neither released anything, so ``notional_allocated``
over-reported for the rest of the run: capital was held against orders that had
never reached the OMS and never would.

The fix puts the release at the point each request's lifecycle actually ends,
and moves the amount into a per-order ledger so a release is attributable --
which is what makes "exactly once" checkable rather than merely asserted. A
second release of the same order now raises.

These tests drive the real pipeline; nothing here is a stub.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.events import AllocationReservationReleased
from alphalab.allocation.exceptions import UnknownReservationError
from alphalab.allocation.views import open_reservations
from alphalab.execution.fill import FillStatus
from alphalab.oms.status import OrderStatus
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    quote,
    running_strategy_state,
)

MID = Decimal("100.005")


def _pipeline(plan: dict[float, Decimal], asset_id: str, **config_kwargs: object):  # type: ignore[no-untyped-def]
    strategy_id = str(uuid4())
    config = pipeline_config(strategy_id, **config_kwargs)  # type: ignore[arg-type]
    strategy = ScriptedStrategy(strategy_id, asset_id, plan)
    return ExecutionPipeline.initialize(config, running_strategy_state(strategy_id, strategy), 1.0)


def _released_events(state) -> list[AllocationReservationReleased]:  # type: ignore[no-untyped-def]
    return [e for e in state.allocation.events if isinstance(e, AllocationReservationReleased)]


# ---------------------------------------------------------------------------
# The leak itself
# ---------------------------------------------------------------------------


def test_a_risk_rejected_request_releases_its_reservation() -> None:
    """The defect: capital stayed held against an order that never existed."""

    asset_id = str(uuid4())
    state = _pipeline(
        {2.0: Decimal("10")},
        asset_id,
        risk_limits=permissive_risk_limits(max_order_quantity=Decimal("1")),
    )

    result = ExecutionPipeline.process_quote(state, quote(asset_id, 2.0, MID), context_factory)

    assert result.risk_decisions and not result.risk_decisions[0].approved
    assert result.oms_orders == ()
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert dict(open_reservations(result.state.allocation)) == {}


def test_the_rejected_release_is_recorded_with_its_amount() -> None:
    asset_id = str(uuid4())
    state = _pipeline(
        {2.0: Decimal("10")},
        asset_id,
        risk_limits=permissive_risk_limits(max_order_quantity=Decimal("1")),
    )

    result = ExecutionPipeline.process_quote(state, quote(asset_id, 2.0, MID), context_factory)
    released = _released_events(result.state)

    assert len(released) == 1
    assert released[0].released_notional == Decimal("10") * MID


def test_repeated_risk_rejections_do_not_accumulate_held_capital() -> None:
    """The leak compounded: every rejection added notional that never came back."""

    asset_id = str(uuid4())
    plan = {2.0: Decimal("10"), 3.0: Decimal("10"), 4.0: Decimal("10")}
    state = _pipeline(
        plan, asset_id, risk_limits=permissive_risk_limits(max_order_quantity=Decimal("1"))
    )

    for timestamp in (2.0, 3.0, 4.0):
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, timestamp, MID), context_factory
        ).state

    assert state.allocation.notional_allocated == Decimal("0.00")
    assert len(_released_events(state)) == 3


def test_a_request_with_no_market_price_releases_its_reservation() -> None:
    """The other silent skip: dropped before risk, and never released."""

    asset_id = str(uuid4())
    unpriced_asset = str(uuid4())
    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(
        strategy_id,
        asset_id,
        {2.0: Decimal("10")},
        asset_for={2.0: unpriced_asset},
    )
    state = ExecutionPipeline.initialize(
        pipeline_config(strategy_id), running_strategy_state(strategy_id, strategy), 1.0
    )

    result = ExecutionPipeline.process_quote(state, quote(asset_id, 2.0, MID), context_factory)

    assert len(result.unpriced_requests) == 1
    assert result.oms_orders == ()
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert dict(open_reservations(result.state.allocation)) == {}


# ---------------------------------------------------------------------------
# The paths that already worked must keep working
# ---------------------------------------------------------------------------


def test_a_partial_fill_consumes_and_then_releases_its_whole_reservation() -> None:
    """A partial fill executes part of the reservation and frees the rest.

    The allocation engine still leaves the residual reserved when the execution
    is applied -- that is correct, and its docstring says why. What changed in
    v2.5 is what happens next: the pipeline withdraws the remainder in the same
    step, because nothing will ever work it, so the residual is released rather
    than held forever. See ADR-0014.
    """

    asset_id = str(uuid4())
    state = _pipeline({2.0: Decimal("10")}, asset_id)

    result = ExecutionPipeline.process_quote(
        state,
        quote(asset_id, 2.0, MID),
        context_factory,
        FillStatus.PARTIAL_FILL,
        Decimal("4"),
    )
    order = result.oms_orders[0]

    assert result.state.oms.orders.find(order.order_id).status is OrderStatus.CANCELLED
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert AllocationEngine.reserved_notional(
        result.state.allocation, str(order.order_id.value)
    ) == Decimal("0.00")
    assert dict(open_reservations(result.state.allocation)) == {}


def test_a_fully_executed_request_consumes_its_reservation() -> None:
    asset_id = str(uuid4())
    state = _pipeline({2.0: Decimal("10")}, asset_id)

    result = ExecutionPipeline.process_quote(state, quote(asset_id, 2.0, MID), context_factory)

    assert len(result.fills) == 1
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert dict(open_reservations(result.state.allocation)) == {}
    # Consumed by the execution, not released as unused capital.
    assert _released_events(result.state) == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (FillStatus.REJECTED, OrderStatus.REJECTED),
        (FillStatus.EXPIRED, OrderStatus.EXPIRED),
        (FillStatus.NO_FILL, OrderStatus.CANCELLED),
    ],
)
def test_every_non_trading_outcome_releases_exactly_once(
    status: FillStatus, expected: OrderStatus
) -> None:
    asset_id = str(uuid4())
    state = _pipeline({2.0: Decimal("10")}, asset_id)

    result = ExecutionPipeline.process_quote(
        state, quote(asset_id, 2.0, MID), context_factory, status
    )
    order = result.oms_orders[0]

    assert result.state.oms.orders.find(order.order_id).status is expected
    assert order.order_id not in result.state.oms.active_orders
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert len(_released_events(result.state)) == 1


# ---------------------------------------------------------------------------
# Exactly once, structurally
# ---------------------------------------------------------------------------


def test_releasing_the_same_reservation_twice_raises() -> None:
    """A double release cannot silently double-subtract from held capital."""

    asset_id = str(uuid4())
    state = _pipeline(
        {2.0: Decimal("10")},
        asset_id,
        risk_limits=permissive_risk_limits(max_order_quantity=Decimal("1")),
    )
    result = ExecutionPipeline.process_quote(state, quote(asset_id, 2.0, MID), context_factory)
    order_id = result.order_requests[0].order_id

    with pytest.raises(UnknownReservationError):
        AllocationEngine.release_reservation(result.state.allocation, order_id, 3.0)


def test_held_capital_always_equals_the_sum_of_live_reservations() -> None:
    """The invariant that makes ``notional_allocated`` trustworthy."""

    asset_id = str(uuid4())
    plan = {2.0: Decimal("10"), 3.0: Decimal("-4"), 5.0: Decimal("6")}
    state = _pipeline(plan, asset_id)

    for index in range(6):
        timestamp = 2.0 + index
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, timestamp, MID + Decimal(index)), context_factory
        ).state
        live = sum(open_reservations(state.allocation).values(), Decimal("0.00"))
        assert state.allocation.notional_allocated == live
