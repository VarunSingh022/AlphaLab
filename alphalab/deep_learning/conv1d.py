"""1D convolution: forward pass, backward pass, and parameter update.

Deliberately 1D, not 2D image convolution: this is a trading framework, and the
data that actually flows through it is price/feature sequences, not images. 1D
convolution over a sequence (as in WaveNet-style architectures for time series) is
the domain-appropriate way to serve the roadmap's "CNN"/"Sequence models" bullets,
rather than building a generic image-CNN nothing in this codebase would ever feed.
Uses "valid" convolution (no padding), stride 1.
"""

import random
from dataclasses import dataclass, replace

from alphalab.deep_learning.activations import (
    ActivationType,
    activation_derivative,
    apply_activation,
)
from alphalab.deep_learning.exceptions import DLInputError


@dataclass(frozen=True, slots=True)
class Conv1DLayer:
    """A single 1D convolution filter's learnable parameters.

    Attributes:
        kernel: The filter weights, shared across every position in the input.
        bias: A single bias term, shared across every output position.
        activation: Activation applied after the convolution.
    """

    kernel: tuple[float, ...]
    bias: float
    activation: ActivationType


@dataclass(frozen=True, slots=True)
class Conv1DForwardCache:
    """Intermediate values from a forward pass, needed for the backward pass."""

    input: tuple[float, ...]
    pre_activation: tuple[float, ...]
    output: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Conv1DGradients:
    """Gradients produced by a backward pass through a Conv1DLayer."""

    kernel_gradients: tuple[float, ...]
    bias_gradient: float
    input_gradients: tuple[float, ...]


def create_conv1d_layer(
    kernel_size: int, activation: ActivationType, seed: int = 42
) -> Conv1DLayer:
    """Creates a Conv1DLayer with deterministic, seeded weight initialization.

    Raises:
        DLInputError: If kernel_size is not positive.
    """
    if kernel_size <= 0:
        raise DLInputError(f"kernel_size must be positive, got {kernel_size}.")

    rng = random.Random(seed)
    scale = (6.0 / (kernel_size + 1)) ** 0.5
    kernel = tuple(rng.uniform(-scale, scale) for _ in range(kernel_size))
    return Conv1DLayer(kernel=kernel, bias=0.0, activation=activation)


def forward_conv1d(layer: Conv1DLayer, x: tuple[float, ...]) -> Conv1DForwardCache:
    """Computes a "valid" (no padding), stride-1 1D convolution over x.

    Raises:
        DLInputError: If x is shorter than the kernel.
    """
    kernel_size = len(layer.kernel)
    if len(x) < kernel_size:
        raise DLInputError(f"Input length {len(x)} is shorter than kernel_size {kernel_size}.")

    n_outputs = len(x) - kernel_size + 1
    pre_activation = tuple(
        layer.bias + sum(x[i + k] * layer.kernel[k] for k in range(kernel_size))
        for i in range(n_outputs)
    )
    output = tuple(apply_activation(layer.activation, z) for z in pre_activation)
    return Conv1DForwardCache(input=x, pre_activation=pre_activation, output=output)


def backward_conv1d(
    layer: Conv1DLayer, cache: Conv1DForwardCache, grad_output: tuple[float, ...]
) -> Conv1DGradients:
    """Computes gradients for a conv1d layer's kernel, bias, and input.

    The kernel is shared across every output position, so `kernel_gradients[k]`
    sums the contribution from every position that weight participated in --
    the standard weight-sharing gradient accumulation for convolution.

    Raises:
        DLInputError: If grad_output's length doesn't match the layer's output count.
    """
    kernel_size = len(layer.kernel)
    n_outputs = len(cache.pre_activation)
    if len(grad_output) != n_outputs:
        raise DLInputError(f"Expected {n_outputs} output gradients, got {len(grad_output)}.")

    grad_pre_activation = tuple(
        grad_output[i] * activation_derivative(layer.activation, cache.pre_activation[i])
        for i in range(n_outputs)
    )

    kernel_gradients = tuple(
        sum(grad_pre_activation[i] * cache.input[i + k] for i in range(n_outputs))
        for k in range(kernel_size)
    )
    bias_gradient = sum(grad_pre_activation)

    n_inputs = len(cache.input)
    input_gradients = tuple(
        sum(
            grad_pre_activation[i] * layer.kernel[j - i]
            for i in range(n_outputs)
            if 0 <= j - i < kernel_size
        )
        for j in range(n_inputs)
    )

    return Conv1DGradients(
        kernel_gradients=kernel_gradients,
        bias_gradient=bias_gradient,
        input_gradients=input_gradients,
    )


def apply_conv1d_gradients(
    layer: Conv1DLayer, gradients: Conv1DGradients, learning_rate: float
) -> Conv1DLayer:
    """Returns a new Conv1DLayer with kernel and bias updated by one SGD step.

    Raises:
        DLInputError: If learning_rate is not positive.
    """
    if learning_rate <= 0:
        raise DLInputError(f"learning_rate must be positive, got {learning_rate}.")

    new_kernel = tuple(
        layer.kernel[k] - learning_rate * gradients.kernel_gradients[k]
        for k in range(len(layer.kernel))
    )
    new_bias = layer.bias - learning_rate * gradients.bias_gradient
    return replace(layer, kernel=new_kernel, bias=new_bias)
