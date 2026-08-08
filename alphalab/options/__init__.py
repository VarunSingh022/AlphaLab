"""AlphaLab Options Engine.

Option chains, Greeks, volatility surfaces, Black-Scholes pricing, and multi-leg
strategy simulation. Deliberately does not define a new Position or Order/Side model
-- `occ_symbol`/`open_option_position` bridge option contracts into the existing
`alphalab.portfolio.position.Position`, and `OptionLeg` reuses
`alphalab.core.enums.Side`, avoiding the domain model fragmentation PR-034/035's
unification work had to clean up after the fact.
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
from alphalab.options.greeks import Greeks
from alphalab.options.pricing import black_scholes_greeks, black_scholes_price, time_to_expiry_years
from alphalab.options.strategy import (
    OptionLeg,
    OptionStrategy,
    compute_payoff_at_expiry,
    compute_pnl,
)
from alphalab.options.volatility_surface import VolatilitySurface, VolPoint, implied_vol_at

__all__ = [
    "ExerciseStyle",
    "Greeks",
    "OptionChain",
    "OptionContract",
    "OptionInputError",
    "OptionLeg",
    "OptionPricingError",
    "OptionStrategy",
    "OptionType",
    "OptionsError",
    "VolPoint",
    "VolatilitySurface",
    "black_scholes_greeks",
    "black_scholes_price",
    "by_expiry",
    "calls",
    "compute_payoff_at_expiry",
    "compute_pnl",
    "expiries",
    "implied_vol_at",
    "occ_symbol",
    "open_option_position",
    "puts",
    "strikes_for_expiry",
    "time_to_expiry_years",
]
