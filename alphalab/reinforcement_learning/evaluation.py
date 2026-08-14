"""Agent evaluation: metrics over a sequence of episode or step rewards."""

from alphalab.reinforcement_learning.exceptions import RLInputError


def _validate_rewards(rewards: tuple[float, ...]) -> None:
    if not rewards:
        raise RLInputError("rewards cannot be empty.")


def cumulative_reward(rewards: tuple[float, ...]) -> float:
    """Total reward across all steps/episodes."""
    _validate_rewards(rewards)
    return sum(rewards)


def average_reward(rewards: tuple[float, ...]) -> float:
    """Mean reward per step/episode."""
    _validate_rewards(rewards)
    return sum(rewards) / len(rewards)


def reward_volatility(rewards: tuple[float, ...]) -> float:
    """Population standard deviation of rewards.

    Raises:
        RLInputError: If fewer than 2 rewards are provided.
    """
    _validate_rewards(rewards)
    if len(rewards) < 2:
        raise RLInputError("reward_volatility requires at least 2 rewards.")
    mean = average_reward(rewards)
    variance = sum((r - mean) ** 2 for r in rewards) / len(rewards)
    return float(variance**0.5)


def sharpe_like_ratio(rewards: tuple[float, ...]) -> float:
    """A Sharpe-style ratio of reward mean to reward standard deviation, unannualized.

    Not the same as a portfolio Sharpe ratio computed by
    `alphalab.analytics.metrics` -- this operates on raw per-step/episode rewards,
    not returns, and applies no annualization, since an RL step has no inherent
    calendar frequency the way a trading period does.

    Raises:
        RLInputError: If fewer than 2 rewards are provided, or reward volatility is
            zero, making the ratio undefined.
    """
    volatility = reward_volatility(rewards)
    if volatility == 0.0:
        raise RLInputError("sharpe_like_ratio is undefined when reward volatility is zero.")
    return average_reward(rewards) / volatility


def win_rate(rewards: tuple[float, ...]) -> float:
    """Fraction of steps/episodes with strictly positive reward."""
    _validate_rewards(rewards)
    return sum(1 for r in rewards if r > 0.0) / len(rewards)


def max_drawdown(rewards: tuple[float, ...]) -> float:
    """Largest peak-to-trough decline in cumulative reward over the sequence.

    Returns a non-negative value: 0.0 if cumulative reward never declines from its
    running peak.

    Raises:
        RLInputError: If rewards is empty.
    """
    _validate_rewards(rewards)
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for r in rewards:
        cumulative += r
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return -worst
