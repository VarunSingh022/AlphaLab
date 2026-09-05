"""Regression guard for the O(N^2) OMS order book fixed in v2.2.

v2.1 made engine *histories* O(1) amortized to append, which left the execution
path's remaining super-linear term exposed: the OMS order book. ``OrderBook.add``
rebuilt the whole order ``dict`` and both index ``frozenset`` s, ``OrderBook.replace``
rebuilt the order ``dict``, and ``OMSEngine._update_sets`` rebuilt both order-id
``frozenset`` s -- once per stored order, and the OMS stores an order on submit
and again on every lifecycle transition. Submitting N orders therefore copied
O(N^2) entries: measured on the development machine, 16k orders took 23.9s and
doubling the workload cost ~4.4x the time. ``benchmarks/benchmark_oms.py``'s
100k workload extrapolated to roughly 15 minutes.

:class:`~alphalab.common.persistent_map.PersistentMap` and
:class:`~alphalab.common.persistent_map.PersistentSet` share structure instead
of copying, so those updates are O(1) amortized.

The structural tests below are deterministic: they assert the book does not copy
its backing store, which is the property the fix rests on, *and* that it still
behaves as a value. The timing test is a coarse backstop with a wide tolerance
-- it is there to catch a return to quadratic behaviour (~4x per doubling), not
to police constant factors.
"""

import gc
import time
from decimal import Decimal

from alphalab.common.persistent_map import PersistentMap, PersistentSet
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.oms.book import OrderBook
from alphalab.oms.engine import OMSEngine
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.state import OMSState

# Ratio of the two workload sizes used by the timing test.
SCALE = 4
SMALL = 1_000
LARGE = SMALL * SCALE
# Linear growth predicts ~4x. Quadratic growth predicts ~16x. Anything under 8x
# (2x the linear prediction) is comfortably not quadratic.
MAX_GROWTH = 8.0
# 4k submit+accept+fill sequences took ~1.24s before the fix and ~0.15s after,
# on the machine this was developed on. A 10s ceiling survives a slow CI runner
# while still failing a reintroduced quadratic.
LARGE_WORKLOAD_BUDGET_SECONDS = 10.0


def _order(order_id: OrderId, asset: str = "AAPL", strategy: str = "BENCH") -> Order:
    return Order(
        order_id=order_id,
        strategy_id=strategy,
        asset_id=asset,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        status=OrderStatus.NEW,
        quantity=Decimal("100.0"),
        filled_quantity=Decimal("0.0"),
        remaining_quantity=Decimal("100.0"),
        limit_price=Decimal("150.0"),
        stop_price=None,
        average_fill_price=Decimal("0.0"),
        created_at=0.0,
        updated_at=0.0,
    )


def _time_order_lifecycles(count: int) -> float:
    """Time one submit/accept/fill run, with the cyclic collector paused.

    Orders, events and states are all container objects, so a run keeps a large
    live heap. Left on, the collector's walks over that heap dominate the
    timing and the growth ratio stops measuring the data structure at all --
    it has been observed both well below and well above linear on the same
    build. Pausing it is what makes this backstop mean something.
    """

    orders = [_order(OrderId.generate()) for _ in range(count)]
    state = OMSState()
    gc.disable()
    try:
        start = time.perf_counter()
        for order in orders:
            state = OMSEngine.submit(state, order, 0.0)
        for order in orders:
            state = OMSEngine.accept(state, order.order_id, 1.0)
            state = OMSEngine.fill(state, order.order_id, Decimal("100.0"), Decimal("150.0"), 2.0)
        return time.perf_counter() - start
    finally:
        gc.enable()


# ---------------------------------------------------------------------------
# Structural guarantees (deterministic)
# ---------------------------------------------------------------------------


def test_the_order_book_uses_persistent_containers() -> None:
    book = OrderBook()

    assert isinstance(book._orders, PersistentMap)
    assert isinstance(book._by_asset, PersistentMap)
    assert isinstance(book._by_strategy, PersistentMap)


def test_the_active_and_completed_sets_are_persistent() -> None:
    state = OMSState()

    assert isinstance(state.active_orders, PersistentSet)
    assert isinstance(state.completed_orders, PersistentSet)


def test_submitting_orders_does_not_copy_the_order_index() -> None:
    """Each submit writes in place; the backing store is never rebuilt."""

    state = OMSState()
    stores = set()
    for _ in range(200):
        order = _order(OrderId.generate())
        state = OMSEngine.submit(state, order, 0.0)
        stores.add(id(state.orders._orders._store))
        stores.add(id(state.active_orders._members._store))

    # One store for the order index, one for the active-order set, across all
    # 200 submissions.
    assert len(stores) == 2
    assert len(state.orders.orders()) == 200


def test_lifecycle_transitions_do_not_copy_either_index() -> None:
    orders = [_order(OrderId.generate()) for _ in range(100)]
    state = OMSState()
    for order in orders:
        state = OMSEngine.submit(state, order, 0.0)

    stores = set()
    for order in orders:
        state = OMSEngine.accept(state, order.order_id, 1.0)
        state = OMSEngine.fill(state, order.order_id, Decimal("100.0"), Decimal("150.0"), 2.0)
        stores.add(id(state.orders._orders._store))
        stores.add(id(state.active_orders._members._store))
        stores.add(id(state.completed_orders._members._store))

    assert len(stores) == 3
    assert len(state.completed_orders) == 100
    assert len(state.active_orders) == 0


def test_the_asset_index_is_not_rebuilt_per_order() -> None:
    """A single hot asset was the worst case: one frozenset copy per add."""

    state = OMSState()
    stores = set()
    for _ in range(200):
        state = OMSEngine.submit(state, _order(OrderId.generate()), 0.0)
        stores.add(id(state.orders._by_asset._store))

    assert len(stores) == 1
    assert len(state.orders.orders_for_asset("AAPL")) == 200


# ---------------------------------------------------------------------------
# The book is still a value: speed must not have cost history
# ---------------------------------------------------------------------------


def test_an_older_book_still_observes_its_own_contents() -> None:
    """Structural sharing must not let a later write reach an earlier state."""

    first = _order(OrderId.generate())
    second = _order(OrderId.generate())

    empty = OMSState()
    one = OMSEngine.submit(empty, first, 0.0)
    two = OMSEngine.submit(one, second, 0.0)

    assert len(empty.orders.orders()) == 0
    assert [o.order_id for o in one.orders.orders()] == [first.order_id]
    assert [o.order_id for o in two.orders.orders()] == [first.order_id, second.order_id]
    assert not one.orders.contains(second.order_id)


def test_an_older_state_keeps_the_order_status_it_had() -> None:
    order = _order(OrderId.generate())
    submitted = OMSEngine.submit(OMSState(), order, 0.0)
    accepted = OMSEngine.accept(submitted, order.order_id, 1.0)
    filled = OMSEngine.fill(accepted, order.order_id, Decimal("100.0"), Decimal("150.0"), 2.0)

    assert submitted.orders.find(order.order_id).status is OrderStatus.NEW
    assert accepted.orders.find(order.order_id).status is OrderStatus.ACCEPTED
    assert filled.orders.find(order.order_id).status is OrderStatus.FILLED
    assert order.order_id in accepted.active_orders
    assert order.order_id not in accepted.completed_orders
    assert order.order_id in filled.completed_orders


def test_branching_two_lifecycles_from_one_state_keeps_both_correct() -> None:
    order = _order(OrderId.generate())
    accepted = OMSEngine.accept(OMSEngine.submit(OMSState(), order, 0.0), order.order_id, 1.0)

    filled = OMSEngine.fill(accepted, order.order_id, Decimal("100.0"), Decimal("150.0"), 2.0)
    cancelled = OMSEngine.cancel(accepted, order.order_id, 2.0)

    assert filled.orders.find(order.order_id).status is OrderStatus.FILLED
    assert cancelled.orders.find(order.order_id).status is OrderStatus.CANCELLED
    assert accepted.orders.find(order.order_id).status is OrderStatus.ACCEPTED


def test_removing_an_order_leaves_the_earlier_book_intact() -> None:
    order = _order(OrderId.generate())
    with_order = OrderBook().add(order)
    without = with_order.remove(order.order_id)

    assert with_order.contains(order.order_id)
    assert not without.contains(order.order_id)
    assert with_order.orders_for_asset("AAPL") == (order,)
    assert without.orders_for_asset("AAPL") == ()


def test_no_orders_are_dropped_across_a_full_workload() -> None:
    """The fix must not silently lose orders to go faster."""

    orders = [_order(OrderId.generate(), asset=f"A{i % 7}") for i in range(500)]
    state = OMSState()
    for order in orders:
        state = OMSEngine.submit(state, order, 0.0)
    for order in orders[:250]:
        state = OMSEngine.accept(state, order.order_id, 1.0)
        state = OMSEngine.fill(state, order.order_id, Decimal("100.0"), Decimal("150.0"), 2.0)

    assert len(state.orders.orders()) == 500
    assert len(state.active_orders) == 250
    assert len(state.completed_orders) == 250
    assert sum(len(state.orders.orders_for_asset(f"A{i}")) for i in range(7)) == 500
    assert len(state.history) == 500 + 250 * 2


def test_orders_are_returned_in_submission_order() -> None:
    """Deterministic ordering, which frozenset iteration never guaranteed."""

    orders = [_order(OrderId.generate()) for _ in range(20)]
    state = OMSState()
    for order in orders:
        state = OMSEngine.submit(state, order, 0.0)

    assert [o.order_id for o in state.orders.orders()] == [o.order_id for o in orders]
    assert list(state.active_orders) == [o.order_id for o in orders]


# ---------------------------------------------------------------------------
# Timing backstop (coarse)
# ---------------------------------------------------------------------------


def test_order_lifecycle_cost_grows_linearly_with_the_workload() -> None:
    # Warm up so import-time and first-call costs do not skew the small sample.
    _time_order_lifecycles(200)

    small = min(_time_order_lifecycles(SMALL) for _ in range(2))
    large = min(_time_order_lifecycles(LARGE) for _ in range(2))

    assert large < LARGE_WORKLOAD_BUDGET_SECONDS, f"{LARGE} order lifecycles took {large:.2f}s"
    growth = large / max(small, 1e-6)
    assert growth < MAX_GROWTH, (
        f"{SCALE}x the workload cost {growth:.1f}x the time "
        f"({small:.3f}s -> {large:.3f}s); the order book looks quadratic again"
    )
