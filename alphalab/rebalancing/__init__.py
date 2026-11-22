"""Rebalancing: a construction rule run as a strategy (v4.3, ADR-0063 section 6, N19).

Until v4.3 construction was standalone: nothing on a run's path imported
:mod:`alphalab.portfolio_optimizer`, so a construction rule could not be
backtested as a strategy, and construction research stayed apart from the
evidence every other strategy produces (GAP-20). This package is the bridge,
above both sides, as the layering requires: ``strategy`` imports only
``common``, ``portfolio_optimizer`` imports nothing on the path, and nothing
imports this package.

* :class:`RebalancingStrategy` decides at a slice's close. When a trigger
  fires it asks a researcher's :class:`ConstructionModel` for a construction
  problem, constructs it, and states a certified answer as lot-rounded
  ``TARGET_QUANTITY`` intents. The run turns them into orders as it does any
  strategy's; the optimizer never generates an order.
* :mod:`~alphalab.rebalancing.triggers`: a calendar trigger (the first slice of
  each day, week or month, in a declared zone or by a declared calendar's
  sessions), a threshold trigger and a drift trigger. Each parameter is
  stated (AC-12).
* :mod:`~alphalab.rebalancing.capital`: what the weights are fractions of --
  a fixed amount, or the account's equity -- or a researcher's rule.

Every rebalance is recorded with its problem's and result's identities, and a
construction that is not certified trades nothing.
"""

from alphalab.rebalancing.capital import AccountEquity, CapitalRule, FixedCapital
from alphalab.rebalancing.exceptions import RebalancingInputError
from alphalab.rebalancing.strategy import (
    REBALANCING_STATE_VERSION,
    ConstructionModel,
    RebalanceExecution,
    RebalanceRecord,
    RebalanceView,
    RebalancingStrategy,
)
from alphalab.rebalancing.triggers import (
    CalendarTrigger,
    DriftTrigger,
    Period,
    RebalanceMoment,
    SessionCalendar,
    ThresholdTrigger,
    Trigger,
)

__all__ = [
    "REBALANCING_STATE_VERSION",
    "AccountEquity",
    "CalendarTrigger",
    "CapitalRule",
    "ConstructionModel",
    "DriftTrigger",
    "FixedCapital",
    "Period",
    "RebalanceExecution",
    "RebalanceMoment",
    "RebalanceRecord",
    "RebalanceView",
    "RebalancingInputError",
    "RebalancingStrategy",
    "SessionCalendar",
    "ThresholdTrigger",
    "Trigger",
]
