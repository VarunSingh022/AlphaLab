"""The canonical broker account snapshot."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ["BrokerAccount"]


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    """Immutable representation of a broker account's financial state.

    Attributes:
        account_id: The venue's account identifier.
        cash: Settled cash balance.
        equity: Total account value including open positions.
        buying_power: Notional the account may still deploy.
        margin: Margin currently posted.
        available_funds: Funds free for withdrawal or new positions.
        currency: Currency every amount above is denominated in.
        broker_id: Which broker this account is held at. Empty for a
            single-broker adapter, populated when a router tracks several.
        metadata: Venue-specific attributes that have no canonical field.
    """

    account_id: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    margin: Decimal
    available_funds: Decimal
    currency: str

    broker_id: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)
