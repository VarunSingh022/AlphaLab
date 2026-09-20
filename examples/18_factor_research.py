"""
AlphaLab Examples
=================

Example 18 : Factor Research

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 17 (feature engineering)

Topics
------

• Cross-sectional ranking, with the tie rule stated
• Three neutralizations that are not interchangeable
• The information coefficient, and what it refuses to report
• Factor decay across forward horizons
• Turnover under a named convention
• Exposure by asset and by a sector the caller supplies

What this shows
---------------

The chain a factor study runs, with every step recorded on the panel::

    FeaturePanel
      -> neutralize_group       sector effect removed
      -> rank                   tie method stated
      -> bucket                 quantiles
      -> weights                long/short, dollar neutral by construction
      -> turnover, exposure

`panel.lineage` carries the feature version and every transform since, so a
diagnostic can say what it was measured on rather than leaving it to be assumed.

Run

    python examples/18_factor_research.py
"""

from _research_panel import banner, lineage, load_panel, sectors

from alphalab.api import observe
from alphalab.common.statistics import RankMethod
from alphalab.factor_library import (
    FactorInputError,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    TurnoverConvention,
    bucket_panel,
    compute_panel,
    factor_decay,
    factor_exposure,
    factor_turnover,
    forward_returns,
    information_coefficient,
    neutralize_beta,
    neutralize_group,
    neutralize_mean,
    rank_panel,
    weights_from_buckets,
)

MOMENTUM = FeatureDefinition("mom_60", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=60)
VOLATILITY = FeatureDefinition(
    "vol_60", FeatureKind.REALIZED_VOLATILITY, FeatureField.CLOSE, window=60
)


def main() -> None:
    banner(18, "Factor Research")

    dataset = load_panel()
    frame = observe(dataset, FeatureField.CLOSE)
    sector = sectors()

    panel = compute_panel(MOMENTUM, frame)

    print()
    print("Step 01 - The raw factor panel")
    print(f"  dataset version : {lineage(dataset)[:54]}...")
    print(f"  factor          : {MOMENTUM.feature_id} ({MOMENTUM.kind.name}, window 60)")
    print(f"  instants        : {len(panel)}   symbols: {len(panel.symbols)}")
    print(f"  lineage         : {panel.lineage[:54]}...")

    # ------------------------------------------------------------------
    # Step 02 : Ranking, with the tie rule and the missing rule stated
    # ------------------------------------------------------------------

    ranked = rank_panel(panel, RankMethod.AVERAGE, minimum_assets=5)
    instant = ranked.panel.timestamps[0]

    print()
    print("Step 02 - Cross-sectional ranking")
    print(f"  tie method      : {ranked.method.name}")
    print(f"  instants ranked : {len(ranked.counts)}")
    print(f"  smallest universe ranked in : {ranked.minimum_cross_section}")
    print(
        f"  ranks at t0     : {dict(sorted(ranked.panel.cross_section(instant).items())[:4])} ..."
    )
    print("  An asset with no score stays absent. It is not ranked last.")

    # ------------------------------------------------------------------
    # Step 03 : Three neutralizations, and why they are not one function
    # ------------------------------------------------------------------

    by_mean = neutralize_mean(panel)
    by_sector = neutralize_group(panel, sector, minimum_group_size=2)

    # The exposure a beta neutralization regresses against is itself a feature,
    # computed from the same dataset, so it carries the same lineage.
    volatility = compute_panel(VOLATILITY, frame)
    by_beta = neutralize_beta(panel, volatility.rows, minimum_assets=5)

    print()
    print("Step 03 - Neutralization: three methods, not interchangeable")
    print(f"  {'method':<20} {'instants':>9} {'skipped':>8}   mean |removed|")
    for name, report in (
        ("neutralize_mean", by_mean),
        ("neutralize_group", by_sector),
        ("neutralize_beta", by_beta),
    ):
        print(
            f"  {name:<20} {report.instants_neutralized:>9} "
            f"{report.instants_skipped:>8}   {report.mean_absolute_removed:.6f}"
        )
    print()
    print("  Mean removes the level. Group removes what the sectors explain.")
    print("  Beta removes what one continuous exposure explains. A factor that is")
    print("  sector-neutral is not market-neutral, and neither is dollar-neutral.")
    print(f"  chain after group : {[str(t) for t in by_sector.panel.transforms]}")

    try:
        neutralize_group(panel, {"AAPL": "TECH"})
    except FactorInputError as error:
        print(f"  ungrouped asset : {str(error)[:64]}...")

    # ------------------------------------------------------------------
    # Step 04 : The information coefficient
    # ------------------------------------------------------------------

    realized = forward_returns(frame, 20)
    raw_ic = information_coefficient(panel, realized, minimum_assets=5)
    neutral_ic = information_coefficient(by_sector.panel, realized, minimum_assets=5)

    print()
    print("Step 04 - Information coefficient at a 20-period horizon")
    print(
        f"  {'panel':<22} {'rank IC':>9} {'pearson':>9} {'instants':>9} {'skipped':>8} {'hit':>7}"
    )
    for name, result in (("raw factor", raw_ic), ("sector neutral", neutral_ic)):
        rank = "-" if result.mean_rank is None else f"{result.mean_rank:+.4f}"
        pearson = "-" if result.mean_pearson is None else f"{result.mean_pearson:+.4f}"
        hit = "-" if result.hit_rate is None else f"{result.hit_rate:.3f}"
        print(
            f"  {name:<22} {rank:>9} {pearson:>9} "
            f"{result.instants_measured:>9} {result.instants_skipped:>8} {hit:>7}"
        )
    print(f"  observations    : {raw_ic.observations} asset-instant pairs")
    print()
    print("  No p-value is reported. Consecutive ICs share 19 of their 20 days")
    print("  by construction, so the usual t-statistic is inflated by an amount")
    print("  this module cannot measure. See alphalab.research.purging for where")
    print("  overlapping information is actually handled.")

    demanding = information_coefficient(panel, realized, minimum_assets=50)
    print()
    print(f"  With minimum_assets=50 (the universe has {len(panel.symbols)}):")
    print(f"    mean rank IC  : {demanding.mean_rank}   <- not zero; not measured")
    print(f"    instants skipped : {demanding.instants_skipped}")

    # ------------------------------------------------------------------
    # Step 05 : Decay
    # ------------------------------------------------------------------

    profile = factor_decay(panel, frame, [1, 5, 10, 20, 40], minimum_assets=5)

    print()
    print("Step 05 - Factor decay")
    print(f"  {'horizon':>8} {'rank IC':>9} {'instants':>9}")
    for horizon, result in profile.ordered:
        value = "-" if result.mean_rank is None else f"{result.mean_rank:+.4f}"
        print(f"  {horizon:>8} {value:>9} {result.instants_measured:>9}")
    print(f"  first negative horizon : {profile.first_negative_horizon}")
    print()
    print("  Each horizon is measured on its own, smaller sample: the last")
    print("  `horizon` instants have no realized outcome. No half-life is")
    print("  reported, because fitting one assumes a functional form.")

    # ------------------------------------------------------------------
    # Step 06 : Turnover
    # ------------------------------------------------------------------

    bucketed = bucket_panel(ranked.panel, 5)
    weights = weights_from_buckets(bucketed, 5)

    one_way = factor_turnover(weights, TurnoverConvention.ONE_WAY)
    absolute = factor_turnover(weights, TurnoverConvention.ABSOLUTE_CHANGE)

    print()
    print("Step 06 - Turnover")
    print(f"  books formed    : {len(weights)}")
    print(f"  ONE_WAY         : {one_way.mean_turnover:.4f} per period")
    print(f"  ABSOLUTE_CHANGE : {absolute.mean_turnover:.4f} per period")
    print(f"  entries / exits : {one_way.entries} / {one_way.exits}")
    print()
    print("  The same book under two conventions. Both are quoted in the")
    print("  literature, so the result records which one it used.")

    # ------------------------------------------------------------------
    # Step 07 : Exposure
    # ------------------------------------------------------------------

    exposure = factor_exposure(weights, sector)

    print()
    print("Step 07 - Exposure")
    print(f"  net exposure    : {exposure.net_exposure:+.6f}")
    print(f"  gross exposure  : {exposure.gross_exposure:.4f}")
    print(f"  concentration   : {exposure.concentration:.4f}")
    print(f"  largest group   : {exposure.largest_group}")
    print(f"  {'sector':<14} {'net':>10} {'gross':>10}")
    for group in sorted(exposure.gross_by_group):
        print(
            f"  {group:<14} {exposure.by_group[group]:>+10.4f} "
            f"{exposure.gross_by_group[group]:>10.4f}"
        )
    print()
    print("  Net and gross are reported separately: a sector that is net flat")
    print("  because it holds equal longs and shorts is not unexposed to it.")

    print()
    print("=" * 74)
    print("The panel's transform chain is what lets each number above be read")
    print("against the thing it was actually measured on:")
    print(f"  feature   {bucketed.definition.feature_version[:60]}...")
    for step in bucketed.transforms:
        print(f"  ->        {step}")
    print("=" * 74)


if __name__ == "__main__":
    main()
