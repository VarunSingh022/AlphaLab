"""The feature computations themselves.

Every kind in :class:`~alphalab.factor_library.definition.FeatureKind` is
implemented here against one contract: given a sequence of values in
chronological order, return a sequence of the *same length* in which position
``i`` holds the feature's value at observation ``i``, or ``None`` where the
window is not yet complete.

Same length, and ``None`` for warmup
------------------------------------

Returning a shorter tuple -- which is what :mod:`alphalab.analytics.rolling`
does, correctly, for equity-curve analytics -- would make the caller
responsible for re-aligning values to timestamps, and an off-by-one there is a
look-ahead bug that produces entirely plausible numbers. Here the alignment is
structural: position ``i`` is observation ``i``, always, and the warmup is
visible as ``None`` rather than inferred from a length difference.

Trailing and inclusive, with no exceptions
------------------------------------------

The value at observation ``i`` is computed from observations at or before
``i``. Every window is trailing and includes the current observation; no kind
here reads ``values[i + 1]``. That is the property
``tests/regression/test_features_cannot_see_the_future.py`` asserts directly,
by recomputing each feature on a truncated series and requiring the overlapping
values to be identical -- a feature that peeked would change when the future
was removed.

Undefined is raised, never substituted
--------------------------------------

A return off a non-positive base, a z-score over a constant window and a ratio
over a zero mean are undefined. Each raises
:class:`~alphalab.factor_library.exceptions.FactorComputationError` naming the
observation, rather than yielding ``0.0`` or ``None`` -- ``None`` here means
"warmup", and overloading it with "undefined" would make a leakage check unable
to tell the two apart.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import mean, ranks, sample_variance, standardize
from alphalab.data.time import resolve_zone
from alphalab.factor_library.definition import (
    FeatureDefinition,
    FeatureKind,
    FeatureScope,
)
from alphalab.factor_library.exceptions import FactorComputationError, FactorInputError

__all__ = ["compute_cross_section", "compute_time_series"]


def _returns(values: Sequence[float], start: int, stop: int) -> list[float]:
    """One-period simple returns over ``values[start:stop]``.

    Raises:
        FactorComputationError: On a non-positive denominator, which makes the
            return undefined rather than zero.
    """

    series = []
    for index in range(start + 1, stop):
        previous = values[index - 1]
        if previous <= 0.0:
            raise FactorComputationError(
                f"A simple return is undefined at observation {index}: the previous value "
                f"is {previous!r}. A series that reaches zero or below has no return at "
                "that point, and reporting one would invent a number."
            )
        series.append(values[index] / previous - 1.0)
    return series


def compute_time_series(
    definition: FeatureDefinition,
    values: Sequence[float],
    timestamps: Sequence[float],
    timezone_name: str,
) -> tuple[float | None, ...]:
    """Compute a time-series feature, aligned to ``values`` position for position.

    ``timestamps`` and ``timezone_name`` are read only by the calendar kinds;
    both are required arguments rather than optional ones because a feature
    layer that could compute a time-of-day without being told the zone would be
    computing it in whatever zone the machine happened to be in -- the mistake
    :mod:`alphalab.data.time` refuses to make one layer down.

    Raises:
        FactorInputError: If the definition is cross-sectional, or if the
            lengths of ``values`` and ``timestamps`` disagree.
        FactorComputationError: If a value the computation needs is undefined.
    """

    if definition.scope is not FeatureScope.TIME_SERIES:
        raise FactorInputError(
            f"{definition.kind.name} is cross-sectional; compute it with compute_cross_section."
        )
    if len(values) != len(timestamps):
        raise FactorInputError(
            f"A feature needs one timestamp per value, got {len(values)} values and "
            f"{len(timestamps)} timestamps."
        )

    count = len(values)
    warmup = definition.warmup_periods
    out: list[float | None] = [None] * count
    kind = definition.kind

    if kind in (FeatureKind.TIME_OF_DAY, FeatureKind.DAY_OF_WEEK):
        zone = resolve_zone(timezone_name)
        for index, stamp in enumerate(timestamps):
            local = datetime.fromtimestamp(stamp, zone)
            if kind is FeatureKind.TIME_OF_DAY:
                out[index] = float(local.hour * 3600 + local.minute * 60 + local.second)
            else:
                out[index] = float(local.weekday())
        return tuple(out)

    window = definition.window
    if window is None:  # pragma: no cover - construction guarantees it
        raise FactorInputError(f"{kind.name} reached computation with no window.")

    if kind is FeatureKind.EXPONENTIAL_MEAN:
        # Seeded with the simple mean of the first full window, then decayed.
        # Seeding with values[0] would make the whole series depend on one
        # observation, and that dependence never fully decays.
        if count >= window:
            alpha = 2.0 / (window + 1.0)
            level = sum(values[:window]) / window
            out[window - 1] = level
            for index in range(window, count):
                level = alpha * values[index] + (1.0 - alpha) * level
                out[index] = level
        return tuple(out)

    for index in range(warmup, count):
        if kind is FeatureKind.RETURN:
            base = values[index - window]
            if base <= 0.0:
                raise FactorComputationError(
                    f"A {window}-period return is undefined at observation {index}: the "
                    f"base value is {base!r}."
                )
            out[index] = values[index] / base - 1.0

        elif kind is FeatureKind.LOG_RETURN:
            base = values[index - window]
            if base <= 0.0 or values[index] <= 0.0:
                raise FactorComputationError(
                    f"A {window}-period log return is undefined at observation {index}: the "
                    f"values are {base!r} and {values[index]!r}, and a logarithm needs both "
                    "to be positive."
                )
            out[index] = math.log(values[index] / base)

        elif kind is FeatureKind.ROLLING_MEAN:
            out[index] = sum(values[index - window + 1 : index + 1]) / window

        elif kind is FeatureKind.ROLLING_STD:
            out[index] = math.sqrt(sample_variance(values[index - window + 1 : index + 1]))

        elif kind is FeatureKind.ROLLING_MIN:
            out[index] = min(values[index - window + 1 : index + 1])

        elif kind is FeatureKind.ROLLING_MAX:
            out[index] = max(values[index - window + 1 : index + 1])

        elif kind is FeatureKind.ROLLING_SUM:
            out[index] = sum(values[index - window + 1 : index + 1])

        elif kind is FeatureKind.REALIZED_VOLATILITY:
            periods = definition.parameters.get("periods_per_year", 252.0)
            out[index] = math.sqrt(
                sample_variance(_returns(values, index - window, index + 1))
            ) * math.sqrt(periods)

        elif kind is FeatureKind.MOMENTUM:
            skip = int(definition.parameters.get("skip_periods", 0.0))
            base = values[index - window - skip]
            if base <= 0.0:
                raise FactorComputationError(
                    f"Momentum is undefined at observation {index}: the base value is {base!r}."
                )
            out[index] = values[index - skip] / base - 1.0

        elif kind in (FeatureKind.MEAN_REVERSION, FeatureKind.ROLLING_ZSCORE):
            span = values[index - window + 1 : index + 1]
            deviation = math.sqrt(sample_variance(span))
            if deviation == 0.0:
                raise FactorComputationError(
                    f"{kind.name} is undefined at observation {index}: the trailing "
                    f"{window} values are identical, so there is no dispersion to measure "
                    "the distance in."
                )
            score = (values[index] - sum(span) / window) / deviation
            out[index] = -score if kind is FeatureKind.MEAN_REVERSION else score

        elif kind is FeatureKind.ROLLING_RATIO:
            average = sum(values[index - window + 1 : index + 1]) / window
            if average == 0.0:
                raise FactorComputationError(
                    f"A rolling ratio is undefined at observation {index}: the trailing "
                    f"{window}-period mean is zero."
                )
            out[index] = values[index] / average

        elif kind is FeatureKind.VOLATILITY_REGIME:
            long_window = int(definition.parameters["long_window"])
            short_vol = math.sqrt(sample_variance(_returns(values, index - window, index + 1)))
            long_vol = math.sqrt(sample_variance(_returns(values, index - long_window, index + 1)))
            if long_vol == 0.0:
                raise FactorComputationError(
                    f"A volatility regime is undefined at observation {index}: the "
                    f"{long_window}-period volatility is zero."
                )
            out[index] = short_vol / long_vol

        else:  # pragma: no cover - the requirements table is exhaustive
            raise FactorInputError(f"{kind.name} has no time-series implementation.")

    return tuple(out)


def compute_cross_section(
    definition: FeatureDefinition, values: Mapping[str, float]
) -> Mapping[str, float]:
    """Compute a cross-sectional feature over one instant's universe.

    ``values`` maps asset to that asset's observation at a single timestamp.
    The result maps the same assets to their cross-sectional values; no asset
    is added and none is dropped, so a panel stays rectangular where it was.

    Raises:
        FactorInputError: If the definition is a time-series one, or if the
            universe holds fewer than two assets -- a rank, a z-score and a
            demeaning over one asset are all degenerate, and the honest answer
            is that the cross-section is too small rather than a value.
        FactorComputationError: If every asset holds the same value, which
            leaves a z-score with no scale.
    """

    if definition.scope is not FeatureScope.CROSS_SECTIONAL:
        raise FactorInputError(
            f"{definition.kind.name} is a time-series computation; compute it with "
            "compute_time_series."
        )
    if len(values) < 2:
        raise FactorInputError(
            f"A cross-sectional feature needs at least 2 assets, got {len(values)}. A "
            "cross-section of one is the asset itself."
        )

    assets = sorted(values)
    ordered = [values[asset] for asset in assets]

    if definition.kind is FeatureKind.CROSS_SECTIONAL_RANK:
        return dict(zip(assets, ranks(ordered), strict=True))

    if definition.kind is FeatureKind.CROSS_SECTIONAL_DEMEAN:
        average = mean(ordered)
        return {asset: value - average for asset, value in zip(assets, ordered, strict=True)}

    try:
        standardized = standardize(ordered)
    except AlphaLabValidationError as error:
        raise FactorComputationError(
            f"A cross-sectional z-score is undefined over these {len(assets)} assets: {error}"
        ) from error
    return dict(zip(assets, standardized, strict=True))
