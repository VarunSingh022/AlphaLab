"""
AlphaLab Examples
=================

Example 19 : Signal Diagnostics

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 18 (factor research)

Topics
------

• Forward-return analysis at several horizons
• Quantile profiles, and what an IC alone hides
• Monotonicity as distinct from spread
• Regime-conditioned diagnostics using the caller's own labels
• Sample counts attached to every number, and None where there is none

What this shows
---------------

Detailed diagnostics rather than one opaque score. There is no blended
"signal score" anywhere in AlphaLab: the weights would be a judgement, and
once blended a reader cannot tell a factor with a strong monotone profile and
a weak IC from its opposite -- which are different factors with different
failure modes.

Run

    python examples/19_signal_diagnostics.py
"""

from _research_panel import banner, lineage, load_panel

from alphalab.api import observe
from alphalab.common.statistics import mean, median
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    compute_panel,
    forward_returns,
)
from alphalab.research import (
    ResearchValidationError,
    conditional_diagnostics,
    signal_diagnostics,
    signal_horizons,
)

REVERSION = FeatureDefinition("rev_10", FeatureKind.MEAN_REVERSION, FeatureField.CLOSE, window=10)
REGIME = FeatureDefinition(
    "regime",
    FeatureKind.VOLATILITY_REGIME,
    FeatureField.CLOSE,
    window=10,
    parameters={"long_window": 60.0},
)


def _render(value: float | None, width: int = 9, spec: str = "+.4f") -> str:
    return "-".rjust(width) if value is None else format(value, spec).rjust(width)


def main() -> None:
    banner(19, "Signal Diagnostics")

    dataset = load_panel()
    frame = observe(dataset, FeatureField.CLOSE)
    signal = compute_panel(REVERSION, frame)

    print()
    print("Step 01 - The signal")
    print(f"  dataset version : {lineage(dataset)[:54]}...")
    print(f"  signal          : {REVERSION.feature_id} ({REVERSION.kind.name}, window 10)")
    print(f"  instants        : {len(signal)}   symbols: {len(signal.symbols)}")

    # ------------------------------------------------------------------
    # Step 02 : Does it predict, and at what horizon?
    # ------------------------------------------------------------------

    horizons = signal_horizons(signal, frame, [1, 3, 5, 10, 20], buckets=5, minimum_assets=5)

    print()
    print("Step 02 - Forward-return analysis across horizons")
    print(f"  {'h':>4} {'rank IC':>9} {'spread':>10} {'monotone':>9} {'obs':>7} {'instants':>9}")
    for horizon, result in sorted(horizons.items()):
        spread = "-".rjust(10) if result.spread is None else f"{result.spread:+.4%}".rjust(10)
        print(
            f"  {horizon:>4} {_render(result.rank_ic.mean_rank)} {spread} "
            f"{_render(result.monotonicity)} {result.observations:>7} {result.instants:>9}"
        )
    print()
    print("  Every row carries the sample it rests on. A longer horizon is")
    print("  measured on fewer instants, because the tail of the series has")
    print("  no realized outcome.")

    # ------------------------------------------------------------------
    # Step 03 : The quantile profile, which an IC alone hides
    # ------------------------------------------------------------------

    chosen = 5
    detail = horizons[chosen]

    print()
    print(f"Step 03 - Quantile profile at h={chosen}")
    print(f"  {'bucket':>7} {'obs':>7} {'instants':>9} {'mean fwd':>11} {'median fwd':>12}")
    for bucket in detail.quantiles:
        print(
            f"  {bucket.bucket:>7} {bucket.observations:>7} {bucket.instants:>9} "
            f"{bucket.mean_forward_return:>+11.4%} {bucket.median_forward_return:>+12.4%}"
        )
    print(f"  spread (top - bottom) : {detail.spread:+.4%}")
    print(f"  monotonicity          : {_render(detail.monotonicity).strip()}")
    print()
    print("  Monotonicity is the rank correlation between bucket index and")
    print("  bucket mean return. A factor with a large spread and a scrambled")
    print("  middle is a different proposition from one that grades smoothly,")
    print("  and a spread alone cannot tell them apart.")

    # ------------------------------------------------------------------
    # Step 04 : Conditioning on a regime the caller defines
    # ------------------------------------------------------------------

    regime_panel = compute_panel(REGIME, frame)

    # The label is the caller's: AlphaLab ships no regime taxonomy, because
    # which states the world is in is a research question and a library that
    # answered it would be answering it for everybody.
    #
    # The regime feature is short-window volatility over long-window
    # volatility, per asset. A *time* regime is a statement about the whole
    # universe at an instant, so the labels come from the cross-sectional mean
    # of that ratio, split at its own median across the sample. Bucketing the
    # cross-section instead would label half the assets high at every instant
    # by construction, and say nothing about time at all.
    universe_stress = {
        stamp: mean(list(regime_panel.cross_section(stamp).values()))
        for stamp in regime_panel.timestamps
        if regime_panel.cross_section(stamp)
    }
    threshold = median(list(universe_stress.values()))
    labels = {
        stamp: ("turbulent" if value > threshold else "calm")
        for stamp, value in universe_stress.items()
    }

    conditional = conditional_diagnostics(
        signal,
        forward_returns(frame, chosen),
        labels,
        buckets=5,
        minimum_assets=5,
        minimum_instants=10,
    )

    print()
    print("Step 04 - Conditional diagnostics, by a regime the caller defined")
    print(f"  labelled instants : {len(labels)}  (split at a stress of {threshold:.4f})")
    print(f"  {'regime':<12} {'rank IC':>9} {'spread':>10} {'obs':>7} {'instants':>9}")
    for name, result in sorted(conditional.items()):
        spread = "-".rjust(10) if result.spread is None else f"{result.spread:+.4%}".rjust(10)
        print(
            f"  {name:<12} {_render(result.rank_ic.mean_rank)} {spread} "
            f"{result.observations:>7} {result.instants:>9}"
        )
    print()
    print("  Instants the labels do not name are excluded from every slice")
    print("  rather than pooled into an 'other' bucket nobody defined.")

    # ------------------------------------------------------------------
    # Step 05 : What the diagnostics refuse to say
    # ------------------------------------------------------------------

    print()
    print("Step 05 - What is refused")

    demanding = signal_diagnostics(
        signal, forward_returns(frame, chosen), buckets=5, minimum_assets=40
    )
    print(f"  minimum_assets=40 on a universe of {len(signal.symbols)}:")
    print(f"    rank IC       : {demanding.rank_ic.mean_rank}")
    print(f"    instants skipped : {demanding.rank_ic.instants_skipped}")
    print("    None is not zero. Zero would read as a measured absence of")
    print("    relationship, which is a finding nobody made.")

    try:
        signal_diagnostics(signal, forward_returns(frame, chosen), buckets=1)
    except ResearchValidationError as error:
        print(f"  one bucket      : {str(error)[:64]}...")

    try:
        conditional_diagnostics(
            signal, forward_returns(frame, chosen), labels, minimum_instants=10_000
        )
    except ResearchValidationError as error:
        print(f"  thin regime     : {str(error)[:64]}...")

    print()
    print("=" * 74)
    print("There is no single number summarising any of this, by design.")
    print(detail.describe())
    print("=" * 74)


if __name__ == "__main__":
    main()
