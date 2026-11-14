"""How an iterative method stopped (ADR-0058, v4.3).

A method whose tolerance changes its answer takes that tolerance and its
iteration limit as required arguments, with no defaults (ADR-0058 section 2).
Its result carries a :class:`ConvergenceReport`: the criterion that stopped it,
what the criterion measured when it stopped, and how many iterations that took.

A report always describes a criterion that was met. A method that does not meet
its criterion within its limit raises
:class:`~alphalab.common.exceptions.ConvergenceError` instead, naming what it
reached, and returns nothing: a result short of its own criterion is not the
result that was asked for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from alphalab.common.exceptions import AlphaLabValidationError

__all__ = ["ConvergenceReport"]


@dataclass(frozen=True, slots=True)
class ConvergenceReport:
    """How an iterative method met its stopping criterion.

    Attributes:
        method: What iterated, as a noun phrase: ``"the cyclic Jacobi
            eigensystem"``.
        criterion: The stopping rule, in words, naming the quantity
            :attr:`reached` measures.
        tolerance: The bound the criterion's quantity had to reach, as the
            caller gave it.
        limit: The iterations the caller allowed.
        iterations: The iterations used, from zero (the input already met the
            criterion) to :attr:`limit`.
        reached: The criterion's quantity when the method stopped. It is at
            most :attr:`tolerance`, because a report describes a criterion that
            was met.

    Raises:
        AlphaLabValidationError: If a field is out of its range: an empty
            method or criterion, a tolerance that is not a finite positive
            number, a limit below one, a count of iterations outside
            ``[0, limit]``, or a measure that is negative, not finite, or above
            the tolerance.
    """

    method: str
    criterion: str
    tolerance: float
    limit: int
    iterations: int
    reached: float

    def __post_init__(self) -> None:
        for name, text in (("method", self.method), ("criterion", self.criterion)):
            if not isinstance(text, str) or not text.strip():
                raise AlphaLabValidationError(f"A convergence report needs a {name}.")
        if not _is_number(self.tolerance) or not (
            math.isfinite(self.tolerance) and self.tolerance > 0.0
        ):
            raise AlphaLabValidationError(
                f"A convergence tolerance is a finite positive number, got {self.tolerance!r}."
            )
        if not _is_count(self.limit) or self.limit < 1:
            raise AlphaLabValidationError(
                f"An iteration limit is a positive integer, got {self.limit!r}."
            )
        if not _is_count(self.iterations) or not 0 <= self.iterations <= self.limit:
            raise AlphaLabValidationError(
                f"{self.iterations!r} iterations is outside the limit of {self.limit}."
            )
        if not _is_number(self.reached) or not (
            math.isfinite(self.reached) and 0.0 <= self.reached <= self.tolerance
        ):
            raise AlphaLabValidationError(
                f"A report describes a criterion that was met: {self.reached!r} is not a "
                f"measure within the tolerance {self.tolerance!r}."
            )


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
