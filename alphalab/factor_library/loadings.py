"""Factor loadings from the factor library's panels, with each factor's lineage kept.

A characteristic-based factor model reads an asset's *exposure* to a factor off
a cross-section of that characteristic: its momentum rank, its demeaned value
score. v3.2's panels already hold those cross-sections, identified by a feature
version and every transform applied since
(:attr:`~alphalab.factor_library.panel.FeaturePanel.lineage`). This module turns
one instant of several panels into the
:class:`~alphalab.analytics.risk_model.FactorLoadings` that portfolio
construction's factor constraints, risk decomposition and cross-strategy
crowding all read -- so there is one factor exposure model in the repository,
and it starts here.

What is not decided here
------------------------

How a characteristic becomes a loading -- raw, ranked, percentile-ranked,
demeaned, neutralized against a group -- is the caller's choice, made with the
panel transforms this package already has. The loadings are the panel values as
transformed, and each factor's lineage names the transform chain, so two
loading sets built differently never share an identity.

Absent is not zero, again
-------------------------

An asset missing from one factor's cross-section at the instant has no loading
on it, and the loadings are refused rather than filled: a zero would claim the
asset was measured and found neutral.
"""

from __future__ import annotations

from collections.abc import Mapping

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_model import FactorLoadings
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.panel import FeaturePanel

__all__ = ["loadings_from_panels"]


def loadings_from_panels(
    panels: Mapping[str, FeaturePanel],
    *,
    instant: float,
    source: str,
    asset_ids: Mapping[str, str] | None,
) -> FactorLoadings:
    """Read every panel's cross-section at ``instant`` as one factor's loadings.

    Args:
        panels: Factor name -> the panel holding its loadings. The name is the
            factor's identity in every constraint and report; the panel's
            lineage becomes the factor's lineage.
        instant: The research instant whose cross-section is read. Every panel
            must hold a value there for the same set of symbols.
        source: The factor model, in words.
        asset_ids: Symbol -> asset id, when the panels' symbols are not the
            asset ids the book and the covariance use; ``None`` when they are.
            Stated either way, because a symbol quietly read as an id is how a
            loading ends up attached to the wrong instrument.

    Raises:
        FactorInputError: If no panel is given, the panels come from different
            dataset versions, a panel holds nothing at ``instant``, the panels'
            cross-sections cover different symbols, or a symbol has no asset
            id.
    """

    if not panels:
        raise FactorInputError("Factor loadings need at least one panel.")
    versions = sorted({str(panel.dataset_version) for panel in panels.values()})
    if len(versions) > 1:
        raise FactorInputError(
            f"The panels were computed from different dataset versions {versions}; loadings "
            "measured on different data are not one factor model."
        )
    sections = {name: panels[name].cross_section(instant) for name in sorted(panels)}
    empty = [name for name, section in sections.items() if not section]
    if empty:
        raise FactorInputError(f"{empty} hold no cross-section at {instant!r}.")
    symbols = sorted(set().union(*(section.keys() for section in sections.values())))
    missing = [
        f"{symbol}/{name}"
        for name, section in sections.items()
        for symbol in symbols
        if symbol not in section
    ]
    if missing:
        raise FactorInputError(
            f"No loading at {instant!r} for {missing[:5]}"
            f"{' and more' if len(missing) > 5 else ''}. An asset absent from a factor's "
            "cross-section is not neutral to it."
        )
    if asset_ids is not None:
        unmapped = sorted(symbol for symbol in symbols if symbol not in asset_ids)
        if unmapped:
            raise FactorInputError(f"No asset id is given for {unmapped}.")
    identify = (lambda symbol: symbol) if asset_ids is None else asset_ids.__getitem__
    mapped = [identify(symbol) for symbol in symbols]
    if len(set(mapped)) != len(mapped):
        repeated = sorted({asset for asset in mapped if mapped.count(asset) > 1})
        raise FactorInputError(
            f"Several symbols map to the asset ids {repeated}; one of their loadings would "
            "silently replace the other."
        )
    try:
        return FactorLoadings.of(
            {
                identify(symbol): {name: section[symbol] for name, section in sections.items()}
                for symbol in symbols
            },
            source=source,
            lineage={name: panels[name].lineage for name in sections},
            as_of=instant,
        )
    except AnalyticsValidationError as error:
        raise FactorInputError(str(error)) from error
