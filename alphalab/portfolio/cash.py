from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.portfolio.exceptions import InsufficientFundsError
from alphalab.portfolio.money import ZERO_MONEY

__all__ = ["CashLedger"]


@dataclass(frozen=True, slots=True)
class CashLedger:
    """Settled cash per currency, and what is reserved against it.

    Amounts are stored exactly as given. Rounding them to each currency's minor
    unit is the producer's job -- :class:`~alphalab.portfolio.engine.PortfolioEngine`
    rounds every movement once, with the account's
    :class:`~alphalab.common.currency_units.CurrencyUnits`, before it reaches
    this ledger (see :mod:`alphalab.portfolio.money`, rule 2). Until v3.10 the
    ledger rounded again, to one cent whatever the currency, which was the
    identity for dollars and wrong for yen.
    """

    # Mapping of currency -> total amount
    balances: Mapping[str, Decimal] = field(default_factory=dict)
    # Mapping of currency -> reserved amount (for open orders)
    reserved: Mapping[str, Decimal] = field(default_factory=dict)

    def deposit(self, amount: Decimal, currency: str) -> "CashLedger":
        new_balances = dict(self.balances)
        new_balances[currency] = new_balances.get(currency, ZERO_MONEY) + amount
        return CashLedger(balances=new_balances, reserved=self.reserved)

    def withdraw(self, amount: Decimal, currency: str) -> "CashLedger":
        avail = self.available_cash(currency)
        if avail < amount:
            raise InsufficientFundsError(f"Cannot withdraw {amount} {currency}. Available: {avail}")
        new_balances = dict(self.balances)
        new_balances[currency] = new_balances.get(currency, ZERO_MONEY) - amount
        return CashLedger(balances=new_balances, reserved=self.reserved)

    def settle(self, amount: Decimal, currency: str) -> "CashLedger":
        """Move ``currency``'s balance by a signed ``amount`` the account owes or is owed.

        Unlike :meth:`withdraw`, a debit is not refused for want of cash: a
        variation margin call, a fee or a funding payment is an obligation, not
        a choice, and a loss the market imposed is owed whether or not cash
        covers it (ledger ACC-005). A balance it takes below zero is the
        deficit, recorded rather than refused -- what a margin call is raised on.
        """

        new_balances = dict(self.balances)
        new_balances[currency] = new_balances.get(currency, ZERO_MONEY) + amount
        return CashLedger(balances=new_balances, reserved=self.reserved)

    def reserve(self, amount: Decimal, currency: str) -> "CashLedger":
        if self.available_cash(currency) < amount:
            raise InsufficientFundsError("Insufficient funds to reserve.")
        new_reserved = dict(self.reserved)
        new_reserved[currency] = new_reserved.get(currency, ZERO_MONEY) + amount
        return CashLedger(balances=self.balances, reserved=new_reserved)

    def release(self, amount: Decimal, currency: str) -> "CashLedger":
        current_res = self.reserved.get(currency, ZERO_MONEY)
        release_amt = min(amount, current_res)
        new_reserved = dict(self.reserved)
        new_reserved[currency] = current_res - release_amt
        return CashLedger(balances=self.balances, reserved=new_reserved)

    def balance(self, currency: str) -> Decimal:
        return self.balances.get(currency, ZERO_MONEY)

    def available_cash(self, currency: str) -> Decimal:
        return self.balance(currency) - self.reserved.get(currency, ZERO_MONEY)
