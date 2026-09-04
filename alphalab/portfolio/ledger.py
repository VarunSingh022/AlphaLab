from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.portfolio.transaction import Transaction


@dataclass(frozen=True, slots=True)
class TransactionLedger:
    """Append-only ledger of every cash and position transaction."""

    transactions: AppendOnlyLog[Transaction] = field(default_factory=AppendOnlyLog)

    def append(self, transaction: Transaction) -> "TransactionLedger":
        return TransactionLedger(transactions=self.transactions.append(transaction))

    def history(self) -> AppendOnlyLog[Transaction]:
        return self.transactions

    def between(self, start_time: float, end_time: float) -> tuple[Transaction, ...]:
        return tuple(t for t in self.transactions if start_time <= t.timestamp <= end_time)

    def last(self) -> Transaction | None:
        return self.transactions[-1] if self.transactions else None

    def by_asset(self, asset_id: str) -> tuple[Transaction, ...]:
        return tuple(t for t in self.transactions if t.asset_id == asset_id)

    def by_account(self, account_id: str) -> tuple[Transaction, ...]:
        return tuple(t for t in self.transactions if t.account_id == account_id)
