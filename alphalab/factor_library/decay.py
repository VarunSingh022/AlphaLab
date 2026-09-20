"""How fast a factor's predictive content fades, measured at several horizons.

Decay is the IC read as a function of horizon. A factor whose rank IC is 0.06
at one period, 0.05 at five and 0.04 at twenty is saying something quite
different from one that reads 0.06, 0.01 and -0.02: the first can be traded
slowly, the second must be traded immediately or not at all, and their
turnover budgets are nothing alike.

:func:`factor_decay` therefore reports the *profile*, not a single half-life
number. A half-life requires assuming a functional form -- usually exponential
-- and fitting it to a handful of noisy points; the fit is then quoted as
though it were measured. :attr:`DecayProfile.horizons` gives the numbers, and
:attr:`DecayProfile.first_negative_horizon` gives the one derived quantity that
needs no model: the shortest horizon at which the mean rank IC turned negative,
or ``None`` if it never did.

Every horizon is measured on its own sample
-------------------------------------------

A longer horizon realizes fewer instants -- the last ``horizon`` observations
of every series have no forward return -- so the horizons in a profile are
*not* measured on the same sample, and the longer ones are measured on less
data. Each :class:`~alphalab.factor_library.ic.InformationCoefficient` carries
its own ``instants_measured``, and
:attr:`DecayProfile.instants_by_horizon` surfaces the same numbers together so
the shrinkage is visible at a glance rather than having to be looked for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.forward_returns import forward_returns
from alphalab.factor_library.ic import InformationCoefficient, information_coefficient
from alphalab.factor_library.observations import ObservationFrame
from alphalab.factor_library.panel import FeaturePanel

__all__ = ["DecayProfile", "factor_decay"]


@dataclass(frozen=True, slots=True)
class DecayProfile:
    """A factor's information coefficient across a set of forward horizons.

    Attributes:
        factor_lineage: The factor's version and transform chain.
        dataset_version: The data behind it, or ``None``.
        horizons: Horizon to the full IC result measured at it, in ascending
            horizon order when iterated through :attr:`ordered`.
    """

    factor_lineage: str
    dataset_version: str | None
    horizons: Mapping[int, InformationCoefficient]

    @property
    def ordered(self) -> tuple[tuple[int, InformationCoefficient], ...]:
        """The horizons in ascending order, which is how a profile reads."""

        return tuple(sorted(self.horizons.items()))

    @property
    def mean_rank_by_horizon(self) -> Mapping[int, float | None]:
        """Just the rank ICs, for plotting or for a metric mapping."""

        return {horizon: result.mean_rank for horizon, result in self.ordered}

    @property
    def instants_by_horizon(self) -> Mapping[int, int]:
        """How many cross-sections each horizon was actually measured on."""

        return {horizon: result.instants_measured for horizon, result in self.ordered}

    @property
    def first_negative_horizon(self) -> int | None:
        """The shortest horizon whose mean rank IC is below zero, if any.

        The one summary of a decay profile that assumes no functional form. A
        horizon that could not be measured is skipped rather than treated as
        non-negative.
        """

        for horizon, result in self.ordered:
            if result.mean_rank is not None and result.mean_rank < 0.0:
                return horizon
        return None


def factor_decay(
    factor: FeaturePanel,
    prices: ObservationFrame,
    horizons: Sequence[int],
    minimum_assets: int = 5,
) -> DecayProfile:
    """Measure one factor's IC at each of several forward horizons.

    ``prices`` is the observation frame the forward returns are computed from.
    It is passed rather than derived because the field a return should be
    measured on is the caller's decision -- a close-to-close study and a
    mid-to-mid study are different studies, and this function will not pick one.

    Raises:
        FactorInputError: If ``horizons`` is empty, if it repeats a horizon, if
            any horizon is not positive, or if the factor and the prices carry
            different dataset versions.
    """

    if not horizons:
        raise FactorInputError("A decay profile needs at least one horizon and was given none.")
    if len(set(horizons)) != len(horizons):
        raise FactorInputError(
            f"The horizons {sorted(horizons)} repeat one. Each horizon is measured once; a "
            "repeat would silently overwrite its own result."
        )
    if factor.dataset_version != prices.dataset_version:
        raise FactorInputError(
            f"The factor was computed on {factor.dataset_version!r} and the prices are from "
            f"{prices.dataset_version!r}. A decay profile across two datasets measures "
            "nothing."
        )

    measured = {
        horizon: information_coefficient(factor, forward_returns(prices, horizon), minimum_assets)
        for horizon in sorted(horizons)
    }

    return DecayProfile(
        factor_lineage=factor.lineage,
        dataset_version=factor.dataset_version,
        horizons=measured,
    )
