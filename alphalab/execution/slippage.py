"""Immutable slippage models.

A slippage model returns an **exact** per-unit price concession, computed in the
pinned :data:`~alphalab.common.arithmetic.ACCOUNTING_CONTEXT`. Until v3.10 the
percentage and impact models quantized the concession to four decimal places,
so a percentage slippage on an instrument priced below one cent rounded to
nothing.
"""

from decimal import Decimal
from typing import Protocol

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.core.enums import Side
from alphalab.execution.exceptions import ExecutionValidationError

_ZERO = Decimal("0")


class SlippageModel(Protocol):
    def calculate(self, fill_quantity: Decimal, fill_price: Decimal, side: Side) -> Decimal: ...


class FixedSlippage:
    __slots__ = ("_slippage_amount",)

    def __init__(self, slippage_amount: Decimal) -> None:
        self._slippage_amount = slippage_amount

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal, side: Side) -> Decimal:
        if fill_quantity <= 0:
            return _ZERO
        return self._slippage_amount


class PercentageSlippage:
    __slots__ = ("_rate",)

    def __init__(self, rate: Decimal) -> None:
        self._rate = rate

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal, side: Side) -> Decimal:
        if fill_quantity <= 0:
            return _ZERO
        return ACCOUNTING_CONTEXT.multiply(fill_price, self._rate)


class MarketImpactSlippage:
    """Concession linear in size: ``price * impact_factor * quantity / reference_quantity``.

    ``reference_quantity`` is the size at which the concession is exactly
    ``impact_factor`` of the price, and is **required**. Until v3.10 it was a
    constant ``1000`` written into the formula -- a scale that means a thousand
    shares of a mega-cap and a thousand contracts of a thin future alike, and
    was stated nowhere a caller could see it. It remains a slippage-role model
    rather than an impact one because it reads no liquidity; see
    :class:`~alphalab.execution.costs.ImpactModel`.

    Raises:
        ExecutionValidationError: If ``impact_factor`` is negative or
            ``reference_quantity`` is not positive.
    """

    __slots__ = ("_impact_factor", "_reference_quantity")

    def __init__(self, impact_factor: Decimal, reference_quantity: Decimal) -> None:
        if impact_factor < _ZERO:
            raise ExecutionValidationError(
                f"MarketImpactSlippage impact_factor is {impact_factor}; impact is not negative."
            )
        if reference_quantity <= _ZERO:
            raise ExecutionValidationError(
                f"MarketImpactSlippage reference_quantity is {reference_quantity}; the size "
                "impact is scaled against must be positive."
            )
        self._impact_factor = impact_factor
        self._reference_quantity = reference_quantity

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal, side: Side) -> Decimal:
        if fill_quantity <= 0:
            return _ZERO
        ctx = ACCOUNTING_CONTEXT
        impact = ctx.multiply(
            ctx.divide(fill_quantity, self._reference_quantity), self._impact_factor
        )
        return ctx.multiply(fill_price, impact)
