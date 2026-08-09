"""AlphaLab Futures Engine.

Continuous contracts, rolls, curve analysis, and calendar spreads. Deliberately does
not define a new Position or Order/Side model -- `futures_symbol`/`open_future_position`
bridge contract months into the existing `alphalab.portfolio.position.Position`, and
`CalendarSpreadLeg` reuses `alphalab.core.enums.Side`, the same principle applied in
`alphalab.options`.
"""

from alphalab.futures.contract import FutureContract, futures_symbol, open_future_position
from alphalab.futures.curve import (
    FuturesCurve,
    FuturesCurvePoint,
    curve_slope,
    is_backwardation,
    is_contango,
    sorted_by_month,
)
from alphalab.futures.exceptions import FuturesComputationError, FuturesError, FuturesInputError
from alphalab.futures.roll import AdjustmentMethod, RollSegment, build_continuous_series
from alphalab.futures.spread import (
    CalendarSpread,
    CalendarSpreadLeg,
    compute_pnl,
    compute_spread_value,
)

__all__ = [
    "AdjustmentMethod",
    "CalendarSpread",
    "CalendarSpreadLeg",
    "FutureContract",
    "FuturesComputationError",
    "FuturesCurve",
    "FuturesCurvePoint",
    "FuturesError",
    "FuturesInputError",
    "RollSegment",
    "build_continuous_series",
    "compute_pnl",
    "compute_spread_value",
    "curve_slope",
    "futures_symbol",
    "is_backwardation",
    "is_contango",
    "open_future_position",
    "sorted_by_month",
]
