"""AlphaLab Reinforcement Learning Engine.

A trading environment wired into the REAL execution pipeline (risk, OMS, execution,
portfolio -- not a self-contained simulation talking only to itself), tabular
Q-learning, REINFORCE policy gradients built on `alphalab.deep_learning`, and agent
evaluation metrics.

Building this environment surfaced a real, previously-unreachable bug in
`alphalab.portfolio.engine.PortfolioEngine.apply_fill` (double-counted realized
P&L on closing trades) -- see `alphalab.reinforcement_learning.environment`'s module
docstring for the full explanation. Not fixed here; flagged clearly instead.
"""

from alphalab.reinforcement_learning.action import Action, action_to_signed_quantity
from alphalab.reinforcement_learning.environment import (
    RLAgentStrategy,
    StepResult,
    TradingEnvConfig,
    TradingEnvState,
    create_environment,
    current_position,
    step_environment,
)
from alphalab.reinforcement_learning.evaluation import (
    average_reward,
    cumulative_reward,
    max_drawdown,
    reward_volatility,
    sharpe_like_ratio,
    win_rate,
)
from alphalab.reinforcement_learning.exceptions import RLError, RLInputError
from alphalab.reinforcement_learning.policy import (
    Trajectory,
    discounted_returns,
    policy_probabilities,
    reinforce_update,
    sample_action,
)
from alphalab.reinforcement_learning.q_learning import (
    QTable,
    State,
    best_action,
    discretize_state,
    q_update,
    q_value,
)

__all__ = [
    "Action",
    "QTable",
    "RLAgentStrategy",
    "RLError",
    "RLInputError",
    "State",
    "StepResult",
    "TradingEnvConfig",
    "TradingEnvState",
    "Trajectory",
    "action_to_signed_quantity",
    "average_reward",
    "best_action",
    "create_environment",
    "cumulative_reward",
    "current_position",
    "discounted_returns",
    "discretize_state",
    "max_drawdown",
    "policy_probabilities",
    "q_update",
    "q_value",
    "reinforce_update",
    "reward_volatility",
    "sample_action",
    "sharpe_like_ratio",
    "step_environment",
    "win_rate",
]
