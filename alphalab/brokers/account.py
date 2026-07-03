"""Immutable models defining broker accounts."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Immutable snapshot of a specific broker account's balances."""

    account_id: str
    broker_id: str
    currency: str
    cash_balance: Decimal
    buying_power: Decimal
    margin: Decimal
    metadata: Mapping[str, str] = field(default_factory=dict)
