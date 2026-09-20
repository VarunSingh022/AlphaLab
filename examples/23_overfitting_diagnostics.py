"""
AlphaLab Examples
=================

Example 23 : Overfitting Diagnostics

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 20 (walk-forward validation)
✓ Example 22 (robustness testing)

Topics
------

• Parameter sweeps that count every configuration evaluated
• Sensitivity and the neighbour drop: a plateau against a cliff
• In-sample versus out-of-sample degradation
• Stability across periods and across symbols
• Multiple-testing risk, and the one correction whose assumption fits
• Measurements, thresholds and interpretation, kept in separate fields

What this shows
---------------

There is no overfit score, and there will not be one. A single number blending
sensitivity, degradation and the size of the search would have to weight them,
the weights would be a judgement nobody could inspect, and the figure would be
unfalsifiable -- there is no experiment that shows an overfit score of 63 to be
wrong.

What a report carries instead:

    measured quantities   facts about the runs that were performed
    thresholds            bounds the caller stated in advance
    findings              which stated bound each measurement crossed

Run

    python examples/23_overfitting_diagnostics.py
"""

from _research_panel import banner, lineage, load_panel

from alphalab.api import observe
from alphalab.common.statistics import rank_correlation
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    compute_panel,
    forward_returns,
)
from alphalab.research import (
    OverfittingPolicy,
    ResearchValidationError,
    build_overfitting_report,
    parameter_sweep,
    period_stability,
    sample_degradation,
    signal_diagnostics,
    symbol_stability,
)

HORIZON = 5
WINDOWS = (5, 10, 15, 20, 25, 30, 40, 50, 60)


def main() -> None:
    banner(23, "Overfitting Diagnostics")

    dataset = load_panel()
    frame = observe(dataset, FeatureField.CLOSE)
    realized = forward_returns(frame, HORIZON)
    instants = list(frame.timestamps)
    half = len(instants) // 2

    def rank_ic(window: int, chosen: list[float] | None = None) -> float:
        definition = FeatureDefinition(
            f"mom_{window}", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=window
        )
        measured = signal_diagnostics(
            compute_panel(definition, frame),
            realized,
            buckets=5,
            minimum_assets=5,
            instants=chosen,
        )
        return measured.rank_ic.mean_rank if measured.rank_ic.mean_rank is not None else 0.0

    print()
    print("Step 01 - The search")
    print(f"  dataset version : {lineage(dataset)[:54]}...")
    print(f"  configurations  : {len(WINDOWS)} momentum windows, {WINDOWS}")
    print(f"  metric          : rank IC at a {HORIZON}-period horizon")

    # ------------------------------------------------------------------
    # Step 02 : The whole surface, in sample
    # ------------------------------------------------------------------

    in_sample_instants = instants[:half]
    configurations = [f"window={window}" for window in WINDOWS]
    sweep = parameter_sweep(
        "rank_ic",
        configurations,
        lambda name: rank_ic(int(name.split("=")[1]), in_sample_instants),
    )

    print()
    print("Step 02 - The full surface, measured on the first half of the sample")
    print(f"  {'configuration':<16} {'rank IC':>9}")
    for name in configurations:
        marker = "  <- best" if name == sweep.best else ""
        print(f"  {name:<16} {sweep.scores[name]:>+9.4f}{marker}")
    print()
    print(f"  trials evaluated : {sweep.trials}")
    print(f"  best             : {sweep.best} at {sweep.best_score:+.4f}")
    print(f"  sensitivity      : {sweep.sensitivity:.4f}   (stdev / |mean| across the surface)")
    print(f"  neighbour drop   : {sweep.neighbour_drop:.4f}   (best to its best neighbour)")
    print()
    print("  Every configuration is evaluated and reported. A sweep that tried")
    print("  nine windows and wrote up the best one has NINE trials, and")
    print("  recording one would make every correction below meaningless.")

    # ------------------------------------------------------------------
    # Step 03 : A cliff and a plateau, side by side
    # ------------------------------------------------------------------

    cliff = {"a": 0.02, "b": 0.03, "c": 0.41, "d": 0.03, "e": 0.02}
    plateau = {"a": 0.34, "b": 0.37, "c": 0.41, "d": 0.38, "e": 0.36}

    print()
    print("Step 03 - The same best score, two different surfaces")
    print(f"  {'surface':<10} {'best':>7} {'sensitivity':>12} {'neighbour drop':>15}")
    for name, surface in (("cliff", cliff), ("plateau", plateau)):
        result = parameter_sweep("rank_ic", list(surface), surface.__getitem__)
        print(
            f"  {name:<10} {result.best_score:>7.2f} {result.sensitivity:>12.4f} "
            f"{result.neighbour_drop:>15.4f}"
        )
    print()
    print("  Identical best scores, and one of them is a parameter chosen by")
    print("  the noise. An optimum sitting on a cliff is the shape overfitting")
    print("  takes; the two numbers above are what distinguish it.")

    # ------------------------------------------------------------------
    # Step 04 : Out-of-sample degradation
    # ------------------------------------------------------------------

    best_window = int(sweep.best.split("=")[1])
    out_of_sample = rank_ic(best_window, instants[half:])
    degradation = sample_degradation(sweep.best_score, out_of_sample)

    print()
    print("Step 04 - Out of sample")
    print(f"  chosen on the first half  : {sweep.best} at {sweep.best_score:+.4f}")
    print(f"  measured on the second    : {out_of_sample:+.4f}")
    print(f"  degradation               : {degradation:+.2%}")
    print()
    print("  0% means the result held. 100% means all of it was lost. Above")
    print("  100% means the sign flipped, which is worse than no result.")
    print(
        f"  A zero in-sample figure returns None rather than a ratio: "
        f"{sample_degradation(0.0, 0.1)}"
    )

    # ------------------------------------------------------------------
    # Step 05 : Stability across periods and symbols
    # ------------------------------------------------------------------

    quarter = len(instants) // 4
    by_period = {
        f"q{index + 1}": rank_ic(best_window, instants[index * quarter : (index + 1) * quarter])
        for index in range(4)
    }
    period = period_stability("rank_ic", by_period)

    definition = FeatureDefinition(
        f"mom_{best_window}", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=best_window
    )
    panel = compute_panel(definition, frame)
    by_symbol: dict[str, float] = {}
    for symbol in panel.symbols:
        pairs = [
            (panel.rows[stamp][symbol], realized.cross_section(stamp)[symbol])
            for stamp in panel.timestamps
            if symbol in panel.rows[stamp] and symbol in realized.cross_section(stamp)
        ]
        if len(pairs) < 3:
            continue
        by_symbol[symbol] = rank_correlation(
            [value for value, _ in pairs], [outcome for _, outcome in pairs]
        )
    across_symbols = symbol_stability("rank_ic", by_symbol)

    print()
    print("Step 05 - Stability")
    print(f"  {'period':<8} {'rank IC':>9}")
    for name, value in sorted(period.by_slice.items()):
        print(f"  {name:<8} {value:>+9.4f}")
    print(f"  weakest / strongest : {period.weakest} / {period.strongest}")
    print(f"  positive share      : {period.positive_share:.0%}")
    print(f"  coefficient of variation : {period.coefficient_of_variation:.4f}")
    print()
    print(
        f"  Across symbols: weakest {across_symbols.weakest}, "
        f"strongest {across_symbols.strongest}, "
        f"{across_symbols.positive_share:.0%} positive"
    )
    print("  A factor whose whole information coefficient comes from one name")
    print("  is not a factor, and an aggregate figure cannot show that.")

    # ------------------------------------------------------------------
    # Step 06 : The report -- measurements, thresholds, findings
    # ------------------------------------------------------------------

    policy = OverfittingPolicy(
        maximum_degradation=0.50,
        maximum_sensitivity=0.75,
        maximum_neighbour_drop=0.30,
        minimum_positive_share=0.60,
        alpha=0.05,
    )
    report = build_overfitting_report(
        metric="rank_ic",
        in_sample=sweep.best_score,
        out_of_sample=out_of_sample,
        policy=policy,
        effective_parameters=1,
        sweep=sweep,
        period=period,
        symbol=across_symbols,
    )

    print()
    print("Step 06 - The report")
    print("  MEASURED")
    print(f"    in sample       : {report.in_sample:+.4f}")
    print(f"    out of sample   : {report.out_of_sample:+.4f}")
    print(f"    degradation     : {report.degradation:+.2%}")
    print(f"    sensitivity     : {report.sensitivity:.4f}")
    print(f"    neighbour drop  : {report.neighbour_drop:.4f}")
    print(f"    trials          : {report.trials}")
    print(f"    free parameters : {report.effective_parameters}")
    print()
    print("  THRESHOLDS (the caller's, stated in advance)")
    print(f"    max degradation : {policy.maximum_degradation}")
    print(f"    max sensitivity : {policy.maximum_sensitivity}")
    print(f"    max neighbour   : {policy.maximum_neighbour_drop}")
    print(f"    min positive    : {policy.minimum_positive_share}")
    print()
    print("  FINDINGS")
    if report.findings:
        for finding in report.findings:
            print(f"    - {finding}")
    else:
        print("    (none crossed -- which is not a statement that the result is")
        print("     sound, only that these checks did not object)")

    # ------------------------------------------------------------------
    # Step 07 : Multiple testing
    # ------------------------------------------------------------------

    print()
    print("Step 07 - Multiple testing")
    print(f"  nominal alpha     : {policy.alpha}")
    print(f"  trials            : {report.trials}")
    print(f"  Bonferroni alpha  : {report.bonferroni_alpha:.6f}")
    print()
    print("  Bonferroni is the only correction offered, because it is the only")
    print("  one whose assumption fits in a line: for k configurations tried, a")
    print("  nominal alpha becomes alpha / k. It is CONSERVATIVE when the")
    print("  trials are correlated -- which neighbouring windows strongly are --")
    print("  and the report says so rather than leaving it to be assumed.")
    print()
    print("  No p-value is computed anywhere in this module, so there is none")
    print("  to correct. The threshold is reported for a caller who brings one.")

    # ------------------------------------------------------------------
    # Step 08 : What the report refuses
    # ------------------------------------------------------------------

    print()
    print("Step 08 - Refusals")
    try:
        OverfittingPolicy()
    except ResearchValidationError as error:
        print(f"  empty policy      : {str(error)[:64]}...")

    absent = build_overfitting_report(
        "rank_ic",
        sweep.best_score,
        out_of_sample,
        OverfittingPolicy(maximum_sensitivity=0.3),
        effective_parameters=1,
    )
    print(f"  bound on an unmeasured quantity : {absent.findings[0][:58]}...")
    print("  An absent measurement is not a passing one -- the rule")
    print("  lifecycle.evaluate_policy applies to a missing metric.")

    print()
    print("=" * 74)
    print(report.describe())
    print("Nothing above is a verdict. The findings name a number and the bound")
    print("it crossed, so each one can be checked.")
    print("=" * 74)


if __name__ == "__main__":
    main()
