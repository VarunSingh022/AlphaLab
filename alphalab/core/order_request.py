"""Canonical proposed-order DTO shared by the allocation and risk engines.

Before this module, ``alphalab.allocation.request`` and ``alphalab.risk.models``
each defined an independent ``OrderRequest`` dataclass and an independent
``OrderSide(Enum)`` (``auto()``-valued), forcing
``alphalab.runtime.execution_pipeline`` to convert a request field-by-field as it
crossed the allocation -> risk boundary. Both engines now share this one type,
whose ``side`` is the canonical :class:`alphalab.core.enums.Side`.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import Side


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """An immutable proposed order: post allocation sizing/netting, pre OMS submission.

    Attributes:
        order_id: Unique identifier for the proposed order.
        strategy_id: Owning strategy identifier, or ``""`` when the request has
            no single owner. Allocation emits ``""`` for every request it
            produces, because a netted order can represent several strategies
            and one identifier cannot describe it. Read ``contributions`` for
            attribution; this field exists for callers who construct a request
            themselves and do have one owner. Until v2.6 allocation stamped the
            fabricated ``"ALLOC-NETTED"`` here.
        contributions: Which strategies asked for this order and for how much,
            signed and pre-netting, ordered by ``strategy_id``. Empty for a
            request that did not come from allocation.
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
    contributions: tuple[StrategyContribution, ...] = field(default_factory=tuple)

    @property
    def notional_value(self) -> Decimal:
        """Absolute notional value (``quantity * price``), quantized to 4 dp."""
        return (self.quantity * self.price).quantize(Decimal("0.0001"))
