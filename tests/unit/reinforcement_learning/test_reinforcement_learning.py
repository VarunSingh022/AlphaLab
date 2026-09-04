"""Comprehensive tests for the Reinforcement Learning Engine: actions, the real
trading environment, Q-learning, REINFORCE, and agent evaluation."""

import math
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.common import new_id
from alphalab.deep_learning import ActivationType, Sequential, create_dense_layer, forward_network
from alphalab.deep_learning.activations import softmax
from alphalab.deep_learning.gradient_check import gradients_match, numerical_gradient
from alphalab.reinforcement_learning import (
    Action,
    QTable,
    RLInputError,
    TradingEnvConfig,
    Trajectory,
    action_to_signed_quantity,
    average_reward,
    best_action,
    create_environment,
    cumulative_reward,
    current_position,
    discounted_returns,
    discretize_state,
    max_drawdown,
    policy_probabilities,
    q_update,
    q_value,
    reinforce_update,
    reward_volatility,
    sample_action,
    sharpe_like_ratio,
    step_environment,
    win_rate,
)

# --------------------------------------------------------------------------- #
# Action
# --------------------------------------------------------------------------- #


def test_action_to_signed_quantity_hold_is_zero() -> None:
    assert action_to_signed_quantity(Action.HOLD, Decimal("10")) == Decimal("0")


def test_action_to_signed_quantity_buy_is_positive() -> None:
    assert action_to_signed_quantity(Action.BUY, Decimal("10")) == Decimal("10")


def test_action_to_signed_quantity_sell_is_negative() -> None:
    assert action_to_signed_quantity(Action.SELL, Decimal("10")) == Decimal("-10")


def test_action_to_signed_quantity_rejects_non_positive_trade_size() -> None:
    with pytest.raises(RLInputError):
        action_to_signed_quantity(Action.BUY, Decimal("0"))


# --------------------------------------------------------------------------- #
# Trading environment: real end-to-end integration, not a toy simulation
# --------------------------------------------------------------------------- #


def _new_config(trade_size: str = "10", starting_cash: str = "100000") -> TradingEnvConfig:
    return TradingEnvConfig(
        asset_id=str(new_id()),
        strategy_id="RL-TEST",
        trade_size=Decimal(trade_size),
        starting_cash=Decimal(starting_cash),
    )


def test_create_environment_starts_at_full_cash_zero_position() -> None:
    config = _new_config()
    state = create_environment(config, timestamp=1000.0)
    assert state.equity == Decimal("100000.00")
    assert current_position(state) == Decimal("0")
    assert state.step_count == 0


def test_create_environment_rejects_non_positive_trade_size() -> None:
    config = TradingEnvConfig(
        asset_id=str(new_id()),
        strategy_id="X",
        trade_size=Decimal("0"),
        starting_cash=Decimal("1000"),
    )
    with pytest.raises(RLInputError):
        create_environment(config, timestamp=0.0)


def test_hold_action_produces_no_trade() -> None:
    config = _new_config()
    state = create_environment(config, timestamp=1000.0)
    result = step_environment(state, Action.HOLD, price=Decimal("150.00"), timestamp=1001.0)
    assert result.info["fills"] == 0
    assert current_position(result.state) == Decimal("0")


def test_buy_action_executes_a_real_fill_through_the_real_pipeline() -> None:
    """Proves this is wired into risk/OMS/execution/portfolio for real, not a
    self-contained toy loop: risk_approved and fills come from the actual engines."""
    config = _new_config()
    state = create_environment(config, timestamp=1000.0)
    result = step_environment(state, Action.BUY, price=Decimal("150.00"), timestamp=1001.0)

    assert result.info["fills"] == 1
    assert result.info["risk_approved"] is True
    assert current_position(result.state) == Decimal("10.000000")
    # Buying at exactly the quoted price with zero slippage/commission is net-zero
    # to equity -- cash spent exactly offsets position value gained.
    assert result.state.equity == Decimal("100000.00")


def test_sell_action_after_buy_reduces_position() -> None:
    config = _new_config()
    state = create_environment(config, timestamp=1000.0)
    bought = step_environment(state, Action.BUY, price=Decimal("150.00"), timestamp=1001.0)
    sold = step_environment(bought.state, Action.SELL, price=Decimal("150.00"), timestamp=1002.0)
    assert current_position(sold.state) == Decimal("0")


def test_held_position_reward_reflects_price_move_via_mark_to_market() -> None:
    """The specific gap this environment fixes: the underlying pipeline only marks
    a position to market on fills, not on every tick. A HOLD after a price move
    must still produce a nonzero reward reflecting unrealized P&L."""
    config = _new_config()
    state = create_environment(config, timestamp=1000.0)
    bought = step_environment(state, Action.BUY, price=Decimal("150.00"), timestamp=1001.0)

    held = step_environment(bought.state, Action.HOLD, price=Decimal("155.00"), timestamp=1002.0)
    assert held.info["fills"] == 0
    assert held.reward == Decimal("50.00")  # (155-150) * 10 shares, unrealized
    assert held.state.equity == Decimal("100050.00")


def test_step_environment_rejects_non_positive_price() -> None:
    config = _new_config()
    state = create_environment(config, timestamp=1000.0)
    with pytest.raises(RLInputError):
        step_environment(state, Action.HOLD, price=Decimal("0"), timestamp=1001.0)


def test_step_count_increments_each_step() -> None:
    config = _new_config()
    state = create_environment(config, timestamp=1000.0)
    result = step_environment(state, Action.HOLD, price=Decimal("150.00"), timestamp=1001.0)
    assert result.state.step_count == 1


def test_environment_state_is_immutable() -> None:
    config = _new_config()
    state = create_environment(config, timestamp=1000.0)
    with pytest.raises(FrozenInstanceError):
        state.step_count = 99  # type: ignore[misc]


def test_environment_round_trip_realizes_correct_pnl() -> None:
    """A BUY -> HOLD -> SELL round trip (buy@150, mark to 155, sell@152) realizes
    exactly (152-150)*10 = +20 total P&L, with no double-count of realized P&L
    into cash. Regression guard for D1 (PortfolioEngine.apply_fill previously
    added ``+ pnl`` to the cash flow; this test asserted the buggy +40 result
    and is now updated to the correct +20).
    """
    config = _new_config()
    state = create_environment(config, timestamp=1000.0)

    bought = step_environment(state, Action.BUY, price=Decimal("150.00"), timestamp=1001.0)
    held = step_environment(bought.state, Action.HOLD, price=Decimal("155.00"), timestamp=1002.0)
    sold = step_environment(held.state, Action.SELL, price=Decimal("152.00"), timestamp=1003.0)

    total_pnl = sold.state.equity - Decimal("100000.00")
    correct_pnl = Decimal("20.00")  # (152 - 150) * 10

    assert total_pnl == correct_pnl
    # 98500 cash after the buy, + 1520 sale proceeds on the close, position flat.
    assert sold.state.equity == Decimal("100020.00")

    # Internal consistency check that IS expected to hold regardless of the bug:
    # summed step rewards must equal the total equity change, since reward is
    # defined as the equity delta at every step.
    assert bought.reward + held.reward + sold.reward == total_pnl


# --------------------------------------------------------------------------- #
# Q-learning
# --------------------------------------------------------------------------- #


def test_q_value_defaults_to_zero_for_unseen_state_action() -> None:
    table = QTable()
    assert q_value(table, ("FLAT", "FLAT"), Action.BUY) == 0.0


def test_q_update_moves_value_toward_target() -> None:
    table = QTable()
    state = ("FLAT", "FLAT")
    updated = q_update(
        table, state, Action.BUY, reward=10.0, next_state=state, learning_rate=0.5, discount=0.9
    )
    # target = 10 + 0.9*0 = 10; new = 0 + 0.5*(10-0) = 5
    assert q_value(updated, state, Action.BUY) == pytest.approx(5.0)


def test_q_update_rejects_invalid_learning_rate() -> None:
    table = QTable()
    with pytest.raises(RLInputError):
        q_update(table, ("A", "B"), Action.HOLD, 1.0, ("A", "B"), learning_rate=0.0, discount=0.9)


def test_best_action_rejects_empty_actions() -> None:
    with pytest.raises(RLInputError):
        best_action(QTable(), ("A", "B"), actions=())


def test_q_learning_converges_to_prefer_higher_reward_action() -> None:
    """A single-state bandit: BUY always yields +1, SELL always yields -1, HOLD 0.
    After enough updates, Q-values must reflect that ordering exactly."""
    state = ("FLAT", "FLAT")
    table = QTable()
    for _ in range(500):
        table = q_update(table, state, Action.BUY, 1.0, state, learning_rate=0.1, discount=0.9)
        table = q_update(table, state, Action.SELL, -1.0, state, learning_rate=0.1, discount=0.9)
        table = q_update(table, state, Action.HOLD, 0.0, state, learning_rate=0.1, discount=0.9)

    assert q_value(table, state, Action.BUY) > q_value(table, state, Action.HOLD)
    assert q_value(table, state, Action.HOLD) > q_value(table, state, Action.SELL)
    assert best_action(table, state) is Action.BUY


def test_discretize_state_buckets_position_correctly() -> None:
    assert discretize_state(Decimal("5"), Decimal("0"))[0] == "LONG"
    assert discretize_state(Decimal("-5"), Decimal("0"))[0] == "SHORT"
    assert discretize_state(Decimal("0"), Decimal("0"))[0] == "FLAT"


def test_discretize_state_buckets_trend_correctly() -> None:
    assert discretize_state(Decimal("0"), Decimal("0.01"))[1] == "UP"
    assert discretize_state(Decimal("0"), Decimal("-0.01"))[1] == "DOWN"
    assert discretize_state(Decimal("0"), Decimal("0"))[1] == "FLAT"


# --------------------------------------------------------------------------- #
# Policy / REINFORCE: gradient-checked, then verified by actual learning
# --------------------------------------------------------------------------- #


def _small_policy_network() -> Sequential:
    return Sequential(
        layers=(
            create_dense_layer(2, 4, ActivationType.TANH, seed=1),
            create_dense_layer(4, 3, ActivationType.LINEAR, seed=2),
        )
    )


def test_policy_probabilities_sum_to_one() -> None:
    network = _small_policy_network()
    probs = policy_probabilities(network, (0.5, -0.3))
    assert sum(probs) == pytest.approx(1.0)


def test_sample_action_is_deterministic_given_same_rng_seed() -> None:
    import random

    probs = (0.2, 0.3, 0.5)
    a = sample_action(probs, random.Random(7))
    b = sample_action(probs, random.Random(7))
    assert a == b


def test_discounted_returns_matches_hand_computed_value() -> None:
    rewards = (1.0, 1.0, 1.0)
    returns = discounted_returns(rewards, discount=0.5)
    # G_2 = 1; G_1 = 1 + 0.5*1 = 1.5; G_0 = 1 + 0.5*1.5 = 1.75
    assert returns == pytest.approx((1.75, 1.5, 1.0))


def test_discounted_returns_rejects_invalid_discount() -> None:
    with pytest.raises(RLInputError):
        discounted_returns((1.0,), discount=1.5)


def test_reinforce_gradient_matches_numerical_gradient() -> None:
    """The closed-form softmax-cross-entropy-with-return gradient, verified
    against numerical differentiation before trusting reinforce_update."""
    network = _small_policy_network()
    state = (0.5, -0.3)
    action = 1
    expected_return = 2.5

    _, logits = forward_network(network, state)
    probabilities = softmax(logits)
    analytical = tuple(
        expected_return * (probabilities[j] - (1.0 if j == action else 0.0)) for j in range(3)
    )

    def loss_fn(flat_logits: tuple[float, ...]) -> float:
        p = softmax(flat_logits)
        return -expected_return * math.log(max(p[action], 1e-12))

    numerical = numerical_gradient(loss_fn, logits)
    assert gradients_match(analytical, numerical)


def test_reinforce_update_shifts_probability_toward_rewarded_action() -> None:
    """End-to-end proof: training on a single-state bandit where action 1 always
    yields reward +1 must shift probability mass toward action 1."""
    network = _small_policy_network()
    state = (0.5, -0.3)
    initial_probs = policy_probabilities(network, state)

    for _ in range(300):
        trajectory = Trajectory(states=(state,), actions=(1,), rewards=(1.0,))
        network, _ = reinforce_update(network, trajectory, learning_rate=0.05, discount=0.9)

    final_probs = policy_probabilities(network, state)
    assert final_probs[1] > initial_probs[1]
    assert final_probs[1] > 0.9


def test_trajectory_rejects_mismatched_lengths() -> None:
    with pytest.raises(RLInputError):
        Trajectory(states=((1.0,),), actions=(0, 1), rewards=(1.0,))


def test_trajectory_rejects_empty() -> None:
    with pytest.raises(RLInputError):
        Trajectory(states=(), actions=(), rewards=())


def test_reinforce_update_rejects_non_positive_learning_rate() -> None:
    network = _small_policy_network()
    trajectory = Trajectory(states=((0.5, -0.3),), actions=(0,), rewards=(1.0,))
    with pytest.raises(RLInputError):
        reinforce_update(network, trajectory, learning_rate=0.0, discount=0.9)


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def test_cumulative_reward_matches_hand_computed_value() -> None:
    assert cumulative_reward((10.0, -5.0, 3.0, -2.0, 8.0)) == pytest.approx(14.0)


def test_average_reward_matches_hand_computed_value() -> None:
    assert average_reward((10.0, -5.0, 3.0, -2.0, 8.0)) == pytest.approx(2.8)


def test_win_rate_matches_hand_computed_value() -> None:
    assert win_rate((10.0, -5.0, 3.0, -2.0, 8.0)) == pytest.approx(0.6)


def test_max_drawdown_matches_hand_computed_value() -> None:
    assert max_drawdown((10.0, -5.0, 3.0, -2.0, 8.0)) == pytest.approx(5.0)


def test_max_drawdown_is_zero_for_monotonically_increasing_rewards() -> None:
    assert max_drawdown((1.0, 2.0, 3.0)) == pytest.approx(0.0)


def test_reward_volatility_requires_at_least_two_rewards() -> None:
    with pytest.raises(RLInputError):
        reward_volatility((1.0,))


def test_sharpe_like_ratio_raises_when_volatility_is_zero() -> None:
    with pytest.raises(RLInputError):
        sharpe_like_ratio((1.0, 1.0, 1.0))


def test_evaluation_metrics_reject_empty_rewards() -> None:
    with pytest.raises(RLInputError):
        cumulative_reward(())
    with pytest.raises(RLInputError):
        win_rate(())
