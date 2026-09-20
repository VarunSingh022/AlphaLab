"""AlphaLab Scenario Engine: one shock contract, reusable everywhere.

v3.3 adds portfolio stress testing, and the thing it adds is *one* contract
rather than a stress function per portfolio class. A :class:`Scenario` is a
named, ordered list of shocks; a :class:`ScenarioState` is a flat projection any
holder of positions can produce; :meth:`Scenario.apply` puts them together and
returns a new state, leaving the one it was given alone.

The package imports :mod:`alphalab.common` and nothing else in AlphaLab, which
is what lets a backtest book, a live book, an optimizer target and a hand-built
one all be stressed by the same object.

Where the numbers come from
---------------------------

Synthetic scenarios take their magnitude from the caller. Historical ones --
2008, 2020, 2022 -- ship as :class:`ScenarioDefinition` **contracts** that name
the window and the observations they need, and refuse to apply until a caller
supplies them from a real dataset. AlphaLab ships no market data and will not
invent a historical move; see :mod:`alphalab.scenario.library`.
"""

from alphalab.scenario.exceptions import (
    ScenarioError,
    ScenarioValidationError,
    UnsupportedShockError,
)
from alphalab.scenario.exposure import ScenarioExposure, ScenarioState, from_positions
from alphalab.scenario.library import (
    COMMODITY_SHOCK_2022,
    COVID_CRASH_2020,
    CRISIS_2008,
    HISTORICAL_SCENARIOS,
    RATES_REPRICING_2022,
    Requirement,
    ScenarioDefinition,
    commodity_shock,
    flash_crash,
    fx_shock,
    rate_shock,
    sector_shock,
)
from alphalab.scenario.scenario import Scenario, ScenarioResult, apply_all, scenario
from alphalab.scenario.shock import (
    ScopeKind,
    Shock,
    ShockKind,
    ShockScope,
    everything,
    of_assets,
    of_currency,
    of_sector,
)

__all__ = [
    "COMMODITY_SHOCK_2022",
    "COVID_CRASH_2020",
    "CRISIS_2008",
    "HISTORICAL_SCENARIOS",
    "RATES_REPRICING_2022",
    "Requirement",
    "Scenario",
    "ScenarioDefinition",
    "ScenarioError",
    "ScenarioExposure",
    "ScenarioResult",
    "ScenarioState",
    "ScenarioValidationError",
    "ScopeKind",
    "Shock",
    "ShockKind",
    "ShockScope",
    "UnsupportedShockError",
    "apply_all",
    "commodity_shock",
    "everything",
    "flash_crash",
    "from_positions",
    "fx_shock",
    "of_assets",
    "of_currency",
    "of_sector",
    "rate_shock",
    "scenario",
    "sector_shock",
]
