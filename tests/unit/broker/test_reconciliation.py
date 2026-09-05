"""Reconciliation: identity mapping, fill classification, and venue comparison."""

from decimal import Decimal

import pytest

from alphalab.broker.account import BrokerAccount
from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import (
    ExecutionOutcome,
    ExternalOrderMap,
    ReconciliationLog,
    apply_execution,
    classify_execution,
    reconcile,
)
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import OrderStatus


def _account(cash: str = "100000") -> BrokerAccount:
    return BrokerAccount(
        account_id="ACC-1",
        cash=Decimal(cash),
        equity=Decimal(cash),
        buying_power=Decimal(cash),
        margin=Decimal("0"),
        available_funds=Decimal(cash),
        currency="USD",
    )


def _order(
    broker_order_id: str = "B-1",
    quantity: str = "100",
    filled: str = "0",
    status: OrderStatus | BrokerOrderStatus = OrderStatus.ACCEPTED,
) -> BrokerOrder:
    from alphalab.core.enums import OrderType, Side

    return BrokerOrder(
        broker_order_id=broker_order_id,
        oms_order_id=f"OMS-{broker_order_id}",
        symbol="AAPL",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        price=Decimal("150"),
        filled_quantity=Decimal(filled),
        average_fill_price=Decimal("150") if Decimal(filled) else Decimal("0"),
        status=status,
        created_at=1000.0,
        updated_at=1000.0,
    )


def _state(*orders: BrokerOrder) -> BrokerState:
    return BrokerState(
        broker_name="PAPER",
        connection_status=ConnectionStatus.CONNECTED,
        account=_account(),
        orders=PersistentMap({order.broker_order_id: order for order in orders}),
    )


def _fill(
    execution_id: str = "E-1",
    broker_order_id: str = "B-1",
    quantity: str = "40",
    price: str = "150",
    timestamp: float = 1001.0,
) -> BrokerExecution:
    return BrokerExecution(
        execution_id=execution_id,
        broker_order_id=broker_order_id,
        symbol="AAPL",
        fill_quantity=Decimal(quantity),
        fill_price=Decimal(price),
        commission=Decimal("0"),
        timestamp=timestamp,
    )


# --- external identity -------------------------------------------------------


def test_the_map_resolves_both_directions() -> None:
    mapping = ExternalOrderMap().bind("OMS-1", "B-1")
    assert mapping.broker_id_for("OMS-1") == "B-1"
    assert mapping.oms_id_for("B-1") == "OMS-1"


def test_rebinding_an_oms_order_to_a_second_handle_is_refused() -> None:
    """Silently rebinding is how one order's fills land on another."""
    mapping = ExternalOrderMap().bind("OMS-1", "B-1")
    with pytest.raises(BrokerValidationError, match="already bound"):
        mapping.bind("OMS-1", "B-2")


def test_reusing_a_broker_handle_for_a_second_oms_order_is_refused() -> None:
    mapping = ExternalOrderMap().bind("OMS-1", "B-1")
    with pytest.raises(BrokerValidationError, match="already bound"):
        mapping.bind("OMS-2", "B-1")


def test_rebinding_an_identical_pair_is_a_no_op_not_an_error() -> None:
    """A venue re-announcing the same order must not be treated as a conflict."""
    mapping = ExternalOrderMap().bind("OMS-1", "B-1").bind("OMS-1", "B-1")
    assert mapping.broker_id_for("OMS-1") == "B-1"


def test_an_empty_identifier_is_refused() -> None:
    with pytest.raises(BrokerValidationError, match="required"):
        ExternalOrderMap().bind("", "B-1")


def test_bind_order_uses_the_identifiers_the_order_carries() -> None:
    mapping = ExternalOrderMap().bind_order(_order("B-9"))
    assert mapping.oms_id_for("B-9") == "OMS-B-9"


def test_an_unmapped_identifier_resolves_to_none() -> None:
    assert ExternalOrderMap().broker_id_for("OMS-1") is None
    assert ExternalOrderMap().oms_id_for("B-1") is None


# --- classification ----------------------------------------------------------


def test_a_valid_fill_against_a_working_order_is_applied() -> None:
    decision = classify_execution(_state(_order()), _fill())
    assert decision.outcome is ExecutionOutcome.APPLIED
    assert decision.applied and not decision.is_break


def test_a_redelivered_fill_is_a_duplicate_and_not_a_break() -> None:
    """Redelivery after a reconnect is routine, so it must be a no-op."""
    state, _, _ = apply_execution(_state(_order()), _fill())
    decision = classify_execution(state, _fill())

    assert decision.outcome is ExecutionOutcome.DUPLICATE
    assert not decision.applied
    assert not decision.is_break


def test_a_fill_for_an_unknown_order_is_surfaced() -> None:
    decision = classify_execution(_state(_order()), _fill(broker_order_id="B-UNKNOWN"))
    assert decision.outcome is ExecutionOutcome.UNKNOWN_ORDER
    assert decision.is_break
    assert "B-UNKNOWN" in decision.reason


@pytest.mark.parametrize(
    "status",
    [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED],
)
def test_a_fill_against_a_terminal_order_is_surfaced_not_applied(status: OrderStatus) -> None:
    decision = classify_execution(_state(_order(status=status)), _fill())
    assert decision.outcome is ExecutionOutcome.TERMINAL_ORDER
    assert decision.is_break


def test_a_fill_that_would_overfill_is_refused() -> None:
    decision = classify_execution(_state(_order(quantity="100", filled="80")), _fill(quantity="40"))
    assert decision.outcome is ExecutionOutcome.OVERFILL
    assert decision.is_break


def test_a_fill_that_exactly_completes_an_order_is_not_an_overfill() -> None:
    decision = classify_execution(_state(_order(quantity="100", filled="60")), _fill(quantity="40"))
    assert decision.outcome is ExecutionOutcome.APPLIED


@pytest.mark.parametrize(
    ("quantity", "price", "commission"),
    [("0", "150", "0"), ("-1", "150", "0"), ("10", "-1", "0"), ("10", "150", "-1")],
)
def test_structurally_invalid_fills_are_refused(quantity: str, price: str, commission: str) -> None:
    execution = BrokerExecution(
        "E-1", "B-1", "AAPL", Decimal(quantity), Decimal(price), Decimal(commission), 1001.0
    )
    assert classify_execution(_state(_order()), execution).outcome is ExecutionOutcome.INVALID


def test_duplicate_detection_precedes_every_other_check() -> None:
    """An already-applied fill stays a duplicate even once its order is terminal."""
    state, _, _ = apply_execution(_state(_order(quantity="40")), _fill(quantity="40"))
    assert state.orders["B-1"].status is OrderStatus.FILLED

    assert classify_execution(state, _fill(quantity="40")).outcome is ExecutionOutcome.DUPLICATE


# --- application -------------------------------------------------------------


def test_applying_a_partial_fill_advances_the_order() -> None:
    state, decision, _ = apply_execution(_state(_order()), _fill(quantity="40"))
    order = state.orders["B-1"]

    assert decision.applied
    assert order.filled_quantity == Decimal("40")
    assert order.remaining_quantity == Decimal("60")
    assert order.status is OrderStatus.PARTIALLY_FILLED


def test_applying_the_final_fill_makes_the_order_terminal() -> None:
    state, _, _ = apply_execution(_state(_order(quantity="100")), _fill(quantity="100"))
    assert state.orders["B-1"].status is OrderStatus.FILLED
    assert state.orders["B-1"].is_terminal


def test_the_average_fill_price_is_volume_weighted() -> None:
    state, _, _ = apply_execution(_state(_order()), _fill("E-1", quantity="40", price="100"))
    state, _, _ = apply_execution(state, _fill("E-2", quantity="60", price="200"))

    # (40*100 + 60*200) / 100 = 160
    assert state.orders["B-1"].average_fill_price == Decimal("160")


def test_a_refused_fill_leaves_the_state_completely_untouched() -> None:
    before = _state(_order(status=OrderStatus.CANCELLED))
    after, decision, _ = apply_execution(before, _fill())

    assert after is before
    assert not decision.applied


def test_applying_the_same_fill_twice_changes_nothing_the_second_time() -> None:
    once, _, _ = apply_execution(_state(_order()), _fill())
    twice, decision, _ = apply_execution(once, _fill())

    assert twice is once
    assert decision.outcome is ExecutionOutcome.DUPLICATE


def test_the_log_separates_duplicates_from_breaks() -> None:
    state, _, log = apply_execution(_state(_order()), _fill())
    state, _, log = apply_execution(state, _fill(), log)
    state, _, log = apply_execution(state, _fill("E-2", broker_order_id="B-NOPE"), log)

    assert len(log.duplicates) == 1
    assert len(log.breaks) == 1
    assert log.breaks[0].outcome is ExecutionOutcome.UNKNOWN_ORDER


def test_an_applied_fill_adds_nothing_to_the_log() -> None:
    _, _, log = apply_execution(_state(_order()), _fill(), ReconciliationLog())
    assert log == ReconciliationLog()


# --- venue comparison --------------------------------------------------------


def test_matching_state_reconciles_cleanly() -> None:
    order = _order()
    report = reconcile(_state(order), [order])
    assert report.reconciled


def test_an_order_the_venue_does_not_have_is_reported_missing() -> None:
    report = reconcile(_state(_order()), [])
    assert [d.broker_order_id for d in report.missing_at_broker] == ["B-1"]
    assert not report.reconciled


def test_a_terminal_local_order_absent_at_the_venue_is_not_a_break() -> None:
    """Venues drop finished orders; that is expected, not a divergence."""
    report = reconcile(_state(_order(status=OrderStatus.FILLED)), [])
    assert not report.missing_at_broker
    assert report.reconciled


def test_an_order_only_the_venue_knows_is_reported_unknown() -> None:
    report = reconcile(_state(), [_order("B-99")])
    assert [d.broker_order_id for d in report.unknown_locally] == ["B-99"]


def test_a_differing_fill_quantity_is_reported() -> None:
    report = reconcile(_state(_order(filled="0")), [_order(filled="40")])
    assert len(report.divergent_orders) == 1
    assert "Filled quantity differs" in report.divergent_orders[0].reason


def test_a_differing_status_is_reported_when_quantities_agree() -> None:
    report = reconcile(
        _state(_order(status=OrderStatus.ACCEPTED)), [_order(status=OrderStatus.CANCELLED)]
    )
    assert len(report.divergent_orders) == 1
    assert "Status differs" in report.divergent_orders[0].reason


def test_position_quantities_are_compared_in_both_directions() -> None:
    local = BrokerState(
        "PAPER",
        ConnectionStatus.CONNECTED,
        _account(),
        positions=PersistentMap(
            {
                "AAPL": BrokerPosition(
                    "AAPL",
                    Decimal("100"),
                    Decimal("150"),
                    Decimal("15000"),
                    Decimal("0"),
                    Decimal("0"),
                )
            }
        ),
    )
    remote = (
        BrokerPosition(
            "AAPL", Decimal("90"), Decimal("150"), Decimal("13500"), Decimal("0"), Decimal("0")
        ),
        BrokerPosition(
            "MSFT", Decimal("10"), Decimal("300"), Decimal("3000"), Decimal("0"), Decimal("0")
        ),
    )

    report = reconcile(local, [], remote)
    divergences = {
        d.symbol: (d.local_quantity, d.remote_quantity) for d in report.divergent_positions
    }

    assert divergences == {
        "AAPL": (Decimal("100"), Decimal("90")),
        "MSFT": (Decimal("0"), Decimal("10")),
    }


def test_cash_difference_is_reported_against_the_venue_account() -> None:
    report = reconcile(_state(), [], (), _account("99000"))
    assert report.cash_difference == Decimal("-1000")
    assert not report.reconciled


def test_no_venue_account_means_no_cash_claim() -> None:
    """Not asking about cash must not read as agreeing about cash."""
    assert reconcile(_state(), []).cash_difference == Decimal("0")
