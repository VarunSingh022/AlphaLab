"""Fully-connected (dense) layer: forward pass, backward pass, and parameter update.

Every function here returns new immutable objects rather than mutating in place,
consistent with the rest of this project's pure-functional state model --
`apply_gradients` returns a new `DenseLayer`, it does not update one in place.
"""

import random
from dataclasses import dataclass, replace

from alphalab.deep_learning.activations import (
    ActivationType,
    activation_derivative,
    apply_activation,
)
from alphalab.deep_learning.exceptions import DLInputError
from alphalab.ml.linalg import Matrix


@dataclass(frozen=True, slots=True)
class DenseLayer:
    """A fully-connected layer's learnable parameters.

    Attributes:
        weights: Shape (n_inputs, n_outputs) -- weights[i][j] connects input i to
            output j.
        bias: One bias value per output.
        activation: Activation applied after the linear transform.
    """

    weights: Matrix
    bias: tuple[float, ...]
    activation: ActivationType


@dataclass(frozen=True, slots=True)
class DenseForwardCache:
    """Intermediate values from a forward pass, needed to compute the backward pass."""

    input: tuple[float, ...]
    pre_activation: tuple[float, ...]
    output: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class DenseGradients:
    """Gradients produced by a backward pass through a DenseLayer."""

    weight_gradients: Matrix
    bias_gradients: tuple[float, ...]
    input_gradients: tuple[float, ...]


def create_dense_layer(
    n_inputs: int, n_outputs: int, activation: ActivationType, seed: int = 42
) -> DenseLayer:
    """Creates a DenseLayer with deterministic, seeded weight initialization.

    Weights are not zero-initialized, unlike this project's single-layer logistic
    regression: a multi-layer network with identical initial weights across all
    neurons in a layer has a symmetry problem where every neuron in that layer
    computes the same gradient forever, so it never learns to specialize. A fixed
    seed keeps initialization fully deterministic -- the same seed always produces
    the same network -- while still breaking that symmetry. Uses a simple bounded
    uniform initialization scaled by fan-in, in the spirit of Xavier/Glorot
    initialization.

    Raises:
        DLInputError: If n_inputs or n_outputs are not positive.
    """
    if n_inputs <= 0 or n_outputs <= 0:
        raise DLInputError(f"n_inputs and n_outputs must be positive, got {n_inputs}, {n_outputs}.")

    rng = random.Random(seed)
    scale = (6.0 / (n_inputs + n_outputs)) ** 0.5
    weights = tuple(
        tuple(rng.uniform(-scale, scale) for _ in range(n_outputs)) for _ in range(n_inputs)
    )
    bias = tuple(0.0 for _ in range(n_outputs))
    return DenseLayer(weights=weights, bias=bias, activation=activation)


def forward_dense(layer: DenseLayer, x: tuple[float, ...]) -> DenseForwardCache:
    """Computes a dense layer's output for a single input sample.

    Raises:
        DLInputError: If x's length doesn't match the layer's input dimension.
    """
    n_inputs = len(layer.weights)
    if len(x) != n_inputs:
        raise DLInputError(f"Expected {n_inputs} inputs, got {len(x)}.")

    n_outputs = len(layer.bias)
    pre_activation = tuple(
        layer.bias[j] + sum(x[i] * layer.weights[i][j] for i in range(n_inputs))
        for j in range(n_outputs)
    )
    output = tuple(apply_activation(layer.activation, z) for z in pre_activation)
    return DenseForwardCache(input=x, pre_activation=pre_activation, output=output)


def backward_dense(
    layer: DenseLayer, cache: DenseForwardCache, grad_output: tuple[float, ...]
) -> DenseGradients:
    """Computes gradients for a dense layer's weights, bias, and input.

    Args:
        layer: The layer this backward pass is through.
        cache: The forward pass cache produced for the same input.
        grad_output: dLoss/dOutput, one value per output.

    Raises:
        DLInputError: If grad_output's length doesn't match the layer's output
            dimension.
    """
    n_inputs = len(layer.weights)
    n_outputs = len(layer.bias)
    if len(grad_output) != n_outputs:
        raise DLInputError(f"Expected {n_outputs} output gradients, got {len(grad_output)}.")

    grad_pre_activation = tuple(
        grad_output[j] * activation_derivative(layer.activation, cache.pre_activation[j])
        for j in range(n_outputs)
    )

    weight_gradients = tuple(
        tuple(grad_pre_activation[j] * cache.input[i] for j in range(n_outputs))
        for i in range(n_inputs)
    )
    bias_gradients = grad_pre_activation
    input_gradients = tuple(
        sum(grad_pre_activation[j] * layer.weights[i][j] for j in range(n_outputs))
        for i in range(n_inputs)
    )

    return DenseGradients(
        weight_gradients=weight_gradients,
        bias_gradients=bias_gradients,
        input_gradients=input_gradients,
    )


def apply_gradients(
    layer: DenseLayer, gradients: DenseGradients, learning_rate: float
) -> DenseLayer:
    """Returns a new DenseLayer with weights and bias updated by one SGD step.

    Raises:
        DLInputError: If learning_rate is not positive.
    """
    if learning_rate <= 0:
        raise DLInputError(f"learning_rate must be positive, got {learning_rate}.")

    n_inputs = len(layer.weights)
    n_outputs = len(layer.bias)
    new_weights = tuple(
        tuple(
            layer.weights[i][j] - learning_rate * gradients.weight_gradients[i][j]
            for j in range(n_outputs)
        )
        for i in range(n_inputs)
    )
    new_bias = tuple(
        layer.bias[j] - learning_rate * gradients.bias_gradients[j] for j in range(n_outputs)
    )
    return replace(layer, weights=new_weights, bias=new_bias)
