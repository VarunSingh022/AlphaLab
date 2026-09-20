"""AlphaLab Options Engine.

Option chains, Greeks, implied volatility, volatility surfaces, Black-Scholes
pricing, expiry resolution and multi-leg strategy simulation. Deliberately does
not define a new Position or Order/Side model -- `occ_symbol` and
`open_option_position` bridge option contracts into the existing
`alphalab.portfolio.position.Position`, and `OptionLeg` reuses
`alphalab.core.enums.Side`, avoiding the domain model fragmentation PR-034/035's
unification work had to clean up after the fact.

What v3.4 added
---------------

The inversion (`implied_volatility`), which refuses rather than fabricating when
a quote has no volatility that reproduces it; `ModelAssumptions`, so a Greek can
travel with the four things the model does not do; and `resolve_expiration`,
which says whether a contract was exercised, assigned or abandoned and moves
cash and underlying units as two separate signed quantities.
"""

from alphalab.options.chain import (
    OptionChain,
    by_expiry,
    calls,
    expiries,
    puts,
    strikes_for_expiry,
)
from alphalab.options.contract import OptionContract, occ_symbol, open_option_position
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.exceptions import OptionInputError, OptionPricingError, OptionsError
from alphalab.options.expiration import (
    ExpirationOutcome,
    ExpirationPolicy,
    ExpirationResult,
    Moneyness,
    SettlementStyle,
    intrinsic_value,
    moneyness,
    resolve_expiration,
    resolve_strategy_expiration,
)
from alphalab.options.greeks import Greeks
from alphalab.options.implied import (
    ImpliedVolatility,
    ImpliedVolatilityError,
    implied_volatility,
)
from alphalab.options.model import BLACK_SCHOLES_MERTON, ModelAssumptions, PricingModel
from alphalab.options.pricing import (
    black_scholes_greeks,
    black_scholes_price,
    black_scholes_value,
    time_to_expiry_years,
)
from alphalab.options.strategy import (
    OptionLeg,
    OptionStrategy,
    compute_payoff_at_expiry,
    compute_pnl,
    net_greeks,
    net_premium,
    signed_quantity,
)
from alphalab.options.volatility_surface import (
    SurfaceRefusal,
    VolatilitySurface,
    VolPoint,
    VolSlice,
    implied_vol_at,
    surface_expiries,
    surface_from_chain,
    surface_slice,
    term_structure,
)

__all__ = [
    "BLACK_SCHOLES_MERTON",
    "ExerciseStyle",
    "ExpirationOutcome",
    "ExpirationPolicy",
    "ExpirationResult",
    "Greeks",
    "ImpliedVolatility",
    "ImpliedVolatilityError",
    "ModelAssumptions",
    "Moneyness",
    "OptionChain",
    "OptionContract",
    "OptionInputError",
    "OptionLeg",
    "OptionPricingError",
    "OptionStrategy",
    "OptionType",
    "OptionsError",
    "PricingModel",
    "SettlementStyle",
    "SurfaceRefusal",
    "VolPoint",
    "VolSlice",
    "VolatilitySurface",
    "black_scholes_greeks",
    "black_scholes_price",
    "black_scholes_value",
    "by_expiry",
    "calls",
    "compute_payoff_at_expiry",
    "compute_pnl",
    "expiries",
    "implied_vol_at",
    "implied_volatility",
    "intrinsic_value",
    "moneyness",
    "net_greeks",
    "net_premium",
    "occ_symbol",
    "open_option_position",
    "puts",
    "resolve_expiration",
    "resolve_strategy_expiration",
    "signed_quantity",
    "strikes_for_expiry",
    "surface_expiries",
    "surface_from_chain",
    "surface_slice",
    "term_structure",
    "time_to_expiry_years",
]
