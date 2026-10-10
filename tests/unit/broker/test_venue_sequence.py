"""A venue's own sequence number orders its absolute reports (ledger BRK-002).

Until v3.11 an amendment, a position and a balance were applied in delivery
order: each replaces a value, so two delivered out of order left the *older*
value in the mirror, and the lifecycle module said so -- ordering them was the
adapter's obligation, with a snapshot reconciliation as the check. A venue that
numbers its reports can now say which is newer, and the mirror records the
number of the last one it applied per amended order, per position and for the
balances.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.lifecycle import (
    LifecycleOutcome,
    VenueEvent,
    apply_venue_event,
    apply_venue_events,
)
from alphalab.broker.snapshot import BrokerSnapshotDecodeError, capture, from_primitives, restore
from alphalab.broker.state import ConnectionStatus
from alphalab.core.enums import OrderStatus
from alphalab.persistence import deserialize, serialize
from tests.unit.broker.venue_fixtures import E, account, fill, mirror, order, position

R = LifecycleOutcome


def _amend(quantity: str, price: str = "150", sequence: int | None = None) -> VenueEvent:
    return VenueEvent(
        E.ORDER_REPLACED,
        30.0,
        "B-1",
        quantity=Decimal(quantity),
        price=Decimal(price),
        sequence=sequence,
    )


def _held(quantity: str, sequence: int | None = None, symbol: str = "AAPL") -> VenueEvent:
    return VenueEvent(
        E.POSITION_CHANGED, 30.0, position=position(symbol, quantity), sequence=sequence
    )


def _balance(cash: str, sequence: int | None) -> VenueEvent:
    return VenueEvent(E.BALANCE_CHANGED, 30.0, account=account(cash), sequence=sequence)


# --------------------------------------------------------------------------- #
# The defect, and the fix
# --------------------------------------------------------------------------- #


def test_unnumbered_amendments_delivered_out_of_order_leave_the_older_value() -> None:
    """The v3.10 behaviour, still what a venue that numbers nothing gets."""

    state, _ = apply_venue_events(mirror(order()), [_amend("300"), _amend("200")])
    # The venue's latest word was 300; the mirror holds the 200 delivered last.
    assert state.orders["B-1"].quantity == Decimal("200")


def test_numbered_amendments_are_ordered_by_the_venue_not_by_delivery() -> None:
    state, decisions = apply_venue_events(
        mirror(order()), [_amend("300", sequence=6), _amend("200", sequence=5)]
    )

    assert state.orders["B-1"].quantity == Decimal("300")
    assert [decision.outcome for decision in decisions] == [R.APPLIED, R.STALE]
    assert "predates report 6" in decisions[1].reason
    assert state.venue_sequences == {"order:B-1": 6}


def test_a_restated_value_with_a_newer_number_is_still_the_latest_word() -> None:
    """5 -> 300, 7 -> 100 (the original), 6 -> 200: delivered 5, 7, 6.

    The report numbered 7 restates the quantity the order had before, so the
    mirror already *holds* its value. It must still be recorded: the report
    numbered 6, delivered after it, is older and must not overwrite it.
    """

    state, decisions = apply_venue_events(
        mirror(order(quantity="300")),
        [_amend("300", sequence=5), _amend("100", sequence=7), _amend("200", sequence=6)],
    )

    assert state.orders["B-1"].quantity == Decimal("100")
    assert [decision.outcome for decision in decisions] == [R.APPLIED, R.APPLIED, R.STALE]
    assert "recorded as the latest" in decisions[0].reason


def test_the_same_number_twice_is_a_duplicate_or_a_contradiction() -> None:
    state, _ = apply_venue_event(mirror(order()), _amend("300", sequence=4))

    again, repeat = apply_venue_event(state, _amend("300", sequence=4))
    assert repeat.outcome is R.DUPLICATE and again is state

    kept, contradiction = apply_venue_event(state, _amend("250", sequence=4))
    assert contradiction.outcome is R.CONFLICT and contradiction.is_break
    assert kept is state
    assert kept.orders["B-1"].quantity == Decimal("300")


def test_once_numbered_an_unnumbered_report_cannot_be_ordered() -> None:
    state, _ = apply_venue_event(mirror(order()), _amend("300", sequence=4))

    kept, refused = apply_venue_event(state, _amend("250"))

    assert refused.outcome is R.INVALID and kept is state
    assert "cannot be ordered" in refused.reason


def test_a_refused_amendment_records_no_number() -> None:
    """Only an applied report moves the record; a conflict leaves it where it was."""

    filled = mirror(order(status=OrderStatus.FILLED, filled="100"))
    state, decision = apply_venue_event(filled, _amend("300", sequence=9))

    assert decision.outcome is R.CONFLICT
    assert state.venue_sequences == {}


# --------------------------------------------------------------------------- #
# Positions and balances
# --------------------------------------------------------------------------- #


def test_positions_are_numbered_per_instrument() -> None:
    state, decisions = apply_venue_events(
        mirror(),
        [
            _held("40", 10),
            _held("70", 3, symbol="MSFT"),  # another instrument: its own record
            _held("30", 9),  # older than the AAPL report applied
        ],
    )

    assert [decision.outcome for decision in decisions] == [R.APPLIED, R.APPLIED, R.STALE]
    assert state.positions["AAPL"].quantity == Decimal("40")
    assert state.positions["MSFT"].quantity == Decimal("70")
    assert state.venue_sequences == {"position:AAPL": 10, "position:MSFT": 3}


def test_balances_are_numbered() -> None:
    state, decisions = apply_venue_events(
        mirror(), [_balance("90000", 21), _balance("95000", 20), _balance("90000", 21)]
    )

    assert [decision.outcome for decision in decisions] == [R.APPLIED, R.STALE, R.DUPLICATE]
    assert state.account.cash == Decimal("90000")
    assert state.venue_sequences == {"account": 21}


@pytest.mark.parametrize("seed", range(20))
def test_any_delivery_order_of_numbered_reports_ends_where_venue_order_does(seed: int) -> None:
    """The property the number exists for, over seeded random deliveries.

    Every report about one entity carries a distinct number, so the value the
    venue said last is the one with the highest number, whatever order the
    network delivered them in.
    """

    rng = random.Random(seed)
    reports = [
        *(_amend(str(100 + 10 * n), sequence=n) for n in range(1, 7)),
        *(_held(str(10 * n), n) for n in range(1, 7)),
        *(_held(str(5 * n), n, symbol="MSFT") for n in range(1, 5)),
        *(_balance(str(90000 + n), n) for n in range(1, 6)),
    ]
    in_order, _ = apply_venue_events(mirror(order()), reports)
    delivered = reports.copy()
    rng.shuffle(delivered)
    shuffled, _ = apply_venue_events(mirror(order()), delivered)

    assert shuffled.orders == in_order.orders
    assert shuffled.positions == in_order.positions
    assert shuffled.account == in_order.account
    assert shuffled.venue_sequences == in_order.venue_sequences
    assert in_order.orders["B-1"].quantity == Decimal("160")
    assert in_order.positions["AAPL"].quantity == Decimal("60")
    assert in_order.account.cash == Decimal("90005")


# --------------------------------------------------------------------------- #
# Sessions, and what is not compared
# --------------------------------------------------------------------------- #


def test_a_new_connection_session_forgets_the_old_numbers() -> None:
    """A venue may number a new session afresh; the resync covers the gap."""

    state, _ = apply_venue_event(mirror(order()), _amend("300", sequence=900))
    down, _ = apply_venue_event(state, VenueEvent(E.BROKER_DISCONNECTED, 31.0))
    assert down.venue_sequences == {"order:B-1": 900}, "a disconnect ends nothing yet"

    up, connected = apply_venue_event(down, VenueEvent(E.BROKER_CONNECTED, 32.0))
    assert connected.resync_required
    assert up.venue_sequences == {}
    renumbered, decision = apply_venue_event(up, _amend("250", sequence=1))
    assert decision.applied and renumbered.orders["B-1"].quantity == Decimal("250")


def test_status_events_and_fills_carry_a_number_that_is_not_compared() -> None:
    """The lifecycle orders a status report and additivity orders a fill."""

    state, _ = apply_venue_event(mirror(order()), _amend("300", sequence=50))
    early_fill = replace(fill("X-1", "40"), sequence=2)
    state, decision = apply_venue_event(state, early_fill)

    assert decision.applied
    assert state.orders["B-1"].filled_quantity == Decimal("40")
    assert state.venue_sequences == {"order:B-1": 50}


@pytest.mark.parametrize("sequence", [-1, True, 1.0, "3"])
def test_a_sequence_is_a_non_negative_integer(sequence: object) -> None:
    with pytest.raises(BrokerValidationError, match="non-negative integer"):
        VenueEvent(E.POSITION_CHANGED, 1.0, position=position(), sequence=sequence)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", [E.BROKER_CONNECTED, E.BROKER_DISCONNECTED])
def test_a_connectivity_event_carries_no_sequence(kind: E) -> None:
    with pytest.raises(BrokerValidationError, match="carries no sequence"):
        VenueEvent(kind, 1.0, sequence=1)


def test_every_outcome_leaves_a_refused_state_untouched() -> None:
    """Anything but APPLIED returns the state itself, numbered or not."""

    state, _ = apply_venue_events(
        mirror(order()), [_amend("300", sequence=5), _held("40", 5), _balance("90000", 5)]
    )
    for event in (
        _amend("200", sequence=4),
        _amend("300", sequence=5),
        _amend("250", sequence=5),
        _amend("250"),
        _held("30", 4),
        _held("30"),
        _balance("80000", 4),
    ):
        after, decision = apply_venue_event(state, event)
        assert not decision.applied
        assert after is state


# --------------------------------------------------------------------------- #
# Durability
# --------------------------------------------------------------------------- #


def test_the_numbers_survive_a_snapshot_round_trip() -> None:
    state, _ = apply_venue_events(
        mirror(order()), [_amend("300", sequence=5), _held("40", 7), _balance("90000", 2)]
    )
    payload = deserialize(serialize(capture(state)))

    assert payload["schema_version"] == 2
    assert payload["venue_sequences"] == {"account": 2, "order:B-1": 5, "position:AAPL": 7}
    restored, _, _ = restore(from_primitives(payload))
    assert restored.venue_sequences == state.venue_sequences
    # And a restored mirror still refuses the older report.
    _, late = apply_venue_event(restored, _amend("200", sequence=4))
    assert late.outcome is R.STALE


@pytest.mark.parametrize("value", [-1, True, 1.5, "5", None])
def test_a_malformed_number_is_refused_on_decode(value: object) -> None:
    payload = deserialize(serialize(capture(mirror(order()))))
    payload["venue_sequences"] = {"order:B-1": value}

    with pytest.raises(BrokerSnapshotDecodeError, match="non-negative integer"):
        from_primitives(payload)


def test_numbering_is_independent_per_entity_kind() -> None:
    """An order, a position and the balances with the same number do not interact."""

    state = mirror(order(), connection=ConnectionStatus.CONNECTED)
    for event in itertools.chain(
        [_amend("300", sequence=1)], [_held("40", 1)], [_balance("90000", 1)]
    ):
        state, decision = apply_venue_event(state, event)
        assert decision.applied
    assert state.venue_sequences == {"order:B-1": 1, "position:AAPL": 1, "account": 1}
