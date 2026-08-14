"""High-performance benchmark suite for the Reinforcement Learning Engine."""

import time
from decimal import Decimal

from alphalab.common import new_id
from alphalab.deep_learning import ActivationType, Sequential, create_dense_layer
from alphalab.reinforcement_learning import (
    Action,
    QTable,
    TradingEnvConfig,
    Trajectory,
    create_environment,
    policy_probabilities,
    q_update,
    reinforce_update,
    step_environment,
)


def run_benchmark() -> None:
    config = TradingEnvConfig(
        asset_id=str(new_id()),
        strategy_id="BENCH",
        trade_size=Decimal("10"),
        starting_cash=Decimal("100000"),
    )
    state = create_environment(config, timestamp=1000.0)

    N_STEPS = 2_000
    print(f"Starting Reinforcement Learning Engine Benchmark: {N_STEPS} real pipeline steps...")

    start = time.perf_counter()
    current = state
    price = Decimal("150.00")
    for i in range(N_STEPS):
        action = (Action.HOLD, Action.BUY, Action.SELL)[i % 3]
        price += Decimal("0.01") if i % 2 == 0 else Decimal("-0.01")
        result = step_environment(current, action, price, timestamp=1000.0 + i)
        current = result.state
    duration = time.perf_counter() - start
    print(
        f"  step_environment (real pipeline): {duration:.4f}s, {N_STEPS / duration:.2f} steps/sec"
    )

    q_state = ("FLAT", "FLAT")
    table = QTable()
    N_Q = 100_000
    start = time.perf_counter()
    for _ in range(N_Q):
        table = q_update(table, q_state, Action.BUY, 1.0, q_state, learning_rate=0.1, discount=0.9)
    duration = time.perf_counter() - start
    print(f"  q_update                        : {duration:.4f}s, {N_Q / duration:.2f} ops/sec")

    network = Sequential(
        layers=(
            create_dense_layer(2, 4, ActivationType.TANH, seed=1),
            create_dense_layer(4, 3, ActivationType.LINEAR, seed=2),
        )
    )
    N_POLICY = 5_000
    start = time.perf_counter()
    for _ in range(N_POLICY):
        policy_probabilities(network, (0.5, -0.3))
    duration = time.perf_counter() - start
    print(f"  policy_probabilities            : {duration:.4f}s, {N_POLICY / duration:.2f} ops/sec")

    N_REINFORCE = 1_000
    trajectory = Trajectory(states=((0.5, -0.3),), actions=(1,), rewards=(1.0,))
    start = time.perf_counter()
    for _ in range(N_REINFORCE):
        network, _ = reinforce_update(network, trajectory, learning_rate=0.01, discount=0.9)
    duration = time.perf_counter() - start
    print(
        f"  reinforce_update                : {duration:.4f}s, {N_REINFORCE / duration:.2f} ops/sec"
    )


if __name__ == "__main__":
    run_benchmark()
