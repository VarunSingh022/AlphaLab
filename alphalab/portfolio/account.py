from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Account:
    account_id: str
    base_currency: str
    name: str
    created_at: float
    status: str = "ACTIVE"
    metadata: Mapping[str, Any] = field(default_factory=dict)
