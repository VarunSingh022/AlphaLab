"""The first real schema evolution, and the constants it must not disturb.

``Position.opened_at`` is the first field added to a persisted state since
v2.5 introduced ``schema_version``. ADR-0014 said the field exists "so that the
first schema change is a decision rather than a silent misread"; this is that
decision, and it is to refuse version 1 rather than migrate it.

A v1 payload does not record when a position opened, and no honest value can be
invented. ``last_updated`` is the last mark-to-market timestamp -- for a position
marked on every event that is effectively *now*, which would report a holding
period of roughly zero for a position held for a year. That is the exact class
of false number v2.6 exists to remove.

The trap this file mainly guards is different, and quieter:
``PORTFOLIO_SNAPSHOT_SCHEMA`` aliased ``DEFAULT_SCHEMA_VERSION`` until v2.6, and
that constant is also the version of the lifecycle snapshot, ``CommonEvent`` and
``BaseEvent``. Bumping the shared constant would have versioned every event in
the system as a side effect of adding one field to a position.
"""

from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.constants import DEFAULT_SCHEMA_VERSION
from alphalab.common.events import BaseEvent, CommonEvent
from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.snapshot import (
    PORTFOLIO_SNAPSHOT_SCHEMA,
    capture,
    from_primitives,
    restore,
)


def _state() -> PortfolioState:
    """A portfolio holding one position opened at a known, non-default time."""

    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=Account("ACC", "USD", "Schema 2", 1.0)),
        Decimal("100000"),
        "USD",
        1.0,
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("10"), Decimal("100.005"), Decimal("1.00"), 2.0, "USD"
    )
    return PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("5"), Decimal("110.00"), Decimal("1.00"), 9.0, "USD"
    )


def _payload() -> dict[str, Any]:
    return dict(deserialize(serialize(capture(_state()))))


# ---------------------------------------------------------------------------
# The bump, and its blast radius
# ---------------------------------------------------------------------------


def test_the_portfolio_snapshot_declares_version_two() -> None:
    assert PORTFOLIO_SNAPSHOT_SCHEMA == 2
    assert capture(_state()).schema_version == 2
    assert _payload()["schema_version"] == 2


def test_no_other_schema_constant_moved() -> None:
    """The trap: these three shared a constant with the portfolio snapshot."""

    assert DEFAULT_SCHEMA_VERSION == 1
    assert LIFECYCLE_SNAPSHOT_SCHEMA == 1
    assert CommonEvent("e").schema_version == 1
    assert BaseEvent("id", 1.0).__dataclass_fields__.keys() == {"event_id", "timestamp"}


def test_the_portfolio_constant_is_no_longer_an_alias() -> None:
    """It must be independently settable, or the next bump repeats the trap."""

    assert PORTFOLIO_SNAPSHOT_SCHEMA != DEFAULT_SCHEMA_VERSION


def test_a_version_one_payload_is_refused_with_a_message_naming_the_version() -> None:
    payload = _payload()
    payload["schema_version"] = 1

    with pytest.raises(StateDecodeError, match="declares schema version 1"):
        from_primitives(payload)


def test_there_is_no_migration_path() -> None:
    """A v1 payload is refused even though it is otherwise well-formed."""

    payload = _payload()
    payload["schema_version"] = 1
    for position in payload["positions"]:
        position.pop("opened_at", None)

    with pytest.raises(StateDecodeError):
        from_primitives(payload)


# ---------------------------------------------------------------------------
# opened_at survives the round trip
# ---------------------------------------------------------------------------


def test_restore_is_the_inverse_of_capture_with_opened_at_populated() -> None:
    state = _state()

    assert state.positions["AAPL"].opened_at == 2.0
    assert restore(capture(state)) == state


def test_opened_at_survives_the_json_round_trip() -> None:
    state = _state()
    restored = restore(from_primitives(deserialize(serialize(capture(state)))))

    assert restored == state
    assert restored.positions["AAPL"].opened_at == 2.0


def test_an_unrecorded_open_time_round_trips_as_null() -> None:
    """A hand-built position reports ``None``, and that survives as ``None``."""

    from alphalab.portfolio.position import Position

    state = PortfolioState(
        account=Account("ACC", "USD", "Schema 2", 1.0),
        positions={
            "AAPL": Position(
                "AAPL", Decimal("1"), Decimal("10"), Decimal("10"), Decimal("0"), "USD", 1.0
            )
        },
    )
    payload = deserialize(serialize(capture(state)))

    assert payload["positions"][0]["opened_at"] is None
    assert restore(from_primitives(payload)).positions["AAPL"].opened_at is None


def test_a_missing_opened_at_names_the_field() -> None:
    payload = _payload()
    del payload["positions"][0]["opened_at"]

    with pytest.raises(StateDecodeError, match="missing 'opened_at'"):
        from_primitives(payload)


def test_a_wrongly_typed_opened_at_names_the_field() -> None:
    payload = _payload()
    payload["positions"][0]["opened_at"] = "recently"

    with pytest.raises(StateDecodeError, match="opened_at"):
        from_primitives(payload)
