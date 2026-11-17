"""Search spaces drawn by a seed, and rounds that keep the best (v4.3, plan I.4, N17).

A grid (:meth:`~alphalab.research.walk_forward_optimization.ParameterSpace.grid`)
is the search when every combination is cheap. When it is not, these draw a
:class:`~alphalab.research.walk_forward_optimization.ParameterSpace` from
stated axes:

* :func:`random_space` -- each candidate an independent draw.
* :func:`latin_hypercube_space` -- each axis cut into as many strata as there
  are candidates, and every stratum drawn once (McKay, Beckman and Conover,
  1979), so a few candidates still cover each axis evenly.

Every coordinate comes from SHA-256 of the seed, the draw and the axis -- the
same on every host and interpreter, unlike ``random``'s algorithms, which are
CPython's -- and a log-uniform axis maps it through the ``decimal`` module's
correctly rounded ``ln`` and ``exp``, never the host's. So a drawn space, and
every trial identity built on it, is the same everywhere. A
:class:`SampledSpace` keeps the space beside how it was drawn, seed included.

:class:`SuccessiveHalving` is a search in rounds (Jamieson and Talwalkar,
2016): every candidate gets the first budget, the best fraction of them the
next, and so on. The schedule is pure; each round runs as an experiment, a
child of the round before it, so every configuration a round evaluates is a
trial in the ledger and counted (:func:`alphalab.lifecycle.run_successive_halving`).

Sobol sequences need a published table of direction numbers, which belongs
where it can be verified against its source: they come through
``alphalab-compute``'s SciPy (ADR-0057), never from a table written here.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal
from itertools import pairwise
from typing import Final

from alphalab.common.types import ParamValue
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.walk_forward_optimization import ParameterSpace, _render

__all__ = [
    "ChoiceAxis",
    "IntegerAxis",
    "LogUniformAxis",
    "SampledSpace",
    "SuccessiveHalving",
    "UniformAxis",
    "latin_hypercube_space",
    "random_space",
]

#: The digits ``decimal`` maps a log-uniform coordinate with: far beyond a float's.
_PRECISION: Final = Context(prec=40)

#: How many draws a random space may spend per candidate on repeats of a discrete axis.
_ATTEMPTS: Final = 64

#: The largest float below one: a coordinate's ceiling.
_BELOW_ONE: Final = math.nextafter(1.0, 0.0)


def _finite(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ResearchValidationError(f"{what} is a number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise ResearchValidationError(f"{what} is finite, got {value!r}.")
    return number


@dataclass(frozen=True, slots=True)
class UniformAxis:
    """Floats evenly in ``[low, high)``."""

    low: float
    high: float

    def __post_init__(self) -> None:
        low, high = _finite(self.low, "low"), _finite(self.high, "high")
        if not low < high:
            raise ResearchValidationError(
                f"A uniform axis needs low < high, got {low!r}, {high!r}."
            )
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)

    def value(self, coordinate: float) -> ParamValue:
        return min(
            self.low + coordinate * (self.high - self.low), math.nextafter(self.high, self.low)
        )

    def describe(self) -> str:
        return f"uniform[{self.low!r}, {self.high!r})"


@dataclass(frozen=True, slots=True)
class LogUniformAxis:
    """Floats in ``[low, high)`` whose logarithms are even: each decade alike."""

    low: float
    high: float

    def __post_init__(self) -> None:
        low, high = _finite(self.low, "low"), _finite(self.high, "high")
        if not 0.0 < low < high:
            raise ResearchValidationError(
                f"A log-uniform axis needs 0 < low < high, got {low!r}, {high!r}."
            )
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)

    def value(self, coordinate: float) -> ParamValue:
        low = _PRECISION.ln(Decimal(self.low))
        span = _PRECISION.subtract(_PRECISION.ln(Decimal(self.high)), low)
        exponent = _PRECISION.add(low, _PRECISION.multiply(Decimal(coordinate), span))
        return min(float(_PRECISION.exp(exponent)), math.nextafter(self.high, 0.0))

    def describe(self) -> str:
        return f"log-uniform[{self.low!r}, {self.high!r})"


@dataclass(frozen=True, slots=True)
class IntegerAxis:
    """Integers from ``low`` to ``high``, both included, each alike."""

    low: int
    high: int

    def __post_init__(self) -> None:
        for name in ("low", "high"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ResearchValidationError(
                    f"An integer range's {name} is an integer, got {value!r}."
                )
        if not self.low <= self.high:
            raise ResearchValidationError(
                f"An integer range needs low <= high, got {self.low!r}, {self.high!r}."
            )

    def value(self, coordinate: float) -> ParamValue:
        return self.low + math.floor(coordinate * (self.high - self.low + 1))

    def describe(self) -> str:
        return f"integers[{self.low}, {self.high}]"


@dataclass(frozen=True, slots=True)
class ChoiceAxis:
    """One of ``values``, each alike."""

    values: tuple[ParamValue, ...]

    def __post_init__(self) -> None:
        if isinstance(self.values, str) or not isinstance(self.values, Sequence) or not self.values:
            raise ResearchValidationError(
                f"A choice needs at least one value, got {self.values!r}."
            )
        values = tuple(self.values)
        rendered = [repr(value) for value in values]
        if len(set(rendered)) != len(rendered):
            raise ResearchValidationError(f"A choice repeats a value: {list(values)}.")
        object.__setattr__(self, "values", values)

    def value(self, coordinate: float) -> ParamValue:
        return self.values[math.floor(coordinate * len(self.values))]

    def describe(self) -> str:
        return f"one of {list(self.values)!r}"


Axis = UniformAxis | LogUniformAxis | IntegerAxis | ChoiceAxis


@dataclass(frozen=True, slots=True)
class SampledSpace:
    """A drawn space, and how it was drawn.

    Attributes:
        space: The candidates, in the order drawn.
        method: The sampler, its axes, its count and its seed, in words.
        seed: The seed every coordinate came from.
    """

    space: ParameterSpace
    method: str
    seed: int


def _axes(axes: Mapping[str, Axis]) -> list[tuple[str, Axis]]:
    if not axes:
        raise ResearchValidationError("A drawn space needs at least one axis.")
    for name, axis in axes.items():
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ResearchValidationError(f"Axis name {name!r} is not a name.")
        if not isinstance(axis, UniformAxis | LogUniformAxis | IntegerAxis | ChoiceAxis):
            raise ResearchValidationError(
                f"Axis {name!r} is a UniformAxis, LogUniformAxis, IntegerAxis or ChoiceAxis, "
                f"got {axis!r}."
            )
    return sorted(axes.items())


def _draw_arguments(count: object, seed: object) -> tuple[int, int]:
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ResearchValidationError(f"A drawn space has at least one candidate, got {count!r}.")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ResearchValidationError(f"A seed is an integer, got {seed!r}.")
    return count, seed


def _coordinate(*parts: object) -> float:
    """A float in ``[0, 1)`` from SHA-256 of ``parts``: 53 bits, exact."""

    text = "|".join(str(part) for part in parts)
    word = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    return (word >> 11) / 2.0**53


def _described(sampler: str, axes: list[tuple[str, Axis]], count: int, seed: int) -> str:
    listed = ", ".join(f"{name} {axis.describe()}" for name, axis in axes)
    return f"{sampler} of {count} over {listed}, seed {seed}"


def random_space(axes: Mapping[str, Axis], *, count: int, seed: int) -> SampledSpace:
    """``count`` candidates, each axis's value an independent draw from the seed.

    A draw that repeats a candidate already drawn -- possible on integer and
    choice axes -- is passed over, and the next draw taken.

    Raises:
        ResearchValidationError: If an axis is not one, ``count`` is below one,
            or the axes hold too few distinct candidates to draw ``count``.
    """

    named = _axes(axes)
    count, seed = _draw_arguments(count, seed)
    candidates: list[dict[str, ParamValue]] = []
    seen: set[str] = set()
    for draw in range(_ATTEMPTS * count):
        candidate = {
            name: axis.value(_coordinate(seed, "random", draw, name)) for name, axis in named
        }
        rendered = _render(candidate)
        if rendered in seen:
            continue
        seen.add(rendered)
        candidates.append(candidate)
        if len(candidates) == count:
            return SampledSpace(
                ParameterSpace.of(candidates), _described("random", named, count, seed), seed
            )
    raise ResearchValidationError(
        f"{_ATTEMPTS * count} draws gave {len(candidates)} distinct candidates of the {count} "
        "asked for: the axes hold too few. Draw fewer, or widen an axis."
    )


def latin_hypercube_space(axes: Mapping[str, Axis], *, count: int, seed: int) -> SampledSpace:
    """``count`` candidates, every axis's ``count`` strata each drawn exactly once.

    On each axis the strata ``[k / count, (k + 1) / count)`` are dealt to the
    candidates in an order ranked by SHA-256 of the seed, and each coordinate
    falls within its stratum by another draw. Coordinates are then mapped by
    the axis, so an integer or choice axis with fewer values than candidates
    repeats values -- evenly.

    Raises:
        ResearchValidationError: If an axis is not one, ``count`` is below one,
            or two candidates coincide, as they can when every axis is discrete.
    """

    named = _axes(axes)
    count, seed = _draw_arguments(count, seed)
    columns: dict[str, list[ParamValue]] = {}
    for name, axis in named:
        strata = sorted(
            range(count),
            key=lambda stratum: (
                hashlib.sha256(f"{seed}|deal|{name}|{stratum}".encode()).digest(),
                stratum,
            ),
        )
        column = []
        for row, stratum in enumerate(strata):
            coordinate = min((stratum + _coordinate(seed, "jitter", name, row)) / count, _BELOW_ONE)
            column.append(axis.value(coordinate))
        columns[name] = column
    candidates = [{name: columns[name][row] for name, _ in named} for row in range(count)]
    return SampledSpace(
        ParameterSpace.of(candidates), _described("latin hypercube", named, count, seed), seed
    )


@dataclass(frozen=True, slots=True)
class SuccessiveHalving:
    """A search in rounds, each giving the best fraction of the last a larger budget.

    Round ``r`` evaluates its candidates with ``parameter`` set to
    ``budgets[r]``. After it, the best ``max(1, ceil(n * keep))`` of its ``n``
    candidates by score -- ties to the earlier candidate -- go to the next
    round, in the order they had. A candidate with no score, its trial having
    failed somewhere, is never kept.

    Attributes:
        budgets: Each round's budget, strictly increasing: at least two rounds.
        keep: The fraction each round keeps, in ``(0, 1)``.
        parameter: The name the budget is passed to a trial by. The rounds'
            candidates are the space's with it added, so the space must not
            name it.

    Raises:
        ResearchValidationError: If a field is out of its range.
    """

    budgets: tuple[int, ...]
    keep: float
    parameter: str

    def __post_init__(self) -> None:
        budgets = tuple(self.budgets) if isinstance(self.budgets, Sequence) else ()
        if len(budgets) < 2 or any(
            isinstance(b, bool) or not isinstance(b, int) or b < 1 for b in budgets
        ):
            raise ResearchValidationError(
                "Successive halving needs at least two positive integer budgets, got "
                f"{self.budgets!r}."
            )
        if any(later <= earlier for earlier, later in pairwise(budgets)):
            raise ResearchValidationError(
                f"Successive halving's budgets increase round by round, got {list(budgets)}."
            )
        keep = _finite(self.keep, "keep")
        if not 0.0 < keep < 1.0:
            raise ResearchValidationError(f"keep is in (0, 1), got {self.keep!r}.")
        if not isinstance(self.parameter, str) or not self.parameter.strip():
            raise ResearchValidationError(
                f"The budget's parameter is a name, got {self.parameter!r}."
            )
        object.__setattr__(self, "budgets", budgets)
        object.__setattr__(self, "keep", keep)

    def round(self, index: int, candidates: Sequence[Mapping[str, ParamValue]]) -> ParameterSpace:
        """Round ``index``'s space: ``candidates`` with the round's budget."""

        if not 0 <= index < len(self.budgets):
            raise ResearchValidationError(
                f"Successive halving has rounds 0 to {len(self.budgets) - 1}, not {index}."
            )
        for candidate in candidates:
            if self.parameter in candidate:
                raise ResearchValidationError(
                    f"The candidates already name {self.parameter!r}, the budget's parameter."
                )
        return ParameterSpace.of(
            [{**candidate, self.parameter: self.budgets[index]} for candidate in candidates]
        )

    def kept(
        self, space: ParameterSpace, scores: Sequence[float | None]
    ) -> tuple[Mapping[str, ParamValue], ...]:
        """The candidates of ``space`` the next round keeps, without the budget, in their order.

        Raises:
            ResearchValidationError: If ``scores`` does not hold one per
                candidate, or no candidate has a score.
        """

        if len(scores) != len(space.candidates):
            raise ResearchValidationError(
                f"A round of {len(space.candidates)} candidates was given {len(scores)} scores."
            )
        scored = [(score, index) for index, score in enumerate(scores) if score is not None]
        if not scored:
            raise ResearchValidationError(
                "No candidate of the round has a score: every trial failed somewhere, so no "
                "round can follow it."
            )
        number = max(1, math.ceil(len(space.candidates) * self.keep))
        best = sorted(scored, key=lambda pair: (-pair[0], pair[1]))[:number]
        chosen = sorted(index for _, index in best)
        return tuple(
            {
                name: value
                for name, value in space.candidates[index].items()
                if name != self.parameter
            }
            for index in chosen
        )
