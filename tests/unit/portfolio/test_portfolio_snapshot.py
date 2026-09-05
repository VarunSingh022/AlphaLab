"""Typed capture / restore for PortfolioState.

The contract ADR-0014 states: ``restore(capture(state)) == state``. The restored
value compares equal; it does not reproduce internal container lineage, and
nothing in AlphaLab can observe the difference.

These tests exercise the whole boundary -- in-memory round trip, round trip
across ``serialize`` / ``deserialize``, and the decode failures a malformed
payload must produce rather than a plausible wrong state.
"""

import json
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.ids import id_scope
from alphalab.persistence.adapter import PersistenceAdapter
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.persistence.state import PersistenceState
from alphalab.persistence.storage import MemoryStorage
from alphalab.persistence.views import latest_snapshot
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.snapshot import (
    PORTFOLIO_SNAPSHOT_SCHEMA,
    PortfolioSnapshot,
    capture,
    from_primitives,
    restore,
)
from alphalab.portfolio.types import TransactionType

#: Transaction ids come from ``new_id()``, so a portfolio built twice agrees on
#: every quantity and disagrees on every transaction id. Seeding the fixture is
#: what lets these tests compare two independently built states -- the same
#: mechanism ``BacktestConfig.seed`` uses.
_SEED = 20250905


def _state() -> PortfolioState:
    """A portfolio with cash, an open position, a realised reduction and a mark."""

    with id_scope(_SEED):
        return _build()


def _build() -> PortfolioState:
    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=Account("ACC-1", "USD", "Round Trip", 1.0)),
        Decimal("100000"),
        "USD",
        1.0,
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("10"), Decimal("100.005"), Decimal("1.00"), 2.0
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("-4"), Decimal("110.007"), Decimal("1.00"), 3.0
    )
    state = PortfolioEngine.apply_fill(
        state, "MSFT", Decimal("5"), Decimal("300.50"), Decimal("0.50"), 4.0
    )
    return PortfolioEngine.update_market_prices(
        state, {"AAPL": Decimal("115.00"), "MSFT": Decimal("305.00")}, 5.0
    )


def _payload() -> dict[str, Any]:
    decoded = json.loads(serialize(capture(_state())))
    assert isinstance(decoded, dict)
    return decoded


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


def test_restore_is_the_inverse_of_capture() -> None:
    state = _state()
    assert restore(capture(state)) == state


def test_restore_is_the_inverse_across_json() -> None:
    state = _state()
    assert restore(from_primitives(deserialize(serialize(capture(state))))) == state


def test_restore_reproduces_semantics_not_container_lineage() -> None:
    """The equality above is semantic. The restored containers are fresh."""

    state = _state()
    restored = restore(capture(state))

    assert restored.events == state.events
    assert restored.events is not state.events
    assert isinstance(restored.events, AppendOnlyLog)
    assert isinstance(restored.ledger.transactions, AppendOnlyLog)


def test_every_field_survives_the_round_trip() -> None:
    state = _state()
    restored = restore(from_primitives(deserialize(serialize(capture(state)))))

    assert restored.account == state.account
    assert restored.cash.balances == state.cash.balances
    assert restored.cash.reserved == state.cash.reserved
    assert restored.positions == state.positions
    assert restored.ledger.transactions.to_tuple() == state.ledger.transactions.to_tuple()
    assert restored.realized_pnl == state.realized_pnl
    assert restored.commission_paid == state.commission_paid


def test_exact_money_survives_the_round_trip() -> None:
    """Decimals go through str, so a price between cents comes back unchanged."""

    restored = restore(from_primitives(deserialize(serialize(capture(_state())))))
    position = restored.positions["AAPL"]

    assert position.quantity == Decimal("6.000000")
    assert restored.realized_pnl == Decimal("40.01")
    assert restored.commission_paid == Decimal("2.50")
    assert restored.cash.balance("USD") == _state().cash.balance("USD")


def test_a_restored_position_keeps_its_exact_cost_basis() -> None:
    state = _state()
    restored = restore(capture(state))
    assert restored.positions["AAPL"].cost_basis == state.positions["AAPL"].cost_basis
    assert restored.positions["AAPL"].unrealized_pnl == state.positions["AAPL"].unrealized_pnl


def test_typed_events_come_back_as_their_own_types() -> None:
    """Without the event_type tag a heterogenous log cannot be read back."""

    restored = restore(from_primitives(deserialize(serialize(capture(_state())))))
    names = [type(event).__name__ for event in restored.events]

    assert names == [
        "CashDeposited",
        "PositionOpened",
        "PositionReduced",
        "PositionOpened",
        "MarketValueUpdated",
    ]


def test_transaction_types_come_back_as_enum_members() -> None:
    restored = restore(from_primitives(deserialize(serialize(capture(_state())))))
    kinds = [t.type for t in restored.ledger.transactions]

    assert kinds[0] is TransactionType.DEPOSIT
    assert TransactionType.BUY in kinds
    assert TransactionType.SELL in kinds


def test_an_empty_portfolio_round_trips() -> None:
    state = PortfolioState(account=Account("EMPTY", "USD", "Empty", 0.0))
    assert restore(from_primitives(deserialize(serialize(capture(state))))) == state


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_capture_and_serialization_are_deterministic() -> None:
    state = _state()
    assert serialize(capture(state)) == serialize(capture(state))


def test_a_restored_state_re_serializes_identically() -> None:
    """Restore must not perturb anything the encoder can see."""

    state = _state()
    payload = serialize(capture(state))
    assert serialize(capture(restore(from_primitives(deserialize(payload))))) == payload


def test_a_restored_state_continues_processing_identically() -> None:
    """The point of restoring: the run carries on where it left off."""

    state = _state()
    restored = restore(from_primitives(deserialize(serialize(capture(state)))))

    with id_scope(_SEED + 1):
        direct = PortfolioEngine.apply_fill(
            state, "AAPL", Decimal("-6"), Decimal("120.00"), Decimal("1.00"), 6.0
        )
    with id_scope(_SEED + 1):
        resumed = PortfolioEngine.apply_fill(
            restored, "AAPL", Decimal("-6"), Decimal("120.00"), Decimal("1.00"), 6.0
        )

    assert resumed.realized_pnl == direct.realized_pnl
    assert resumed.cash.balance("USD") == direct.cash.balance("USD")
    assert "AAPL" not in resumed.positions
    assert serialize(capture(resumed)) == serialize(capture(direct))


# --------------------------------------------------------------------------- #
# The persistence store is a real consumer
# --------------------------------------------------------------------------- #


def test_a_snapshot_round_trips_through_the_persistence_store() -> None:
    """capture -> Snapshot -> save -> load -> from_primitives -> restore."""

    state = _state()
    storage = MemoryStorage()
    persistence = PersistenceState(engine_id="ENGINE-1")

    record = PersistenceAdapter.to_snapshot("SNAP-1", "portfolio", 9.0, capture(state))
    persistence, _ = storage.save_snapshot(persistence, record, 9.0)

    stored = latest_snapshot(persistence, "portfolio")
    assert stored is not None
    restored = restore(from_primitives(PersistenceAdapter.snapshot_payload(stored)))

    assert restored == state


def test_a_payload_that_is_not_an_object_is_refused_by_the_adapter() -> None:
    from alphalab.persistence.snapshot import Snapshot

    bad = Snapshot(snapshot_id="S", subsystem="portfolio", timestamp=1.0, payload="[1, 2, 3]")
    with pytest.raises(StateDecodeError, match="does not contain an object"):
        PersistenceAdapter.snapshot_payload(bad)


# --------------------------------------------------------------------------- #
# Malformed payloads fail explicitly
# --------------------------------------------------------------------------- #


def test_a_missing_field_names_the_field() -> None:
    payload = _payload()
    del payload["realized_pnl"]
    with pytest.raises(StateDecodeError, match="missing 'realized_pnl'"):
        from_primitives(payload)


def test_a_missing_nested_field_names_the_field() -> None:
    payload = _payload()
    del payload["positions"][0]["cost_basis"]
    with pytest.raises(StateDecodeError, match="missing 'cost_basis'"):
        from_primitives(payload)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("positions", {}, "positions is not an array"),
        ("balances", [], "balances is not an object"),
        ("realized_pnl", "not-a-number", "realized_pnl is not a decimal"),
        ("account", "ACC-1", "account is not an object"),
        ("events", {}, "events is not an array"),
    ],
)
def test_a_wrong_type_names_the_field(field: str, value: Any, match: str) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises(StateDecodeError, match=match):
        from_primitives(payload)


def test_a_string_is_not_accepted_as_an_array() -> None:
    payload = _payload()
    payload["positions"] = "AAPL"
    with pytest.raises(StateDecodeError, match="positions is not an array"):
        from_primitives(payload)


def test_an_unknown_event_type_is_refused() -> None:
    payload = _payload()
    payload["events"][0]["event_type"] = "SomethingElse"
    with pytest.raises(StateDecodeError, match="is not a portfolio event"):
        from_primitives(payload)


def test_a_malformed_transaction_type_is_refused() -> None:
    payload = _payload()
    payload["transactions"][0]["type"] = "DEPOSIT"
    with pytest.raises(StateDecodeError, match="is not a TransactionType"):
        from_primitives(payload)


def test_an_unknown_transaction_type_member_is_refused() -> None:
    payload = _payload()
    payload["transactions"][0]["type"] = "TransactionType.TELEPORT"
    with pytest.raises(StateDecodeError, match="names no TransactionType member"):
        from_primitives(payload)


def test_a_number_is_not_accepted_where_a_string_belongs() -> None:
    payload = _payload()
    payload["account"]["account_id"] = 42
    with pytest.raises(StateDecodeError, match=r"account\.account_id is not a string"):
        from_primitives(payload)


def test_a_boolean_is_not_accepted_as_a_decimal() -> None:
    payload = _payload()
    payload["realized_pnl"] = True
    with pytest.raises(StateDecodeError, match="realized_pnl is not a decimal"):
        from_primitives(payload)


def test_a_payload_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(StateDecodeError, match="portfolio snapshot is not an object"):
        from_primitives([])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Schema version
# --------------------------------------------------------------------------- #


def test_the_snapshot_declares_its_schema_version() -> None:
    assert capture(_state()).schema_version == PORTFOLIO_SNAPSHOT_SCHEMA
    assert _payload()["schema_version"] == PORTFOLIO_SNAPSHOT_SCHEMA


def test_an_unknown_schema_version_is_refused() -> None:
    """There is no migration path in v2.5, so an unreadable version says so."""

    payload = _payload()
    payload["schema_version"] = PORTFOLIO_SNAPSHOT_SCHEMA + 1
    with pytest.raises(StateDecodeError, match="declares schema version"):
        from_primitives(payload)


def test_a_missing_schema_version_is_refused() -> None:
    payload = _payload()
    del payload["schema_version"]
    with pytest.raises(StateDecodeError, match="missing 'schema_version'"):
        from_primitives(payload)


def test_a_non_integer_schema_version_is_refused() -> None:
    payload = _payload()
    payload["schema_version"] = "1"
    with pytest.raises(StateDecodeError, match="schema_version is not an integer"):
        from_primitives(payload)


def test_an_extra_field_is_ignored_rather_than_rejected() -> None:
    """Decoding names what it needs; a field it does not read is not an error,
    and is deliberately not carried into the restored state either."""

    payload = _payload()
    payload["written_by"] = "some future build"
    snapshot = from_primitives(payload)

    assert isinstance(snapshot, PortfolioSnapshot)
    assert restore(snapshot) == _state()


def test_the_snapshot_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    snapshot = capture(_state())
    with pytest.raises(FrozenInstanceError):
        snapshot.realized_pnl = Decimal("0")  # type: ignore[misc]


def test_capture_does_not_mutate_the_state() -> None:
    state = _state()
    before = serialize(state)
    capture(state)
    assert serialize(state) == before
    assert replace(state) == state
