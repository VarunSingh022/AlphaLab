"""Unit tests for the allocation reservation ledger.

``notional_allocated`` used to be a bare running total: anything could subtract
from it, nothing could say which order the capital belonged to, and a release
that never happened was indistinguishable from one that happened twice. The
ledger makes reservations attributable, which is what "released exactly once"
needs in order to be checkable at all.
"""

from decimal import Decimal

import pytest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.events import AllocationReservationReleased
from alphalab.allocation.exceptions import UnknownReservationError
from alphalab.allocation.sizing import FixedQuantitySizing
from alphalab.allocation.state import AllocationState
from alphalab.allocation.views import open_reservations, reserved_for_order
from alphalab.core.order_request import OrderRequest
from alphalab.strategy.events import Intent

PRICES = {"AAPL": Decimal("100.00")}


def _state() -> AllocationState:
    return AllocationEngine.initialize(
        CapitalBudget(Decimal("1000000"), Decimal("1000000"), Decimal("0"))
    )


def _allocate(quantity: str = "10") -> tuple[AllocationState, OrderRequest]:
    intents = (Intent(strategy_id="S", instrument="AAPL", target=Decimal(quantity), timestamp=1.0),)
    state, requests = AllocationEngine.allocate(
        _state(), intents, PRICES, FixedQuantitySizing(), AllocationConstraints(), 1.0
    )
    return state, requests[0]


def test_allocation_reserves_the_requests_notional_by_order_id() -> None:
    state, request = _allocate()

    assert reserved_for_order(state, request.order_id) == Decimal("1000.00")
    assert state.notional_allocated == Decimal("1000.00")
    assert dict(open_reservations(state)) == {request.order_id: Decimal("1000.00")}


def test_release_frees_exactly_what_was_reserved() -> None:
    state, request = _allocate()

    released = AllocationEngine.release_reservation(state, request.order_id, 2.0)

    assert released.notional_allocated == Decimal("0.00")
    assert reserved_for_order(released, request.order_id) == Decimal("0.00")
    assert dict(open_reservations(released)) == {}


def test_release_records_the_amount_it_freed() -> None:
    state, request = _allocate()

    released = AllocationEngine.release_reservation(state, request.order_id, 2.0)
    events = [e for e in released.events if isinstance(e, AllocationReservationReleased)]

    assert len(events) == 1
    assert events[0].order_id == request.order_id
    assert events[0].released_notional == Decimal("1000.00")


def test_releasing_twice_raises_rather_than_double_subtracting() -> None:
    """The structural guarantee behind 'released exactly once'."""

    state, request = _allocate()
    released = AllocationEngine.release_reservation(state, request.order_id, 2.0)

    with pytest.raises(UnknownReservationError, match="no allocation reservation"):
        AllocationEngine.release_reservation(released, request.order_id, 3.0)


def test_releasing_an_unknown_order_raises() -> None:
    state, _ = _allocate()

    with pytest.raises(UnknownReservationError):
        AllocationEngine.release_reservation(state, "never-allocated", 2.0)


def test_full_execution_consumes_the_whole_reservation() -> None:
    state, request = _allocate()

    executed = AllocationEngine.apply_execution(state, request.order_id, Decimal("1000.00"), 2.0)

    assert executed.notional_allocated == Decimal("0.00")
    assert dict(open_reservations(executed)) == {}


def test_partial_execution_leaves_the_residual_reserved() -> None:
    """The order is still working, so its remaining capital is still committed."""

    state, request = _allocate()

    executed = AllocationEngine.apply_execution(state, request.order_id, Decimal("400.00"), 2.0)

    assert reserved_for_order(executed, request.order_id) == Decimal("600.00")
    assert executed.notional_allocated == Decimal("600.00")


def test_execution_cannot_free_more_capital_than_was_reserved() -> None:
    state, request = _allocate()

    executed = AllocationEngine.apply_execution(state, request.order_id, Decimal("9999.00"), 2.0)

    assert executed.notional_allocated == Decimal("0.00")
    assert dict(open_reservations(executed)) == {}


def test_release_after_a_partial_execution_frees_only_the_residual() -> None:
    state, request = _allocate()
    executed = AllocationEngine.apply_execution(state, request.order_id, Decimal("400.00"), 2.0)

    released = AllocationEngine.release_reservation(executed, request.order_id, 3.0)
    events = [e for e in released.events if isinstance(e, AllocationReservationReleased)]

    assert events[-1].released_notional == Decimal("600.00")
    assert released.notional_allocated == Decimal("0.00")


def test_execution_against_a_released_order_is_recorded_but_frees_nothing() -> None:
    state, request = _allocate()
    released = AllocationEngine.release_reservation(state, request.order_id, 2.0)

    executed = AllocationEngine.apply_execution(released, request.order_id, Decimal("500.00"), 3.0)

    assert executed.notional_allocated == Decimal("0.00")
    assert len(executed.events) == len(released.events) + 1


def test_reservations_are_per_order_not_pooled() -> None:
    intents = (
        Intent(strategy_id="S", instrument="AAPL", target=Decimal("10"), timestamp=1.0),
        Intent(strategy_id="S", instrument="MSFT", target=Decimal("5"), timestamp=1.0),
    )
    prices = {"AAPL": Decimal("100.00"), "MSFT": Decimal("20.00")}
    state, requests = AllocationEngine.allocate(
        _state(), intents, prices, FixedQuantitySizing(), AllocationConstraints(), 1.0
    )
    first, second = requests

    released = AllocationEngine.release_reservation(state, first.order_id, 2.0)

    assert reserved_for_order(released, first.order_id) == Decimal("0.00")
    assert reserved_for_order(released, second.order_id) == Decimal("100.00")
    assert released.notional_allocated == Decimal("100.00")


def test_a_batch_rejected_by_the_budget_reserves_nothing() -> None:
    tiny = AllocationEngine.initialize(CapitalBudget(Decimal("10"), Decimal("10"), Decimal("0")))
    intents = (Intent(strategy_id="S", instrument="AAPL", target=Decimal("10"), timestamp=1.0),)

    state, requests = AllocationEngine.allocate(
        tiny, intents, PRICES, FixedQuantitySizing(), AllocationConstraints(), 1.0
    )

    assert requests == ()
    assert state.notional_allocated == Decimal("0.00")
    assert dict(open_reservations(state)) == {}
