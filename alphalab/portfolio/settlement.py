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
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from alphalab.portfolio.transaction import Transaction
from alphalab.portfolio.types import TransactionType

__all__ = [
    "SETTLEMENT_CASH",
    "SETTLES_AT",
    "CashPosition",
    "PendingSettlement",
    "pending_from",
    "settle_pending",
]

#: The transaction metadata key a fill's settlement instant is booked under.
SETTLES_AT: Final = "settles_at"
#: The metadata key of the cash the fill moves, signed: positive received.
SETTLEMENT_CASH: Final = "settlement_cash"

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


def pending_from(transactions: Iterable[Transaction]) -> tuple[PendingSettlement, ...]:
    """The index a ledger implies: its fills replayed through :func:`settle_pending`, in order."""

    pending: tuple[PendingSettlement, ...] = ()
    for transaction in transactions:
        if transaction.type not in (TransactionType.BUY, TransactionType.SELL):
            continue  # only a fill keeps the index, live or rebuilt
        settles = transaction.metadata.get(SETTLES_AT)
        entry = (
            None
            if settles is None
            else PendingSettlement(
                transaction.transaction_id,
                transaction.asset_id,
                transaction.currency,
                Decimal(str(transaction.metadata[SETTLEMENT_CASH])),
                transaction.quantity,
                transaction.timestamp,
                float(settles),
            )
        )
        pending = settle_pending(pending, transaction.timestamp, entry)
    return pending
