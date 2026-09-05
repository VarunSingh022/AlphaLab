"""The canonical broker position snapshot."""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.core.enums import AssetType

__all__ = ["BrokerPosition"]


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    """Immutable representation of an open asset position held at the broker.

    ``market_price`` and ``market_value`` are both carried because a venue
    reports both and they are not the same number: the mark for one unit, and
    the mark for the whole holding.

    Attributes:
        symbol: Instrument identifier as the venue names it.
        quantity: Signed position size; negative is short.
        average_price: Volume-weighted average entry price.
        market_value: Current mark-to-market value of the whole position.
        unrealized_pnl: Open profit at the current mark.
        realized_pnl: Profit crystallised by reductions and closes.
        account_id: Account holding the position. Empty for a single-account
            adapter.
        asset_class: Canonical instrument category.
        market_price: Current mark for one unit.
    """

    symbol: str
    quantity: Decimal
    average_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal

    account_id: str = ""
    asset_class: AssetType = AssetType.EQUITY
    market_price: Decimal = field(default=Decimal("0"))
