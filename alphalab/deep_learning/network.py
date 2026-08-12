"""A sequential stack of dense layers: forward pass, full backpropagation, and a
mini-batch SGD training loop.
"""

from dataclasses import dataclass

from alphalab.deep_learning.dense import (
    DenseForwardCache,
    DenseLayer,
    apply_gradients,
    backward_dense,
    forward_dense,
)
from alphalab.deep_learning.exceptions import DLInputError


@dataclass(frozen=True, slots=True)
class Sequential:
    """An ordered stack of dense layers applied one after another."""

    layers: tuple[DenseLayer, ...]


def forward_network(
    network: Sequential, x: tuple[float, ...]
) -> tuple[tuple[DenseForwardCache, ...], tuple[float, ...]]:
    """Runs a forward pass through every layer, returning each layer's cache and
    the final output."""
    caches = []
    current = x
    for layer in network.layers:
        cache = forward_dense(layer, current)
        caches.append(cache)
        current = cache.output
    return tuple(caches), current


def _mse_loss_gradient(
    prediction: tuple[float, ...], target: tuple[float, ...]
) -> tuple[float, float]:
    """Returns (loss, dLoss/dPrediction per output) for mean squared error."""
    if len(prediction) != len(target):
        raise DLInputError(f"prediction has {len(prediction)} values but target has {len(target)}.")
    n = len(prediction)
    loss = sum((p - t) ** 2 for p, t in zip(prediction, target, strict=True)) / n
    return loss, 2.0 / n


def train_step(
    network: Sequential, x: tuple[float, ...], target: tuple[float, ...], learning_rate: float
) -> tuple[Sequential, float]:
    """Runs one forward pass, backward pass, and SGD update for a single sample.

    Returns the updated network and the loss (mean squared error) for this sample.

    Raises:
        DLInputError: If prediction and target shapes mismatch, or learning_rate is
            not positive.
    """
    if learning_rate <= 0:
        raise DLInputError(f"learning_rate must be positive, got {learning_rate}.")

    caches, prediction = forward_network(network, x)
    loss, mse_scale = _mse_loss_gradient(prediction, target)
    grad_output = tuple(mse_scale * (p - t) for p, t in zip(prediction, target, strict=True))

    new_layers = list(network.layers)
    grad = grad_output
    for i in range(len(network.layers) - 1, -1, -1):
        gradients = backward_dense(network.layers[i], caches[i], grad)
        new_layers[i] = apply_gradients(network.layers[i], gradients, learning_rate)
        grad = gradients.input_gradients

    return Sequential(layers=tuple(new_layers)), loss


def train_network(
    network: Sequential,
    x: tuple[tuple[float, ...], ...],
    y: tuple[tuple[float, ...], ...],
    learning_rate: float,
    epochs: int,
) -> tuple[Sequential, tuple[float, ...]]:
    """Trains a network via full-batch-per-epoch SGD (one sample at a time, in order).

    Returns the trained network and the mean loss for each epoch, in order --
    useful for confirming loss actually decreases during training.

    Raises:
        DLInputError: If x and y have mismatched sample counts, x is empty, or
            epochs is not positive.
    """
    if not x or not y:
        raise DLInputError("x and y cannot be empty.")
    if len(x) != len(y):
        raise DLInputError(f"x has {len(x)} samples but y has {len(y)}.")
    if epochs <= 0:
        raise DLInputError(f"epochs must be positive, got {epochs}.")

    current_network = network
    epoch_losses = []
    for _ in range(epochs):
        losses = []
        for sample_x, sample_y in zip(x, y, strict=True):
            current_network, loss = train_step(current_network, sample_x, sample_y, learning_rate)
            losses.append(loss)
        epoch_losses.append(sum(losses) / len(losses))

    return current_network, tuple(epoch_losses)


def predict_network(network: Sequential, x: tuple[float, ...]) -> tuple[float, ...]:
    """Runs a forward pass and returns only the final output, discarding caches."""
    _, output = forward_network(network, x)
    return output
