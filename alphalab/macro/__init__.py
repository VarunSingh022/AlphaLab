"""AlphaLab Macro Engine.

Economic indicators (with point-in-time correctness), central bank policy events,
yield curves and inversion signals, real interest rates, GDP calculations, and
(v3.4) fixed-rate bond analytics: cash flows, accrued interest, clean and dirty
price, yield inversion, duration and convexity.

The bond surface is a **foundation**, not a fixed-income engine. It covers what
a fixed-rate bond with known coupon dates admits exactly, and deliberately
excludes credit, embedded optionality, floating coupons and curve bootstrapping
-- each of which needs a model whose choice is the researcher's. See
`alphalab.macro.bond` and ROADMAP.md.

Scope beyond ROADMAP.md's minimal listing is deliberate: point-in-time indicator
queries (`known_as_of`) and economic surprise tracking prevent a specific, real
look-ahead bias risk in macro-driven backtests; the named 2s10s/3m10y spreads avoid
conflating two genuinely different, commonly confused recession signals.
"""

from alphalab.macro.bond import (
    Bond,
    CashFlow,
    accrued_interest,
    cash_flows,
    clean_price,
    convexity,
    dirty_price,
    macaulay_duration,
    modified_duration,
    yield_from_clean_price,
)
from alphalab.macro.central_bank import CentralBankEvent, rate_change_bps
from alphalab.macro.enums import Frequency, PolicyAction
from alphalab.macro.exceptions import MacroComputationError, MacroError, MacroInputError
from alphalab.macro.gdp import gdp_growth_rate, real_gdp
from alphalab.macro.indicator import IndicatorMetadata, IndicatorObservation, known_as_of, surprise
from alphalab.macro.inflation import real_interest_rate_approx, real_interest_rate_exact
from alphalab.macro.yield_curve import (
    YieldCurve,
    YieldCurvePoint,
    discount_factor_at,
    is_inverted,
    sorted_by_tenor,
    spread,
    three_month_ten_year_spread,
    two_year_ten_year_spread,
    yield_at_tenor,
)

__all__ = [
    "Bond",
    "CashFlow",
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
    "accrued_interest",
    "cash_flows",
    "clean_price",
    "convexity",
    "dirty_price",
    "discount_factor_at",
    "gdp_growth_rate",
    "is_inverted",
    "known_as_of",
    "macaulay_duration",
    "modified_duration",
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
    "yield_from_clean_price",
]
