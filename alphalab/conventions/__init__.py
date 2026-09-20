"""Market conventions: the facts that decide what an instrument's numbers mean.

A price, a quantity and a contract count are not self-describing. The same
``150`` is 150 shares in New York, 150 lots of fifty in Mumbai, and 150
contracts on a thousand barrels in Chicago, and the difference is not visible in
the number. This package is where those differences are **declared**, so that
nothing downstream has to assume them.

Why it is a leaf
----------------

``alphalab.conventions`` imports :mod:`alphalab.common` and nothing else in
``alphalab``. That is not a style preference: ``alphalab.data`` imports
``alphalab.options``, which imports ``alphalab.portfolio``, so a convention
authority that reached into any of those could not also be used *by* them
without closing a package-level import cycle --
``tests/regression/test_import_graph_stays_acyclic.py`` measures that invariant
on every run. Being a leaf is what lets ``options``, ``futures``, ``crypto``,
``portfolio``, ``data`` and ``api`` all speak one convention vocabulary.

The one place that would have needed an edge is settlement, which counts trading
days. It takes a :class:`~alphalab.conventions.settlement.TradingDayCalendar`
-- a one-method structural protocol that
:class:`alphalab.data.calendar.MarketCalendar` already satisfies -- so the
calendar authority stays where it is and is *passed in*. The precedent is
:class:`alphalab.common.point_in_time.PointInTimeRecord`.

Nothing here ships an exchange's data
--------------------------------------

No holiday list, no tick table, no lot schedule, no venue registry. The same
position ``alphalab.data.calendar`` takes on holidays and ``alphalab.portfolio.fx``
takes on rates: the mechanism is AlphaLab's, the data is the application's, and a
table baked in here would be wrong within a year while looking authoritative.
"""

from alphalab.conventions.daycount import DayCount, day_count_days, year_fraction
from alphalab.conventions.exceptions import (
    ConventionError,
    ConventionInputError,
    ConventionViolationError,
)
from alphalab.conventions.lot import LotSpecification, lots_in, round_down_to_lot
from alphalab.conventions.market import ContractNotional, MarketConvention, contract_notional
from alphalab.conventions.rates import Compounding, compound_factor, discount_factor
from alphalab.conventions.settlement import (
    MAX_SETTLEMENT_SEARCH_DAYS,
    SettlementBasis,
    SettlementRule,
    TradingDayCalendar,
    settlement_date,
)
from alphalab.conventions.tick import (
    RoundingDirection,
    TickBand,
    TickSchedule,
    TickValue,
    is_on_tick,
    round_to_tick,
    tick_value,
)

__all__ = [
    "MAX_SETTLEMENT_SEARCH_DAYS",
    "Compounding",
    "ContractNotional",
    "ConventionError",
    "ConventionInputError",
    "ConventionViolationError",
    "DayCount",
    "LotSpecification",
    "MarketConvention",
    "RoundingDirection",
    "SettlementBasis",
    "SettlementRule",
    "TickBand",
    "TickSchedule",
    "TickValue",
    "TradingDayCalendar",
    "compound_factor",
    "contract_notional",
    "day_count_days",
    "discount_factor",
    "is_on_tick",
    "lots_in",
    "round_down_to_lot",
    "round_to_tick",
    "settlement_date",
    "tick_value",
    "year_fraction",
]
