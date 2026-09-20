"""Turning a definition and a dataset into a feature, with the lineage attached.

This is the entry point the rest of v3.2 computes through.
:func:`compute_feature` takes a
:class:`~alphalab.factor_library.definition.FeatureDefinition` and an
:class:`~alphalab.factor_library.observations.ObservationFrame` and produces a
:class:`~alphalab.factor_library.series.FeatureSeries` per symbol, carrying the
dataset version the frame was read from. :func:`compute_panel` does the same and
indexes the result into a
:class:`~alphalab.factor_library.panel.FeaturePanel`.

Why a frame and not a dataset
-----------------------------

``compute_feature`` takes the frame rather than the ``Dataset`` so that reading
a field happens exactly once no matter how many features are computed over it.
Ten features on one dataset's closes read the records once, not ten times --
which is the difference between linear and ten-times-linear on a million-row
dataset, and is measured in ``benchmarks/benchmark_feature_engineering.py``.
:func:`~alphalab.factor_library.observations.observations_from_dataset` is the
one-line bridge for a caller who has a dataset and one feature.

Cross-sectional features are computed instant by instant
--------------------------------------------------------

A ``CROSS_SECTIONAL`` definition has no trailing window and cannot be computed
one symbol at a time, so :func:`compute_feature` pivots the frame, computes
each instant's cross-section, and pivots back into per-symbol series. Instants
whose cross-section is too small to be meaningful are dropped under
:attr:`~alphalab.factor_library.definition.MissingPolicy.SKIP` and refused
under ``REFUSE`` -- not filled, and not computed over a universe of one.

Nothing here converts anything to a factor value
------------------------------------------------

:func:`to_factor_results` is the seam to Feature Store, and it is the only
place the two vocabularies meet: a feature series becomes a tuple of
:class:`~alphalab.factor_library.result.FactorResult`, which structurally
satisfies :class:`~alphalab.feature_store.protocol.FeatureValueProtocol` and
can be written through
:class:`~alphalab.feature_store.adapter.FeatureValueAdapter` without Feature
Store importing this package -- the decoupling the v2 architecture established
and that v3.2 uses rather than replaces.
"""

from __future__ import annotations

from collections.abc import Sequence

from alphalab.factor_library.definition import (
    FeatureDefinition,
    FeatureScope,
    MissingPolicy,
)
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.observations import ObservationFrame
from alphalab.factor_library.panel import FeaturePanel
from alphalab.factor_library.primitives import compute_cross_section, compute_time_series
from alphalab.factor_library.result import FactorResult
from alphalab.factor_library.series import FeatureSeries

__all__ = ["compute_feature", "compute_features", "compute_panel", "to_factor_results"]


def _require_field(definition: FeatureDefinition, frame: ObservationFrame) -> None:
    if frame.source_field is not definition.source_field:
        raise FactorInputError(
            f"{definition.feature_id!r} reads {definition.source_field.name} and the frame "
            f"holds {frame.source_field.name}. Computing it over the wrong field would "
            "produce a number that looks right and measures something else."
        )


def compute_feature(
    definition: FeatureDefinition, frame: ObservationFrame
) -> tuple[FeatureSeries, ...]:
    """Compute one feature over every symbol in ``frame``.

    Returns one series per symbol, in sorted symbol order so that two runs
    produce the same sequence. Under
    :attr:`~alphalab.factor_library.definition.MissingPolicy.SKIP` a symbol
    with too little history to produce any value still gets a series -- an
    empty one, which reports its own ``observations`` and ``warmup_periods`` --
    rather than being dropped, so a caller can tell "no history" from "not in
    the universe".

    Raises:
        FactorInputError: If the frame holds a different field than the
            definition reads, or -- under
            :attr:`~alphalab.factor_library.definition.MissingPolicy.REFUSE` --
            if any symbol has fewer observations than the warmup consumes.
        FactorComputationError: If any value the computation needs is undefined.
    """

    _require_field(definition, frame)

    if definition.scope is FeatureScope.CROSS_SECTIONAL:
        return _compute_cross_sectional(definition, frame)

    warmup = definition.warmup_periods
    computed: list[FeatureSeries] = []

    for symbol in frame.symbols:
        row = frame.series[symbol]
        if definition.missing is MissingPolicy.REFUSE and len(row) <= warmup:
            raise FactorInputError(
                f"{definition.feature_id!r} consumes {warmup} observation(s) of warmup and "
                f"{symbol} has {len(row)}, so it produces no value at all. The definition "
                "asks to be refused rather than skipped; use MissingPolicy.SKIP to accept "
                "a shorter series."
            )

        values = compute_time_series(definition, row.values, row.timestamps, frame.timezone_name)
        kept = [
            (stamp, value)
            for stamp, value in zip(row.timestamps, values, strict=True)
            if value is not None
        ]
        computed.append(
            FeatureSeries(
                definition=definition,
                symbol=symbol,
                timestamps=tuple(stamp for stamp, _ in kept),
                values=tuple(value for _, value in kept),
                dataset_version=frame.dataset_version,
                timezone_name=frame.timezone_name,
                observations=len(row),
                warmup_periods=min(warmup, len(row)),
            )
        )

    return tuple(computed)


def _compute_cross_sectional(
    definition: FeatureDefinition, frame: ObservationFrame
) -> tuple[FeatureSeries, ...]:
    """One cross-section per instant, pivoted back into per-symbol series."""

    by_instant: dict[float, dict[str, float]] = {}
    for symbol in frame.symbols:
        row = frame.series[symbol]
        for stamp, value in zip(row.timestamps, row.values, strict=True):
            by_instant.setdefault(stamp, {})[symbol] = value

    results: dict[str, list[tuple[float, float]]] = {symbol: [] for symbol in frame.symbols}

    for stamp in sorted(by_instant):
        universe = by_instant[stamp]
        if len(universe) < 2:
            if definition.missing is MissingPolicy.REFUSE:
                raise FactorInputError(
                    f"{definition.feature_id!r} is cross-sectional and only "
                    f"{len(universe)} asset(s) have a value at {stamp!r}. A cross-section "
                    "of one is the asset itself; use MissingPolicy.SKIP to omit such "
                    "instants."
                )
            continue
        for symbol, value in compute_cross_section(definition, universe).items():
            results[symbol].append((stamp, value))

    return tuple(
        FeatureSeries(
            definition=definition,
            symbol=symbol,
            timestamps=tuple(stamp for stamp, _ in results[symbol]),
            values=tuple(value for _, value in results[symbol]),
            dataset_version=frame.dataset_version,
            timezone_name=frame.timezone_name,
            observations=len(frame.series[symbol]),
            warmup_periods=0,
        )
        for symbol in frame.symbols
    )


def compute_panel(definition: FeatureDefinition, frame: ObservationFrame) -> FeaturePanel:
    """Compute one feature over every symbol and index it into a panel.

    The shape every cross-sectional diagnostic in v3.2 consumes.

    Raises:
        FactorInputError: For every reason :func:`compute_feature` does, and if
            no symbol produced a single value -- a panel with nothing in it
            would make each statistic over it vacuously true.
    """

    series = compute_feature(definition, frame)
    populated = [row for row in series if len(row) > 0]
    if not populated:
        raise FactorInputError(
            f"{definition.feature_id!r} produced no values on any of the "
            f"{len(series)} symbol(s) in the frame; every series was shorter than the "
            f"{definition.warmup_periods}-observation warmup."
        )
    return FeaturePanel.of(populated)


def to_factor_results(series: FeatureSeries, version: int) -> tuple[FactorResult, ...]:
    """Render a feature series as Feature Store-writable values.

    ``version`` is the Feature Store *registration* version, which is a
    different number from the feature's derived ``feature_version`` and is not
    derivable from it: the registration is a fact about a registry this package
    does not import. It is therefore supplied by the caller, who holds the
    :class:`~alphalab.feature_store.metadata.FeatureMetadata` that states it.

    Raises:
        FactorInputError: If ``version`` is not positive. Feature Store's
            versions start at 1, and a zero here would be accepted by this
            package and refused by that one.
    """

    if version < 1:
        raise FactorInputError(f"A Feature Store registration version starts at 1, got {version}.")

    return tuple(
        FactorResult(
            feature_id=series.definition.feature_id,
            version=version,
            asset_id=series.symbol,
            value=value,
            timestamp=stamp,
        )
        for stamp, value in zip(series.timestamps, series.values, strict=True)
    )


def compute_features(
    definitions: Sequence[FeatureDefinition], frame: ObservationFrame
) -> dict[str, tuple[FeatureSeries, ...]]:
    """Compute several features over one frame, keyed by ``feature_version``.

    Keyed by the *derived version* rather than the ``feature_id``, so two
    definitions that share an id but differ in a window cannot silently
    overwrite each other -- which is the whole reason the identity is derived.

    Raises:
        FactorInputError: If two definitions in ``definitions`` have the same
            derived version, which means the same computation was requested
            twice; or for any reason :func:`compute_feature` raises.
    """

    computed: dict[str, tuple[FeatureSeries, ...]] = {}
    for definition in definitions:
        version = definition.feature_version
        if version in computed:
            raise FactorInputError(
                f"{definition.feature_id!r} was requested twice with identical parameters. "
                "Computing it once and reading it twice is what the derived version is for."
            )
        computed[version] = compute_feature(definition, frame)
    return computed
