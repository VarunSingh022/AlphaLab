"""AlphaLab Crypto Engine.

Spot, futures, and perpetual instruments; funding rate mechanics; exchange symbol
normalization. Deliberately does not define a new Position or Order/Side model --
`crypto_symbol`/`open_crypto_position` bridge instruments into the existing
`alphalab.portfolio.position.Position`, the same principle applied in
`alphalab.options` and `alphalab.futures`. Does not depend on `alphalab.marketdata`'s
exchange connectors, which are scaffold-only stubs returning hardcoded data as of
this PR, not real integrations.
"""

from alphalab.crypto.enums import InstrumentType
from alphalab.crypto.exceptions import CryptoComputationError, CryptoError, CryptoInputError
from alphalab.crypto.funding import (
    FundingRate,
    FundingRateHistory,
    annualized_funding_rate,
    average_funding_rate,
    compute_funding_payment,
)
from alphalab.crypto.instrument import CryptoInstrument, crypto_symbol, open_crypto_position
from alphalab.crypto.perpetual import compute_liquidation_price, mark_to_market
from alphalab.crypto.symbol_normalization import (
    parse_exchange_symbol,
    to_canonical_symbol,
    to_exchange_symbol,
)

__all__ = [
    "CryptoComputationError",
    "CryptoError",
    "CryptoInputError",
    "CryptoInstrument",
    "FundingRate",
    "FundingRateHistory",
    "InstrumentType",
    "annualized_funding_rate",
    "average_funding_rate",
    "compute_funding_payment",
    "compute_liquidation_price",
    "crypto_symbol",
    "mark_to_market",
    "open_crypto_position",
    "parse_exchange_symbol",
    "to_canonical_symbol",
    "to_exchange_symbol",
]
