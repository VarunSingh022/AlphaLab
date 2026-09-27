"""Reconciling the mirror against a whole venue snapshot, and ``reconcile``'s two refusals."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

import pytest

from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.order import BrokerOrderStatus
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import (
    SnapshotDivergenceKind,
    SnapshotFreshness,
    SnapshotReconciliation,
    VenueSnapshot,
    reconcile,
    reconcile_snapshot,
)
from alphalab.broker.state import BrokerState
from alphalab.core.enums import OrderStatus
from tests.unit.broker.venue_fixtures import account, execution, mirror, order, position

K = SnapshotDivergenceKind
AT = 100.0


def _state() -> BrokerState:
    working = order("B-1", filled="40", status=OrderStatus.PARTIALLY_FILLED, updated_at=50.0)
    done = order("B-2", filled="100", status=OrderStatus.FILLED, updated_at=60.0)
    return mirror(
        working,
        done,
        positions=(position("AAPL", "140"),),
        executions=(
            execution("X-1", "B-1", "40", timestamp=50.0),
            execution("X-2", "B-2", "100", timestamp=60.0),
        ),
    )


def _snapshot(**overrides: Any) -> VenueSnapshot:
    state = _state()
    base = VenueSnapshot(
        as_of=90.0,
        orders=tuple(state.orders.values()),
        executions=tuple(state.executions.values()),
        positions=tuple(state.positions.values()),
        account=state.account,
    )
    return dataclasses.replace(base, **overrides)


def _run(snapshot: VenueSnapshot, **kwargs: float) -> SnapshotReconciliation:
    return reconcile_snapshot(
        _state(),
        snapshot,
        evaluated_at=kwargs.get("evaluated_at", AT),
        max_age_seconds=kwargs.get("max_age_seconds", 60.0),
    )


def test_an_exact_match_is_fully_reconciled() -> None:
    result = _run(_snapshot())
    assert result.freshness is SnapshotFreshness.CURRENT
    assert result.divergences == ()
    assert result.reconciled and result.fully_reconciled
    assert (result.compared_orders, result.compared_executions, result.compared_positions) == (
        2,
        2,
        1,
    )


@pytest.mark.parametrize(
    ("field", "value", "kind"),
    [
        ("quantity", Decimal("90"), K.ORDER_QUANTITY),
        ("price", Decimal("151"), K.ORDER_PRICE),
        ("filled_quantity", Decimal("50"), K.ORDER_FILLED_QUANTITY),
        ("status", OrderStatus.CANCELLED, K.ORDER_STATUS),
    ],
)
def test_each_order_field_is_compared_on_its_own(field: str, value: Any, kind: K) -> None:
    state = _state()
    changed = dataclasses.replace(state.orders["B-1"], **{field: value})
    result = _run(_snapshot(orders=(changed, state.orders["B-2"])))
    assert [d.kind for d in result.divergences] == [kind]
    assert result.divergences[0].key == "B-1"
    assert not result.reconciled


def test_every_differing_field_is_reported_not_just_the_first() -> None:
    state = _state()
    changed = dataclasses.replace(
        state.orders["B-1"], quantity=Decimal("90"), price=Decimal("1"), status=OrderStatus.EXPIRED
    )
    kinds = {d.kind for d in _run(_snapshot(orders=(changed, state.orders["B-2"]))).divergences}
    assert kinds == {K.ORDER_QUANTITY, K.ORDER_PRICE, K.ORDER_STATUS}


def test_an_in_flight_status_is_consistent_with_the_canonical_one_it_relates_to() -> None:
    pending = order("B-1", filled="40", status=BrokerOrderStatus.PENDING_CANCEL, updated_at=50.0)
    state = mirror(pending, executions=(execution("X-1", "B-1", "40", timestamp=50.0),))
    venue = dataclasses.replace(pending, status=OrderStatus.PARTIALLY_FILLED)
    result = reconcile_snapshot(
        state,
        VenueSnapshot(90.0, (venue,), tuple(state.executions.values()), None, None),
        evaluated_at=AT,
        max_age_seconds=60.0,
    )
    assert result.of(K.ORDER_STATUS) == ()


def test_a_missing_working_order_and_an_extra_order_are_both_reported() -> None:
    state = _state()
    stranger = order("B-9", updated_at=10.0)
    result = _run(_snapshot(orders=(state.orders["B-2"], stranger)))
    assert [(d.kind, d.key) for d in result.divergences if d.kind is not K.MISSING_EXECUTION] == [
        (K.MISSING_ORDER, "B-1"),
        (K.UNKNOWN_ORDER, "B-9"),
    ]


def test_a_finished_order_the_venue_has_purged_is_not_missing() -> None:
    state = _state()
    result = _run(_snapshot(orders=(state.orders["B-1"],), executions=(state.executions["X-1"],)))
    assert result.divergences == ()


def test_fills_are_compared_for_the_orders_the_snapshot_reports() -> None:
    state = _state()
    extra = execution("X-9", "B-1", "5", timestamp=55.0)
    wrong = dataclasses.replace(state.executions["X-2"], fill_price=Decimal("149"))
    result = _run(_snapshot(executions=(extra, wrong)))
    found = {(d.kind, d.key) for d in result.divergences}
    assert found == {
        (K.UNKNOWN_EXECUTION, "X-9"),
        (K.EXECUTION_MISMATCH, "X-2"),
        (K.MISSING_EXECUTION, "X-1"),
    }


@pytest.mark.parametrize(
    ("positions", "expected"),
    [
        ((), [("AAPL", "140", "0")]),
        ((position("AAPL", "140"), position("MSFT", "5")), [("MSFT", "0", "5")]),
        ((position("AAPL", "100"),), [("AAPL", "140", "100")]),
    ],
)
def test_positions_are_compared_in_both_directions(
    positions: tuple[BrokerPosition, ...], expected: list[tuple[str, str, str]]
) -> None:
    result = _run(_snapshot(positions=positions))
    assert [(d.key, d.local, d.remote) for d in result.of(K.POSITION_QUANTITY)] == expected


def test_every_balance_is_compared_and_the_account_identity_first() -> None:
    state = _state()
    poorer = dataclasses.replace(state.account, cash=Decimal("90000"), margin=Decimal("5"))
    assert {d.key for d in _run(_snapshot(account=poorer)).of(K.BALANCE)} == {"cash", "margin"}

    foreign = _run(_snapshot(account=account(currency="EUR")))
    assert [d.kind for d in foreign.divergences] == [K.ACCOUNT_IDENTITY]
    other = _run(_snapshot(account=account(account_id="ACC-2")))
    assert [d.kind for d in other.divergences] == [K.ACCOUNT_IDENTITY]


def test_duplicated_evidence_is_reported_and_not_compared() -> None:
    state = _state()
    twin = dataclasses.replace(state.orders["B-1"], quantity=Decimal("1"))
    result = _run(
        _snapshot(
            orders=(state.orders["B-1"], twin, state.orders["B-2"]),
            positions=(position("AAPL", "140"), position("AAPL", "1")),
        )
    )
    duplicates = {d.key for d in result.of(K.DUPLICATE_EVIDENCE)}
    assert duplicates == {"B-1", "AAPL"}
    assert result.of(K.ORDER_QUANTITY) == ()
    assert result.of(K.POSITION_QUANTITY) == ()
    assert not result.reconciled


@pytest.mark.parametrize(
    ("as_of", "evaluated_at", "budget", "freshness"),
    [
        (55.0, AT, 60.0, SnapshotFreshness.PREDATES_MIRROR),
        (90.0, 200.0, 60.0, SnapshotFreshness.TOO_OLD),
        (150.0, AT, 60.0, SnapshotFreshness.FROM_THE_FUTURE),
        (60.0, AT, 60.0, SnapshotFreshness.CURRENT),
    ],
)
def test_a_snapshot_that_is_not_current_is_not_compared(
    as_of: float, evaluated_at: float, budget: float, freshness: SnapshotFreshness
) -> None:
    state = _state()
    wrong = dataclasses.replace(state.orders["B-1"], quantity=Decimal("1"))
    result = _run(
        _snapshot(as_of=as_of, orders=(wrong, state.orders["B-2"])),
        evaluated_at=evaluated_at,
        max_age_seconds=budget,
    )
    assert result.freshness is freshness
    if freshness is SnapshotFreshness.CURRENT:
        assert result.of(K.ORDER_QUANTITY)
    else:
        assert result.divergences == ()
        assert not result.reconciled
        assert result.compared_orders == 0


def test_what_the_snapshot_did_not_report_is_named_not_assumed() -> None:
    result = _run(_snapshot(executions=None, positions=None, account=None))
    assert result.unexamined == ("executions", "positions", "account")
    assert result.reconciled
    assert not result.fully_reconciled


def test_reconciling_a_repeated_snapshot_is_idempotent() -> None:
    first = _run(_snapshot())
    second = _run(_snapshot())
    assert first == second
    assert first.reconciliation_id == second.reconciliation_id


def test_the_snapshot_identity_ignores_listing_order_and_sees_content() -> None:
    state = _state()
    forward = _snapshot()
    backward = _snapshot(orders=tuple(reversed(tuple(state.orders.values()))))
    assert forward.snapshot_id == backward.snapshot_id
    assert _snapshot(as_of=91.0).snapshot_id != forward.snapshot_id
    assert _snapshot(positions=()).snapshot_id != forward.snapshot_id
    assert _snapshot(positions=None).snapshot_id != _snapshot(positions=()).snapshot_id


def test_the_judgement_inputs_are_required_to_make_sense() -> None:
    with pytest.raises(BrokerValidationError):
        _run(_snapshot(), evaluated_at=float("nan"))
    with pytest.raises(BrokerValidationError):
        _run(_snapshot(), max_age_seconds=-1.0)
    with pytest.raises(BrokerValidationError):
        VenueSnapshot(float("inf"), (), None, None, None)


# --------------------------------------------------------------------------- #
# reconcile(): what it used to do silently, it now refuses
# --------------------------------------------------------------------------- #


def test_reconcile_refuses_two_records_for_one_order() -> None:
    state = mirror(order())
    with pytest.raises(BrokerValidationError, match="more than one order"):
        reconcile(state, [order(), order(filled="10")])


def test_reconcile_refuses_two_records_for_one_position() -> None:
    with pytest.raises(BrokerValidationError, match="more than one position"):
        reconcile(mirror(), [], [position("AAPL", "1"), position("AAPL", "2")])


def test_reconcile_refuses_to_subtract_cash_across_currencies() -> None:
    with pytest.raises(BrokerValidationError, match="no unit"):
        reconcile(mirror(), [], (), account(currency="EUR"))
    assert reconcile(mirror(), [], (), account(cash="99000")).cash_difference == -1000
