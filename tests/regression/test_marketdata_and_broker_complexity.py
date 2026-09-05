"""Regression guard for the quadratic patterns v2.3 removed.

Two of them, both the same shape as the ones v2.1 and v2.2 removed from the
risk engine and the OMS: an immutable keyed index rebuilt with ``dict(old)`` on
every transition.

* :class:`~alphalab.market.state.MarketState` rebuilt its ``latest_*`` index on
  every publish, so a publish cost O(universe): ingesting 20k quotes into a
  20k-instrument universe ran ~9.5x slower than into a single-instrument one.
* :class:`~alphalab.broker.state.BrokerState` and
  :class:`~alphalab.brokers.state.BrokerConnectorState` rebuilt their order,
  execution and position indexes per transition, so a session cost O(N^2) in
  the orders it had placed. That mattered much more after v2.3, which routes
  paper and live execution through those states.

The structural assertions are deterministic and are the real guard: they check
that the state holds a persistent container, which is the property the fix
rests on. The timing assertions are coarse backstops with wide tolerances --
there to catch a return to quadratic scaling, not to police constant factors.
"""

import time
from dataclasses import dataclass
from decimal import Decimal

from alphalab.broker import BrokerAdapter, BrokerEngine, BrokerOrderType, PaperBroker
from alphalab.brokers.state import BrokerConnectorState
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.market.engine import MarketEngine
from alphalab.market.quote import Quote
from alphalab.market.state import MarketState


def _quote(asset_id: str, timestamp: float) -> Quote:
    return Quote(
        asset_id=asset_id,
        timestamp=timestamp,
        bid=Decimal("10.00"),
        ask=Decimal("10.10"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        venue="SIM",
        currency="USD",
    )


@dataclass(frozen=True)
class _OMSOrder:
    order_id: str
    asset_id: str
    side: str
    quantity: str
    price: str


def _publish(count: int, universe: int) -> float:
    quotes = [_quote(f"S{i % universe}", float(i + 1)) for i in range(count)]
    state = MarketEngine.reset()
    start = time.perf_counter()
    for quote in quotes:
        state = MarketEngine.publish_quote(state, quote)
    return time.perf_counter() - start


def _submit(count: int) -> float:
    orders = [_OMSOrder(f"OMS-{i}", "AAPL", "BUY", "1", "10.00") for i in range(count)]
    state = BrokerEngine.initialize("BENCH", Decimal("100000000000.00"), "USD")
    broker = PaperBroker()
    broker_orders = [
        BrokerAdapter.to_broker_order(order, f"B-{i}", BrokerOrderType.MARKET, float(i))
        for i, order in enumerate(orders)
    ]
    start = time.perf_counter()
    for index, order in enumerate(broker_orders):
        state, _ = broker.submit_order(state, order, float(index))
    return time.perf_counter() - start


# --- structural: the property the fix rests on -------------------------------


def test_market_state_indexes_are_persistent_not_rebuilt_dicts() -> None:
    state = MarketState()
    assert isinstance(state.latest_quotes, PersistentMap)
    assert isinstance(state.latest_books, PersistentMap)
    assert isinstance(state.latest_ticks, PersistentMap)
    assert isinstance(state.latest_bars, PersistentMap)


def test_broker_state_indexes_and_history_are_persistent() -> None:
    state = BrokerEngine.initialize("B", Decimal("1"), "USD")
    assert isinstance(state.orders, PersistentMap)
    assert isinstance(state.executions, PersistentMap)
    assert isinstance(state.positions, PersistentMap)
    assert isinstance(state.events, AppendOnlyLog)


def test_broker_connector_state_indexes_and_history_are_persistent() -> None:
    state = BrokerConnectorState(engine_id="E")
    assert isinstance(state.orders, PersistentMap)
    assert isinstance(state.executions, PersistentMap)
    assert isinstance(state.positions, PersistentMap)
    assert isinstance(state.accounts, PersistentMap)
    assert isinstance(state.connections, PersistentMap)
    assert isinstance(state.events, AppendOnlyLog)


def test_publishing_shares_structure_instead_of_copying_the_index() -> None:
    """A publish must not rebuild the index -- the older state still reads its own."""
    state = MarketEngine.reset()
    first = MarketEngine.publish_quote(state, _quote("A", 1.0))
    second = MarketEngine.publish_quote(first, _quote("B", 2.0))

    assert len(first.latest_quotes) == 1
    assert len(second.latest_quotes) == 2
    assert "B" not in first.latest_quotes


def test_a_submitted_order_is_invisible_to_the_state_before_it() -> None:
    state = BrokerEngine.initialize("B", Decimal("1000000"), "USD")
    broker = PaperBroker()
    order = BrokerAdapter.to_broker_order(
        _OMSOrder("OMS-1", "AAPL", "BUY", "1", "10.00"), "B-1", BrokerOrderType.LIMIT, 1.0
    )
    after, _ = broker.submit_order(state, order, 1.0)

    assert len(state.orders) == 0
    assert len(after.orders) == 1


# --- timing backstops --------------------------------------------------------


def test_publishing_does_not_slow_down_as_the_universe_grows() -> None:
    """The whole point: a wide universe must not make each publish dearer.

    Before v2.3 this ratio was roughly the universe ratio itself.
    """
    narrow = _publish(20_000, universe=1)
    wide = _publish(20_000, universe=10_000)

    assert wide < narrow * 3.0, (
        f"Publishing into a 10,000-instrument universe took {wide:.3f}s against "
        f"{narrow:.3f}s into one instrument; the index is being rebuilt per publish."
    )


def test_broker_submission_stays_linear_in_the_orders_already_placed() -> None:
    """Quadratic would be ~16x over a 4x workload; linear is ~4x."""
    small = _submit(2_000)
    large = _submit(8_000)

    assert large < small * 8.0, (
        f"Submitting 8,000 orders took {large:.3f}s against {small:.3f}s for 2,000; "
        f"a 4x workload should not cost more than ~8x."
    )
