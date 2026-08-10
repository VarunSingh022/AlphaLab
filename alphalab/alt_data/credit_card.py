"""Credit card transaction panel data.

Modeled on real consumer spending panel alt-data (as used commercially by vendors
like Earnest Research and similar transaction-panel providers): year-over-year
spending growth used as a same-store-sales/revenue leading indicator ahead of
official earnings releases.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.provenance import DataProvenance


@dataclass(frozen=True, slots=True)
class CreditCardSpendingObservation:
    """A single spending panel observation for one company.

    Attributes:
        asset_id: Identifier of the company this observation concerns.
        reference_period: Unix timestamp of the period this observation describes.
        release_date: Unix timestamp this observation became available. Panel data
            typically has real aggregation lag after the reference period closes.
        spending_growth_yoy: Year-over-year spending growth, as a decimal fraction
            (0.05 for +5%).
        transaction_count_growth_yoy: Year-over-year transaction count growth, if
            reported separately from dollar spending.
        source: Vendor and reliability information.
    """

    asset_id: str
    reference_period: float
    release_date: float
    spending_growth_yoy: Decimal
    source: DataProvenance
    transaction_count_growth_yoy: Decimal | None = None

    def __post_init__(self) -> None:
        if self.release_date < self.reference_period:
            raise AltDataInputError("release_date cannot be before reference_period.")
