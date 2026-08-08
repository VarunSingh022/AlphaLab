"""AlphaLab Factor Library.

Reusable, stateless quantitative factor computations: Momentum, Value, Quality,
Carry, Volatility, Liquidity. Results are shaped to satisfy
`alphalab.feature_store.protocol.FeatureValueProtocol`, so they can be written into
Feature Store without Feature Store ever importing this package. Factor Library
does not register features, manage versions, or persist anything -- see
`alphalab.feature_store` for that lifecycle.
"""

from alphalab.factor_library.carry import compute_carry
from alphalab.factor_library.catalog import (
    FACTOR_CATALOG,
    FactorCategory,
    FactorSpec,
    RequiredInput,
    get_spec,
)
from alphalab.factor_library.exceptions import (
    FactorComputationError,
    FactorInputError,
    FactorLibraryError,
)
from alphalab.factor_library.inputs import FundamentalSnapshot, PriceSeries
from alphalab.factor_library.liquidity import compute_liquidity
from alphalab.factor_library.momentum import compute_momentum
from alphalab.factor_library.quality import compute_quality
from alphalab.factor_library.result import FactorResult
from alphalab.factor_library.value import compute_value
from alphalab.factor_library.volatility import compute_volatility

__all__ = [
    "FACTOR_CATALOG",
    "FactorCategory",
    "FactorComputationError",
    "FactorInputError",
    "FactorLibraryError",
    "FactorResult",
    "FactorSpec",
    "FundamentalSnapshot",
    "PriceSeries",
    "RequiredInput",
    "compute_carry",
    "compute_liquidity",
    "compute_momentum",
    "compute_quality",
    "compute_value",
    "compute_volatility",
    "get_spec",
]
