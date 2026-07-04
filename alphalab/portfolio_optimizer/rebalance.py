"""Deterministic rules triggering portfolio restructuring."""

from collections.abc import Mapping
from enum import Enum, auto


class RebalanceTrigger(Enum):
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
    """Triggers if the absolute drift of any asset exceeds the threshold."""
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
    """Triggers strictly off chronological deltas."""
    if trigger == RebalanceTrigger.DAILY:
        return (current_time - last_rebalance) >= 86400.0
    if trigger == RebalanceTrigger.WEEKLY:
        return (current_time - last_rebalance) >= (86400.0 * 7)
    if trigger == RebalanceTrigger.MONTHLY:
        return (current_time - last_rebalance) >= (86400.0 * 30)
    return False
