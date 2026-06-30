from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.portfolio.exceptions import InsufficientFundsError

CURRENCY_QUANT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class CashLedger:
    # Mapping of currency -> total amount
    balances: Mapping[str, Decimal] = field(default_factory=dict)
    # Mapping of currency -> reserved amount (for open orders)
    reserved: Mapping[str, Decimal] = field(default_factory=dict)

    def deposit(self, amount: Decimal, currency: str) -> "CashLedger":
        new_balances = dict(self.balances)
        new_balances[currency] = new_balances.get(currency, Decimal("0.00")) + amount.quantize(
            CURRENCY_QUANT
        )
        return CashLedger(balances=new_balances, reserved=self.reserved)

    def withdraw(self, amount: Decimal, currency: str) -> "CashLedger":
        avail = self.available_cash(currency)
        if avail < amount:
            raise InsufficientFundsError(f"Cannot withdraw {amount} {currency}. Available: {avail}")
        new_balances = dict(self.balances)
        new_balances[currency] -= amount.quantize(CURRENCY_QUANT)
        return CashLedger(balances=new_balances, reserved=self.reserved)

    def reserve(self, amount: Decimal, currency: str) -> "CashLedger":
        if self.available_cash(currency) < amount:
            raise InsufficientFundsError("Insufficient funds to reserve.")
        new_reserved = dict(self.reserved)
        new_reserved[currency] = new_reserved.get(currency, Decimal("0.00")) + amount.quantize(
            CURRENCY_QUANT
        )
        return CashLedger(balances=self.balances, reserved=new_reserved)

    def release(self, amount: Decimal, currency: str) -> "CashLedger":
        current_res = self.reserved.get(currency, Decimal("0.00"))
        release_amt = min(amount, current_res).quantize(CURRENCY_QUANT)
        new_reserved = dict(self.reserved)
        new_reserved[currency] = current_res - release_amt
        return CashLedger(balances=self.balances, reserved=new_reserved)

    def balance(self, currency: str) -> Decimal:
        return self.balances.get(currency, Decimal("0.00"))

    def available_cash(self, currency: str) -> Decimal:
        return self.balance(currency) - self.reserved.get(currency, Decimal("0.00"))
