"""The canonical broker execution: one fill, as the venue reported it."""

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["BrokerExecution"]


@dataclass(frozen=True, slots=True)
class BrokerExecution:
    """Immutable report of a single order fill from the broker.

    Attributes:
        execution_id: AlphaLab's identifier for this fill. Deduplication keys on
            it, so it must be stable across redelivery of the same fill.
        broker_order_id: The venue order this fill belongs to.
        symbol: Instrument the fill executed.
        fill_quantity: Positive quantity executed.
        fill_price: Price the quantity executed at.
        commission: Cost charged for the fill.
        timestamp: Unix timestamp the venue reported for the fill.
        account_id: Account the fill settles into. Empty for a single-account
            adapter.
        external_id: The venue's own identifier for this fill, preserved
            verbatim so a report can be traced back to the venue's records.
            Empty when the venue supplies none.
    """

    execution_id: str
    broker_order_id: str
    symbol: str
    fill_quantity: Decimal
    fill_price: Decimal
    commission: Decimal
    timestamp: float

    account_id: str = ""
    external_id: str = ""
