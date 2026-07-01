"""Immutable commission models."""

from decimal import Decimal
from typing import Protocol


class CommissionModel(Protocol):
    def calculate(self, fill_quantity: Decimal, fill_price: Decimal) -> Decimal: ...


class FixedCommission:
    __slots__ = ("_fee",)

    def __init__(self, fee: Decimal) -> None:
        self._fee = fee

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal) -> Decimal:
        return self._fee if fill_quantity > 0 else Decimal("0.00")


class PercentageCommission:
    __slots__ = ("_rate",)

    def __init__(self, rate: Decimal) -> None:
        self._rate = rate

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal) -> Decimal:
        trade_value = fill_quantity * fill_price
        return (trade_value * self._rate).quantize(Decimal("0.0001"))


class PerShareCommission:
    __slots__ = ("_rate_per_share",)

    def __init__(self, rate_per_share: Decimal) -> None:
        self._rate_per_share = rate_per_share

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal) -> Decimal:
        return (fill_quantity * self._rate_per_share).quantize(Decimal("0.0001"))
