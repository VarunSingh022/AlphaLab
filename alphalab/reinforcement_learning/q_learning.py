"""Tabular Q-learning.

The algorithm itself is generic over any hashable state -- `discretize_state`
provides one reasonable, trading-specific way to turn continuous position/price
information into the small discrete state space tabular Q-learning needs, but
`q_update`/`best_action` don't depend on it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.reinforcement_learning.action import Action
from alphalab.reinforcement_learning.exceptions import RLInputError

State = tuple[str, str]
"""A discretized (position_bucket, trend_bucket) state."""

_ALL_ACTIONS: tuple[Action, ...] = (Action.HOLD, Action.BUY, Action.SELL)


@dataclass(frozen=True, slots=True)
class QTable:
    """An immutable table of learned (state, action) -> value estimates."""

    values: Mapping[tuple[State, Action], float] = field(default_factory=dict)


def discretize_state(position: Decimal, price_change_pct: Decimal) -> State:
    """Buckets a continuous position and recent price change into a small discrete state.

    Position buckets: LONG (>0), FLAT (==0), SHORT (<0).
    Trend buckets: UP (>0.1%), FLAT (within +/-0.1%), DOWN (<-0.1%).
    """
    if position > Decimal("0"):
        position_bucket = "LONG"
    elif position < Decimal("0"):
        position_bucket = "SHORT"
    else:
        position_bucket = "FLAT"

    threshold = Decimal("0.001")
    if price_change_pct > threshold:
        trend_bucket = "UP"
    elif price_change_pct < -threshold:
        trend_bucket = "DOWN"
    else:
        trend_bucket = "FLAT"

    return (position_bucket, trend_bucket)


def q_value(table: QTable, state: State, action: Action) -> float:
    """Returns the current estimate for (state, action), defaulting to 0.0 if unseen."""
    return table.values.get((state, action), 0.0)


def best_action(table: QTable, state: State, actions: tuple[Action, ...] = _ALL_ACTIONS) -> Action:
    """Returns the action with the highest Q-value for a state.

    Ties are broken by `actions`' order -- the first max-valued action wins,
    deterministic given the same table and action order.

    Raises:
        RLInputError: If actions is empty.
    """
    if not actions:
        raise RLInputError("actions cannot be empty.")
    return max(actions, key=lambda a: q_value(table, state, a))


def q_update(
    table: QTable,
    state: State,
    action: Action,
    reward: float,
    next_state: State,
    learning_rate: float,
    discount: float,
    actions: tuple[Action, ...] = _ALL_ACTIONS,
) -> QTable:
    """Applies one standard Q-learning update.

    Q(s,a) <- Q(s,a) + alpha * (reward + gamma * max_a' Q(s',a') - Q(s,a))

    Raises:
        RLInputError: If learning_rate is not in (0, 1] or discount is not in [0, 1].
    """
    if not (0.0 < learning_rate <= 1.0):
        raise RLInputError(f"learning_rate must be in (0, 1], got {learning_rate}.")
    if not (0.0 <= discount <= 1.0):
        raise RLInputError(f"discount must be in [0, 1], got {discount}.")

    current = q_value(table, state, action)
    next_max = max(q_value(table, next_state, a) for a in actions)
    target = reward + discount * next_max
    updated_value = current + learning_rate * (target - current)

    new_values = dict(table.values)
    new_values[(state, action)] = updated_value
    return QTable(values=new_values)
