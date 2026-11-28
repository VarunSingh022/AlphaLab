"""Optimisation and root finding (v4.3, ADR-0058).

``common.optimize`` is the library's one authority for each method here
(ADR-0058 section 1): Brent's root finder, golden-section search, Nelder-Mead,
BFGS, L-BFGS-B for simple bounds, and central-difference gradients.

Tolerances are the caller's
---------------------------
Every method's tolerance and iteration limit are required arguments, with no
defaults: where to stop decides the answer (ADR-0058 section 2). A difference
gradient's step is required for the same reason, and so is the history
L-BFGS-B keeps. Each result carries a
:class:`~alphalab.common.convergence.ConvergenceReport`, and a method that
does not meet its criterion within its limit raises
:class:`~alphalab.common.exceptions.ConvergenceError`, never a best effort.

Constants a method states
-------------------------
Nelder-Mead's coefficients (reflection 1, expansion 2, contraction and shrink
1/2; Lagarias, Reeds, Wright and Wright, 1998), the Wolfe constants of the line
search (1e-4 and 0.9; Nocedal and Wright, *Numerical Optimization*, 2006,
section 3.1) and the line search's trial limit shape a method's path, not what
it converges to. They are stated constants of this module, as a distribution's
criteria are of its own.

Determinism
-----------
The methods' own arithmetic is ``+ - * /``, ``sqrt`` and :func:`math.fsum` in a
fixed order, and every tie is broken by position, so a method is class 2
(ADR-0058 section 3): an objective computed by correctly rounded operations
reaches the same bits on every host. An objective that calls ``exp`` or ``log``
is class 3, and so is its minimum. A function must return finite numbers; a
value that is not one is refused, naming the point that produced it.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError

__all__ = [
    "CONTRACTION",
    "CURVATURE",
    "EXPANSION",
    "GOLDEN_FRACTION",
    "LINE_SEARCH_LIMIT",
    "REFLECTION",
    "SHRINK",
    "SUFFICIENT_DECREASE",
    "Minimum",
    "Root",
    "ScalarMinimum",
    "bfgs",
    "brent_root",
    "golden_section_minimum",
    "l_bfgs_b",
    "nelder_mead",
    "numerical_gradient",
]

_EPSILON: Final = sys.float_info.epsilon

#: ``(sqrt(5) - 1) / 2``: the fraction of its bracket golden-section search keeps.
GOLDEN_FRACTION: Final = (math.sqrt(5.0) - 1.0) / 2.0
#: Nelder-Mead's reflection coefficient.
REFLECTION: Final = 1.0
#: Nelder-Mead's expansion coefficient.
EXPANSION: Final = 2.0
#: Nelder-Mead's contraction coefficient, inside and outside.
CONTRACTION: Final = 0.5
#: Nelder-Mead's shrink coefficient.
SHRINK: Final = 0.5
#: The Wolfe condition of sufficient decrease: ``f(x + a p) <= f(x) + c1 a g'p``.
SUFFICIENT_DECREASE: Final = 1e-4
#: The strong Wolfe condition of curvature: ``|g(x + a p)'p| <= c2 |g'p|``.
CURVATURE: Final = 0.9
#: Trial steps a line search may take before it reports that none was found.
LINE_SEARCH_LIMIT: Final = 60

ScalarFunction = Callable[[float], float]
Objective = Callable[[Sequence[float]], float]
Gradient = Callable[[Sequence[float]], Sequence[float]]


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Root:
    """A root of a scalar function, and the bracket that holds it.

    Attributes:
        x: The root found: the end of the final bracket where the function is
            smaller in magnitude.
        value: The function there.
        bracket: The final bracket, ``(low, high)``; the function changes sign
            across it, or is zero at ``x``.
        evaluations: Calls of the function.
        convergence: The criterion met.
    """

    x: float
    value: float
    bracket: tuple[float, float]
    evaluations: int
    convergence: ConvergenceReport


@dataclass(frozen=True, slots=True)
class ScalarMinimum:
    """A minimum of a scalar function within a bracket.

    Attributes:
        x: The best point found.
        value: The function there.
        bracket: The final bracket, ``(low, high)``.
        evaluations: Calls of the function.
        convergence: The criterion met.
    """

    x: float
    value: float
    bracket: tuple[float, float]
    evaluations: int
    convergence: ConvergenceReport


@dataclass(frozen=True, slots=True)
class Minimum:
    """A minimum of a function of several variables.

    Attributes:
        x: The point found.
        value: The objective there.
        gradient: The gradient there, for the methods that use one (for
            L-BFGS-B, the gradient, whose projection met the criterion);
            ``None`` for Nelder-Mead.
        evaluations: Calls of the objective.
        gradient_evaluations: Calls of the gradient.
        convergence: The criterion met.
    """

    x: tuple[float, ...]
    value: float
    gradient: tuple[float, ...] | None
    evaluations: int
    gradient_evaluations: int
    convergence: ConvergenceReport


# --------------------------------------------------------------------------- #
# Validation and evaluation
# --------------------------------------------------------------------------- #


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _finite(value: object, name: str) -> float:
    if not _is_number(value) or not math.isfinite(value):  # type: ignore[arg-type]
        raise AlphaLabValidationError(f"{name} must be a finite number, got {value!r}.")
    return float(value)  # type: ignore[arg-type]


def _positive(value: object, name: str) -> float:
    number = _finite(value, name)
    if number <= 0.0:
        raise AlphaLabValidationError(f"{name} must be positive, got {value!r}.")
    return number


def _limit(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AlphaLabValidationError(f"{name} must be a positive integer, got {value!r}.")
    return value


def _vector(values: Sequence[float], name: str) -> tuple[float, ...]:
    point = tuple(_finite(value, f"{name}[{index}]") for index, value in enumerate(values))
    if not point:
        raise AlphaLabValidationError(f"{name} needs at least one coordinate.")
    return point


def _scalar(function: ScalarFunction, x: float) -> float:
    value = function(x)
    if not _is_number(value) or not math.isfinite(value):
        raise AlphaLabValidationError(
            f"The function returned {value!r} at x = {x!r}; it must be a finite number."
        )
    return float(value)


def _objective(function: Objective, x: tuple[float, ...]) -> float:
    value = function(x)
    if not _is_number(value) or not math.isfinite(value):
        raise AlphaLabValidationError(
            f"The objective returned {value!r} at {x!r}; it must be a finite number."
        )
    return float(value)


def _gradient(gradient: Gradient, x: tuple[float, ...]) -> tuple[float, ...]:
    values = tuple(gradient(x))
    if len(values) != len(x):
        raise AlphaLabValidationError(
            f"The gradient has {len(values)} components at a point of {len(x)}."
        )
    for index, value in enumerate(values):
        if not _is_number(value) or not math.isfinite(value):
            raise AlphaLabValidationError(
                f"The gradient's component {index} is {value!r} at {x!r}; it must be finite."
            )
    return tuple(float(value) for value in values)


def _dot(first: Sequence[float], second: Sequence[float]) -> float:
    return math.fsum(a * b for a, b in zip(first, second, strict=True))


def _largest(values: Sequence[float]) -> float:
    return max((abs(value) for value in values), default=0.0)


# --------------------------------------------------------------------------- #
# Brent's root finder
# --------------------------------------------------------------------------- #


def brent_root(
    function: ScalarFunction,
    lower: float,
    upper: float,
    *,
    tolerance: float,
    max_iterations: int,
) -> Root:
    """A root of ``function`` in ``[lower, upper]``, by Brent's method.

    Inverse quadratic interpolation, secant steps and bisection, as in Brent's
    ``zeroin`` (*Algorithms for Minimization without Derivatives*, 1973,
    chapter 4): the bracket always holds a sign change, so the method converges
    whenever bisection would, and superlinearly near a simple root.

    It stops when the bracket is at most ``tolerance`` wide beyond four units
    of rounding at the root -- the spacing of the floating-point numbers there,
    which no tolerance can go below -- or when the function is exactly zero.

    Args:
        function: Finite at every point it is called.
        lower: One end of the bracket.
        upper: The other end. The function must have opposite signs at the
            two ends, or be zero at one.
        tolerance: The bracket width accepted, absolutely. Required.
        max_iterations: The iterations allowed. Required.

    Raises:
        AlphaLabValidationError: If an argument is out of range, the ends do
            not bracket a sign change, or the function returns a value that is
            not a finite number.
        ConvergenceError: If the bracket is not narrowed to the tolerance in
            ``max_iterations`` iterations.
    """

    tolerance = _positive(tolerance, "tolerance")
    _limit(max_iterations, "max_iterations")
    a = _finite(lower, "lower")
    b = _finite(upper, "upper")
    if a == b:
        raise AlphaLabValidationError("A bracket needs two distinct ends.")
    method = "Brent's root finder"
    criterion = "bracket width beyond four units of rounding at the root, or an exact zero"

    def report(iterations: int, reached: float) -> ConvergenceReport:
        return ConvergenceReport(method, criterion, tolerance, max_iterations, iterations, reached)

    fa, fb = _scalar(function, a), _scalar(function, b)
    evaluations = 2
    if fa == 0.0:
        return Root(a, 0.0, (a, a), evaluations, report(0, 0.0))
    if fb == 0.0:
        return Root(b, 0.0, (b, b), evaluations, report(0, 0.0))
    if (fa > 0.0) == (fb > 0.0):
        raise AlphaLabValidationError(
            f"[{a!r}, {b!r}] does not bracket a root: the function is {fa!r} and {fb!r} at its "
            "ends, of one sign."
        )
    c, fc = a, fa
    d = e = b - a
    iterations = 0
    while True:
        if (fb > 0.0) == (fc > 0.0):
            c, fc = a, fa
            d = e = b - a
        if abs(fc) < abs(fb):
            a, b, c = b, c, b
            fa, fb, fc = fb, fc, fb
        floor = 2.0 * _EPSILON * abs(b)
        allowance = floor + 0.5 * tolerance
        middle = 0.5 * (c - b)
        reached = 0.0 if fb == 0.0 else max(0.0, 2.0 * abs(middle) - 2.0 * floor)
        if fb == 0.0 or abs(middle) <= allowance:
            return Root(b, fb, (min(b, c), max(b, c)), evaluations, report(iterations, reached))
        if iterations == max_iterations:
            raise ConvergenceError(method, tolerance, max_iterations, reached)
        if abs(e) >= allowance and abs(fa) > abs(fb):
            s = fb / fa
            if a == c:
                p = 2.0 * middle * s
                q = 1.0 - s
            else:
                q = fa / fc
                r = fb / fc
                p = s * (2.0 * middle * q * (q - r) - (b - a) * (r - 1.0))
                q = (q - 1.0) * (r - 1.0) * (s - 1.0)
            if p > 0.0:
                q = -q
            else:
                p = -p
            if 2.0 * p < min(3.0 * middle * q - abs(allowance * q), abs(e * q)):
                e = d
                d = p / q
            else:
                d = e = middle
        else:
            d = e = middle
        a, fa = b, fb
        b += d if abs(d) > allowance else (allowance if middle > 0.0 else -allowance)
        fb = _scalar(function, b)
        evaluations += 1
        iterations += 1


# --------------------------------------------------------------------------- #
# Golden-section search
# --------------------------------------------------------------------------- #


def golden_section_minimum(
    function: ScalarFunction,
    lower: float,
    upper: float,
    *,
    tolerance: float,
    max_iterations: int,
) -> ScalarMinimum:
    """A minimum of ``function`` in ``[lower, upper]``, by golden-section search.

    Each iteration keeps the part of the bracket that holds the lower of two
    interior points, :data:`GOLDEN_FRACTION` of it, and reuses one point, so
    each costs one evaluation. On a unimodal function the bracket always holds
    the minimum; on one with several minima in the bracket it holds one of them.
    Ties go to the left. It stops when the bracket is at most ``tolerance``
    wide beyond four units of rounding at its ends.

    A minimum can be located no closer than about ``sqrt(eps)`` times its
    scale, by this method or any other that compares values: nearer than that
    the function's change is lost in its rounding. A tolerance below it
    narrows the bracket around a point the rounding chose.

    Raises:
        AlphaLabValidationError: If an argument is out of range or the function
            returns a value that is not a finite number.
        ConvergenceError: If the bracket is not narrowed to the tolerance in
            ``max_iterations`` iterations.
    """

    tolerance = _positive(tolerance, "tolerance")
    _limit(max_iterations, "max_iterations")
    a, b = sorted((_finite(lower, "lower"), _finite(upper, "upper")))
    if a == b:
        raise AlphaLabValidationError("A bracket needs two distinct ends.")
    method = "golden-section search"
    criterion = "bracket width beyond four units of rounding at its ends"
    c = b - GOLDEN_FRACTION * (b - a)
    d = a + GOLDEN_FRACTION * (b - a)
    fc, fd = _scalar(function, c), _scalar(function, d)
    evaluations = 2
    iterations = 0
    while True:
        floor = 4.0 * _EPSILON * max(abs(a), abs(b))
        reached = max(0.0, (b - a) - floor)
        if reached <= tolerance:
            x, value = (c, fc) if fc <= fd else (d, fd)
            report = ConvergenceReport(
                method, criterion, tolerance, max_iterations, iterations, reached
            )
            return ScalarMinimum(x, value, (a, b), evaluations, report)
        if iterations == max_iterations:
            raise ConvergenceError(method, tolerance, max_iterations, reached)
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - GOLDEN_FRACTION * (b - a)
            fc = _scalar(function, c)
        else:
            a, c, fc = c, d, fd
            d = a + GOLDEN_FRACTION * (b - a)
            fd = _scalar(function, d)
        evaluations += 1
        iterations += 1


# --------------------------------------------------------------------------- #
# Nelder-Mead
# --------------------------------------------------------------------------- #


def nelder_mead(
    function: Objective,
    start: Sequence[float],
    *,
    step: float | Sequence[float],
    tolerance: float,
    max_iterations: int,
) -> Minimum:
    """A minimum of ``function`` by the Nelder-Mead simplex method, without derivatives.

    The simplex starts at ``start`` and ``start + step_i e_i`` for each
    coordinate. Each iteration reflects, expands, contracts or shrinks it with
    the stated coefficients, by the rules and tie-breaking of Lagarias et al.
    (1998): a new vertex ranks after the vertices it ties with. It stops when
    every vertex is within ``tolerance`` of the best, coordinate by coordinate.

    Nelder-Mead can stall short of a minimum on some functions (McKinnon,
    1998); a simplex that has collapsed meets the criterion all the same. A
    gradient method is the stronger choice where a gradient exists.

    Args:
        function: The objective, of a tuple of coordinates.
        start: The first vertex.
        step: The initial simplex's edge, one for every coordinate or one each.
            It is the scale the search begins at, so it is required.
        tolerance: The simplex's size accepted, absolutely. Required.
        max_iterations: The iterations allowed. Required.

    Raises:
        AlphaLabValidationError: If an argument is out of range or the
            objective returns a value that is not a finite number.
        ConvergenceError: If the simplex does not shrink to the tolerance in
            ``max_iterations`` iterations.
    """

    origin = _vector(start, "start")
    size = len(origin)
    if _is_number(step):
        steps = (_positive(step, "step"),) * size
    else:
        steps = tuple(
            _positive(value, f"step[{index}]")
            for index, value in enumerate(step)  # type: ignore[arg-type]
        )
        if len(steps) != size:
            raise AlphaLabValidationError(
                f"step has {len(steps)} entries for a start of {size} coordinates."
            )
    tolerance = _positive(tolerance, "tolerance")
    _limit(max_iterations, "max_iterations")
    method = "the Nelder-Mead simplex"
    criterion = "largest distance of a vertex from the best, coordinate by coordinate"

    vertices = [origin]
    for index in range(size):
        moved = list(origin)
        moved[index] = origin[index] + steps[index]
        vertices.append(tuple(moved))
    values = [_objective(function, vertex) for vertex in vertices]
    tags = list(range(size + 1))
    next_tag = size + 1
    evaluations = size + 1
    iterations = 0

    while True:
        order = sorted(range(size + 1), key=lambda index: (values[index], tags[index]))
        vertices = [vertices[index] for index in order]
        values = [values[index] for index in order]
        tags = [tags[index] for index in order]
        best = vertices[0]
        spread = max(
            abs(vertex[coordinate] - best[coordinate])
            for vertex in vertices[1:]
            for coordinate in range(size)
        )
        if spread <= tolerance:
            report = ConvergenceReport(
                method, criterion, tolerance, max_iterations, iterations, spread
            )
            return Minimum(best, values[0], None, evaluations, 0, report)
        if iterations == max_iterations:
            raise ConvergenceError(method, tolerance, max_iterations, spread)
        iterations += 1

        centroid = tuple(
            math.fsum(vertex[coordinate] for vertex in vertices[:size]) / size
            for coordinate in range(size)
        )
        worst = vertices[size]
        reflected = tuple(c + REFLECTION * (c - w) for c, w in zip(centroid, worst, strict=True))
        reflected_value = _objective(function, reflected)
        evaluations += 1
        replacement: tuple[tuple[float, ...], float] | None = None
        if values[0] <= reflected_value < values[size - 1]:
            replacement = (reflected, reflected_value)
        elif reflected_value < values[0]:
            expanded = tuple(
                c + EXPANSION * (r - c) for c, r in zip(centroid, reflected, strict=True)
            )
            expanded_value = _objective(function, expanded)
            evaluations += 1
            replacement = (
                (expanded, expanded_value)
                if expanded_value < reflected_value
                else (reflected, reflected_value)
            )
        elif reflected_value < values[size]:
            outside = tuple(
                c + CONTRACTION * (r - c) for c, r in zip(centroid, reflected, strict=True)
            )
            outside_value = _objective(function, outside)
            evaluations += 1
            if outside_value <= reflected_value:
                replacement = (outside, outside_value)
        else:
            inside = tuple(c + CONTRACTION * (w - c) for c, w in zip(centroid, worst, strict=True))
            inside_value = _objective(function, inside)
            evaluations += 1
            if inside_value < values[size]:
                replacement = (inside, inside_value)

        if replacement is not None:
            vertices[size], values[size] = replacement
            tags[size] = next_tag
            next_tag += 1
            continue
        for index in range(1, size + 1):
            vertices[index] = tuple(
                b + SHRINK * (v - b) for b, v in zip(best, vertices[index], strict=True)
            )
            values[index] = _objective(function, vertices[index])
            tags[index] = next_tag
            next_tag += 1
        evaluations += size


# --------------------------------------------------------------------------- #
# Gradients
# --------------------------------------------------------------------------- #


def numerical_gradient(
    function: Objective, x: Sequence[float], *, step: float
) -> tuple[float, ...]:
    """The gradient of ``function`` at ``x`` by central differences.

    Component ``i`` is ``(f(x + h e_i) - f(x - h e_i)) / (2h)`` with ``h = step *
    max(1, |x_i|)``, and the denominator is the difference of the two points as
    represented, not ``2h``, which removes the error of rounding them. The
    truncation error is of order ``h^2`` times the third derivative and the
    rounding error of order ``eps |f| / h``, so the step decides the accuracy,
    and is required: about ``eps^(1/3)``, ``6e-6``, balances the two for a
    well-scaled function.

    Raises:
        AlphaLabValidationError: If ``x`` is empty or not finite, ``step`` is
            not a positive finite number, or the objective returns a value
            that is not a finite number.
    """

    point = _vector(x, "x")
    step = _positive(step, "step")
    components = []
    for index, coordinate in enumerate(point):
        width = step * max(1.0, abs(coordinate))
        up = list(point)
        down = list(point)
        up[index] = coordinate + width
        down[index] = coordinate - width
        spacing = up[index] - down[index]
        if spacing == 0.0:
            raise AlphaLabValidationError(
                f"step {step!r} is below the spacing of the numbers at x[{index}] = "
                f"{coordinate!r}; the two points are one."
            )
        components.append(
            (_objective(function, tuple(up)) - _objective(function, tuple(down))) / spacing
        )
    return tuple(components)


# --------------------------------------------------------------------------- #
# The line search
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Trial:
    step: float
    x: tuple[float, ...]
    value: float
    gradient: tuple[float, ...] | None = None


def _project(
    point: Sequence[float], lows: Sequence[float], highs: Sequence[float]
) -> tuple[float, ...]:
    return tuple(min(max(v, low), high) for v, low, high in zip(point, lows, highs, strict=True))


def _wolfe_search(
    function: Objective,
    gradient: Gradient,
    x: tuple[float, ...],
    value: float,
    slope: float,
    direction: Sequence[float],
    largest_step: float,
    counts: list[int],
    *,
    first_step: float = 1.0,
    box: tuple[Sequence[float], Sequence[float]] | None = None,
) -> _Trial | None:
    """A step meeting the strong Wolfe conditions (Nocedal and Wright, Algorithms 3.5 and 3.6).

    Bracketing by doubling from ``first_step`` (or ``largest_step``, if
    smaller), then bisection within the bracket. ``slope`` is ``g'p``,
    negative. ``counts`` collects the evaluations made. Within ``box``, each
    trial point is projected onto it, which only ever moves a coordinate
    rounding put a unit beyond its bound. At ``largest_step`` a step with
    sufficient decrease is taken although the slope still falls: the boundary
    stops it. ``None`` if no step met the conditions in
    :data:`LINE_SEARCH_LIMIT` trials.
    """

    def trial(step: float) -> _Trial:
        point = tuple(xi + step * pi for xi, pi in zip(x, direction, strict=True))
        if box is not None:
            point = _project(point, box[0], box[1])
        counts[0] += 1
        return _Trial(step, point, _objective(function, point))

    def slope_at(candidate: _Trial) -> float:
        if candidate.gradient is None:
            candidate.gradient = _gradient(gradient, candidate.x)
            counts[1] += 1
        return _dot(candidate.gradient, direction)

    def sufficient(candidate: _Trial) -> bool:
        return candidate.value <= value + SUFFICIENT_DECREASE * candidate.step * slope

    def curved(candidate: _Trial) -> bool:
        return abs(slope_at(candidate)) <= -CURVATURE * slope

    previous = _Trial(0.0, x, value)
    step = min(first_step, largest_step)
    trials = 0
    low: _Trial | None = None
    high: _Trial | None = None
    while trials < LINE_SEARCH_LIMIT:
        current = trial(step)
        trials += 1
        if not sufficient(current) or (previous.step > 0.0 and current.value >= previous.value):
            low, high = previous, current
            break
        if curved(current):
            return current
        if slope_at(current) >= 0.0:
            low, high = current, previous
            break
        if step >= largest_step:
            # The boundary, with sufficient decrease and the slope still falling.
            return current
        previous = current
        step = min(2.0 * step, largest_step)
    if low is None or high is None:
        return None
    while trials < LINE_SEARCH_LIMIT:
        middle = 0.5 * (low.step + high.step)
        if middle in (low.step, high.step):
            return None
        current = trial(middle)
        trials += 1
        if not sufficient(current) or current.value >= low.value:
            high = current
            continue
        if curved(current):
            return current
        if slope_at(current) * (high.step - low.step) >= 0.0:
            high = low
        low = current
    return None


# --------------------------------------------------------------------------- #
# BFGS
# --------------------------------------------------------------------------- #


def bfgs(
    function: Objective,
    gradient: Gradient,
    start: Sequence[float],
    *,
    tolerance: float,
    max_iterations: int,
) -> Minimum:
    """A minimum of ``function`` by BFGS, with a strong Wolfe line search.

    The inverse Hessian approximation starts as the identity, is scaled by
    ``y's / y'y`` before its first update (Nocedal and Wright, equation 6.20),
    and is updated only when the curvature ``y's`` is positive, which the
    Wolfe conditions ensure. It stops when the gradient's largest component is
    at most ``tolerance``.

    Args:
        function: The objective, of a tuple of coordinates.
        gradient: Its gradient, of the same tuple. Pass
            ``lambda x: numerical_gradient(function, x, step=...)`` for
            differences; the step is then the caller's to state.
        start: The first point.
        tolerance: The gradient's largest component accepted. Required.
        max_iterations: The iterations allowed. Required.

    Raises:
        AlphaLabValidationError: If an argument is out of range, or the
            objective or gradient returns a value that is not finite.
        ConvergenceError: If the gradient does not fall to the tolerance in
            ``max_iterations`` iterations, or a line search finds no step
            meeting the Wolfe conditions, which happens when the tolerance is
            below what the objective's rounding allows.
    """

    x = _vector(start, "start")
    tolerance = _positive(tolerance, "tolerance")
    _limit(max_iterations, "max_iterations")
    size = len(x)
    method = "BFGS"
    criterion = "largest gradient component"
    value = _objective(function, x)
    g = _gradient(gradient, x)
    counts = [1, 1]
    inverse: list[list[float]] | None = None
    iterations = 0
    while True:
        largest = _largest(g)
        if largest <= tolerance:
            report = ConvergenceReport(
                method, criterion, tolerance, max_iterations, iterations, largest
            )
            return Minimum(x, value, g, counts[0], counts[1], report)
        if iterations == max_iterations:
            raise ConvergenceError(method, tolerance, max_iterations, largest)
        if inverse is None:
            direction = [-component for component in g]
        else:
            direction = [-_dot(row, g) for row in inverse]
        slope = _dot(g, direction)
        if slope >= 0.0:
            # Rounding has cost the approximation its definiteness: restart it.
            inverse = None
            direction = [-component for component in g]
            slope = _dot(g, direction)
        accepted = _wolfe_search(function, gradient, x, value, slope, direction, math.inf, counts)
        if accepted is None:
            raise ConvergenceError(
                "BFGS, whose line search found no step meeting the Wolfe conditions,",
                tolerance,
                max_iterations,
                largest,
            )
        new_gradient = accepted.gradient
        if new_gradient is None:
            new_gradient = _gradient(gradient, accepted.x)
            counts[1] += 1
        s = [b - a for a, b in zip(x, accepted.x, strict=True)]
        y = [b - a for a, b in zip(g, new_gradient, strict=True)]
        curvature = _dot(y, s)
        if curvature > 0.0:
            if inverse is None:
                scale = curvature / _dot(y, y)
                inverse = [[scale if i == j else 0.0 for j in range(size)] for i in range(size)]
            rho = 1.0 / curvature
            hy = [_dot(row, y) for row in inverse]
            weight = rho * rho * _dot(y, hy) + rho
            # Every term is formed symmetrically, so the update keeps the matrix exactly so.
            inverse = [
                [
                    inverse[i][j] - rho * (s[i] * hy[j] + hy[i] * s[j]) + weight * (s[i] * s[j])
                    for j in range(size)
                ]
                for i in range(size)
            ]
        x, value, g = accepted.x, accepted.value, new_gradient
        iterations += 1


# --------------------------------------------------------------------------- #
# L-BFGS-B
# --------------------------------------------------------------------------- #


def _times(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [_dot(row, vector) for row in matrix]


def _gauss_jordan(
    matrix: Sequence[Sequence[float]], right: Sequence[Sequence[float]]
) -> list[list[float]] | None:
    """``matrix^-1 right`` by Gauss-Jordan with partial pivoting, the first of equal pivots.

    ``None`` when a pivot is exactly zero: the matrix is singular.
    """

    size = len(matrix)
    rows = [list(matrix[i]) + list(right[i]) for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(rows[row][column]))
        if rows[pivot][column] == 0.0:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        lead = rows[column][column]
        rows[column] = [value / lead for value in rows[column]]
        for row in range(size):
            if row != column and rows[row][column] != 0.0:
                factor = rows[row][column]
                rows[row] = [a - factor * b for a, b in zip(rows[row], rows[column], strict=True)]
    return [row[size:] for row in rows]


def _compact(
    steps: list[tuple[float, ...]], changes: list[tuple[float, ...]], theta: float, size: int
) -> tuple[list[list[float]], list[list[float]]] | None:
    """``W = [Y, theta S]`` by rows and ``M``, with ``B = theta I - W M W'``.

    ``M`` is the inverse of ``[[-D, L'], [L, theta S'S]]`` (Byrd, Lu, Nocedal
    and Zhu, 1995, section 3); ``None`` if it is singular.
    """

    count = len(steps)
    rows = [
        [changes[k][i] for k in range(count)] + [theta * steps[k][i] for k in range(count)]
        for i in range(size)
    ]
    middle = [[0.0] * (2 * count) for _ in range(2 * count)]
    for j in range(count):
        middle[j][j] = -_dot(steps[j], changes[j])
        for k in range(count):
            middle[count + j][count + k] = theta * _dot(steps[j], steps[k])
            if j > k:
                coupling = _dot(steps[j], changes[k])
                middle[count + j][k] = coupling
                middle[k][count + j] = coupling
    identity = [[1.0 if i == j else 0.0 for j in range(2 * count)] for i in range(2 * count)]
    inverse = _gauss_jordan(middle, identity)
    return None if inverse is None else (rows, inverse)


def _cauchy_point(
    x: tuple[float, ...],
    g: tuple[float, ...],
    lows: Sequence[float],
    highs: Sequence[float],
    theta: float,
    rows: list[list[float]],
    middle: list[list[float]],
) -> tuple[list[float], list[float]]:
    """The generalized Cauchy point and ``c = W'(x_c - x)`` (Byrd et al., Algorithm CP).

    The first local minimizer of the quadratic model along the projected
    steepest-descent path, found breakpoint by breakpoint in increasing order,
    the lower index first among equal ones.
    """

    size = len(x)
    width = len(middle)
    direction = [0.0] * size
    breakpoints: list[tuple[float, int]] = []
    for i in range(size):
        if g[i] < 0.0:
            reach = (x[i] - highs[i]) / g[i] if highs[i] < math.inf else math.inf
        elif g[i] > 0.0:
            reach = (x[i] - lows[i]) / g[i] if lows[i] > -math.inf else math.inf
        else:
            reach = math.inf
        if reach > 0.0:
            direction[i] = -g[i]
            if reach < math.inf:
                breakpoints.append((reach, i))
    breakpoints.sort()
    cauchy = list(x)
    p = [math.fsum(rows[i][k] * direction[i] for i in range(size)) for k in range(width)]
    c = [0.0] * width
    first = -math.fsum(d * d for d in direction)
    second = -theta * first - _dot(p, _times(middle, p))
    floor = _EPSILON * -theta * first
    second = max(second, floor)
    minimum = -first / second if second > 0.0 else 0.0
    previous = 0.0
    for reach, b in breakpoints:
        interval = reach - previous
        if minimum < interval:
            break
        cauchy[b] = highs[b] if direction[b] > 0.0 else lows[b]
        offset = cauchy[b] - x[b]
        c = [ci + interval * pi for ci, pi in zip(c, p, strict=True)]
        gb = g[b]
        wb = rows[b]
        first = math.fsum(
            [
                first,
                interval * second,
                gb * gb,
                theta * gb * offset,
                -gb * _dot(wb, _times(middle, c)),
            ]
        )
        second = math.fsum(
            [
                second,
                -theta * gb * gb,
                -2.0 * gb * _dot(wb, _times(middle, p)),
                -gb * gb * _dot(wb, _times(middle, wb)),
            ]
        )
        p = [pi + gb * wi for pi, wi in zip(p, wb, strict=True)]
        direction[b] = 0.0
        second = max(second, floor)
        minimum = -first / second if second > 0.0 else 0.0
        previous = reach
    minimum = max(minimum, 0.0)
    previous += minimum
    for i in range(size):
        if direction[i] != 0.0:
            cauchy[i] = min(max(x[i] + previous * direction[i], lows[i]), highs[i])
    c = [ci + minimum * pi for ci, pi in zip(c, p, strict=True)]
    return cauchy, c


def _subspace_minimum(
    x: tuple[float, ...],
    g: tuple[float, ...],
    cauchy: list[float],
    c: list[float],
    lows: Sequence[float],
    highs: Sequence[float],
    theta: float,
    rows: list[list[float]],
    middle: list[list[float]],
) -> list[float]:
    """Minimize the model over the variables free at the Cauchy point (Byrd et al., section 5.1).

    The direct primal method: the reduced Newton step by Sherman-Morrison-
    Woodbury, then cut back along itself until it is within the bounds.
    """

    free = [i for i in range(len(x)) if lows[i] < cauchy[i] < highs[i]]
    if not free:
        return cauchy
    width = len(middle)
    mc = _times(middle, c)
    reduced = {i: g[i] + theta * (cauchy[i] - x[i]) - _dot(rows[i], mc) for i in free}
    step = {i: -reduced[i] / theta for i in free}
    if width:
        v = _times(middle, [math.fsum(rows[i][k] * reduced[i] for i in free) for k in range(width)])
        gram = [
            [math.fsum(rows[i][a] * rows[i][b] for i in free) for b in range(width)]
            for a in range(width)
        ]
        coupled = [_times(middle, [gram[a][b] for a in range(width)]) for b in range(width)]
        system = [
            [(1.0 if a == b else 0.0) - coupled[b][a] / theta for b in range(width)]
            for a in range(width)
        ]
        solved = _gauss_jordan(system, [[value] for value in v])
        if solved is not None:
            correction = [row[0] for row in solved]
            step = {
                i: -reduced[i] / theta - _dot(rows[i], correction) / (theta * theta) for i in free
            }
    fraction = 1.0
    for i in free:
        if step[i] > 0.0:
            fraction = min(fraction, (highs[i] - cauchy[i]) / step[i])
        elif step[i] < 0.0:
            fraction = min(fraction, (lows[i] - cauchy[i]) / step[i])
    target = list(cauchy)
    for i in free:
        target[i] = min(max(cauchy[i] + fraction * step[i], lows[i]), highs[i])
    return target


def _bounds(
    bounds: Sequence[tuple[float | None, float | None]], size: int
) -> tuple[list[float], list[float]]:
    if len(bounds) != size:
        raise AlphaLabValidationError(f"bounds has {len(bounds)} pairs for {size} coordinates.")
    lows, highs = [], []
    for index, pair in enumerate(bounds):
        if len(pair) != 2:
            raise AlphaLabValidationError(f"bounds[{index}] is not a (low, high) pair: {pair!r}.")
        low, high = pair
        low_value = -math.inf if low is None else float(low)
        high_value = math.inf if high is None else float(high)
        for name, value in (("low", low_value), ("high", high_value)):
            if (
                math.isnan(value)
                or (name == "low" and value == math.inf)
                or (name == "high" and value == -math.inf)
            ):
                raise AlphaLabValidationError(f"bounds[{index}]'s {name} is {value!r}.")
        if low_value > high_value:
            raise AlphaLabValidationError(
                f"bounds[{index}] is empty: its low {low_value!r} is above its high {high_value!r}."
            )
        lows.append(low_value)
        highs.append(high_value)
    return lows, highs


def l_bfgs_b(
    function: Objective,
    gradient: Gradient,
    start: Sequence[float],
    *,
    bounds: Sequence[tuple[float | None, float | None]],
    memory: int,
    tolerance: float,
    max_iterations: int,
) -> Minimum:
    """A minimum of ``function`` within simple bounds, by L-BFGS-B.

    Byrd, Lu, Nocedal and Zhu (1995): a limited-memory BFGS model in compact
    form; its generalized Cauchy point along the projected steepest-descent
    path; the model minimized over the variables still free there (the direct
    primal method) and cut back into the bounds; then a strong Wolfe line
    search along the step, projected onto the bounds. A pair is kept only when
    its curvature ``s'y`` exceeds ``eps * y'y``. A line search that fails
    discards the history and tries once more from steepest descent, as the
    authors' code does. It stops when the projected gradient,
    ``P(x - g) - x``, has no component above ``tolerance``.

    Args:
        function: The objective, of a tuple of coordinates.
        gradient: Its gradient, of the same tuple.
        start: The first point, projected onto the bounds.
        bounds: A ``(low, high)`` pair for every coordinate; ``None`` or an
            infinity for no bound on that side.
        memory: The pairs of the history kept, at least one. It shapes the
            search, so it is required; between 3 and 20 is usual.
        tolerance: The projected gradient's largest component accepted.
            Required.
        max_iterations: The iterations allowed. Required.

    Raises:
        AlphaLabValidationError: If an argument is out of range, a bound pair
            is empty, or the objective or gradient returns a value that is not
            finite.
        ConvergenceError: If the projected gradient does not fall to the
            tolerance in ``max_iterations`` iterations, or the line search
            fails from steepest descent too.
    """

    origin = _vector(start, "start")
    size = len(origin)
    lows, highs = _bounds(bounds, size)
    _limit(memory, "memory")
    tolerance = _positive(tolerance, "tolerance")
    _limit(max_iterations, "max_iterations")
    method = "L-BFGS-B"
    criterion = "largest component of the projected gradient"
    box = (lows, highs)
    x = _project(origin, lows, highs)
    value = _objective(function, x)
    g = _gradient(gradient, x)
    counts = [1, 1]
    steps: list[tuple[float, ...]] = []
    changes: list[tuple[float, ...]] = []
    theta = 1.0
    iterations = 0
    while True:
        projected = _largest(
            [
                min(max(xi - gi, low), high) - xi
                for xi, gi, low, high in zip(x, g, lows, highs, strict=True)
            ]
        )
        if projected <= tolerance:
            report = ConvergenceReport(
                method, criterion, tolerance, max_iterations, iterations, projected
            )
            return Minimum(x, value, g, counts[0], counts[1], report)
        if iterations == max_iterations:
            raise ConvergenceError(method, tolerance, max_iterations, projected)

        accepted: _Trial | None = None
        for attempt in range(2):
            compact = _compact(steps, changes, theta, size) if steps else None
            if steps and compact is None:
                steps, changes, theta = [], [], 1.0
            rows, middle = compact if compact is not None else ([[] for _ in range(size)], [])
            cauchy, c = _cauchy_point(x, g, lows, highs, theta, rows, middle)
            target = _subspace_minimum(x, g, cauchy, c, lows, highs, theta, rows, middle)
            direction = [t - xi for t, xi in zip(target, x, strict=True)]
            slope = _dot(g, direction)
            if slope < 0.0:
                largest = math.inf
                for xi, di, low, high in zip(x, direction, lows, highs, strict=True):
                    if di > 0.0:
                        largest = min(largest, (high - xi) / di)
                    elif di < 0.0:
                        largest = min(largest, (low - xi) / di)
                first = 1.0 if steps else min(1.0 / math.sqrt(_dot(direction, direction)), largest)
                accepted = _wolfe_search(
                    function,
                    gradient,
                    x,
                    value,
                    slope,
                    direction,
                    largest,
                    counts,
                    first_step=first,
                    box=box,
                )
            if accepted is not None or (attempt == 0 and not steps):
                break
            # Start again from steepest descent, as the authors' code does.
            steps, changes, theta = [], [], 1.0
        if accepted is None:
            raise ConvergenceError(
                "L-BFGS-B, whose line search found no step meeting the Wolfe conditions,",
                tolerance,
                max_iterations,
                projected,
            )
        new_gradient = accepted.gradient
        if new_gradient is None:
            new_gradient = _gradient(gradient, accepted.x)
            counts[1] += 1
        s = tuple(b - a for a, b in zip(x, accepted.x, strict=True))
        y = tuple(b - a for a, b in zip(g, new_gradient, strict=True))
        curvature = _dot(s, y)
        squared = _dot(y, y)
        if curvature > _EPSILON * squared:
            steps.append(s)
            changes.append(y)
            if len(steps) > memory:
                steps.pop(0)
                changes.pop(0)
            theta = squared / curvature
        x, value, g = accepted.x, accepted.value, new_gradient
        iterations += 1
