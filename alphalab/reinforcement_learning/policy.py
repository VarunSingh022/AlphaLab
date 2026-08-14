"""REINFORCE: Monte Carlo policy gradient.

The policy network's final layer must use `ActivationType.LINEAR` (raw logits);
softmax is applied here, not inside the network, since it's a joint function of the
whole output vector, not a per-element activation like the ones
`alphalab.deep_learning.dense` supports.

The gradient of the policy-gradient loss `-G * log(softmax(logits)[a])` with
respect to the logits has the well-known closed form `G * (probs[j] - 1{j==a})`
for each logit j -- avoiding the need to backpropagate through softmax itself. This
is verified numerically in this package's tests via
`alphalab.deep_learning.gradient_check`, the same discipline applied to every other
learnable component in this project.
"""

import math
import random
from dataclasses import dataclass

from alphalab.deep_learning.activations import softmax
from alphalab.deep_learning.dense import apply_gradients, backward_dense
from alphalab.deep_learning.network import Sequential, forward_network
from alphalab.reinforcement_learning.exceptions import RLInputError

_MIN_PROBABILITY = 1e-12


@dataclass(frozen=True, slots=True)
class Trajectory:
    """One episode's worth of (state, action, reward) transitions, in order.

    Attributes:
        states: The state vector at each timestep.
        actions: The action index taken at each timestep (0-based, indexing into
            whatever fixed action ordering the caller uses).
        rewards: The reward received after each action.
    """

    states: tuple[tuple[float, ...], ...]
    actions: tuple[int, ...]
    rewards: tuple[float, ...]

    def __post_init__(self) -> None:
        if not (len(self.states) == len(self.actions) == len(self.rewards)):
            raise RLInputError(
                f"states ({len(self.states)}), actions ({len(self.actions)}), and "
                f"rewards ({len(self.rewards)}) must have equal length."
            )
        if not self.states:
            raise RLInputError("Trajectory cannot be empty.")


def policy_probabilities(network: Sequential, state: tuple[float, ...]) -> tuple[float, ...]:
    """Runs a forward pass and returns action probabilities via softmax over the logits."""
    _, logits = forward_network(network, state)
    return softmax(logits)


def sample_action(probabilities: tuple[float, ...], rng: random.Random) -> int:
    """Samples an action index from a probability distribution using the given RNG.

    Determinism is entirely controlled by the caller's `rng` -- the same seeded
    `random.Random` instance always produces the same sequence of actions.
    """
    draw = rng.random()
    cumulative = 0.0
    for index, probability in enumerate(probabilities):
        cumulative += probability
        if draw <= cumulative:
            return index
    return len(probabilities) - 1


def discounted_returns(rewards: tuple[float, ...], discount: float) -> tuple[float, ...]:
    """Computes the discounted return G_t at every timestep, working backward from the end.

    Raises:
        RLInputError: If discount is not in [0, 1].
    """
    if not (0.0 <= discount <= 1.0):
        raise RLInputError(f"discount must be in [0, 1], got {discount}.")

    returns = [0.0] * len(rewards)
    running = 0.0
    for t in range(len(rewards) - 1, -1, -1):
        running = rewards[t] + discount * running
        returns[t] = running
    return tuple(returns)


def reinforce_update(
    network: Sequential, trajectory: Trajectory, learning_rate: float, discount: float
) -> tuple[Sequential, float]:
    """Runs one REINFORCE update over a full trajectory.

    Raises:
        RLInputError: If learning_rate is not positive.
    """
    if learning_rate <= 0:
        raise RLInputError(f"learning_rate must be positive, got {learning_rate}.")

    returns = discounted_returns(trajectory.rewards, discount)
    current_network = network
    total_loss = 0.0

    for state, action, expected_return in zip(
        trajectory.states, trajectory.actions, returns, strict=True
    ):
        caches, logits = forward_network(current_network, state)
        probabilities = softmax(logits)

        grad_logits = tuple(
            expected_return * (probabilities[j] - (1.0 if j == action else 0.0))
            for j in range(len(probabilities))
        )

        new_layers = list(current_network.layers)
        grad = grad_logits
        for i in range(len(current_network.layers) - 1, -1, -1):
            gradients = backward_dense(current_network.layers[i], caches[i], grad)
            new_layers[i] = apply_gradients(current_network.layers[i], gradients, learning_rate)
            grad = gradients.input_gradients
        current_network = Sequential(layers=tuple(new_layers))

        total_loss += -expected_return * math.log(max(probabilities[action], _MIN_PROBABILITY))

    return current_network, total_loss / len(trajectory.states)
