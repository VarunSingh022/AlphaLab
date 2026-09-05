"""The failure modes a broker connection actually has, and their defined answers.

Each test here corresponds to something a real venue does: redeliver a fill after
a reconnect, report fills out of order, cross a cancel with a fill, or report an
order this process never sent. None of these are exceptional, so each has a
deterministic answer rather than an exception at the point of surprise.
"""

from decimal import Decimal

import pytest

from alphalab.broker import (
    BrokerAdapter,
    BrokerEngine,
    BrokerOrderType,
    BrokerState,
    ExecutionOutcome,
    InvalidBrokerStateError,
    PaperBroker,
    ReconciliationLog,
    apply_execution,
    reconcile,
)
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.core.enums import OrderStatus, OrderType, Side


class _OMSOrder:
    """Minimal stand-in satisfying broker.adapter.OMSOrderProtocol."""

    def __init__(self, order_id: str, quantity: str = "100") -> None:
        self.order_id = order_id
        self.asset_id = "AAPL"
        self.side = "BUY"
        self.quantity = quantity
        self.price = "150.00"


def _working_state(quantity: str = "100") -> tuple[BrokerState, PaperBroker, BrokerOrder]:
    """A connected paper venue holding one accepted limit order."""
    state = BrokerEngine.initialize("PAPER", Decimal("1000000.00"), "USD")
    broker = PaperBroker()
    state, _ = broker.connect(state, 1000.0)
    order = BrokerAdapter.to_broker_order(
        _OMSOrder("OMS-1", quantity), "B-1", BrokerOrderType.LIMIT, 1000.0
    )
    state, _ = broker.submit_order(state, order, 1000.0)
    return state, broker, state.orders["B-1"]


def _fill(execution_id: str, quantity: str, price: str, timestamp: float) -> BrokerExecution:
    return BrokerExecution(
        execution_id=execution_id,
        broker_order_id="B-1",
        symbol="AAPL",
        fill_quantity=Decimal(quantity),
        fill_price=Decimal(price),
        commission=Decimal("0"),
        timestamp=timestamp,
        external_id=f"VENUE-{execution_id}",
    )


# --- duplicate delivery ------------------------------------------------------


def test_a_redelivered_fill_does_not_double_count() -> None:
    """The reconnect case: the same fill arrives twice and must count once."""
    state, broker, _ = _working_state()

    state, events = broker.apply_execution(state, _fill("E-1", "40", "150", 1001.0), 1001.0)
    assert state.orders["B-1"].filled_quantity == Decimal("40")
    assert len(events) == 1

    state, events = broker.apply_execution(state, _fill("E-1", "40", "150", 1001.0), 1002.0)
    assert state.orders["B-1"].filled_quantity == Decimal("40")
    assert events == ()


def test_redelivery_is_recorded_but_is_not_a_break() -> None:
    state, _, _ = _working_state()
    state, _, log = apply_execution(state, _fill("E-1", "40", "150", 1001.0))
    _, _, log = apply_execution(state, _fill("E-1", "40", "150", 1001.0), log)

    assert len(log.duplicates) == 1
    assert log.breaks == ()


# --- out-of-order delivery ---------------------------------------------------


def test_two_fills_produce_the_same_result_in_either_order() -> None:
    """Fills are additive, so out-of-order delivery needs no special handling."""
    early = _fill("E-1", "40", "100", 1001.0)
    late = _fill("E-2", "60", "200", 1002.0)

    forward, _, _ = apply_execution(_working_state()[0], early)
    forward, _, _ = apply_execution(forward, late)

    backward, _, _ = apply_execution(_working_state()[0], late)
    backward, _, _ = apply_execution(backward, early)

    assert forward.orders["B-1"].filled_quantity == backward.orders["B-1"].filled_quantity
    assert forward.orders["B-1"].average_fill_price == backward.orders["B-1"].average_fill_price
    assert forward.orders["B-1"].status == backward.orders["B-1"].status
    assert set(forward.executions) == set(backward.executions)


def test_an_out_of_order_fill_never_moves_the_order_timestamp_backwards() -> None:
    state, _, _ = apply_execution(_working_state()[0], _fill("E-1", "40", "150", 2000.0))
    state, _, _ = apply_execution(state, _fill("E-2", "20", "150", 1500.0))

    assert state.orders["B-1"].updated_at == 2000.0


# --- the cancel/fill race ----------------------------------------------------


def test_fill_then_cancel_leaves_the_order_filled_and_refuses_the_cancel() -> None:
    state, broker, _ = _working_state()
    state, _ = broker.apply_execution(state, _fill("E-1", "100", "150", 1001.0), 1001.0)

    assert state.orders["B-1"].status is OrderStatus.FILLED
    with pytest.raises(InvalidBrokerStateError, match="FILLED"):
        broker.cancel_order(state, "B-1", 1002.0)


def test_cancel_then_fill_surfaces_the_fill_instead_of_applying_or_dropping_it() -> None:
    """Applying it would resurrect a terminal order; dropping it would hide a position."""
    state, broker, _ = _working_state()
    state, _ = broker.cancel_order(state, "B-1", 1001.0)
    assert state.orders["B-1"].status is OrderStatus.CANCELLED

    after, decision, log = apply_execution(state, _fill("E-1", "100", "150", 1002.0))

    assert after is state
    assert decision.outcome is ExecutionOutcome.TERMINAL_ORDER
    assert len(log.breaks) == 1
    assert log.breaks[0].execution.execution_id == "E-1"


def test_both_race_orders_end_deterministically() -> None:
    """Whichever wins, the order is terminal and the position is never doubled."""
    filled_first, broker, _ = _working_state()
    filled_first, _ = broker.apply_execution(
        filled_first, _fill("E-1", "100", "150", 1001.0), 1001.0
    )

    cancelled_first, _, _ = _working_state()
    cancelled_first, _ = broker.cancel_order(cancelled_first, "B-1", 1001.0)
    cancelled_first, _ = broker.apply_execution(
        cancelled_first, _fill("E-1", "100", "150", 1002.0), 1002.0
    )

    assert filled_first.orders["B-1"].is_terminal
    assert cancelled_first.orders["B-1"].is_terminal
    assert filled_first.orders["B-1"].filled_quantity == Decimal("100")
    assert cancelled_first.orders["B-1"].filled_quantity == Decimal("0")


# --- unknown and overfilling reports -----------------------------------------


def test_a_fill_for_an_order_this_session_never_sent_is_never_applied() -> None:
    state, broker, _ = _working_state()
    orphan = BrokerExecution(
        "E-9", "B-OTHER", "AAPL", Decimal("10"), Decimal("150"), Decimal("0"), 1001.0
    )

    after, events = broker.apply_execution(state, orphan, 1001.0)

    assert after is state
    assert events == ()


def test_an_overfilling_report_is_refused_not_truncated() -> None:
    state, _, _ = _working_state(quantity="100")
    state, _, _ = apply_execution(state, _fill("E-1", "80", "150", 1001.0))

    after, decision, _ = apply_execution(state, _fill("E-2", "40", "150", 1002.0))

    assert after is state
    assert decision.outcome is ExecutionOutcome.OVERFILL
    assert state.orders["B-1"].filled_quantity == Decimal("80")


# --- external identity -------------------------------------------------------


def test_the_venue_execution_id_survives_application() -> None:
    """A fill must stay traceable to the venue's own record of it."""
    state, _, _ = apply_execution(_working_state()[0], _fill("E-1", "40", "150", 1001.0))
    assert state.executions["E-1"].external_id == "VENUE-E-1"


def test_the_adapter_keeps_the_oms_and_broker_identities_distinct() -> None:
    order = BrokerAdapter.to_broker_order(_OMSOrder("OMS-7"), "B-7", BrokerOrderType.MARKET, 1.0)
    assert order.oms_order_id == "OMS-7"
    assert order.broker_order_id == "B-7"


# --- reconciliation ----------------------------------------------------------


def test_a_venue_that_agrees_reconciles_clean() -> None:
    state, broker, _ = _working_state()
    report = reconcile(
        state, list(state.orders.values()), broker.positions(state), broker.account(state)
    )
    assert report.reconciled


def test_a_fill_lost_in_flight_is_detected_by_reconciling() -> None:
    """AlphaLab thinks nothing filled; the venue says 40 did."""
    from dataclasses import replace

    state, _, _ = _working_state()
    at_venue = replace(
        state.orders["B-1"],
        filled_quantity=Decimal("40"),
        average_fill_price=Decimal("150"),
        status=OrderStatus.PARTIALLY_FILLED,
    )

    report = reconcile(state, [at_venue])

    assert not report.reconciled
    assert len(report.divergent_orders) == 1
    assert report.divergent_orders[0].broker_order_id == "B-1"


def test_reconciling_states_differences_without_resolving_them() -> None:
    """No safe default exists: re-sending an order that exists would duplicate it."""
    state, _, _ = _working_state()
    report = reconcile(state, [])

    assert len(report.missing_at_broker) == 1
    assert report.missing_at_broker[0].local is not None
    assert report.missing_at_broker[0].remote is None


def test_an_empty_log_and_a_clean_report_are_the_only_proof_of_agreement() -> None:
    state, _broker, _ = _working_state()
    state, _, log = apply_execution(state, _fill("E-1", "100", "150", 1001.0), ReconciliationLog())

    assert log.breaks == () and log.duplicates == ()
    assert reconcile(state, list(state.orders.values())).reconciled


# --- the canonical vocabulary ------------------------------------------------


def test_a_paper_broker_satisfies_the_canonical_contract() -> None:
    from alphalab.broker.protocol import BrokerProtocol

    assert isinstance(PaperBroker(), BrokerProtocol)


def test_both_broker_packages_route_one_order_type() -> None:
    """`broker` defines the vocabulary; `brokers` routes it."""
    from alphalab.broker.order import BrokerOrder as Canonical
    from alphalab.brokers import BrokerOrder as Routed
    from alphalab.brokers import ExecutionReport, PositionSnapshot

    assert Routed is Canonical
    assert ExecutionReport is BrokerExecution
    assert PositionSnapshot.__module__ == "alphalab.broker.position"


def test_the_router_and_the_adapter_agree_on_account_and_asset_vocabulary() -> None:
    from alphalab.broker.account import BrokerAccount
    from alphalab.brokers import AccountSnapshot, AssetClass
    from alphalab.core.enums import AssetType

    assert AccountSnapshot is BrokerAccount
    assert AssetClass is AssetType


def test_broker_local_statuses_are_one_set_shared_by_both_packages() -> None:
    from alphalab.broker.order import BrokerOrderStatus
    from alphalab.brokers import OrderStatus as ConnectorStatus

    assert ConnectorStatus is BrokerOrderStatus
    assert {"PENDING_SUBMIT", "SUBMITTED", "PENDING_CANCEL"} <= set(BrokerOrderStatus.__members__)
    # And none of them leaked into the canonical lifecycle.
    assert not set(BrokerOrderStatus.__members__) & set(OrderStatus.__members__)


def test_the_canonical_order_type_and_side_remain_the_core_enums() -> None:
    order = BrokerAdapter.to_broker_order(_OMSOrder("OMS-1"), "B-1", OrderType.MARKET, 1.0)
    assert order.side is Side.BUY
    assert order.order_type is OrderType.MARKET
