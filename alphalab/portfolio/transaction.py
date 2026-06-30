from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from alphalab.portfolio.types import TransactionType


@dataclass(frozen=True, slots=True)
class Transaction:
    transaction_id: str
    timestamp: float
    account_id: str
    type: TransactionType
    asset_id: str
    quantity: Decimal
    price: Decimal
    commission: Decimal
    currency: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
