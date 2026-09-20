"""What a factor is concentrated in, along dimensions the caller already has.

A factor score of 0.8 on twenty assets means one thing if the twenty are spread
across ten sectors and something else entirely if nineteen of them are
semiconductors. :func:`factor_exposure` reports that composition -- by asset,
and by any grouping the caller supplies -- so a result can carry it rather than
leaving it to be discovered after the fact.

The taxonomy is the caller's
----------------------------

``groups`` maps asset to a label and AlphaLab supplies none. This is deliberate
and is the boundary the v3.2 roadmap draws: a sector scheme, a country scheme
or a venue scheme is a classification somebody maintains, and inventing one
here would put a second, worse one in the repository beside whatever the caller
already uses. Where AlphaLab *does* hold a classification --
:mod:`alphalab.instrument` derives a sector for an instrument it has a
declaration for -- a caller passes it in as the mapping; this module does not
reach for it, because reaching would give ``factor_library`` an edge to a
package it has no other reason to know about.

An asset with no group is refused rather than pooled, for the same reason
:func:`~alphalab.factor_library.neutralization.neutralize_group` refuses it.

Weights, not scores
-------------------

Exposure is a property of a *book*, so the input is weights. A raw factor score
is not a weight, and summing scores by sector would produce a number whose
units depend on the factor's scale.
:func:`~alphalab.factor_library.turnover.weights_from_buckets` is the explicit
construction from a bucketed panel.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from alphalab.factor_library.exceptions import FactorInputError

__all__ = ["ExposureReport", "factor_exposure"]


@dataclass(frozen=True, slots=True)
class ExposureReport:
    """Where a weighting's exposure sits, at one instant or on average.

    Attributes:
        instants: How many books were aggregated.
        by_asset: Asset to its mean weight across those books. An asset absent
            from a book contributes zero to its own mean, because it genuinely
            held none there.
        by_group: Group label to its mean *net* weight -- longs less shorts.
            The number a sector-neutrality claim is checked against.
        gross_by_group: Group label to its mean *gross* weight, the sum of
            absolute weights. A group that is net flat because it holds equal
            longs and shorts is not unexposed to it, and this is what says so.
        gross_exposure: Mean sum of absolute weights across the whole book.
        net_exposure: Mean sum of signed weights across the whole book.
        largest_group: The group with the largest mean gross weight, or ``None``
            when no grouping was supplied.
        concentration: The largest single asset's share of mean gross weight,
            in ``[0, 1]``. A book of one name reads 1.0.
    """

    instants: int
    by_asset: Mapping[str, float]
    by_group: Mapping[str, float]
    gross_by_group: Mapping[str, float]
    gross_exposure: float
    net_exposure: float
    largest_group: str | None
    concentration: float


def factor_exposure(
    weights: Mapping[float, Mapping[str, float]],
    groups: Mapping[str, str] | None = None,
) -> ExposureReport:
    """Aggregate a weighting's exposure by asset and, optionally, by group.

    Every mean is taken over *all* instants, not only the ones an asset was
    present in: an asset held in one book out of ten has a mean weight of a
    tenth of its held weight, which is what "mean exposure" means. Averaging
    only over the instants it was held would report the size of the position
    rather than the exposure of the book.

    Raises:
        FactorInputError: If ``weights`` is empty, or if a grouping is supplied
            that does not name every asset the books hold.
    """

    if not weights:
        raise FactorInputError("An exposure report needs at least one book and was given none.")

    instants = len(weights)
    assets = sorted({asset for book in weights.values() for asset in book})

    if groups is not None:
        unnamed = [asset for asset in assets if asset not in groups]
        if unnamed:
            raise FactorInputError(
                f"The books hold {len(unnamed)} asset(s) the group mapping does not name: "
                f"{unnamed[:5]}. Pooling them into a default group would report an exposure "
                "to a category nobody defined."
            )

    by_asset = {
        asset: sum(book.get(asset, 0.0) for book in weights.values()) / instants for asset in assets
    }
    gross_by_asset = {
        asset: sum(abs(book.get(asset, 0.0)) for book in weights.values()) / instants
        for asset in assets
    }

    by_group: dict[str, float] = {}
    gross_by_group: dict[str, float] = {}
    if groups is not None:
        for asset in assets:
            label = groups[asset]
            by_group[label] = by_group.get(label, 0.0) + by_asset[asset]
            gross_by_group[label] = gross_by_group.get(label, 0.0) + gross_by_asset[asset]

    total_gross = sum(gross_by_asset.values())

    return ExposureReport(
        instants=instants,
        by_asset=by_asset,
        by_group=by_group,
        gross_by_group=gross_by_group,
        gross_exposure=total_gross,
        net_exposure=sum(by_asset.values()),
        largest_group=(
            max(sorted(gross_by_group), key=lambda label: gross_by_group[label])
            if gross_by_group
            else None
        ),
        concentration=(max(gross_by_asset.values()) / total_gross if total_gross > 0.0 else 0.0),
    )
