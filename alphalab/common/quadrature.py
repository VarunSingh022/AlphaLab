"""Adaptive Gauss-Kronrod quadrature (v4.3, ADR-0058).

:func:`integrate` is the library's one numerical integrator: globally adaptive
bisection with the 15-point Kronrod rule and its embedded 7-point Gauss rule,
as QUADPACK's ``QAG`` uses them (Piessens, de Doncker-Kapenga, Ueberhuber and
Kahaner, 1983). Each interval is integrated by both rules. Their difference,
scaled as QUADPACK scales it, estimates the interval's error. The interval with
the largest estimate is halved until the estimates sum to within the caller's
tolerance.

The tolerances are required
---------------------------
How closely to integrate decides the answer, so ``absolute_tolerance``,
``relative_tolerance`` and ``max_subdivisions`` have no defaults (ADR-0058
section 2). The result carries a
:class:`~alphalab.common.convergence.ConvergenceReport`. An integral whose
estimate does not come within ``max(absolute_tolerance, relative_tolerance *
|value|)`` after ``max_subdivisions`` halvings raises
:class:`~alphalab.common.exceptions.ConvergenceError`, never a best effort.

The error is an estimate
------------------------
The Gauss-Kronrod difference is a heuristic, not a bound: QUADPACK's scaling
makes it conservative for smooth integrands, and an integrand that hides a
feature between every node can defeat it, as it can defeat any rule that
samples. The integrand is never evaluated at an endpoint, so an integrable
endpoint singularity -- ``1 / sqrt(x)`` at 0 -- converges, by bisection toward
it, as far as double precision can place nodes near it. Floats are dense near 0
and sparse near 1, so the same singularity at 1 can need intervals narrower
than the spacing of the numbers there; the integration then stops with
:class:`~alphalab.common.exceptions.ConvergenceError` rather than evaluate the
endpoint. There is no extrapolation (QUADPACK's ``QAGS``), and an interior
singularity is best given its own endpoint by integrating the two sides
separately. Intervals are finite.

Determinism
-----------
The nodes and weights are QUADPACK's, to 33 digits, each converted to the
nearest double. Every weighted sum is a :func:`math.fsum`, the error scaling's
power ``3/2`` is ``t * sqrt(t)``, and ties between intervals go to the
leftmost. The quadrature's own arithmetic is therefore class 2 (ADR-0058
section 3): an integrand computed by correctly rounded operations integrates to
the same bits on every host. One that calls ``exp`` or ``log`` is class 3, and
so is its integral.
"""

from __future__ import annotations

import heapq
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError

__all__ = ["KRONROD_NODES", "Integral", "integrate"]

_EPSILON = sys.float_info.epsilon
_SMALLEST = sys.float_info.min

#: The 15-point Kronrod nodes on ``[-1, 1]``, the non-negative half, largest
#: first, as decimal text to 33 digits (QUADPACK ``QK15``). Odd positions are
#: the 7-point Gauss rule's nodes; the last is the centre.
KRONROD_NODES: tuple[str, ...] = (
    "0.991455371120812639206854697526329",
    "0.949107912342758524526189684047851",
    "0.864864423359769072789712788640926",
    "0.741531185599394439863864773280788",
    "0.586087235467691130294144845693013",
    "0.405845151377397166906606412076961",
    "0.207784955007898467600689403773245",
    "0.000000000000000000000000000000000",
)
#: The Kronrod weights of :data:`KRONROD_NODES`, in the same order.
KRONROD_WEIGHTS: tuple[str, ...] = (
    "0.022935322010529224963732008058970",
    "0.063092092629978553290700663189204",
    "0.104790010322250183839876322541518",
    "0.140653259715525918745189590510238",
    "0.169004726639267902826583426598550",
    "0.190350578064785409913256402421014",
    "0.204432940075298892414161999234649",
    "0.209482141084727828012999174891714",
)
#: The 7-point Gauss weights, for the nodes at odd positions of
#: :data:`KRONROD_NODES` and then the centre.
GAUSS_WEIGHTS: tuple[str, ...] = (
    "0.129484966168869693270611432679082",
    "0.279705391489276667901467771423780",
    "0.381830050505118944950369775488975",
    "0.417959183673469387755102040816327",
)

_NODES = tuple(float(text) for text in KRONROD_NODES)
_WEIGHTS = tuple(float(text) for text in KRONROD_WEIGHTS)
_GAUSS = tuple(float(text) for text in GAUSS_WEIGHTS)


@dataclass(frozen=True, slots=True)
class Integral:
    """A definite integral, its error estimate, and how it was reached.

    Attributes:
        value: The integral: the sum of every final interval's Kronrod value.
        error_estimate: The sum of their error estimates. An estimate, not a
            bound (see the module docstring).
        intervals: How many intervals the range was divided into.
        evaluations: How many times the integrand was called.
        convergence: The criterion met: the error estimate at most
            ``max(absolute_tolerance, relative_tolerance * |value|)``, which
            the report gives as its tolerance, after ``iterations`` halvings.
    """

    value: float
    error_estimate: float
    intervals: int
    evaluations: int
    convergence: ConvergenceReport


def _call(function: Callable[[float], float], x: float) -> float:
    value = function(x)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise AlphaLabValidationError(
            f"The integrand returned {value!r} at x = {x!r}; it must be a finite number at "
            "every point inside the interval."
        )
    return float(value)


def _kronrod(function: Callable[[float], float], lower: float, upper: float) -> tuple[float, float]:
    """The 15-point Kronrod value of one interval and QUADPACK's estimate of its error."""

    centre = 0.5 * (lower + upper)
    half = 0.5 * (upper - lower)
    middle = _call(function, centre)
    left = [_call(function, centre - half * node) for node in _NODES[:7]]
    right = [_call(function, centre + half * node) for node in _NODES[:7]]

    kronrod = math.fsum(
        [
            _WEIGHTS[7] * middle,
            *(w * (a + b) for w, a, b in zip(_WEIGHTS[:7], left, right, strict=True)),
        ]
    )
    gauss = math.fsum(
        [
            _GAUSS[3] * middle,
            *(_GAUSS[k] * (left[2 * k + 1] + right[2 * k + 1]) for k in range(3)),
        ]
    )
    absolute = math.fsum(
        [
            _WEIGHTS[7] * abs(middle),
            *(w * (abs(a) + abs(b)) for w, a, b in zip(_WEIGHTS[:7], left, right, strict=True)),
        ]
    )
    mean = 0.5 * kronrod
    spread = math.fsum(
        [
            _WEIGHTS[7] * abs(middle - mean),
            *(
                w * (abs(a - mean) + abs(b - mean))
                for w, a, b in zip(_WEIGHTS[:7], left, right, strict=True)
            ),
        ]
    )
    width = abs(half)
    value = kronrod * half
    absolute *= width
    spread *= width
    error = abs((kronrod - gauss) * half)
    if spread != 0.0 and error != 0.0:
        ratio = 200.0 * error / spread
        error = spread * min(1.0, ratio * math.sqrt(ratio))
    if absolute > _SMALLEST / (50.0 * _EPSILON):
        error = max(50.0 * _EPSILON * absolute, error)
    return value, error


def _resolvable(lower: float, upper: float) -> bool:
    """Whether every node of the rule falls strictly inside ``(lower, upper)``.

    An interval a few units in the last place wide puts its outermost nodes on
    its endpoints when ``centre +- half * node`` rounds; the rule would then call
    the integrand where it may be singular.
    """

    centre = 0.5 * (lower + upper)
    reach = 0.5 * (upper - lower) * _NODES[0]
    return lower < centre - reach and centre + reach < upper


def _require_tolerance(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise AlphaLabValidationError(f"{name} must be a finite number, got {value!r}.")
    if value < 0.0:
        raise AlphaLabValidationError(f"{name} must not be negative, got {value!r}.")
    return float(value)


def integrate(
    function: Callable[[float], float],
    lower: float,
    upper: float,
    *,
    absolute_tolerance: float,
    relative_tolerance: float,
    max_subdivisions: int,
) -> Integral:
    """``∫ function(x) dx`` from ``lower`` to ``upper``, by adaptive Gauss-Kronrod.

    Args:
        function: The integrand, finite at every point strictly inside the
            interval. It is never called at an endpoint.
        lower: The lower limit, finite.
        upper: The upper limit, finite. Below ``lower`` the integral is the
            negative of the one from ``upper`` to ``lower``.
        absolute_tolerance: The error estimate accepted, absolutely; zero to
            rely on the relative one alone.
        relative_tolerance: The error estimate accepted, relative to the
            integral; zero to rely on the absolute one alone. A relative
            tolerance below ``50 * eps`` cannot be met, since every interval's
            estimate is held at least that far above its rounding, and nor can
            a relative tolerance alone for an integral near zero: give an
            absolute one where the integral may vanish.
        max_subdivisions: The halvings allowed. Required (ADR-0058).

    Raises:
        AlphaLabValidationError: If a limit is not finite, a tolerance is
            negative or not finite, both tolerances are zero, the subdivision
            limit is not a positive integer, or the integrand returns a value
            that is not a finite number.
        ConvergenceError: If the error estimate is not within tolerance after
            ``max_subdivisions`` halvings.
    """

    for name, limit in (("lower", lower), ("upper", upper)):
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int | float)
            or not math.isfinite(limit)
        ):
            raise AlphaLabValidationError(
                f"The {name} limit must be a finite number, got {limit!r}."
            )
    absolute_tolerance = _require_tolerance(absolute_tolerance, "absolute_tolerance")
    relative_tolerance = _require_tolerance(relative_tolerance, "relative_tolerance")
    if absolute_tolerance == 0.0 and relative_tolerance == 0.0:
        raise AlphaLabValidationError(
            "An integral needs a positive absolute_tolerance or relative_tolerance; with both "
            "zero no estimate could ever be accepted."
        )
    if (
        isinstance(max_subdivisions, bool)
        or not isinstance(max_subdivisions, int)
        or max_subdivisions < 1
    ):
        raise AlphaLabValidationError(
            f"max_subdivisions must be a positive integer, got {max_subdivisions!r}."
        )

    method = "adaptive Gauss-Kronrod quadrature"
    criterion = "error estimate, against max(absolute_tolerance, relative_tolerance * |value|)"
    # The report's tolerance is the bound the estimate met. An exact zero with no
    # error meets a relative tolerance too, whose bound is then zero; the report,
    # which holds a positive bound, gives the relative tolerance itself.
    if lower == upper:
        report = ConvergenceReport(
            method, criterion, max(absolute_tolerance, relative_tolerance), max_subdivisions, 0, 0.0
        )
        return Integral(0.0, 0.0, 1, 0, report)
    sign = 1.0
    a, b = float(lower), float(upper)
    if a > b:
        a, b, sign = b, a, -1.0

    if not _resolvable(a, b):
        raise AlphaLabValidationError(
            f"The interval [{a!r}, {b!r}] is too narrow for the rule's nodes to fall strictly "
            "inside it in double precision."
        )
    value, error = _kronrod(function, a, b)
    # A heap keyed on (-error, left endpoint): the largest error, the leftmost of equals.
    intervals: list[tuple[float, float, float, float]] = [(-error, a, b, value)]
    halvings = 0
    while True:
        total = math.fsum(entry[3] for entry in intervals)
        estimate = math.fsum(-entry[0] for entry in intervals)
        bound = max(absolute_tolerance, relative_tolerance * abs(total))
        if estimate <= bound:
            break
        if halvings == max_subdivisions:
            raise ConvergenceError(method, bound, max_subdivisions, estimate)
        _, left, right, _ = heapq.heappop(intervals)
        middle = 0.5 * (left + right)
        if not (_resolvable(left, middle) and _resolvable(middle, right)):
            # Binary64 cannot resolve the integrand any closer to this point.
            raise ConvergenceError(
                f"{method}, halted at an interval double precision cannot halve further,",
                bound,
                max_subdivisions,
                estimate,
            )
        for start, end in ((left, middle), (middle, right)):
            part, part_error = _kronrod(function, start, end)
            heapq.heappush(intervals, (-part_error, start, end, part))
        halvings += 1

    report = ConvergenceReport(
        method,
        criterion,
        bound if bound > 0.0 else relative_tolerance,
        max_subdivisions,
        halvings,
        estimate,
    )
    return Integral(
        value=sign * total,
        error_estimate=estimate,
        intervals=len(intervals),
        evaluations=15 * (1 + 2 * halvings),
        convergence=report,
    )
