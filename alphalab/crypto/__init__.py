"""AlphaLab Crypto Engine.

Spot, futures, and perpetual instruments; funding rate mechanics and accrual;
per-venue trading conditions; 24/7 observation coverage; exchange symbol
normalization. Deliberately does not define a new Position or Order/Side model --
`crypto_symbol`/`open_crypto_position` bridge instruments into the existing
`alphalab.portfolio.position.Position`, the same principle applied in
`alphalab.options` and `alphalab.futures`. Does not depend on `alphalab.marketdata`'s
exchange connectors, which are scaffold-only stubs returning hardcoded data as of
this PR, not real integrations.
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
from alphalab.crypto.perpetual import compute_liquidation_price, mark_to_market
from alphalab.crypto.symbol_normalization import (
    parse_exchange_symbol,
    to_canonical_symbol,
    to_exchange_symbol,
)
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
    "parse_exchange_symbol",
    "to_canonical_symbol",
    "to_exchange_symbol",
    "trading_fee",
]
