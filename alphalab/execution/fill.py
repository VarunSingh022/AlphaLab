"""Fill models and execution instructions."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto


class FillStatus(Enum):
    FULL_FILL = auto()
    PARTIAL_FILL = auto()
    NO_FILL = auto()
    REJECTED = auto()
    EXPIRED = auto()


@dataclass(frozen=True, slots=True)
class OrderInstruction:
    """Immutable representation of an order sent for execution."""

    order_id: str
    strategy_id: str
    asset_id: str
    quantity: Decimal
    price: Decimal
    side: str
    venue: str
    currency: str
