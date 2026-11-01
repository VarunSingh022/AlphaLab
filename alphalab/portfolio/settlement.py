"""Settlement-date accounting: what a trade owes or is owed, and when it settles (ledger FUT-015).

The portfolio books trade-date: a fill moves the position and the cash at its
instant, as every fill always has. A market whose trades settle after the trade
-- T+1, T+2, a delivery days later -- moves the money on its settlement date,
and until then a sale's proceeds are a receivable, not cash a run may spend.

An instrument declares that by its record's
:attr:`~alphalab.instrument.record.InstrumentRecord.settlement` rule; the
execution path computes each of its fills' settlement instant from the rule and
the listing venue's declared calendar -- holidays and weekends included, in the
venue's own time zone -- and books it on the fill's
:class:`~alphalab.portfolio.transaction.Transaction` as metadata
(:data:`SETTLES_AT`, :data:`SETTLEMENT_CASH`). The ledger is persisted with the
run, so what is unsettled at any instant follows from it exactly, in a backtest,
a replay and a restored run alike; the :class:`PendingSettlement` index on the
portfolio state is derived from it and rebuilt on restore.

Nothing settles twice and nothing is processed: an obligation is settled once
its instant has passed, a fact of the clock rather than an event that could be
applied twice. A trade in an instrument that declares no rule settles at its
trade, as every trade did before v4.2.0, and writes no metadata -- so a run of
such instruments records exactly what it did.

* **Trade-date cash** is the cash ledger's balance.
* **Unsettled credits** are sale proceeds not yet received; **unsettled debits**
  purchase costs not yet paid -- already out of trade-date cash.
* **Settled cash** is trade-date cash less unsettled credits plus unsettled
  debits: what the account's bank balance holds.
* **Available cash** -- what buying power is -- is trade-date cash less unsettled
  credits: a purchase's cost is committed the moment it trades, a sale's
  proceeds are spendable only once they arrive.

A corporate action's payment can be amended after it is booked (FUT-034, phase
4): delayed, failed, corrected, reversed or replaced by the issuer's later word.
Each amendment is a ledger transaction of its own, naming the entitlement it
amends and the pending entries it re-dates or closes, so the original stays as
booked, the index a restore rebuilds equals the one a run kept, and
:func:`entitlements` reconciles each payment from the ledger alone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import Final

from alphalab.portfolio.transaction import Transaction
from alphalab.portfolio.types import TransactionType

__all__ = [
    "AFFECTS",
    "ENTITLEMENT",
    "PAYMENT_CHANGE",
    "SETTLEMENT_CASH",
    "SETTLES_AT",
    "CashPosition",
    "Entitlement",
    "EntitlementStatus",
    "PaymentChange",
    "PendingSettlement",
    "amend_pending",
    "entitlement_key",
    "entitlements",
    "payment_chain",
    "pending_from",
    "settle_pending",
]

#: The transaction metadata key a fill's settlement instant is booked under.
SETTLES_AT: Final = "settles_at"
#: The metadata key of the cash the fill moves, signed: positive received.
SETTLEMENT_CASH: Final = "settlement_cash"
#: The metadata key naming the entitlement an amendment amends (FUT-034):
#: :func:`entitlement_key` of the holding and the action's effective instant.
ENTITLEMENT: Final = "entitlement"
#: The metadata key naming the amendment's :class:`PaymentChange`, by name.
PAYMENT_CHANGE: Final = "payment_change"
#: The metadata key of the ledger transactions whose pending cash an amendment
#: re-dates or closes, comma-separated.
AFFECTS: Final = "affects"


class PaymentChange(Enum):
    """What an issuer's later word does to a corporate action's payment (FUT-034, phase 4)."""

    #: Not paid when declared: the payment is expected at a later instant.
    DELAYED = auto()
    #: Will not be paid: the entitlement is written off, never settled cash.
    FAILED = auto()
    #: Paid at another amount per unit held: the difference is booked.
    CORRECTED = auto()
    #: Paid, then clawed back.
    REVERSED = auto()
    #: Paid instead in another currency, at its own amount and instant.
    REPLACED = auto()


#: The changes after which nothing more may amend a payment.
_TERMINAL: Final = frozenset({PaymentChange.FAILED.name, PaymentChange.REVERSED.name})
#: The changes that close the pending cash they affect.
_CLOSING: Final = frozenset(
    {PaymentChange.FAILED.name, PaymentChange.REVERSED.name, PaymentChange.REPLACED.name}
)


def entitlement_key(holding: str, effective_at: float) -> str:
    """The key amendments name a corporate action's entitlement by: holding and instant."""

    return f"{holding}|{effective_at!r}"


class EntitlementStatus(Enum):
    """Where one currency's payment of a corporate action stands."""

    PENDING = auto()
    PAID = auto()
    FAILED = auto()
    REVERSED = auto()
    #: Moved to another currency's payment.
    REPLACED = auto()


@dataclass(frozen=True, slots=True)
class Entitlement:
    """One currency's payment of a corporate action, reconciled from the ledger (FUT-034).

    Attributes:
        holding: The holding the action exchanged.
        effective_at: The action's effective instant.
        currency: The payment's currency.
        declared: What the action booked in it at its effective instant (zero for
            a payment a replacement opened in it).
        owed: What is owed in it now, after every amendment: signed, received
            positive.
        pending: What of it is unsettled at the instant reconciled at.
        settles_at: When the unsettled part is paid, or ``None``.
        status: Where it stands.
        changes: The amendments made to it, in order.
        transaction_ids: Every ledger transaction that booked or amended it.
    """

    holding: str
    effective_at: float
    currency: str
    declared: Decimal
    owed: Decimal
    pending: Decimal
    settles_at: float | None
    status: EntitlementStatus
    changes: tuple[PaymentChange, ...]
    transaction_ids: tuple[str, ...]


_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PendingSettlement:
    """One fill whose cash and units have not settled yet.

    Attributes:
        transaction_id: The fill's ledger transaction.
        asset_id: What it traded.
        currency: What its cash is in.
        cash: The cash it moves, signed: positive received (a sale's proceeds),
            negative paid (a purchase's cost), commission included.
        quantity: The units it moves, signed: positive received.
        traded_at: The trade instant.
        settles_at: The settlement instant, by its instrument's declared rule and
            its venue's calendar.
    """

    transaction_id: str
    asset_id: str
    currency: str
    cash: Decimal
    quantity: Decimal
    traded_at: float
    settles_at: float

    def pending_at(self, at: float) -> bool:
        """Whether it is still unsettled at ``at``."""

        return self.settles_at > at


@dataclass(frozen=True, slots=True)
class CashPosition:
    """One currency's cash, trade-date and settled, at one instant.

    Attributes:
        currency: The currency.
        trade_date: The cash ledger's balance -- every fill booked at its trade.
        unsettled_credits: Proceeds traded and not yet received.
        unsettled_debits: Costs traded and not yet paid, as a positive figure.
    """

    currency: str
    trade_date: Decimal
    unsettled_credits: Decimal
    unsettled_debits: Decimal

    @property
    def settled(self) -> Decimal:
        """What has actually moved: trade-date cash undone by what has not."""

        return self.trade_date - self.unsettled_credits + self.unsettled_debits

    @property
    def available(self) -> Decimal:
        """What may be spent: trade-date cash less proceeds not yet received."""

        return self.trade_date - self.unsettled_credits


def settle_pending(
    pending: tuple[PendingSettlement, ...], at: float, new: PendingSettlement | None = None
) -> tuple[PendingSettlement, ...]:
    """``pending`` without what has settled by ``at``, and with ``new``, if it is pending.

    The one rule the index is kept by, at every fill and on every rebuild, so a
    restored index equals the one a run kept.
    """

    kept = tuple(entry for entry in pending if entry.pending_at(at))
    if new is not None and new.pending_at(at):
        kept = (*kept, new)
    return kept


#: The ledger entries a pending settlement is booked by: a fill, and an
#: exchange's cash paid after its instant (FUT-025).
_PENDING_TYPES: Final = frozenset(
    {TransactionType.BUY, TransactionType.SELL, TransactionType.EXCHANGE}
)


def _entry_of(transaction: Transaction, settles_at: float) -> PendingSettlement:
    """``transaction``'s cash, pending until ``settles_at``."""

    exchange = transaction.type is TransactionType.EXCHANGE
    cash = transaction.metadata.get(SETTLEMENT_CASH)
    return PendingSettlement(
        transaction.transaction_id,
        transaction.asset_id,
        transaction.currency,
        # An exchange's cash moves no units: its quantity is the cash.
        transaction.quantity if cash is None else Decimal(str(cash)),
        Decimal("0") if exchange else transaction.quantity,
        transaction.timestamp,
        settles_at,
    )


def amend_pending(
    pending: tuple[PendingSettlement, ...],
    amendment: Transaction,
    booked: Mapping[str, Transaction],
) -> tuple[PendingSettlement, ...]:
    """``pending`` as a payment amendment leaves it (FUT-034): re-dated, or closed.

    A delay re-dates each transaction it affects to its own settlement instant
    -- one already paid by the clock is pending again, since it was not paid --
    and a failure, a reversal or a replacement closes them: their cash never
    settles. ``booked`` holds the affected transactions, by id. The one rule a
    run and a restore keep the index by.
    """

    change = amendment.metadata.get(PAYMENT_CHANGE)
    affected = [i for i in str(amendment.metadata.get(AFFECTS, "")).split(",") if i]
    if change == PaymentChange.DELAYED.name:
        settles = float(amendment.metadata[SETTLES_AT])
        kept = tuple(entry for entry in pending if entry.transaction_id not in affected)
        return (*kept, *(_entry_of(booked[i], settles) for i in affected if i in booked))
    if change in _CLOSING:
        return tuple(entry for entry in pending if entry.transaction_id not in affected)
    return pending


def pending_from(transactions: Iterable[Transaction]) -> tuple[PendingSettlement, ...]:
    """The index a ledger implies: its fills replayed through :func:`settle_pending`, in order.

    A fill, an exchange's cash paid after its instant (FUT-025), and an amendment
    of it (FUT-034) keep it.
    """

    pending: tuple[PendingSettlement, ...] = ()
    booked: dict[str, Transaction] = {}
    for transaction in transactions:
        if transaction.type not in _PENDING_TYPES:
            continue  # only what moves cash later keeps the index, live or rebuilt
        if transaction.type is TransactionType.EXCHANGE:
            booked[transaction.transaction_id] = transaction
            if PAYMENT_CHANGE in transaction.metadata:
                pending = amend_pending(
                    settle_pending(pending, transaction.timestamp), transaction, booked
                )
                if transaction.metadata[PAYMENT_CHANGE] == PaymentChange.DELAYED.name:
                    continue  # a delay moves no cash of its own
        settles = transaction.metadata.get(SETTLES_AT)
        entry = None if settles is None else _entry_of(transaction, float(settles))
        pending = settle_pending(pending, transaction.timestamp, entry)
    return pending


def payment_chain(
    transactions: Iterable[Transaction], holding: str, effective_at: float, currency: str
) -> tuple[Transaction, ...]:
    """Every ledger transaction that booked or amended one currency's payment, in order."""

    key = entitlement_key(holding, effective_at)
    chain = []
    for transaction in transactions:
        if transaction.type is not TransactionType.EXCHANGE or transaction.currency != currency:
            continue
        named = transaction.metadata.get(ENTITLEMENT)
        if named == key or (
            named is None
            and transaction.asset_id == holding
            and transaction.timestamp == effective_at
        ):
            chain.append(transaction)
    return tuple(chain)


def entitlements(transactions: Iterable[Transaction], at: float) -> tuple[Entitlement, ...]:
    """Each corporate action's payment in each currency, reconciled at ``at`` (FUT-034).

    Read from the ledger alone, as it stood at ``at`` -- every action that moved
    cash, every amendment of it, and the pending index those imply -- so a
    payment is reconciled as of any instant, however far the run has gone
    since. Ordered by holding, effective instant and currency.
    """

    ledger = tuple(t for t in transactions if t.timestamp <= at)
    pending = pending_from(ledger)
    keys: dict[tuple[str, float, str], None] = {}
    for transaction in ledger:
        if transaction.type is not TransactionType.EXCHANGE:
            continue
        named = transaction.metadata.get(ENTITLEMENT)
        if named is None:
            keys[(transaction.asset_id, transaction.timestamp, transaction.currency)] = None
        else:
            holding, _, instant = str(named).rpartition("|")
            keys[(holding, float(instant), transaction.currency)] = None
    unsettled = {entry.transaction_id: entry for entry in pending if entry.pending_at(at)}
    found = []
    for holding, effective_at, currency in sorted(keys):
        chain = payment_chain(ledger, holding, effective_at, currency)
        changes = tuple(
            PaymentChange[t.metadata[PAYMENT_CHANGE]]
            for t in chain
            if PAYMENT_CHANGE in t.metadata and t.timestamp <= at
        )
        declared = sum((t.quantity for t in chain if ENTITLEMENT not in t.metadata), Decimal("0"))
        owed = sum((t.quantity for t in chain if t.timestamp <= at), Decimal("0"))
        waiting = [unsettled[t.transaction_id] for t in chain if t.transaction_id in unsettled]
        pending_cash = sum((entry.cash for entry in waiting), Decimal("0"))
        if PaymentChange.FAILED in changes:
            status = EntitlementStatus.FAILED
        elif PaymentChange.REVERSED in changes:
            status = EntitlementStatus.REVERSED
        elif waiting:
            status = EntitlementStatus.PENDING
        elif PaymentChange.REPLACED in changes and owed == 0:
            status = EntitlementStatus.REPLACED
        else:
            status = EntitlementStatus.PAID
        found.append(
            Entitlement(
                holding,
                effective_at,
                currency,
                declared,
                owed,
                pending_cash,
                max((entry.settles_at for entry in waiting), default=None),
                status,
                changes,
                tuple(t.transaction_id for t in chain),
            )
        )
    return tuple(found)
