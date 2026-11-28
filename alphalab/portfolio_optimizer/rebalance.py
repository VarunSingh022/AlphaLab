"""Deterministic rules triggering portfolio restructuring.

**Superseded since 4.3, unchanged until 5.0** by :mod:`alphalab.rebalancing`'s
triggers (ADR-0063 section 6). No warning is raised. Each rule here assumes
what a trigger there states (AC-12):

* ``check_schedule_rebalance`` counts a day as 86,400 seconds, a week as seven
  days and a month as thirty, from timestamps read in no zone.
  :class:`alphalab.rebalancing.CalendarTrigger` fires at the first slice of each
  day, ISO week or calendar month, read in a stated zone or by a declared
  calendar's sessions.
* ``check_threshold_rebalance``'s threshold defaults to 0.05.
  :class:`alphalab.rebalancing.ThresholdTrigger` states its distance, and
  :class:`alphalab.rebalancing.DriftTrigger` measures the book's total distance.
"""

from collections.abc import Mapping
from enum import Enum, auto


class RebalanceTrigger(Enum):
    """What ``check_schedule_rebalance`` and ``PortfolioManager.rebalance`` count.

    Superseded since 4.3, unchanged until 5.0: :class:`alphalab.rebalancing.Period`
    with :class:`alphalab.rebalancing.CalendarTrigger`, or a researcher's own
    :class:`alphalab.rebalancing.Trigger`.
    """

    DAILY = auto()
    WEEKLY = auto()
    MONTHLY = auto()
    THRESHOLD = auto()
    VOLATILITY = auto()
    CUSTOM = auto()


def check_threshold_rebalance(
    current_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    threshold: float = 0.05,
) -> bool:
    """Triggers if the absolute drift of any asset exceeds the threshold.

    Superseded since 4.3, unchanged until 5.0:
    :class:`alphalab.rebalancing.ThresholdTrigger`, whose distance is stated.
    """
    all_symbols = set(current_weights.keys()) | set(target_weights.keys())
    for s in all_symbols:
        cw = current_weights.get(s, 0.0)
        tw = target_weights.get(s, 0.0)
        if abs(cw - tw) > threshold:
            return True
    return False


def check_schedule_rebalance(
    last_rebalance: float, current_time: float, trigger: RebalanceTrigger
) -> bool:
    """Triggers strictly off chronological deltas: a month is thirty days.

    Superseded since 4.3, unchanged until 5.0:
    :class:`alphalab.rebalancing.CalendarTrigger`, which reads a stated zone or a
    declared calendar's sessions.
    """
    if trigger == RebalanceTrigger.DAILY:
        return (current_time - last_rebalance) >= 86400.0
    if trigger == RebalanceTrigger.WEEKLY:
        return (current_time - last_rebalance) >= (86400.0 * 7)
    if trigger == RebalanceTrigger.MONTHLY:
        return (current_time - last_rebalance) >= (86400.0 * 30)
    return False
