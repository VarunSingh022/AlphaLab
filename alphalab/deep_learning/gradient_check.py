"""Numerical gradient checking.

Backpropagation implemented by hand, without an automatic differentiation engine,
is exactly the kind of code where a subtle sign error or transposed dimension
silently produces a plausible-looking but wrong gradient -- the network still
trains, just not correctly. Every learnable component in this package (dense
layers, convolution, LSTM gates) is verified against `numerical_gradient` in its
own tests before being trusted, the same discipline applied to verifying
Black-Scholes against a textbook reference value or matrix inversion against a
hand-computed example elsewhere in this project.
"""

from collections.abc import Callable

from alphalab.deep_learning.exceptions import DLInputError

_DEFAULT_EPSILON = 1e-5
_DEFAULT_RTOL = 1e-2
_DEFAULT_ATOL = 1e-4


def numerical_gradient(
    f: Callable[[tuple[float, ...]], float],
    params: tuple[float, ...],
    epsilon: float = _DEFAULT_EPSILON,
) -> tuple[float, ...]:
    """Estimates the gradient of a scalar-valued function via central differences.

    grad_i ~= (f(params + epsilon*e_i) - f(params - epsilon*e_i)) / (2*epsilon)

    Raises:
        DLInputError: If params is empty, or epsilon is not positive.
    """
    if not params:
        raise DLInputError("params cannot be empty.")
    if epsilon <= 0:
        raise DLInputError(f"epsilon must be positive, got {epsilon}.")

    gradients = []
    for i in range(len(params)):
        params_plus = list(params)
        params_plus[i] += epsilon
        params_minus = list(params)
        params_minus[i] -= epsilon

        gradients.append((f(tuple(params_plus)) - f(tuple(params_minus))) / (2 * epsilon))
    return tuple(gradients)


def gradients_match(
    analytical: tuple[float, ...],
    numerical: tuple[float, ...],
    rtol: float = _DEFAULT_RTOL,
    atol: float = _DEFAULT_ATOL,
) -> bool:
    """Checks two gradient vectors agree within a combined relative/absolute tolerance.

    Uses |a - n| <= atol + rtol * |n|, the standard combined tolerance form -- pure
    relative tolerance breaks down near zero, and pure absolute tolerance is too
    loose for large-magnitude gradients.

    Raises:
        DLInputError: If analytical and numerical have different lengths.
    """
    if len(analytical) != len(numerical):
        raise DLInputError(
            f"analytical has {len(analytical)} entries but numerical has {len(numerical)}."
        )
    return all(
        abs(a - n) <= atol + rtol * abs(n) for a, n in zip(analytical, numerical, strict=True)
    )
