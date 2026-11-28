"""S-02's screening stage: candidate pairs tested for cointegration, the family corrected (v4.3).

:func:`cointegration_screen` runs Engle and Granger's test on every candidate
pair over one stated sample, then corrects the whole family's p-values by a
stated procedure (:mod:`~alphalab.research.multiple_testing`) at a stated
``alpha`` (ADR-0074 section 8). It returns every pair's result, the ones the
test refused included, and the corrected family.

Every pair is counted
---------------------
A screen that tried two hundred pairs has run two hundred tests, and the
family's size is every candidate's, not the survivors'. A pair the test
refuses -- its series too nearly multiples of each other for the regression,
say -- is still a candidate tried: it enters the family with a p-value of one,
counted and never rejected. So does a pair whose p-value is only bounded from
below, beyond MacKinnon's table: one is at least the true value, as the
correction needs. A p-value bounded from above enters as its bound, which is
at least the true value too. A correction fed values no smaller than the true
ones keeps its promise.

What a rejection says
---------------------
The null of each test is no cointegration. A pair the corrected family
rejects is a candidate whose residuals the test finds stationary at the
family's level -- a candidate for the next stage, not a strategy. Its hedge
ratio is ``beta_1`` of the test's cointegrating regression, fitted on the
screen's sample only.

Its identity
------------
``screen_id`` is SHA-256 over the pairs in order, the identity of each series
over the sample, every choice, and each test's class-2 values: its statistic,
its lags and observations, and the identities of its two regressions, which
cover their coefficients. The p-values pass through the normal distribution,
class 3, and the decisions follow from them: both are recorded beside the
identity, never in it (ADR-0058).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from alphalab.common.linalg import IllConditionedError
from alphalab.econometrics import (
    CointegrationTest,
    Deterministic,
    EconometricsInputError,
    FixedLags,
    LagRule,
    PBound,
    SelectedLags,
    engle_granger,
    require_series,
    series_identity,
)
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.multiple_testing import Correction, MultipleTestingResult, correct_p_values

__all__ = [
    "SCREEN_SCHEME",
    "CointegrationScreen",
    "ScreenedPair",
    "cointegration_screen",
]

#: The scheme of :attr:`CointegrationScreen.screen_id`.
SCREEN_SCHEME: Final = "alphalab.research.cointegration_screen.v1"


@dataclass(frozen=True, slots=True)
class ScreenedPair:
    """One candidate pair's test, or why the test could not be run.

    Attributes:
        dependent: The series regressed, by name.
        regressor: The series it is regressed on, by name.
        test: Engle and Granger's test over the screen's sample, or ``None``
            when it was refused.
        refusal: Why the test was refused, or ``None``.
        p_value: What the pair enters the family with: the test's p-value,
            its bound when the bound is from above, and one when the test was
            refused or its p-value is bounded only from below.
    """

    dependent: str
    regressor: str
    test: CointegrationTest | None
    refusal: str | None
    p_value: float

    @property
    def hypothesis(self) -> str:
        """The pair's name in the family: ``dependent~regressor``."""

        return f"{self.dependent}~{self.regressor}"

    @property
    def hedge_ratio(self) -> float | None:
        """``beta_1`` of the cointegrating regression, or ``None`` when refused."""

        return None if self.test is None else self.test.cointegrating.parameters["beta_1"]


@dataclass(frozen=True, slots=True)
class CointegrationScreen:
    """Every candidate pair's test over one sample, and the family corrected.

    Attributes:
        pairs: Every candidate, in the order given.
        family: Every candidate's p-value, corrected at ``alpha``.
        start: The sample's first index, the same for every series.
        stop: One past its last.
        screen_id: SHA-256 over the problem and the class-2 results.
    """

    pairs: tuple[ScreenedPair, ...]
    family: MultipleTestingResult
    start: int
    stop: int
    screen_id: str

    @property
    def selected(self) -> tuple[ScreenedPair, ...]:
        """The pairs the corrected family rejects no cointegration for, in the order given."""

        rejected = set(self.family.rejected)
        return tuple(pair for pair in self.pairs if pair.hypothesis in rejected)

    @property
    def refused(self) -> tuple[ScreenedPair, ...]:
        """The pairs whose test was refused: counted, never selected."""

        return tuple(pair for pair in self.pairs if pair.test is None)


def _lag_text(lags: LagRule) -> str:
    if isinstance(lags, FixedLags):
        return f"fixed {lags.count}"
    return f"{lags.criterion.value} to {lags.maximum}, threshold {lags.threshold!r}"


def _entered(test: CointegrationTest) -> float:
    value = test.p_value
    if value is None or value.bound is PBound.AT_LEAST:
        return 1.0
    return value.value


def cointegration_screen(
    series: Mapping[str, Sequence[float]],
    pairs: Sequence[tuple[str, str]],
    *,
    start: int,
    stop: int,
    deterministic: Deterministic,
    lags: LagRule,
    maximum_condition: float,
    correction: Correction,
    alpha: float,
) -> CointegrationScreen:
    """Test every candidate pair for cointegration over ``[start, stop)``, and correct the family.

    Args:
        series: Every series a pair names, by name, aligned and as long as
            each other.
        pairs: The candidates, each ``(dependent, regressor)``: the dependent
            is regressed on the regressor, and ``beta_1`` is the hedge ratio.
        start: The sample's first index.
        stop: One past its last.
        deterministic: The cointegrating regression's terms: a constant at
            least.
        lags: The residuals' regression's lagged differences.
        maximum_condition: The largest condition number a regression may have.
        correction: The multiple-testing procedure over the family.
        alpha: The family's level, strictly inside ``(0, 1)``.

    Raises:
        ResearchValidationError: If there is no pair, a pair names a series
            not given, names one series twice or repeats another pair, the
            series differ in length or hold a value that is not finite, the
            sample is not within them, or a choice is malformed.
    """

    if not isinstance(pairs, Sequence) or not pairs:
        raise ResearchValidationError("A screen tests one candidate pair at least.")
    if not isinstance(correction, Correction):
        raise ResearchValidationError(f"correction must be a Correction, got {correction!r}.")
    if not isinstance(deterministic, Deterministic) or deterministic is Deterministic.NONE:
        raise ResearchValidationError(
            f"An Engle-Granger regression states a constant at least, got {deterministic!r}."
        )
    if not isinstance(lags, FixedLags | SelectedLags):
        raise ResearchValidationError(f"lags must be FixedLags or SelectedLags, got {lags!r}.")
    if (
        isinstance(maximum_condition, bool)
        or not isinstance(maximum_condition, int | float)
        or not (math.isfinite(maximum_condition) and maximum_condition >= 1.0)
    ):
        raise ResearchValidationError(
            f"maximum_condition is a finite number of 1 at least, got {maximum_condition!r}."
        )
    seen: set[tuple[str, str]] = set()
    for candidate in pairs:
        if (
            not isinstance(candidate, tuple)
            or len(candidate) != 2
            or not all(isinstance(name, str) and name for name in candidate)
        ):
            raise ResearchValidationError(f"A pair is two series' names, got {candidate!r}.")
        if candidate[0] == candidate[1]:
            raise ResearchValidationError(f"Pair {candidate!r} names one series twice.")
        if candidate in seen:
            raise ResearchValidationError(f"Pair {candidate!r} is a candidate twice.")
        for name in candidate:
            if name not in series:
                raise ResearchValidationError(
                    f"Pair {candidate!r} names {name!r}, which is not given."
                )
        seen.add(candidate)
    named = sorted({name for pair in pairs for name in pair})
    try:
        values = {name: require_series(series[name], 1, f"Series {name!r}") for name in named}
    except EconometricsInputError as error:
        raise ResearchValidationError(str(error)) from error
    lengths = {len(each) for each in values.values()}
    if len(lengths) != 1:
        raise ResearchValidationError(
            f"The series are aligned, so one length; they have {sorted(lengths)}."
        )
    length = lengths.pop()
    for name, value in (("start", start), ("stop", stop)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ResearchValidationError(f"{name} is an index, got {value!r}.")
    if not 0 <= start < stop <= length:
        raise ResearchValidationError(
            f"The sample [{start}, {stop}) is not within the series' {length} values."
        )
    sampled = {name: each[start:stop] for name, each in values.items()}

    screened = []
    for dependent, regressor in pairs:
        try:
            test = engle_granger(
                sampled[dependent],
                [sampled[regressor]],
                deterministic=deterministic,
                lags=lags,
                maximum_condition=float(maximum_condition),
            )
        except (EconometricsInputError, IllConditionedError) as error:
            screened.append(ScreenedPair(dependent, regressor, None, str(error), 1.0))
            continue
        screened.append(ScreenedPair(dependent, regressor, test, None, _entered(test)))
    family = correct_p_values(
        {pair.hypothesis: pair.p_value for pair in screened}, correction, alpha
    )

    identities = {name: series_identity(each) for name, each in sampled.items()}
    lines = [
        SCREEN_SCHEME,
        f"sample=[{start}, {stop})",
        f"deterministic={deterministic.value}",
        f"lags={_lag_text(lags)}",
        f"maximum_condition={float(maximum_condition)!r}",
        f"correction={correction.name}",
        f"alpha={float(alpha)!r}",
    ]
    for pair in screened:
        lines.append(
            f"pair={pair.dependent}:{identities[pair.dependent]}~"
            f"{pair.regressor}:{identities[pair.regressor]}"
        )
        if pair.test is None:
            lines.append("refused")
        else:
            test = pair.test
            lines.append(
                f"statistic={test.statistic!r},lags={test.lags},observations={test.observations},"
                f"cointegrating={test.cointegrating.estimate_id},"
                f"residuals={test.residual_regression.estimate_id}"
            )
    return CointegrationScreen(
        pairs=tuple(screened),
        family=family,
        start=start,
        stop=stop,
        screen_id=hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
    )
