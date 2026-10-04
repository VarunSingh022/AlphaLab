"""AlphaLab Crypto Engine.

Spot, futures, and perpetual instruments; funding rate mechanics and accrual;
per-venue trading conditions; 24/7 observation coverage; the canonical
"BASE-QUOTE" pair symbol. Deliberately does not define a new Position or
Order/Side model -- `crypto_symbol`/`open_crypto_position` bridge instruments
into the existing `alphalab.portfolio.position.Position`, the same principle
applied in `alphalab.options` and `alphalab.futures`. Knows no exchange by name:
a venue's own symbol spelling is the host application's, declared to AlphaLab as
an instrument alias (ledger BND-004).
"""

from alphalab.crypto.availability import (
    CoverageReport,
    ObservationGap,
    coverage,
    observation_gaps,
)
from alphalab.crypto.enums import InstrumentType
from alphalab.crypto.exceptions import CryptoComputationError, CryptoError, CryptoInputError
from alphalab.crypto.funding import (
    FundingAccrual,
    FundingRate,
    FundingRateHistory,
    FundingSummary,
    accrued_funding,
    annualized_funding_rate,
    average_funding_rate,
    compute_funding_payment,
    funding_instants,
)
from alphalab.crypto.instrument import CryptoInstrument, crypto_symbol, open_crypto_position
from alphalab.crypto.perpetual import (
    MaintenanceBasis,
    compute_liquidation_price,
    mark_to_market,
)
from alphalab.crypto.symbol_normalization import to_canonical_symbol
from alphalab.crypto.venue import (
    BASIS_POINT,
    FeeSchedule,
    LiquidityRole,
    PriceSource,
    VenueDispersion,
    VenueSpecification,
    cross_venue_dispersion,
    trading_fee,
)

__all__ = [
    "BASIS_POINT",
    "CoverageReport",
    "CryptoComputationError",
    "CryptoError",
    "CryptoInputError",
    "CryptoInstrument",
    "FeeSchedule",
    "FundingAccrual",
    "FundingRate",
    "FundingRateHistory",
    "FundingSummary",
    "InstrumentType",
    "LiquidityRole",
    "MaintenanceBasis",
    "ObservationGap",
    "PriceSource",
    "VenueDispersion",
    "VenueSpecification",
    "accrued_funding",
    "annualized_funding_rate",
    "average_funding_rate",
    "compute_funding_payment",
    "compute_liquidation_price",
    "coverage",
    "cross_venue_dispersion",
    "crypto_symbol",
    "funding_instants",
    "mark_to_market",
    "observation_gaps",
    "open_crypto_position",
    "to_canonical_symbol",
    "trading_fee",
]
