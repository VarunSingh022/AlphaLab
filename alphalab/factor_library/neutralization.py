"""Removing a known exposure from a factor, by methods that are not interchangeable.

"Neutralize the factor" names three different operations, and a library that
offered one function for all of them would be claiming they are the same. They
are not: demeaning removes the level, group demeaning removes whatever the
groups explain, and beta neutralization removes what a continuous exposure
explains. A factor that is sector-neutral is not market-neutral, and neither is
dollar-neutral.

So each is a named function, each records what it did on the returned panel's
transform chain, and :func:`neutralize` does not exist.

What is implemented, and where the list stops
---------------------------------------------

* :func:`neutralize_mean` -- subtract the cross-sectional mean. Exact.
* :func:`neutralize_group` -- subtract each group's own mean. This *is* the
  residual of a cross-sectional regression on group dummies, computed exactly:
  for a design matrix of mutually exclusive indicators the least-squares fit is
  the group mean, so there is no matrix to invert and no conditioning to worry
  about.
* :func:`neutralize_beta` -- the residual of an ordinary least squares fit on
  one continuous exposure, with an intercept, through
  :func:`~alphalab.common.statistics.linear_regression`.
* :func:`neutralize_exposures` -- the residual of one least-squares fit on
  *several* continuous exposures at once, with an intercept (v3.11, ledger
  OFE-004).

Several exposures at once, and the case it refuses
--------------------------------------------------
Until v3.11 this was deliberately not offered: a general least-squares solve
over a rank-deficient or ill-conditioned design -- two exposures that are
nearly collinear, which factor exposures routinely are -- produces residuals
that look like a result and are numerically meaningless. The objection was to
the failure mode, not to the operation, and
:func:`~alphalab.common.linalg.least_squares` removes the failure mode: it
solves by Householder QR (never the normal equations, which square the
condition number) and *measures* the design's condition, refusing it above a
stated bound.

Each exposure is centred and scaled to unit length at every instant before
the solve, and the intercept is a unit column too. None of that changes the
residuals -- they depend only on the space the columns span -- but it makes
the condition number a measure of how collinear the *exposures* are, rather
than of the units a market capitalization happens to be quoted in. An instant
whose exposures are too collinear is not neutralized: it is counted as
skipped and its reason is kept in :attr:`NeutralizationReport.refusals`, with
the condition number that was measured.

Composing -- by group, then by beta -- remains the right tool when the
exposures are of different kinds; it is not the same operation as a joint
fit, and the transform chain records which of the two was done.

Alignment is checked, never assumed
-----------------------------------

Every function here takes its exposures or groups per instant and per asset,
and refuses an asset the cross-section holds and the exposure does not. Filling
the gap with a zero exposure would say "this asset has no market beta", which
is a claim, and dropping it silently would shrink the universe without saying
so.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.linalg import IllConditionedError, least_squares
from alphalab.common.statistics import linear_regression, mean
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.panel import FactorTransform, FeaturePanel

__all__ = [
    "MAXIMUM_EXPOSURE_CONDITION",
    "NeutralizationReport",
    "neutralize_beta",
    "neutralize_exposures",
    "neutralize_group",
    "neutralize_mean",
]

#: The largest condition number :func:`neutralize_exposures` accepts by default
#: for its centred, unit-scaled design. The residuals of a least-squares fit
#: lose about ``log10(condition)`` digits to rounding, so at this bound they
#: keep about ten of their sixteen; a design past it has exposures so nearly
#: collinear that which of them "explains" the factor is decided by rounding.
MAXIMUM_EXPOSURE_CONDITION: Final = 1e6


@dataclass(frozen=True, slots=True)
class NeutralizationReport:
    """A neutralized panel, and what the neutralization actually removed.

    Attributes:
        panel: The residual factor, carrying the transform that produced it.
        instants_neutralized: How many cross-sections were transformed.
        instants_skipped: How many were left out because they were too small,
            or because the exposure had no dispersion to regress on. Reported
            rather than silently absorbed: a neutralization that quietly
            skipped half the sample is a different study from one that did not.
        mean_absolute_removed: Average of ``|original - residual|`` over every
            transformed value. A number near zero says the exposure explained
            almost nothing, which is worth seeing before concluding a factor is
            "neutral".
        refusals: ``(instant, reason)`` for every cross-section that was large
            enough to transform and was refused anyway -- an exposure with no
            dispersion, or a design too ill-conditioned to fit. A subset of
            :attr:`instants_skipped`, in time order; the remainder were too
            small to try.
    """

    panel: FeaturePanel
    instants_neutralized: int
    instants_skipped: int
    mean_absolute_removed: float
    refusals: tuple[tuple[float, str], ...] = ()


def _finish(
    panel: FeaturePanel,
    rows: dict[float, dict[str, float]],
    removed: list[float],
    skipped: int,
    transform: FactorTransform,
    refusals: tuple[tuple[float, str], ...] = (),
) -> NeutralizationReport:
    if not rows:
        detail = f" The first refusal: {refusals[0][1]}" if refusals else ""
        raise FactorInputError(
            f"{transform.operation} transformed no instant at all; every one of the "
            f"{len(panel)} cross-sections was skipped. A panel of nothing is not a "
            f"neutralized factor.{detail}"
        )
    return NeutralizationReport(
        panel=panel.derive(rows, transform),
        instants_neutralized=len(rows),
        instants_skipped=skipped,
        mean_absolute_removed=sum(removed) / len(removed) if removed else 0.0,
        refusals=refusals,
    )


def neutralize_mean(panel: FeaturePanel, minimum_assets: int = 2) -> NeutralizationReport:
    """Subtract each cross-section's own mean.

    The residual sums to zero at every instant, which is what "dollar neutral
    before weighting" means for a factor score. It removes the *level* and
    nothing else: a factor that is entirely a market bet is unchanged in
    ranking by this, and a diagnostic that reads it as market-neutral would be
    wrong. :func:`neutralize_beta` is the one that removes a market exposure.

    Raises:
        FactorInputError: If ``minimum_assets`` is below 2, or if no instant
            held that many.
    """

    if minimum_assets < 2:
        raise FactorInputError(f"minimum_assets must be at least 2, got {minimum_assets}.")

    rows: dict[float, dict[str, float]] = {}
    removed: list[float] = []
    skipped = 0

    for stamp in panel.timestamps:
        section = panel.cross_section(stamp)
        if len(section) < minimum_assets:
            skipped += 1
            continue
        average = mean([section[asset] for asset in sorted(section)])
        rows[stamp] = {asset: value - average for asset, value in section.items()}
        removed.extend(abs(average) for _ in section)

    return _finish(
        panel,
        rows,
        removed,
        skipped,
        FactorTransform("neutralize_mean", f"minimum_assets={minimum_assets}"),
    )


def neutralize_group(
    panel: FeaturePanel,
    groups: Mapping[str, str],
    minimum_group_size: int = 2,
) -> NeutralizationReport:
    """Subtract each group's own cross-sectional mean.

    Exactly the residual of a regression on group dummies; see the module
    docstring. ``groups`` maps asset to its group label -- a sector, a country,
    a venue, whatever taxonomy the caller already has. AlphaLab supplies no
    taxonomy of its own here and invents no default group: an asset the mapping
    does not name is refused rather than pooled into an "other" bucket that
    nobody defined.

    Groups smaller than ``minimum_group_size`` at an instant are left
    untransformed at that instant and counted as skipped -- demeaning a group
    of one sets it to exactly zero, which is not a neutralization, it is a
    deletion.

    Raises:
        FactorInputError: If ``groups`` is empty, if ``minimum_group_size`` is
            below 2, if the panel holds an asset ``groups`` does not name, or
            if no group at any instant was large enough.
    """

    if not groups:
        raise FactorInputError(
            "Group neutralization needs a group for every asset and was given none."
        )
    if minimum_group_size < 2:
        raise FactorInputError(
            f"minimum_group_size must be at least 2, got {minimum_group_size}. Demeaning a "
            "group of one sets it to zero, which deletes the asset rather than neutralizing it."
        )

    unnamed = sorted(set(panel.symbols) - set(groups))
    if unnamed:
        raise FactorInputError(
            f"The panel holds {len(unnamed)} asset(s) the group mapping does not name: "
            f"{unnamed[:5]}. Pooling them into a default group would put assets together "
            "that nobody said belong together."
        )

    rows: dict[float, dict[str, float]] = {}
    removed: list[float] = []
    skipped = 0

    for stamp in panel.timestamps:
        section = panel.cross_section(stamp)
        members: dict[str, list[str]] = {}
        for asset in sorted(section):
            members.setdefault(groups[asset], []).append(asset)

        residuals: dict[str, float] = {}
        for assets in members.values():
            if len(assets) < minimum_group_size:
                continue
            average = mean([section[asset] for asset in assets])
            for asset in assets:
                residuals[asset] = section[asset] - average
                removed.append(abs(average))

        if not residuals:
            skipped += 1
            continue
        rows[stamp] = residuals

    return _finish(
        panel,
        rows,
        removed,
        skipped,
        FactorTransform(
            "neutralize_group",
            f"groups={len(set(groups.values()))},minimum_group_size={minimum_group_size}",
        ),
    )


def neutralize_beta(
    panel: FeaturePanel,
    exposures: Mapping[float, Mapping[str, float]],
    minimum_assets: int = 3,
) -> NeutralizationReport:
    """Keep the residual of an OLS fit of the factor on one continuous exposure.

    ``exposures`` maps instant to ``{asset: exposure}``, which is the shape
    :meth:`~alphalab.factor_library.panel.FeaturePanel.rows` already has -- so
    the exposure is normally another panel, computed the same way the factor
    was, and inherits the same dataset lineage.

    ``minimum_assets`` defaults to 3 rather than 2 because a two-point fit is
    exact and its residuals are identically zero: the regression would "remove"
    the entire factor and report a perfect neutralization, which is an artefact
    of the sample size rather than a finding.

    Raises:
        FactorInputError: If ``minimum_assets`` is below 3, if an instant the
            panel holds is missing from ``exposures``, if an asset in a
            cross-section has no exposure at that instant, or if no instant
            could be fitted at all.
    """

    if minimum_assets < 3:
        raise FactorInputError(
            f"minimum_assets must be at least 3, got {minimum_assets}. A two-point fit has "
            "zero residuals by construction and would report a perfect neutralization."
        )

    rows: dict[float, dict[str, float]] = {}
    removed: list[float] = []
    skipped = 0

    for stamp in panel.timestamps:
        section = panel.cross_section(stamp)
        if len(section) < minimum_assets:
            skipped += 1
            continue

        at_instant = exposures.get(stamp)
        if at_instant is None:
            raise FactorInputError(
                f"The panel holds a cross-section at {stamp!r} and the exposures do not. "
                "Neutralizing against an exposure that was not measured at that instant "
                "would mean substituting one measured at another."
            )

        assets = sorted(section)
        missing = [asset for asset in assets if asset not in at_instant]
        if missing:
            raise FactorInputError(
                f"At {stamp!r} the cross-section holds {missing[:5]} and the exposures do "
                "not. A zero exposure is a claim that the asset has none, not an absence "
                "of information."
            )

        try:
            fit = linear_regression(
                [section[asset] for asset in assets], [at_instant[asset] for asset in assets]
            )
        except AlphaLabValidationError:
            # A constant exposure across the cross-section: there is nothing to
            # neutralize against, and every slope fits equally well.
            skipped += 1
            continue

        rows[stamp] = dict(zip(assets, fit.residuals, strict=True))
        removed.extend(
            abs(section[asset] - residual)
            for asset, residual in zip(assets, fit.residuals, strict=True)
        )

    return _finish(
        panel,
        rows,
        removed,
        skipped,
        FactorTransform("neutralize_beta", f"minimum_assets={minimum_assets}"),
    )


def neutralize_exposures(
    panel: FeaturePanel,
    exposures: Mapping[str, Mapping[float, Mapping[str, float]]],
    *,
    minimum_assets: int,
    maximum_condition: float = MAXIMUM_EXPOSURE_CONDITION,
) -> NeutralizationReport:
    """Keep the residual of one least-squares fit on several exposures and an intercept.

    ``exposures`` maps an exposure's name to its values in the shape
    :func:`neutralize_beta` takes -- instant to ``{asset: exposure}``. With a
    single exposure this is :func:`neutralize_beta` computed by QR; with
    several it removes everything their joint span explains, which neutralizing
    against each in turn does not (the second pass reintroduces what the first
    removed whenever the exposures are correlated).

    Args:
        panel: The factor.
        exposures: At least one named exposure.
        minimum_assets: The smallest cross-section fitted. Must exceed the
            number of coefficients -- the exposures plus the intercept -- by at
            least one: a fit with as many coefficients as assets is exact, and
            its residuals are identically zero.
        maximum_condition: The largest condition number of the centred,
            unit-scaled design accepted at an instant; see the module
            docstring and :data:`MAXIMUM_EXPOSURE_CONDITION`.

    Raises:
        FactorInputError: If no exposure is named, ``minimum_assets`` is too
            small for the number of coefficients, ``maximum_condition`` is not
            a finite number of at least one, an instant the panel holds is
            missing from an exposure, an asset has no value for an exposure at
            an instant it is held, or no instant could be fitted at all.
    """

    names = sorted(exposures)
    if not names:
        raise FactorInputError("Neutralizing against exposures needs at least one exposure.")
    coefficients = len(names) + 1
    if minimum_assets < coefficients + 1:
        raise FactorInputError(
            f"minimum_assets must be at least {coefficients + 1} for {len(names)} exposure(s) "
            f"and an intercept, got {minimum_assets}. A fit with as many coefficients as "
            "assets is exact and would report a perfect neutralization."
        )
    if not math.isfinite(maximum_condition) or maximum_condition < 1.0:
        raise FactorInputError(
            f"maximum_condition must be a finite number of at least 1, got {maximum_condition!r}."
        )

    rows: dict[float, dict[str, float]] = {}
    removed: list[float] = []
    refusals: list[tuple[float, str]] = []
    skipped = 0

    for stamp in panel.timestamps:
        section = panel.cross_section(stamp)
        if len(section) < minimum_assets:
            skipped += 1
            continue

        assets = sorted(section)
        columns: list[list[float]] = [[1.0 / math.sqrt(len(assets))] * len(assets)]
        refusal: str | None = None
        for name in names:
            at_instant = exposures[name].get(stamp)
            if at_instant is None:
                raise FactorInputError(
                    f"The panel holds a cross-section at {stamp!r} and the exposure {name!r} "
                    "does not. Neutralizing against an exposure that was not measured at that "
                    "instant would mean substituting one measured at another."
                )
            missing = [asset for asset in assets if asset not in at_instant]
            if missing:
                raise FactorInputError(
                    f"At {stamp!r} the cross-section holds {missing[:5]} and the exposure "
                    f"{name!r} does not. A zero exposure is a claim that the asset has none, "
                    "not an absence of information."
                )
            values = [at_instant[asset] for asset in assets]
            centre = mean(values)
            centred = [value - centre for value in values]
            length = math.hypot(*centred)
            if length == 0.0:
                refusal = (
                    f"the exposure {name!r} is constant across the cross-section, so there "
                    "is nothing to neutralize against and every coefficient fits equally well"
                )
                break
            columns.append([value / length for value in centred])

        if refusal is None:
            design = [[column[row] for column in columns] for row in range(len(assets))]
            try:
                fit = least_squares(design, [section[asset] for asset in assets], maximum_condition)
            except IllConditionedError as error:
                refusal = (
                    f"the exposures {names} are too collinear to separate: {error.condition:.6g} "
                    f"against a maximum of {maximum_condition:g}"
                )
            else:
                rows[stamp] = dict(zip(assets, fit.residuals, strict=True))
                removed.extend(
                    abs(section[asset] - residual)
                    for asset, residual in zip(assets, fit.residuals, strict=True)
                )
                continue

        skipped += 1
        refusals.append((stamp, refusal))

    return _finish(
        panel,
        rows,
        removed,
        skipped,
        FactorTransform(
            "neutralize_exposures",
            f"exposures={','.join(names)},minimum_assets={minimum_assets},"
            f"maximum_condition={maximum_condition:g}",
        ),
        tuple(refusals),
    )
