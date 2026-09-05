"""Integration test proving the broker layer speaks the canonical core domain vocabulary.

This guards the Phase 3-5 unification (commits 4119224, 9cf3b86, bb536bd): before that
work, `alphalab.broker` and `alphalab.brokers` each defined their own independent
Side/OrderType/OrderStatus enums with different member sets. If either package
reintroduces a parallel enum, the identity assertions below fail immediately, even
though the reintroduced enum could have identical member names and values -- Python
enums are never `is`-equal across distinct classes, and equality is also
identity-based by default, so `==` would fail too.

This test deliberately does not touch alphalab.runtime.execution_pipeline, which is
already covered by test_execution_pipeline.py and does not route through
alphalab.broker at all -- this is the one integration path that does.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.broker import (
    BrokerEngine,
    BrokerOrderStatus,
    PaperBroker,
)
from alphalab.broker.adapter import BrokerAdapter
from alphalab.core.enums import OrderStatus, OrderType, Side, TimeInForce


@dataclass(frozen=True)
class _OMSOrderStub:
    """Minimal stand-in satisfying broker.adapter.OMSOrderProtocol.

    Mirrors the shape of a real alphalab.oms.order.Order well enough to exercise the
    adapter boundary without importing the full OMS package, consistent with
    OMSOrderProtocol's stated purpose of decoupling the broker layer from a strict
    OMS import.
    """

    order_id: str
    asset_id: str
    side: str
    quantity: str
    price: str


def test_canonical_order_status_propagates_from_broker_adapter_through_paper_broker() -> None:
    """Order enums stay canonical from adapter construction through a live fill."""
    oms_order = _OMSOrderStub(
        order_id="OMS-DOMAIN-CHECK-1",
        asset_id="AAPL",
        side="BUY",
        quantity="100",
        price="150.00",
    )

    # Step 1: OMS order crosses into the broker layer via the real adapter, using a
    # canonical OrderType at the call site -- exactly as the existing
    # tests/unit/broker/test_broker.py::test_adapter_conversion exercises it.
    broker_order = BrokerAdapter.to_broker_order(
        oms_order, "BROKER-DOMAIN-CHECK-1", OrderType.MARKET, timestamp=1000.0
    )

    # The adapter must have produced canonical enum members, not broker-local ones.
    assert broker_order.side is Side.BUY
    assert type(broker_order.side) is Side
    assert broker_order.order_type is OrderType.MARKET
    assert type(broker_order.order_type) is OrderType

    # Before submission, status is a broker-local staging status -- this is the one
    # value that is legitimately NOT canonical, by design (see broker/order.py's
    # docstring: broker-local operational states must remain subsystem-specific).
    assert broker_order.status is BrokerOrderStatus.PENDING_SUBMIT
    assert type(broker_order.status) is BrokerOrderStatus

    # Step 2: submit the order through the real PaperBroker state machine.
    state = BrokerEngine.initialize("PAPER-DOMAIN-CHECK", Decimal("100000.00"), "USD")
    broker = PaperBroker()
    connected_state, _ = broker.connect(state, timestamp=1000.0)
    filled_state, events = broker.submit_order(connected_state, broker_order, timestamp=1000.0)

    # Step 3: once accepted/filled, status must be the canonical core OrderStatus --
    # this is the assertion that would fail if broker/ ever reintroduces its own
    # ACCEPTED/FILLED-equivalent status instead of routing lifecycle transitions
    # through alphalab.core.enums.OrderStatus.
    final_order = filled_state.orders[broker_order.broker_order_id]
    assert final_order.status is OrderStatus.FILLED
    assert type(final_order.status) is OrderStatus

    # Sanity: the paper broker actually did something, not a no-op returning the
    # input unchanged.
    assert final_order.filled_quantity == Decimal("100")
    assert len(events) >= 2


def test_brokers_registry_order_also_uses_canonical_side_and_type() -> None:
    """The separate multi-broker `brokers` package agrees with `broker` on vocabulary.

    Before Phase 4, `alphalab.brokers.order.OrderType/OrderStatus/OrderSide` were an
    independent enum family from `alphalab.broker`'s. Both packages then converged on
    the exact same alphalab.core.enums members, not just similarly-named ones.

    v2.3 goes one step further: `brokers` no longer defines its own order *type*
    either. The assertion below is that the class itself is the canonical one --
    two dataclasses with identical fields would still be different types, so this
    checks identity, not shape.
    """
    from alphalab.broker.order import BrokerOrder as CanonicalBrokerOrder
    from alphalab.brokers.order import BrokerOrder as RegistryBrokerOrder

    assert RegistryBrokerOrder is CanonicalBrokerOrder

    order = RegistryBrokerOrder(
        broker_order_id="REG-ORDER-1",
        oms_order_id="OMS-ORDER-1",
        symbol="AAPL",
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        quantity=Decimal("50"),
        price=Decimal("151.00"),
        filled_quantity=Decimal("0"),
        average_fill_price=Decimal("0"),
        status=OrderStatus.NEW,
        created_at=1000.0,
        updated_at=1000.0,
        account_id="ACC-1",
        tif=TimeInForce.DAY,
        stop_price=Decimal("0"),
    )

    # Cross-package identity check: the Side value constructed here (from core.enums,
    # imported independently at the top of this test file) is the exact same object
    # the `brokers` package's own BrokerOrder stores -- not a look-alike from a
    # locally redefined enum.
    assert order.side is Side.SELL
    assert order.order_type is OrderType.LIMIT
    assert order.status is OrderStatus.NEW

    # The two identities an order carries stay distinct, which is what makes
    # reconciliation possible at all.
    assert order.broker_order_id != order.oms_order_id
