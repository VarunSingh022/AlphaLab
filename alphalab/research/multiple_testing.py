"""Multiple-testing corrections, each with the assumption it rests on.

A search that tried two hundred configurations and reports the best one has
run two hundred tests, and a nominal 5% threshold applied to the winner is a
statement about one test that was never run alone. Until v3.11 the only
correction AlphaLab offered was the Bonferroni threshold on
:class:`~alphalab.research.overfitting.OverfittingReport` (ledger OFE-005), and
the module docstring there said why: every other correction needs an
assumption that had not been stated. This module states them.

Two error rates, not one
------------------------
A **family-wise** correction bounds the probability of *any* false rejection
among the family. A **false-discovery-rate** correction bounds the expected
*share* of false rejections among those made -- a weaker promise, bought with
more power, and the right one when the question is "which of these hundred
factors are worth a second look" rather than "is any of them real". The two
are not interchangeable, so :class:`Correction` says which one each method
controls, and the result carries it.

============================  ==========  ==========================================
Correction                    Controls    Valid when
============================  ==========  ==========================================
``BONFERRONI``                FWER        always (any dependence between the tests)
``HOLM``                      FWER        always; never rejects less than Bonferroni
``BENJAMINI_HOCHBERG``        FDR         the tests are independent or positively
                                          dependent (PRDS) -- *not* arbitrary
``BENJAMINI_YEKUTIELI``       FDR         always (any dependence), at the cost of a
                                          factor ``1 + 1/2 + ... + 1/m``
============================  ==========  ==========================================

A parameter sweep's trials are strongly and positively correlated, which is
the case Benjamini-Hochberg is proved for; a set of unrelated strategies may
not be, and Benjamini-Yekutieli is the one that needs no argument.

What a p-value must count
-------------------------
Every trial the search evaluated, not the survivors: the adjusted values
depend on ``m``, and dropping the losers before correcting is exactly the
error the correction exists to prevent. :func:`correct_p_values` takes the
whole family by name and reports its size.

Adjusted p-values, not only decisions
-------------------------------------
Each method's adjusted p-value is the smallest family-level ``alpha`` at which
that hypothesis would be rejected, so the decisions at any threshold can be
read off without re-running anything. They are made monotone in the raw
p-values, and ties receive identical adjusted values whatever order the
family was supplied in.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType

from alphalab.research.exceptions import ResearchValidationError

__all__ = [
    "Correction",
    "ErrorRate",
    "MultipleTestingResult",
    "correct_p_values",
]


class ErrorRate(Enum):
    """What a correction bounds."""

    #: The probability of at least one false rejection in the family.
    FAMILY_WISE = auto()
    #: The expected share of false rejections among the rejections made.
    FALSE_DISCOVERY = auto()


class Correction(Enum):
    """A multiple-testing procedure. See the module docstring for when each holds."""

    BONFERRONI = auto()
    HOLM = auto()
    BENJAMINI_HOCHBERG = auto()
    BENJAMINI_YEKUTIELI = auto()

    @property
    def controls(self) -> ErrorRate:
        """The error rate this procedure bounds at ``alpha``."""

        if self in (Correction.BONFERRONI, Correction.HOLM):
            return ErrorRate.FAMILY_WISE
        return ErrorRate.FALSE_DISCOVERY

    @property
    def assumption(self) -> str:
        """The dependence between tests under which the bound is proved."""

        if self is Correction.BENJAMINI_HOCHBERG:
            return (
                "the tests are independent or positively regression dependent (PRDS); "
                "under arbitrary dependence use BENJAMINI_YEKUTIELI"
            )
        return "none: valid under any dependence between the tests"


@dataclass(frozen=True, slots=True)
class MultipleTestingResult:
    """A family of p-values, corrected, and what was rejected at ``alpha``.

    Attributes:
        correction: The procedure applied.
        alpha: The family-level threshold the decisions were made at.
        p_values: Hypothesis name to its raw p-value, as supplied.
        adjusted: Hypothesis name to its adjusted p-value, in ``[0, 1]``.
        rejected: The hypotheses whose adjusted p-value is at most ``alpha``,
            ordered by adjusted p-value and then by name.
    """

    correction: Correction
    alpha: float
    p_values: Mapping[str, float]
    adjusted: Mapping[str, float]
    rejected: tuple[str, ...]

    @property
    def trials(self) -> int:
        """The size of the family: every test the search ran."""

        return len(self.p_values)

    @property
    def controls(self) -> ErrorRate:
        """What ``alpha`` bounds for this result."""

        return self.correction.controls

    def describe(self) -> str:
        """One line: the procedure, the family and what survived it."""

        return (
            f"{self.correction.name} ({self.controls.name}) at alpha={self.alpha:g}: "
            f"{len(self.rejected)} of {self.trials} rejected"
        )


def _validated(p_values: Mapping[str, float], alpha: float) -> list[tuple[float, str]]:
    if not p_values:
        raise ResearchValidationError("A multiple-testing correction needs at least one test.")
    if not 0.0 < alpha < 1.0:
        raise ResearchValidationError(f"alpha must lie strictly between 0 and 1, got {alpha!r}.")
    family: list[tuple[float, str]] = []
    for name, value in p_values.items():
        if not isinstance(name, str) or not name.strip():
            raise ResearchValidationError(f"Every hypothesis needs a name, got {name!r}.")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ResearchValidationError(f"The p-value of {name!r} is {value!r}, not a number.")
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ResearchValidationError(
                f"The p-value of {name!r} is {value!r}; a probability lies in [0, 1]."
            )
        family.append((float(value), name))
    family.sort()
    return family


def correct_p_values(
    p_values: Mapping[str, float], correction: Correction, alpha: float
) -> MultipleTestingResult:
    """Adjust a family of p-values and decide each hypothesis at ``alpha``.

    Args:
        p_values: Every test the search ran, by name. The whole family.
        correction: The procedure; see :class:`Correction`.
        alpha: The family-level error rate, strictly inside ``(0, 1)``.

    Raises:
        ResearchValidationError: If the family is empty, a name is blank, a
            p-value is not a finite number in ``[0, 1]``, or ``alpha`` is
            outside ``(0, 1)``.
    """

    family = _validated(p_values, alpha)
    size = len(family)
    adjusted: dict[str, float] = {}

    if correction is Correction.BONFERRONI:
        for value, name in family:
            adjusted[name] = min(1.0, size * value)
    elif correction is Correction.HOLM:
        # Step-down. A run of tied p-values takes one adjusted value: the running
        # maximum carries the first member's (larger) multiplier through the
        # rest, whatever order the names sorted the tie into.
        running = 0.0
        for index, (value, name) in enumerate(family):
            running = max(running, min(1.0, (size - index) * value))
            adjusted[name] = running
    else:
        # Step-up, from the largest p-value down. A tie takes the value of its
        # last member (the smallest multiplier) through the running minimum.
        scale = 1.0
        if correction is Correction.BENJAMINI_YEKUTIELI:
            scale = math.fsum(1.0 / k for k in range(1, size + 1))
        running = 1.0
        for index in range(size - 1, -1, -1):
            value, name = family[index]
            running = min(running, min(1.0, scale * size * value / (index + 1)))
            adjusted[name] = running

    rejected = tuple(
        name
        for _, name in sorted((adjusted[name], name) for _, name in family)
        if adjusted[name] <= alpha
    )
    return MultipleTestingResult(
        correction=correction,
        alpha=alpha,
        p_values=MappingProxyType({name: float(p_values[name]) for name in sorted(p_values)}),
        adjusted=MappingProxyType({name: adjusted[name] for name in sorted(adjusted)}),
        rejected=rejected,
    )
