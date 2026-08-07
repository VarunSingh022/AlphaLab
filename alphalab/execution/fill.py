"""Fill models and execution instructions."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.core.enums import Side


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
    side: Side
    venue: str
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.side, Side):
            raise TypeError("side must be a core Side")
