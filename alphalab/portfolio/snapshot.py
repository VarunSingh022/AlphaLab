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

Currency, and what version 3 carries
------------------------------------

``realized_pnl`` and ``commission_paid`` are **mappings from currency to
amount** as of v2.17, matching the per-currency accumulation the state now
holds (ADR-0035). A book that settled only in USD writes ``{"USD": "40.00"}``
where it used to write ``"40.00"``; a book that settled in two writes both, and
neither is summed into the other by a payload that cannot say what it is in.

Minor units, and what version 4 carries
---------------------------------------

v3.10 books each currency at its own minor unit (see
:mod:`alphalab.common.currency_units`), so version 4 records the units in force:
``account.currency_units`` holds the currencies the account declared outside
ISO 4217, and each position its ``minor_units``. Version 3 is **upgraded**, not
refused -- see :data:`PORTFOLIO_SCHEMA_HISTORY` for the step and the one case
it refuses.

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
from alphalab.common.currency_units import ISO_4217_MINOR_UNITS, CurrencyUnits
from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.persistence.decode import (
    as_bool,
    as_decimal,
    as_decimal_mapping,
    as_float,
    as_int,
    as_mapping,
    as_named_enum,
    as_optional_decimal,
    as_sequence,
    as_str,
    require,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.upgrade import SchemaHistory, SchemaStep, SchemaUpgradeRefused
from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.events import (
    CashConverted,
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
    "PORTFOLIO_SCHEMA_HISTORY",
    "PORTFOLIO_SNAPSHOT_SCHEMA",
    "PortfolioEventRecord",
    "PortfolioSnapshot",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. See ADR-0014, ADR-0015, ADR-0035.
#:
#: Version 2 added ``Position.opened_at``. Version 3 carries ``realized_pnl`` and
#: ``commission_paid`` as **per-currency mappings** rather than scalars. This is
#: deliberately a portfolio-local constant rather than ``DEFAULT_SCHEMA_VERSION``,
#: which it aliased until v2.6: that constant is also the version of the
#: lifecycle snapshot and of ``BaseEvent``, so bumping it would have versioned
#: every event in the system as a side effect of one field on a position.
#:
#: **Versions 1 and 2 are refused rather than migrated**, and the reason is the
#: same in both cases: no honest value can be invented for what the payload does
#: not record.
#:
#: A v1 payload does not record when a position opened, and ``last_updated`` is
#: the last mark-to-market time -- for a position marked on every event that
#: would report a holding period of roughly zero for a position held for a year.
#:
#: A v2 payload records ``realized_pnl`` as a bare number in **no currency**.
#: Reading it as ``{account.base_currency: amount}`` looks like a migration and
#: is a guess: a v2 run that settled in one currency while its account declared
#: another -- which nothing before v2.8 refused -- would have its whole P&L
#: history relabelled into a currency it was never in, silently, and every
#: figure derived from it would be confidently wrong. ADR-0014 said the version
#: field exists "so that the first schema change is a decision rather than a
#: silent misread"; refusing is the decision that cannot misread. A v2 payload
#: is still readable by a v2.16 build, which is where a caller who needs those
#: numbers converts them deliberately.
#:
#: Version 4 (v3.10) records the minor units money is booked at:
#: ``account.currency_units`` and each position's ``minor_units`` -- and which
#: positions a fill priced since the market last did (``pending_marks``), so a
#: resumed run marks exactly what an uninterrupted one would. Version 3 is
#: upgraded by :data:`PORTFOLIO_SCHEMA_HISTORY`; versions 1 and 2 are still
#: refused, for the reasons above, which that history states as its refusals.
PORTFOLIO_SNAPSHOT_SCHEMA = 4

_SUBSYSTEM = "portfolio"

#: Every portfolio event type, by the tag written into a snapshot.
_EVENT_TYPES: Mapping[str, type[PortfolioEvent]] = {
    cls.__name__: cls
    for cls in (
        CashConverted,
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
    # CashConverted (v2.17): the rate that joined two settlement currencies.
    "converted": as_decimal,
    "rate": as_decimal,
    "rate_as_of": as_float,
    "rate_derived": as_bool,
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
    realized_pnl: Mapping[str, Decimal]
    commission_paid: Mapping[str, Decimal]
    #: Sorted; see :attr:`~alphalab.portfolio.engine.PortfolioState.pending_marks`.
    pending_marks: tuple[str, ...]
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
        realized_pnl=dict(state.realized_pnl),
        commission_paid=dict(state.commission_paid),
        pending_marks=state.pending_marks,
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
        realized_pnl=CurrencyAmounts(dict(snapshot.realized_pnl)),
        commission_paid=CurrencyAmounts(dict(snapshot.commission_paid)),
        pending_marks=snapshot.pending_marks,
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
        currency_units=_currency_units(
            require(payload, "currency_units"), "account.currency_units"
        ),
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
        opened_at=_optional_float(require(payload, "opened_at"), f"{where}.opened_at"),
        minor_units=_optional_int(require(payload, "minor_units"), f"{where}.minor_units"),
    )


def _optional_int(value: Any, field_name: str) -> int | None:
    """An integer, or ``None`` for a hand-built position that left it to ISO 4217."""

    if value is None:
        return None
    return as_int(value, field_name)


def _optional_float(value: Any, field_name: str) -> float | None:
    """A float, or ``None`` for a position whose open time was never recorded."""

    if value is None:
        return None
    return as_float(value, field_name)


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


def _whole_minor_units(amount: Any, units: int) -> bool:
    try:
        value = Decimal(str(amount))
    except ArithmeticError:
        return False
    return value.is_finite() and value == value.quantize(Decimal(1).scaleb(-units))


def _v3_to_v4(payload: dict[str, Any]) -> dict[str, Any]:
    """Record the minor units a version-3 book was kept at.

    Version 3 rounded every currency to ``0.01``. That is exact for every ISO
    currency with two or more decimals, so their amounts carry over unchanged
    and each position records ISO's figure. A currency outside ISO 4217 was
    booked at a cent by the v3 writer, so the upgraded account **declares** it
    with two decimals -- what the payload already meant, not a guess. A
    currency ISO gives fewer than two decimals (JPY, KRW, CLP, ...) is exact at
    its own unit only if every amount the book holds in it is a whole number of
    that unit; a book that holds fractional yen is refused, because no v3.10
    book can represent it and rounding it here would change recorded money.
    """

    account = as_mapping(payload.get("account"), "account")
    declared = declared_units_of_v3_book(payload)

    def at_unit(currency: str, amount: Any) -> Any:
        # Whole amounts in a currency with fewer than two decimals are restated
        # at that currency's own exponent ("150.00" JPY becomes "150"): the
        # same number, without the two decimals the currency does not have.
        standard = ISO_4217_MINOR_UNITS.get(currency)
        if amount is None or standard is None or standard >= 2:
            return amount
        return str(Decimal(str(amount)).quantize(Decimal(1).scaleb(-standard)))

    upgraded = dict(payload)
    upgraded["account"] = {**account, "currency_units": declared}
    for key in ("balances", "reserved", "realized_pnl", "commission_paid"):
        upgraded[key] = {
            currency: at_unit(currency, amount) for currency, amount in payload[key].items()
        }
    upgraded["positions"] = [
        {
            **position,
            "cost_basis": at_unit(position["currency"], position["cost_basis"]),
            "realized_pnl": at_unit(position["currency"], position["realized_pnl"]),
            "minor_units": declared.get(
                position["currency"], ISO_4217_MINOR_UNITS.get(position["currency"])
            ),
        }
        for position in payload.get("positions", [])
    ]
    # v3.9 re-marked every held position on every market event, so every one is
    # pending: the resumed run's next event re-marks them all, as v3.9's would.
    upgraded["pending_marks"] = sorted(
        {str(position["asset_id"]) for position in payload.get("positions", [])}
    )
    return upgraded


def declared_units_of_v3_book(payload: Mapping[str, Any]) -> dict[str, int]:
    """The currency units a version-3 portfolio payload was kept at, or refuse.

    Shared with the pipeline snapshot's upgrade, whose configuration names the
    same account and must declare the same units. See :func:`_v3_to_v4`.

    Raises:
        SchemaUpgradeRefused: If the book holds amounts no v3.10 book can
            represent, or a currency code this build cannot name.
    """

    currencies: dict[str, list[tuple[str, Any]]] = {}

    def note(currency: Any, where: str, amount: Any) -> None:
        currencies.setdefault(as_str(currency, where), []).append((where, amount))

    for key in ("balances", "reserved", "realized_pnl", "commission_paid"):
        for currency, amount in as_mapping(payload.get(key, {}), key).items():
            note(currency, f"{key}[{currency}]", amount)
    for index, position in enumerate(as_sequence(payload.get("positions", []), "positions")):
        entry = as_mapping(position, f"positions[{index}]")
        held_in = entry.get("currency")
        note(held_in, f"positions[{index}].cost_basis", entry.get("cost_basis"))
        note(held_in, f"positions[{index}].realized_pnl", entry.get("realized_pnl"))

    declared: dict[str, int] = {}
    for currency, amounts in sorted(currencies.items()):
        standard = ISO_4217_MINOR_UNITS.get(currency)
        if standard is None:
            declared[currency] = 2
            continue
        if standard >= 2:
            continue
        inexact = [
            where
            for where, amount in amounts
            if amount is not None and not _whole_minor_units(amount, standard)
        ]
        if inexact:
            raise SchemaUpgradeRefused(
                f"This version-3 portfolio holds {currency} amounts that are not whole "
                f"{currency} minor units ({', '.join(inexact)}). ISO 4217 gives {currency} "
                f"{standard} decimal places; a v3.10 book is kept at that unit, and rounding "
                "these here would change recorded money. Read the payload with v3.9, or "
                "re-run from the source data."
            )
    try:
        CurrencyUnits(declared)
    except AlphaLabValidationError as exc:
        raise SchemaUpgradeRefused(
            f"This version-3 portfolio settles a currency this build cannot name: {exc}"
        ) from exc
    return declared


#: How every portfolio payload a release has written is read by this one.
PORTFOLIO_SCHEMA_HISTORY = SchemaHistory(
    subsystem=_SUBSYSTEM,
    current=PORTFOLIO_SNAPSHOT_SCHEMA,
    steps=(
        SchemaStep(
            1,
            "version 2 recorded each position's opened_at",
            refusal=(
                "a version-1 payload does not record when a position opened, and its "
                "last_updated is the last mark -- no honest holding period can be derived. "
                "Read it with v2.5."
            ),
        ),
        SchemaStep(
            2,
            "version 3 recorded realized P&L and commission per currency",
            refusal=(
                "a version-2 payload records realized P&L as a bare number in no currency, "
                "and choosing one for it would be a guess about money. Read it with v2.16."
            ),
        ),
        SchemaStep(
            3,
            "version 4 records the minor units money is booked at, and the pending marks",
            upgrade=_v3_to_v4,
        ),
    ),
)


def _currency_units(value: Any, where: str) -> CurrencyUnits:
    payload = as_mapping(value, where)
    try:
        return CurrencyUnits(
            {
                as_str(currency, f"{where} key"): as_int(units, f"{where}[{currency}]")
                for currency, units in payload.items()
            }
        )
    except AlphaLabValidationError as exc:
        raise StateDecodeError(f"{where} is not a valid set of currency units: {exc}") from exc


def from_primitives(payload: Mapping[str, Any]) -> PortfolioSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`PortfolioSnapshot`.

    A payload written by an earlier release is first brought to the current
    version by :data:`PORTFOLIO_SCHEMA_HISTORY`.

    Raises:
        StateDecodeError: If the payload is not an object, declares a schema
            version this build does not read, is missing a field, or holds a
            value of the wrong type. The message names the field.
        SchemaUpgradeRefused: If it declares an earlier version no honest
            upgrade exists from.
    """

    payload = as_mapping(payload, "portfolio snapshot")
    payload = PORTFOLIO_SCHEMA_HISTORY.upgrade(payload)
    positions = tuple(
        _position(item, index) for index, item in enumerate(_indexed(payload, "positions"))
    )

    return PortfolioSnapshot(
        account=_account(require(payload, "account")),
        balances=as_decimal_mapping(require(payload, "balances"), "balances"),
        reserved=as_decimal_mapping(require(payload, "reserved"), "reserved"),
        positions=positions,
        transactions=tuple(
            _transaction(item, index)
            for index, item in enumerate(_indexed(payload, "transactions"))
        ),
        events=tuple(_event(item, index) for index, item in enumerate(_indexed(payload, "events"))),
        realized_pnl=as_decimal_mapping(require(payload, "realized_pnl"), "realized_pnl"),
        commission_paid=as_decimal_mapping(require(payload, "commission_paid"), "commission_paid"),
        pending_marks=_pending_marks(payload, positions),
        schema_version=PORTFOLIO_SNAPSHOT_SCHEMA,
    )


def _pending_marks(payload: Mapping[str, Any], positions: tuple[Position, ...]) -> tuple[str, ...]:
    """The fill-priced positions awaiting a market mark: held ones, each once, sorted."""

    marks = tuple(
        as_str(item, f"pending_marks[{index}]")
        for index, item in enumerate(
            as_sequence(require(payload, "pending_marks"), "pending_marks")
        )
    )
    held = {position.asset_id for position in positions}
    if list(marks) != sorted(set(marks)) or not held.issuperset(marks):
        raise StateDecodeError(
            "pending_marks must list held positions, each once and sorted; got "
            f"{list(marks)} against positions {sorted(held)}."
        )
    return marks
