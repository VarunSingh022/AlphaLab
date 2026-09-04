"""Canonical proposed-order DTO shared by the allocation and risk engines.

Before this module, ``alphalab.allocation.request`` and ``alphalab.risk.models``
each defined an independent ``OrderRequest`` dataclass and an independent
``OrderSide(Enum)`` (``auto()``-valued), forcing
``alphalab.runtime.execution_pipeline`` to convert a request field-by-field as it
crossed the allocation -> risk boundary. Both engines now share this one type,
whose ``side`` is the canonical :class:`alphalab.core.enums.Side`.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.core.enums import Side


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """An immutable proposed order: post allocation sizing/netting, pre OMS submission.

    Attributes:
        order_id: Unique identifier for the proposed order.
        strategy_id: Owning strategy identifier (``"ALLOC-NETTED"`` for a request
            produced by cross-strategy netting).
        asset_id: Asset the order targets.
        side: Canonical execution direction.
        quantity: Absolute (non-signed) order quantity.
        price: Reference price used for notional/budget checks.
        timestamp: Unix timestamp the request was produced. Defaults to ``0.0``
            for callers that do not track it.
    """

    order_id: str
    strategy_id: str
    asset_id: str
    side: Side
    quantity: Decimal
    price: Decimal
    timestamp: float = 0.0

    @property
    def notional_value(self) -> Decimal:
        """Absolute notional value (``quantity * price``), quantized to 4 dp."""
        return (self.quantity * self.price).quantize(Decimal("0.0001"))
