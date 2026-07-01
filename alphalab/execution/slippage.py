"""Immutable slippage models."""

from decimal import Decimal
from typing import Protocol


class SlippageModel(Protocol):
    def calculate(self, fill_quantity: Decimal, fill_price: Decimal, side: str) -> Decimal: ...


class FixedSlippage:
    __slots__ = ("_slippage_amount",)

    def __init__(self, slippage_amount: Decimal) -> None:
        self._slippage_amount = slippage_amount

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal, side: str) -> Decimal:
        if fill_quantity <= 0:
            return Decimal("0.00")
        return self._slippage_amount


class PercentageSlippage:
    __slots__ = ("_rate",)

    def __init__(self, rate: Decimal) -> None:
        self._rate = rate

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal, side: str) -> Decimal:
        if fill_quantity <= 0:
            return Decimal("0.00")
        return (fill_price * self._rate).quantize(Decimal("0.0001"))


class MarketImpactSlippage:
    __slots__ = ("_impact_factor",)

    def __init__(self, impact_factor: Decimal) -> None:
        self._impact_factor = impact_factor

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal, side: str) -> Decimal:
        if fill_quantity <= 0:
            return Decimal("0.00")
        # Simplified deterministic impact: quadratic to quantity scaled by factor
        impact = (fill_quantity / Decimal("1000")) * self._impact_factor
        return (fill_price * impact).quantize(Decimal("0.0001"))
