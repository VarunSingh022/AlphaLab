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
    """Immutable representation of an order sent for execution.

    ``minor_units`` is the number of decimals of ``currency``'s minor unit, which
    a simulated fill's cash costs are rounded to. ``None`` means ISO 4217's
    figure for ``currency``; the execution pipeline passes its account's, so a
    settlement currency the account declared is rounded at the declared unit.
    """

    order_id: str
    strategy_id: str
    asset_id: str
    quantity: Decimal
    price: Decimal
    side: Side
    venue: str
    currency: str
    minor_units: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.side, Side):
            raise TypeError("side must be a core Side")
