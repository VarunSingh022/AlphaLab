from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from alphalab.common.currency_units import STANDARD_CURRENCY_UNITS, CurrencyUnits


@dataclass(frozen=True, slots=True)
class Account:
    """Who a portfolio belongs to, and the minor units it books money at.

    Attributes:
        currency_units: The minor unit of every currency this account settles.
            ISO 4217 by default; a book that settles a currency outside ISO 4217
            (a stablecoin, a crypto asset, a precious metal) declares it here,
            for example ``CurrencyUnits({"USDT": 6})``, and a fill, deposit or
            conversion in a currency with no known minor unit is refused. See
            :mod:`alphalab.common.currency_units`.
    """

    account_id: str
    base_currency: str
    name: str
    created_at: float
    status: str = "ACTIVE"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    currency_units: CurrencyUnits = STANDARD_CURRENCY_UNITS
