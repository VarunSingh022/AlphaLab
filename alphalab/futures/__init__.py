"""AlphaLab Futures Engine.

Contract chains, roll rules, continuous contracts, curve analysis, calendar
spreads and the margin requirement a clearing house publishes. Deliberately does
not define a new Position or Order/Side model -- `futures_symbol`/`open_future_position`
bridge contract months into the existing `alphalab.portfolio.position.Position`, and
`CalendarSpreadLeg` reuses `alphalab.core.enums.Side`, the same principle applied in
`alphalab.options`.

What v3.4 added
---------------

v1 could splice segments a caller had already chosen. v3.4 supplies the choice:
`ContractChain` is the listed months, `RollPolicy` is the rule, `roll_schedule`
is when each handover happened and why, and `continuous_segments` turns those
into the segments `build_continuous_series` has always taken. The adjustment
method stays a separate, explicit argument, so a continuous series is
reproducible from four stated things and nothing else.
"""

from alphalab.futures.chain import (
    ContractChain,
    RollEvent,
    RollPolicy,
    RollTrigger,
    SessionCalendar,
    active_contract_at,
    continuous_segments,
    roll_schedule,
)
from alphalab.futures.contract import (
    FutureContract,
    contract_tick_value,
    futures_symbol,
    open_future_position,
)
from alphalab.futures.curve import (
    SECONDS_PER_YEAR,
    CurveShape,
    FuturesCurve,
    FuturesCurvePoint,
    curve_shape,
    curve_slope,
    is_backwardation,
    is_contango,
    roll_yield,
    sorted_by_month,
)
from alphalab.futures.exceptions import FuturesComputationError, FuturesError, FuturesInputError
from alphalab.futures.margin import ContractMarginSpec, MarginPosture, position_margin
from alphalab.futures.roll import AdjustmentMethod, RollSegment, build_continuous_series
from alphalab.futures.spread import (
    CalendarSpread,
    CalendarSpreadLeg,
    compute_pnl,
    compute_spread_value,
)

__all__ = [
    "SECONDS_PER_YEAR",
    "AdjustmentMethod",
    "CalendarSpread",
    "CalendarSpreadLeg",
    "ContractChain",
    "ContractMarginSpec",
    "CurveShape",
    "FutureContract",
    "FuturesComputationError",
    "FuturesCurve",
    "FuturesCurvePoint",
    "FuturesError",
    "FuturesInputError",
    "MarginPosture",
    "RollEvent",
    "RollPolicy",
    "RollSegment",
    "RollTrigger",
    "SessionCalendar",
    "active_contract_at",
    "build_continuous_series",
    "compute_pnl",
    "compute_spread_value",
    "continuous_segments",
    "contract_tick_value",
    "curve_shape",
    "curve_slope",
    "futures_symbol",
    "is_backwardation",
    "is_contango",
    "open_future_position",
    "position_margin",
    "roll_schedule",
    "roll_yield",
    "sorted_by_month",
]
