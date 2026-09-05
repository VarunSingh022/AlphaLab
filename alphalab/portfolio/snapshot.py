"""Complete, restorable snapshots of a :class:`~alphalab.portfolio.engine.PortfolioState`.

The books are the thing a run most needs to be able to reload: cash by currency,
open positions with their exact cost basis, the transaction ledger and the event
history that explains how they got that way. Until v2.5 a ``PortfolioState``
could be *written* -- ``alphalab.persistence.serialize`` has encoded it since
v2.1 -- and could not be read back into anything but nested dictionaries.

What restore guarantees
-----------------------
``restore(capture(state)) == state``. The restored value compares equal to the
original; it does not reproduce its internal container lineage, and nothing in
AlphaLab can observe the difference -- every persistent container defines value
equality (see ADR-0014). This is the same contract
:mod:`alphalab.oms.snapshot` has held since v2.2, where the order book's asset
and strategy indices are rebuilt rather than restored.

Round trip
----------
:func:`capture` and :func:`restore` are inverses in memory. Across JSON, decode
with :func:`~alphalab.persistence.serializer.deserialize` and pass the result
through :func:`from_primitives`::

    payload = serialize(capture(state))
    assert restore(from_primitives(deserialize(payload))) == state

Note that it is the *snapshot* that is serialized, not the state. A snapshot
tags every event with its type, without which a heterogenous event log cannot be
read back into typed events, and carries the schema version the payload was
written at.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.constants import DEFAULT_SCHEMA_VERSION
from alphalab.persistence.decode import (
    as_decimal,
    as_decimal_mapping,
    as_float,
    as_mapping,
    as_named_enum,
    as_optional_decimal,
    as_sequence,
    as_str,
    require,
    require_schema_version,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.account import Account
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.events import (
    CashDeposited,
    CashWithdrawn,
    MarketValueUpdated,
    PortfolioEvent,
    PortfolioValuationUpdated,
    PositionClosed,
    PositionIncreased,
    PositionOpened,
    PositionReduced,
)
from alphalab.portfolio.ledger import TransactionLedger
from alphalab.portfolio.position import Position
from alphalab.portfolio.transaction import Transaction
from alphalab.portfolio.types import TransactionType

__all__ = [
    "PORTFOLIO_SNAPSHOT_SCHEMA",
    "PortfolioEventRecord",
    "PortfolioSnapshot",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. See ADR-0014.
PORTFOLIO_SNAPSHOT_SCHEMA = DEFAULT_SCHEMA_VERSION

_SUBSYSTEM = "portfolio"

#: Every portfolio event type, by the tag written into a snapshot.
_EVENT_TYPES: Mapping[str, type[PortfolioEvent]] = {
    cls.__name__: cls
    for cls in (
        CashDeposited,
        CashWithdrawn,
        PositionOpened,
        PositionIncreased,
        PositionReduced,
        PositionClosed,
        MarketValueUpdated,
        PortfolioValuationUpdated,
    )
}

#: How to decode each event field that is not already a JSON primitive. Every
#: other field is a string and is checked as one.
_EVENT_FIELD_DECODERS: Mapping[str, Any] = {
    "timestamp": as_float,
    "amount": as_decimal,
    "quantity": as_decimal,
    "added_quantity": as_decimal,
    "reduced_quantity": as_decimal,
    "price": as_decimal,
    "realized_pnl": as_decimal,
    "nav": as_decimal,
    "prices": as_decimal_mapping,
}


@dataclass(frozen=True, slots=True)
class PortfolioEventRecord:
    """One portfolio event plus the tag needed to read it back as its own type."""

    event_type: str
    event: PortfolioEvent


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Complete, JSON-serializable projection of a :class:`PortfolioState`."""

    account: Account
    balances: Mapping[str, Decimal]
    reserved: Mapping[str, Decimal]
    positions: tuple[Position, ...]
    transactions: tuple[Transaction, ...]
    events: tuple[PortfolioEventRecord, ...]
    realized_pnl: Decimal
    commission_paid: Decimal
    schema_version: int = PORTFOLIO_SNAPSHOT_SCHEMA


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(state: PortfolioState) -> PortfolioSnapshot:
    """Project ``state`` into its complete serializable snapshot.

    Positions serialize as an array rather than a mapping: the key is the
    position's own ``asset_id``, so storing it twice would let a payload
    disagree with itself.
    """

    return PortfolioSnapshot(
        account=state.account,
        balances=dict(state.cash.balances),
        reserved=dict(state.cash.reserved),
        positions=tuple(state.positions.values()),
        transactions=state.ledger.transactions.to_tuple(),
        events=tuple(PortfolioEventRecord(type(e).__name__, e) for e in state.events),
        realized_pnl=state.realized_pnl,
        commission_paid=state.commission_paid,
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(snapshot: PortfolioSnapshot) -> PortfolioState:
    """Rebuild the state a snapshot was captured from.

    The positions mapping is rebuilt from the position array, keyed by each
    position's own ``asset_id``, so the restored state indexes exactly as the
    captured one did.
    """

    return PortfolioState(
        account=snapshot.account,
        cash=CashLedger(balances=dict(snapshot.balances), reserved=dict(snapshot.reserved)),
        positions={position.asset_id: position for position in snapshot.positions},
        ledger=TransactionLedger(transactions=AppendOnlyLog(snapshot.transactions)),
        events=AppendOnlyLog(record.event for record in snapshot.events),
        realized_pnl=snapshot.realized_pnl,
        commission_paid=snapshot.commission_paid,
    )


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _account(value: Any) -> Account:
    payload = as_mapping(value, "account")
    return Account(
        account_id=as_str(require(payload, "account_id"), "account.account_id"),
        base_currency=as_str(require(payload, "base_currency"), "account.base_currency"),
        name=as_str(require(payload, "name"), "account.name"),
        created_at=as_float(require(payload, "created_at"), "account.created_at"),
        status=as_str(require(payload, "status"), "account.status"),
        metadata=dict(as_mapping(require(payload, "metadata"), "account.metadata")),
    )


def _position(value: Any, index: int) -> Position:
    where = f"positions[{index}]"
    payload = as_mapping(value, where)
    return Position(
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
        average_cost=as_decimal(require(payload, "average_cost"), f"{where}.average_cost"),
        market_price=as_decimal(require(payload, "market_price"), f"{where}.market_price"),
        realized_pnl=as_decimal(require(payload, "realized_pnl"), f"{where}.realized_pnl"),
        currency=as_str(require(payload, "currency"), f"{where}.currency"),
        last_updated=as_float(require(payload, "last_updated"), f"{where}.last_updated"),
        cost_basis=as_optional_decimal(require(payload, "cost_basis"), f"{where}.cost_basis"),
    )


def _transaction(value: Any, index: int) -> Transaction:
    where = f"transactions[{index}]"
    payload = as_mapping(value, where)
    return Transaction(
        transaction_id=as_str(require(payload, "transaction_id"), f"{where}.transaction_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        account_id=as_str(require(payload, "account_id"), f"{where}.account_id"),
        type=as_named_enum(TransactionType, require(payload, "type"), f"{where}.type"),
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
        price=as_decimal(require(payload, "price"), f"{where}.price"),
        commission=as_decimal(require(payload, "commission"), f"{where}.commission"),
        currency=as_str(require(payload, "currency"), f"{where}.currency"),
        metadata=dict(as_mapping(require(payload, "metadata"), f"{where}.metadata")),
    )


def _event(value: Any, index: int) -> PortfolioEventRecord:
    where = f"events[{index}]"
    record = as_mapping(value, where)
    event_type = as_str(require(record, "event_type"), f"{where}.event_type")
    cls = _EVENT_TYPES.get(event_type)
    if cls is None:
        known = ", ".join(sorted(_EVENT_TYPES))
        raise StateDecodeError(
            f"{where}.event_type is not a portfolio event: {event_type!r}; expected one of {known}"
        )

    payload = as_mapping(require(record, "event"), f"{where}.event")
    kwargs: dict[str, Any] = {}
    for field in fields(cls):
        raw = require(payload, field.name)
        decoder = _EVENT_FIELD_DECODERS.get(field.name)
        kwargs[field.name] = (
            decoder(raw, f"{where}.{field.name}")
            if decoder is not None
            else as_str(raw, f"{where}.{field.name}")
        )
    return PortfolioEventRecord(event_type, cls(**kwargs))


def _indexed(payload: Mapping[str, Any], key: str) -> Sequence[Any]:
    return as_sequence(require(payload, key), key)


def from_primitives(payload: Mapping[str, Any]) -> PortfolioSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`PortfolioSnapshot`.

    Raises:
        StateDecodeError: If the payload is not an object, declares a schema
            version this build does not read, is missing a field, or holds a
            value of the wrong type. The message names the field.
    """

    payload = as_mapping(payload, "portfolio snapshot")
    require_schema_version(payload, PORTFOLIO_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    return PortfolioSnapshot(
        account=_account(require(payload, "account")),
        balances=as_decimal_mapping(require(payload, "balances"), "balances"),
        reserved=as_decimal_mapping(require(payload, "reserved"), "reserved"),
        positions=tuple(
            _position(item, index) for index, item in enumerate(_indexed(payload, "positions"))
        ),
        transactions=tuple(
            _transaction(item, index)
            for index, item in enumerate(_indexed(payload, "transactions"))
        ),
        events=tuple(_event(item, index) for index, item in enumerate(_indexed(payload, "events"))),
        realized_pnl=as_decimal(require(payload, "realized_pnl"), "realized_pnl"),
        commission_paid=as_decimal(require(payload, "commission_paid"), "commission_paid"),
        schema_version=PORTFOLIO_SNAPSHOT_SCHEMA,
    )
