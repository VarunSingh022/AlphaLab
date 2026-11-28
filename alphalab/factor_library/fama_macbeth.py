"""Fama and MacBeth's two passes: what a factor is paid, priced across assets (v4.3, WS-25).

At each instant the forward returns are regressed, across the assets, on the
factors' values there: the first pass, by M2's least squares. A factor's
premium is the mean of its coefficients over the instants, with Newey and
West's standard error of that mean: the second pass (Fama and MacBeth, 1973).
The standard error's lag is stated. ``horizon - 1`` matches the overlap of
forward returns ``horizon`` periods long, as the information coefficient's
does (:mod:`~alphalab.factor_library.ic`).

What is measured, and what is skipped
-------------------------------------
An instant is measured where the returns and every factor have values for the
same assets: at least ``minimum_assets`` of them, more than the coefficients,
with a design the solver accepts under ``maximum_condition``. Any other
instant is skipped and counted, by its reason, and never padded. The standard
error takes the measured instants as consecutive: a skipped instant leaves a
gap its lag cannot see, which is one more reason the skips are reported.

Classes
-------
Least squares by Householder QR, means by ``fsum``, and the Newey-West
standard error's sums and square root are correctly rounded, in a fixed order:
every value is class 2, so the estimate's identity covers its values
(ADR-0058). Its data identity is derived from every value the regressions
read, never typed.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from alphalab.common.estimate import Estimate, NumericalClass, Setting
from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.linalg import IllConditionedError, least_squares
from alphalab.common.statistics import newey_west_standard_error
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.forward_returns import ForwardReturnPanel
from alphalab.factor_library.panel import FeaturePanel

__all__ = [
    "FAMA_MACBETH_DATA_SCHEME",
    "FAMA_MACBETH_METHOD",
    "FamaMacBeth",
    "fama_macbeth",
]

#: The method scheme: cross-sectional least squares, then the coefficients' means.
FAMA_MACBETH_METHOD: Final = "alphalab.factor_library.fama_macbeth.fama_macbeth/two-pass-ols.v1"

#: The scheme of the data identity: every value the regressions read, in order.
FAMA_MACBETH_DATA_SCHEME: Final = "alphalab.factor_library.fama_macbeth.data.v1"


@dataclass(frozen=True, slots=True)
class FamaMacBeth:
    """Each factor's premium, with the cross-sections it rests on.

    Attributes:
        estimate: The premiums -- ``intercept`` when there is one, then each
            factor by its feature id -- with their Newey-West standard
            errors; class 2.
        t_statistics: Each premium over its standard error.
        per_instant: Every measured instant's coefficients, in the estimate's
            order, so a caller can read the series rather than its mean.
        instants_measured: Cross-sections regressed.
        instants_too_small: Cross-sections with fewer than ``minimum_assets``
            assets present in the returns and every factor.
        instants_ill_conditioned: Cross-sections whose design the solver
            refused under ``maximum_condition``.
        observations: Asset-instant pairs regressed.
        horizon: The forward returns' horizon, in periods.
        lag: Their implementation lag, in observations.
        newey_west_lag: The standard error's lag, as stated.
        factor_lineage: Each factor's version and transforms, in order.
    """

    estimate: Estimate
    t_statistics: Mapping[str, float]
    per_instant: tuple[tuple[float, tuple[float, ...]], ...]
    instants_measured: int
    instants_too_small: int
    instants_ill_conditioned: int
    observations: int
    horizon: int
    lag: int
    newey_west_lag: int
    factor_lineage: tuple[str, ...]


def _require_count(value: object, name: str, least: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise FactorInputError(f"{name} is an integer of {least} at least, got {value!r}.")
    return value


def fama_macbeth(
    factors: Sequence[FeaturePanel],
    returns: ForwardReturnPanel,
    *,
    intercept: bool,
    minimum_assets: int,
    newey_west_lag: int,
    maximum_condition: float,
) -> FamaMacBeth:
    """Price ``factors`` across assets at each instant, and average the prices over time.

    Args:
        factors: One panel or more, each a different feature.
        returns: The forward returns each cross-section is regressed.
        intercept: Whether each cross-sectional regression has a constant: the
            return no factor explains.
        minimum_assets: The fewest assets a cross-section is regressed over;
            more than the coefficients.
        newey_west_lag: The standard error's lag: ``horizon - 1`` for
            overlapping forward returns, ``0`` for none.
        maximum_condition: The largest condition number a cross-section's
            design may have; one above it is skipped and counted.

    Raises:
        FactorInputError: If there is no factor, a factor is not a panel or
            repeats a feature id (or is called ``intercept`` beside one), a
            choice is malformed, or fewer than ``newey_west_lag + 2``
            cross-sections are measured.
    """

    if not isinstance(factors, Sequence) or not factors:
        raise FactorInputError("Fama-MacBeth prices one factor at least.")
    for index, panel in enumerate(factors):
        if not isinstance(panel, FeaturePanel):
            raise FactorInputError(f"Factor {index + 1} is not a FeaturePanel: {panel!r}.")
    if not isinstance(returns, ForwardReturnPanel):
        raise FactorInputError(f"returns must be a ForwardReturnPanel, got {returns!r}.")
    if not isinstance(intercept, bool):
        raise FactorInputError(f"intercept is a bool, got {intercept!r}.")
    names = [*(("intercept",) if intercept else ()), *(p.definition.feature_id for p in factors)]
    if len(set(names)) != len(names):
        raise FactorInputError(f"Each coefficient needs its own name, and these repeat: {names}.")
    width = len(names)
    minimum = _require_count(minimum_assets, "minimum_assets", width + 1)
    lag = _require_count(newey_west_lag, "newey_west_lag", 0)
    if (
        isinstance(maximum_condition, bool)
        or not isinstance(maximum_condition, int | float)
        or not (math.isfinite(maximum_condition) and maximum_condition >= 1.0)
    ):
        raise FactorInputError(
            f"maximum_condition is a finite number of 1 at least, got {maximum_condition!r}."
        )

    instants = sorted(set(returns.rows).intersection(*(panel.rows for panel in factors)))
    measured: list[tuple[float, tuple[float, ...]]] = []
    too_small = ill_conditioned = observations = 0
    lines = [
        FAMA_MACBETH_DATA_SCHEME,
        *(f"factor={panel.lineage}" for panel in factors),
        f"returns=horizon {returns.horizon}, lag {returns.lag}",
    ]
    for instant in instants:
        target = returns.cross_section(instant)
        sections = [panel.rows[instant] for panel in factors]
        assets = sorted(set(target).intersection(*sections))
        if len(assets) < minimum:
            too_small += 1
            continue
        design = [
            [*((1.0,) if intercept else ()), *(section[asset] for section in sections)]
            for asset in assets
        ]
        values = [target[asset] for asset in assets]
        try:
            solution = least_squares(design, values, float(maximum_condition))
        except IllConditionedError:
            ill_conditioned += 1
            continue
        measured.append((instant, solution.coefficients))
        observations += len(assets)
        lines.append(f"instant={instant!r}")
        lines.extend(
            f"{asset}," + ",".join(map(repr, row)) + f",{value!r}"
            for asset, row, value in zip(assets, design, values, strict=True)
        )

    if len(measured) < lag + 2:
        raise FactorInputError(
            f"{len(measured)} cross-sections were measured ({too_small} too small, "
            f"{ill_conditioned} ill-conditioned); a standard error at lag {lag} needs "
            f"{lag + 2} at least."
        )
    count = len(measured)
    premiums: dict[str, float] = {}
    errors: dict[str, float] = {}
    for position, name in enumerate(names):
        series = [coefficients[position] for _, coefficients in measured]
        premiums[name] = math.fsum(series) / count
        try:
            errors[name] = newey_west_standard_error(series, lag)
        except AlphaLabValidationError as error:
            raise FactorInputError(
                f"{name}'s coefficients have no standard error: {error}"
            ) from error
    settings: dict[str, Setting] = {
        "horizon": returns.horizon,
        "intercept": intercept,
        "lag": returns.lag,
        "maximum_condition": float(maximum_condition),
        "minimum_assets": minimum,
        "newey_west_lag": lag,
    }
    try:
        estimate = Estimate(
            method=FAMA_MACBETH_METHOD,
            data_id=hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
            parameters=premiums,
            standard_errors=errors,
            settings=settings,
            numerical_class=NumericalClass.CORRECTLY_ROUNDED,
        )
    except AlphaLabValidationError as error:
        raise FactorInputError(f"The factors' feature ids cannot name premiums: {error}") from error
    return FamaMacBeth(
        estimate=estimate,
        t_statistics=MappingProxyType({name: premiums[name] / errors[name] for name in names}),
        per_instant=tuple(measured),
        instants_measured=count,
        instants_too_small=too_small,
        instants_ill_conditioned=ill_conditioned,
        observations=observations,
        horizon=returns.horizon,
        lag=returns.lag,
        newey_west_lag=lag,
        factor_lineage=tuple(panel.lineage for panel in factors),
    )
