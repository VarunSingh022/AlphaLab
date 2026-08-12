"""Activation functions: forward value and derivative, for use in backpropagation."""

import math
from enum import Enum, auto


def relu(x: float) -> float:
    """Rectified linear unit: max(0, x)."""
    return max(0.0, x)


def relu_derivative(x: float) -> float:
    """Derivative of ReLU. Defined as 0 at x=0, one common convention among a few."""
    return 1.0 if x > 0.0 else 0.0


def sigmoid(x: float) -> float:
    """Numerically stable sigmoid, avoiding overflow for large-magnitude x."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def sigmoid_derivative(x: float) -> float:
    """Derivative of sigmoid: sigmoid(x) * (1 - sigmoid(x))."""
    s = sigmoid(x)
    return s * (1.0 - s)


def tanh_activation(x: float) -> float:
    """Hyperbolic tangent."""
    return math.tanh(x)


def tanh_derivative(x: float) -> float:
    """Derivative of tanh: 1 - tanh(x)^2."""
    t = math.tanh(x)
    return 1.0 - t * t


def softmax(values: tuple[float, ...]) -> tuple[float, ...]:
    """Softmax over a vector, shifted by the max value for numerical stability."""
    max_value = max(values)
    exp_values = tuple(math.exp(v - max_value) for v in values)
    total = sum(exp_values)
    return tuple(v / total for v in exp_values)


class ActivationType(Enum):
    """Which activation function a layer applies after its linear transform.

    Defined here, alongside the raw activation functions, so both
    `alphalab.deep_learning.dense` and `alphalab.deep_learning.conv1d` share one
    dispatch implementation instead of each defining their own.
    """

    LINEAR = auto()
    RELU = auto()
    SIGMOID = auto()
    TANH = auto()


def apply_activation(activation: ActivationType, z: float) -> float:
    """Dispatches to the correct activation function for the given type."""
    if activation is ActivationType.LINEAR:
        return z
    if activation is ActivationType.RELU:
        return relu(z)
    if activation is ActivationType.SIGMOID:
        return sigmoid(z)
    return tanh_activation(z)


def activation_derivative(activation: ActivationType, z: float) -> float:
    """Dispatches to the correct activation derivative for the given type."""
    if activation is ActivationType.LINEAR:
        return 1.0
    if activation is ActivationType.RELU:
        return relu_derivative(z)
    if activation is ActivationType.SIGMOID:
        return sigmoid_derivative(z)
    return tanh_derivative(z)
