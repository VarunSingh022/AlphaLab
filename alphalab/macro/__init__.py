"""AlphaLab Macro Engine.

Economic indicators (with point-in-time correctness), central bank policy events,
yield curves and inversion signals, real interest rates, and GDP calculations.

Scope beyond ROADMAP.md's minimal listing is deliberate: point-in-time indicator
queries (`known_as_of`) and economic surprise tracking prevent a specific, real
look-ahead bias risk in macro-driven backtests; the named 2s10s/3m10y spreads avoid
conflating two genuinely different, commonly confused recession signals.
"""

from alphalab.macro.central_bank import CentralBankEvent, rate_change_bps
from alphalab.macro.enums import Frequency, PolicyAction
from alphalab.macro.exceptions import MacroComputationError, MacroError, MacroInputError
from alphalab.macro.gdp import gdp_growth_rate, real_gdp
from alphalab.macro.indicator import IndicatorMetadata, IndicatorObservation, known_as_of, surprise
from alphalab.macro.inflation import real_interest_rate_approx, real_interest_rate_exact
from alphalab.macro.yield_curve import (
    YieldCurve,
    YieldCurvePoint,
    is_inverted,
    sorted_by_tenor,
    spread,
    three_month_ten_year_spread,
    two_year_ten_year_spread,
    yield_at_tenor,
)

__all__ = [
    "CentralBankEvent",
    "Frequency",
    "IndicatorMetadata",
    "IndicatorObservation",
    "MacroComputationError",
    "MacroError",
    "MacroInputError",
    "PolicyAction",
    "YieldCurve",
    "YieldCurvePoint",
    "gdp_growth_rate",
    "is_inverted",
    "known_as_of",
    "rate_change_bps",
    "real_gdp",
    "real_interest_rate_approx",
    "real_interest_rate_exact",
    "sorted_by_tenor",
    "spread",
    "surprise",
    "three_month_ten_year_spread",
    "two_year_ten_year_spread",
    "yield_at_tenor",
]
