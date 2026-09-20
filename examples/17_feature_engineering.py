"""
AlphaLab Examples
=================

Example 17 : Feature Engineering

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 15 (universal data ingestion)
✓ Example 16 (research from a canonical dataset)

Topics
------

• Typed feature definitions with no implied defaults
• Derived feature identity, and what changes it
• Trailing windows, warmup, and why a value is absent
• Missing data that is skipped or refused, never filled
• Feature lineage back to the exact dataset version
• Writing features through the Feature Store seam

What this shows
---------------

A feature is a *specification* before it is a number::

    FeatureDefinition            what to compute, stated completely
       + ObservationFrame        one field, per symbol, from a versioned dataset
       -> FeatureSeries          values, warmup, and both identities

The identity is derived from the specification, so two processes that describe
the same feature agree on its version with no shared state -- and changing a
window changes the version rather than quietly reusing it.

Run

    python examples/17_feature_engineering.py
"""

from _research_panel import banner, lineage, load_panel

from alphalab.api import observe
from alphalab.factor_library import (
    FactorInputError,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureScope,
    MissingPolicy,
    canonical_feature_key,
    compute_feature,
    compute_features,
    to_factor_results,
)
from alphalab.feature_store import FeatureValueAdapter


def main() -> None:
    banner(17, "Feature Engineering")

    dataset = load_panel()
    frame = observe(dataset, FeatureField.CLOSE)

    print()
    print("Step 01 - The observation frame")
    print(f"  dataset version : {lineage(dataset)[:54]}...")
    print(f"  symbols         : {len(frame.symbols)}  {', '.join(frame.symbols[:5])}, ...")
    print(f"  instants        : {len(frame.timestamps)}")
    print(f"  field read      : {frame.source_field.name}")

    # ------------------------------------------------------------------
    # Step 02 : A definition states everything, and defaults nothing
    # ------------------------------------------------------------------

    print()
    print("Step 02 - A definition defaults nothing")
    try:
        FeatureDefinition("momentum", FeatureKind.MOMENTUM, FeatureField.CLOSE)
    except FactorInputError as error:
        print(f"  no window       : {str(error).splitlines()[0][:66]}...")

    try:
        FeatureDefinition(
            "sma", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=5, parameters={"decay": 1.0}
        )
    except FactorInputError as error:
        print(f"  unread param    : {str(error)[:66]}...")

    # ------------------------------------------------------------------
    # Step 03 : Identity is derived from the specification
    # ------------------------------------------------------------------

    momentum = FeatureDefinition("mom_20", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20)
    longer = FeatureDefinition("mom_20", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=21)
    same_again = FeatureDefinition("mom_20", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20)

    print()
    print("Step 03 - Derived feature identity")
    print("  canonical key rendered for the digest:")
    for line in canonical_feature_key(momentum).splitlines():
        print(f"    {line}")
    print(f"  version         : {momentum.feature_version[:54]}...")
    print(f"  same spec again : {momentum.feature_version == same_again.feature_version}")
    print(f"  window 20 vs 21 : {momentum.feature_version != longer.feature_version}")

    # ------------------------------------------------------------------
    # Step 04 : Trailing windows and warmup
    # ------------------------------------------------------------------

    definitions = (
        FeatureDefinition("ret_1", FeatureKind.RETURN, FeatureField.CLOSE, window=1),
        FeatureDefinition("sma_20", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=20),
        FeatureDefinition("std_20", FeatureKind.ROLLING_STD, FeatureField.CLOSE, window=20),
        momentum,
        FeatureDefinition(
            "mom_20_1",
            FeatureKind.MOMENTUM,
            FeatureField.CLOSE,
            window=20,
            parameters={"skip_periods": 1.0},
        ),
        FeatureDefinition("rev_10", FeatureKind.MEAN_REVERSION, FeatureField.CLOSE, window=10),
        FeatureDefinition("z_20", FeatureKind.ROLLING_ZSCORE, FeatureField.CLOSE, window=20),
        FeatureDefinition("vol_20", FeatureKind.REALIZED_VOLATILITY, FeatureField.CLOSE, window=20),
        FeatureDefinition("ema_20", FeatureKind.EXPONENTIAL_MEAN, FeatureField.CLOSE, window=20),
        FeatureDefinition(
            "regime",
            FeatureKind.VOLATILITY_REGIME,
            FeatureField.CLOSE,
            window=10,
            parameters={"long_window": 60.0},
        ),
        FeatureDefinition("dow", FeatureKind.DAY_OF_WEEK, FeatureField.CLOSE),
    )

    print()
    print("Step 04 - Trailing windows, and what each one costs")
    print(f"  {'feature':<10} {'kind':<22} {'warmup':>6} {'values':>7}   first value")
    computed = compute_features(definitions, frame)
    for definition in definitions:
        series = next(row for row in computed[definition.feature_version] if row.symbol == "AAPL")
        first = f"{series.values[0]:+.6f}" if series.values else "-"
        print(
            f"  {definition.feature_id:<10} {definition.kind.name:<22} "
            f"{definition.warmup_periods:>6} {len(series):>7}   {first}"
        )
    print()
    print("  A value is absent during warmup rather than filled with a guess.")
    print("  observations - warmup = values, exactly, for every one of them.")

    # ------------------------------------------------------------------
    # Step 05 : Volume, and a field read from a different column
    # ------------------------------------------------------------------

    volume_frame = observe(dataset, FeatureField.VOLUME)
    surge = FeatureDefinition(
        "vol_surge", FeatureKind.ROLLING_RATIO, FeatureField.VOLUME, window=20
    )
    surge_series = compute_feature(surge, volume_frame)[0]

    print()
    print("Step 05 - A volume feature reads the volume frame")
    print(f"  {surge.feature_id:<10} {len(surge_series)} values on {surge_series.symbol}")
    print(f"  max surge       : {max(surge_series.values):.3f}x its own 20-period mean")
    try:
        compute_feature(surge, frame)
    except FactorInputError as error:
        print(f"  wrong frame     : {str(error)[:66]}...")

    # ------------------------------------------------------------------
    # Step 06 : Missing data is skipped or refused, never filled
    # ------------------------------------------------------------------

    print()
    print("Step 06 - Missing data")
    short = FeatureDefinition("sma_500", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=500)
    skipped = compute_feature(short, frame)[0]
    print(f"  SKIP            : {len(skipped)} values from {skipped.observations} observations")

    refusing = FeatureDefinition(
        "sma_500",
        FeatureKind.ROLLING_MEAN,
        FeatureField.CLOSE,
        window=500,
        missing=MissingPolicy.REFUSE,
    )
    try:
        compute_feature(refusing, frame)
    except FactorInputError as error:
        print(f"  REFUSE          : {str(error)[:66]}...")
    print("  There is no third option. A forward fill is a claim about the world.")

    # ------------------------------------------------------------------
    # Step 07 : Lineage
    # ------------------------------------------------------------------

    series = next(row for row in computed[momentum.feature_version] if row.symbol == "AAPL")

    print()
    print("Step 07 - Feature lineage")
    print(f"  symbol          : {series.symbol}")
    print(f"  feature version : {series.feature_version[:54]}...")
    print(f"  dataset version : {str(series.dataset_version)[:54]}...")
    print(f"  lineage id      : {series.require_lineage()[:54]}...")
    print()
    print("  The lineage id is a digest of (dataset, definition, symbol, zone).")
    print("  Recomputing the same feature on the same data reproduces it exactly;")
    print("  changing either input produces a different one.")

    # ------------------------------------------------------------------
    # Step 08 : The Feature Store seam
    # ------------------------------------------------------------------

    values = to_factor_results(series, version=1)
    stored = FeatureValueAdapter.to_feature_value(values[0])

    print()
    print("Step 08 - Writing through the Feature Store seam")
    print(f"  values produced : {len(values)}")
    print(
        f"  first value     : {stored.feature_id} v{stored.version} "
        f"{stored.asset_id} = {stored.value:+.6f}"
    )
    print()
    print("  Feature Store never imports this package: a FactorResult satisfies")
    print("  FeatureValueProtocol structurally, which is what keeps the two")
    print("  independent. Registration and versioning stay Feature Store's job.")

    # ------------------------------------------------------------------
    # Step 09 : Cross-sectional features see one instant, never history
    # ------------------------------------------------------------------

    cross = FeatureDefinition(
        "close_rank",
        FeatureKind.CROSS_SECTIONAL_RANK,
        FeatureField.CLOSE,
        scope=FeatureScope.CROSS_SECTIONAL,
    )
    ranked = compute_feature(cross, frame)

    print()
    print("Step 09 - A cross-sectional feature")
    print(f"  scope           : {cross.scope.name}")
    print(f"  warmup          : {cross.warmup_periods} (it reads no history)")
    first_instant = frame.timestamps[0]
    ranks_now = {
        row.symbol: row.value_at(first_instant)
        for row in ranked
        if row.value_at(first_instant) is not None
    }
    print(f"  ranks at t0     : {dict(sorted(ranks_now.items())[:4])} ...")

    print()
    print("=" * 74)
    print("Every number above is reproducible: the same file, the same policy and")
    print("the same definitions produce the same identities on any machine.")
    print("=" * 74)


if __name__ == "__main__":
    main()
