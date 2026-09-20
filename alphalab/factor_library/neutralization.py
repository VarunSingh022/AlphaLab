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

Neutralization against *several* continuous exposures at once is deliberately
not offered. It needs a general least-squares solve, and a rank-deficient or
ill-conditioned design -- two exposures that are nearly collinear, which
factor exposures routinely are -- produces residuals that look like a result
and are numerically meaningless. The v3.2 roadmap asks only for methods that
"can be made deterministic and well-defined within the current architecture",
and a solver whose failure mode is a plausible wrong answer does not meet that
bar. A caller who needs it composes: neutralize by group, then by beta, and the
transform chain records that this is what they did.

Alignment is checked, never assumed
-----------------------------------

Every function here takes its exposures or groups per instant and per asset,
and refuses an asset the cross-section holds and the exposure does not. Filling
the gap with a zero exposure would say "this asset has no market beta", which
is a claim, and dropping it silently would shrink the universe without saying
so.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import linear_regression, mean
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.panel import FactorTransform, FeaturePanel

__all__ = [
    "NeutralizationReport",
    "neutralize_beta",
    "neutralize_group",
    "neutralize_mean",
]


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
    """

    panel: FeaturePanel
    instants_neutralized: int
    instants_skipped: int
    mean_absolute_removed: float


def _finish(
    panel: FeaturePanel,
    rows: dict[float, dict[str, float]],
    removed: list[float],
    skipped: int,
    transform: FactorTransform,
) -> NeutralizationReport:
    if not rows:
        raise FactorInputError(
            f"{transform.operation} transformed no instant at all; every one of the "
            f"{len(panel)} cross-sections was skipped. A panel of nothing is not a "
            "neutralized factor."
        )
    return NeutralizationReport(
        panel=panel.derive(rows, transform),
        instants_neutralized=len(rows),
        instants_skipped=skipped,
        mean_absolute_removed=sum(removed) / len(removed) if removed else 0.0,
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
