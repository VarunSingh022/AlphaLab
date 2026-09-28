"""Immutable commission models.

A commission model returns the **exact** commission for a fill, computed in the
pinned :data:`~alphalab.common.arithmetic.ACCOUNTING_CONTEXT`. Rounding it to
money happens once, where the settlement currency is known:
:meth:`~alphalab.execution.costs.ExecutionCostModel.quote` rounds every cash
cost to the fill's currency's minor unit. Until v3.10 the percentage and
per-share models quantized to four decimal places whatever the currency, which
was a second, independent rounding of the same charge.
"""

from decimal import Decimal
from typing import Protocol

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT

_ZERO = Decimal("0")


class CommissionModel(Protocol):
    def calculate(self, fill_quantity: Decimal, fill_price: Decimal) -> Decimal: ...


class FixedCommission:
    __slots__ = ("_fee",)

    def __init__(self, fee: Decimal) -> None:
        self._fee = fee

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal) -> Decimal:
        return self._fee if fill_quantity > 0 else _ZERO


class PercentageCommission:
    __slots__ = ("_rate",)

    def __init__(self, rate: Decimal) -> None:
        self._rate = rate

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal) -> Decimal:
        trade_value = ACCOUNTING_CONTEXT.multiply(fill_quantity, fill_price)
        return ACCOUNTING_CONTEXT.multiply(trade_value, self._rate)


class PerShareCommission:
    __slots__ = ("_rate_per_share",)

    def __init__(self, rate_per_share: Decimal) -> None:
        self._rate_per_share = rate_per_share

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal) -> Decimal:
        return ACCOUNTING_CONTEXT.multiply(fill_quantity, self._rate_per_share)
