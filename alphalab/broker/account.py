"""Immutable account snapshot models."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    """Immutable representation of a broker account's financial state."""
    account_id: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    margin: Decimal
    available_funds: Decimal
    currency: str