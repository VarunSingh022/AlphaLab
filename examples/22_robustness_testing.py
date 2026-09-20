"""
AlphaLab Examples
=================

Example 22 : Robustness Testing

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 19 (signal diagnostics)

Topics
------

• Parameter perturbation, and a shift that always changes something
• Data and signal perturbation, seeded and reproducible
• Missing data simulated by deleting, never by filling
• Delayed execution, the cheapest robustness test there is
• Execution costs, as a research question rather than a model
• Block bootstrap and Monte Carlo, with independent seeded paths

What this shows
---------------

A robustness test means something only if the baseline is untouched, exactly
one thing was perturbed, and the perturbation can be reproduced. Each
`PerturbationRun` records the change, the seed, the baseline it was compared
against and the dataset both sides were measured on.

Run

    python examples/22_robustness_testing.py
"""

from _research_panel import banner, lineage, load_panel

from alphalab.api import observe
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    TurnoverConvention,
    bucket_panel,
    compute_panel,
    factor_turnover,
    forward_returns,
    rank_panel,
    weights_from_buckets,
)
from alphalab.factor_library.forward_returns import ForwardReturnPanel
from alphalab.factor_library.observations import ObservationFrame
from alphalab.research import (
    Perturbation,
    PerturbationKind,
    PerturbationRun,
    ResearchValidationError,
    apply_cost,
    block_bootstrap_indices,
    delay_signal,
    drop_observations,
    monte_carlo_orders,
    perturb_observations,
    perturb_signal,
    sample_by,
    shift_parameter,
    signal_diagnostics,
)

SEED = 20260920
WINDOW = 20
HORIZON = 5


def _definition(window: int) -> FeatureDefinition:
    return FeatureDefinition(
        f"mom_{window}", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=window
    )


def _measure(
    frame: ObservationFrame, definition: FeatureDefinition, *, delay: int = 0
) -> dict[str, float]:
    panel = compute_panel(definition, frame)
    if delay:
        panel = delay_signal(panel, delay)
    measured = signal_diagnostics(
        panel, forward_returns(frame, HORIZON), buckets=5, minimum_assets=5
    )
    return {
        "rank_ic": measured.rank_ic.mean_rank if measured.rank_ic.mean_rank is not None else 0.0,
        "spread": measured.spread if measured.spread is not None else 0.0,
        "observations": float(measured.observations),
    }


def _report(runs: list[PerturbationRun], metric: str = "rank_ic") -> None:
    """Absolute change first, relative second.

    A relative change against a baseline close to zero is unstable -- a move
    from -0.008 to -0.012 is "-55%" and means almost nothing. The absolute
    figure is the one to read here, and it is printed first for that reason.
    """

    print(f"  {'perturbation':<46} {'baseline':>9} {'perturbed':>10} {'abs':>9} {'rel':>9}")
    for run in runs:
        relative = run.relative_change(metric)
        rendered = "-" if relative is None else f"{relative:+.1%}"
        print(
            f"  {run.perturbation!s:<46} "
            f"{run.baseline_metrics[metric]:>+9.4f} {run.metrics[metric]:>+10.4f} "
            f"{run.change(metric):>+9.4f} {rendered:>9}"
        )


def main() -> None:
    banner(22, "Robustness Testing")

    dataset = load_panel()
    frame = observe(dataset, FeatureField.CLOSE)
    baseline_definition = _definition(WINDOW)
    baseline = _measure(frame, baseline_definition)
    version = lineage(dataset)

    print()
    print("Step 01 - The baseline, which nothing below alters")
    print(f"  dataset version : {version[:54]}...")
    print(f"  signal          : {baseline_definition.feature_id} (window {WINDOW})")
    print(f"  rank IC (h={HORIZON})   : {baseline['rank_ic']:+.4f}")
    print(f"  spread          : {baseline['spread']:+.4%}")
    print(f"  observations    : {int(baseline['observations'])}")

    runs: list[PerturbationRun] = []

    # ------------------------------------------------------------------
    # Step 02 : Parameter perturbation
    # ------------------------------------------------------------------

    print()
    print("Step 02 - Parameter perturbation")
    for relative in (-0.25, -0.10, 0.10, 0.25):
        shifted = shift_parameter(WINDOW, relative)
        runs.append(
            PerturbationRun(
                perturbation=Perturbation(
                    PerturbationKind.PARAMETER, relative, detail=f"window {WINDOW}->{shifted}"
                ),
                baseline=baseline_definition.feature_version,
                dataset_version=version,
                metrics=_measure(frame, _definition(shifted)),
                baseline_metrics=baseline,
            )
        )
    _report(runs)
    print()
    print("  shift_parameter rounds AWAY from no change, so a 10% shift on a")
    print("  window of 3 moves it to 4 rather than rounding back to 3. A")
    print("  'perturbation' that changed nothing would be recorded as one the")
    print("  result survived.")
    try:
        shift_parameter(2, -1.0)
    except ResearchValidationError as error:
        print(f"  refused         : {str(error)[:64]}...")

    # ------------------------------------------------------------------
    # Step 03 : Data and signal perturbation, seeded
    # ------------------------------------------------------------------

    data_runs: list[PerturbationRun] = []
    for sigma in (0.002, 0.005, 0.010):
        jittered = perturb_observations(frame, sigma, seed=SEED)
        data_runs.append(
            PerturbationRun(
                perturbation=Perturbation(PerturbationKind.DATA, sigma, seed=SEED),
                baseline=baseline_definition.feature_version,
                dataset_version=version,
                metrics=_measure(jittered, baseline_definition),
                baseline_metrics=baseline,
            )
        )

    panel = compute_panel(baseline_definition, frame)
    for sigma in (0.01, 0.05):
        noisy = perturb_signal(panel, sigma, seed=SEED)
        measured = signal_diagnostics(
            noisy, forward_returns(frame, HORIZON), buckets=5, minimum_assets=5
        )
        data_runs.append(
            PerturbationRun(
                perturbation=Perturbation(PerturbationKind.SIGNAL, sigma, seed=SEED),
                baseline=baseline_definition.feature_version,
                dataset_version=version,
                metrics={
                    "rank_ic": measured.rank_ic.mean_rank or 0.0,
                    "spread": measured.spread or 0.0,
                    "observations": float(measured.observations),
                },
                baseline_metrics=baseline,
            )
        )

    print()
    print("Step 03 - Data and signal perturbation")
    _report(data_runs)
    print()
    print("  Data jitter is MULTIPLICATIVE: a price series' noise scale is")
    print("  proportional, and an additive jitter would test the universe's")
    print("  price levels rather than the result. Signal jitter is ADDITIVE,")
    print("  because a rank or a z-score is centred near zero.")

    # The seed is what makes any of this reproducible.
    again = perturb_observations(frame, 0.005, seed=SEED)
    other = perturb_observations(frame, 0.005, seed=SEED + 1)
    first = perturb_observations(frame, 0.005, seed=SEED)
    print(f"  same seed reproduces  : {again.series['AAPL'].values == first.series['AAPL'].values}")
    print(
        f"  a different seed does not : "
        f"{other.series['AAPL'].values != first.series['AAPL'].values}"
    )
    try:
        Perturbation(PerturbationKind.DATA, 0.005)
    except ResearchValidationError as error:
        print(f"  unseeded refused  : {str(error)[:62]}...")

    # ------------------------------------------------------------------
    # Step 04 : Missing data, by deleting
    # ------------------------------------------------------------------

    print()
    print("Step 04 - Missing data")
    missing_runs: list[PerturbationRun] = []
    for fraction in (0.05, 0.15, 0.30):
        thinned = drop_observations(frame, fraction, seed=SEED)
        missing_runs.append(
            PerturbationRun(
                perturbation=Perturbation(PerturbationKind.MISSING_DATA, fraction, seed=SEED),
                baseline=baseline_definition.feature_version,
                dataset_version=version,
                metrics=_measure(thinned, baseline_definition),
                baseline_metrics=baseline,
            )
        )
    _report(missing_runs)
    thinned = drop_observations(frame, 0.30, seed=SEED)
    print(f"  AAPL observations : {len(frame.series['AAPL'])} -> {len(thinned.series['AAPL'])}")
    print("  The gaps are real. Nothing is filled, so the feature layer's")
    print("  MissingPolicy is what handles them -- which is the point.")

    # ------------------------------------------------------------------
    # Step 05 : Delayed execution
    # ------------------------------------------------------------------

    print()
    print("Step 05 - Delayed execution")
    delay_runs = [
        PerturbationRun(
            perturbation=Perturbation(PerturbationKind.EXECUTION_DELAY, float(periods)),
            baseline=baseline_definition.feature_version,
            dataset_version=version,
            metrics=_measure(frame, baseline_definition, delay=periods),
            baseline_metrics=baseline,
        )
        for periods in (1, 2, 5)
    ]
    _report(delay_runs)
    print()
    print("  The cheapest and most informative robustness test there is. A")
    print("  result that vanishes at a delay of one period was measuring the")
    print("  simultaneity of the signal and the outcome, not a prediction.")

    # ------------------------------------------------------------------
    # Step 06 : Execution cost
    # ------------------------------------------------------------------

    realized = forward_returns(frame, HORIZON)

    print()
    print("Step 06 - Execution cost")

    # A flat charge against every return moves every bucket by the same amount,
    # so it shifts the LEVEL and leaves the SPREAD exactly where it was. That
    # is arithmetic rather than a finding, and it is the reason a long/short
    # book's cost has to be charged against its turnover instead.
    charged = apply_cost(realized.rows, 0.0010)
    after = ForwardReturnPanel(
        horizon=realized.horizon,
        rows=charged,
        dataset_version=realized.dataset_version,
        timezone_name=realized.timezone_name,
        symbols=realized.symbols,
        unrealized_instants=realized.unrealized_instants,
    )
    flat = signal_diagnostics(panel, after, buckets=5, minimum_assets=5)

    before = signal_diagnostics(panel, realized, buckets=5, minimum_assets=5)
    print("  A flat 10bp charge on every return:")
    print(
        f"    bottom bucket : {flat.quantiles[0].mean_forward_return:+.4%} "
        f"(was {before.quantiles[0].mean_forward_return:+.4%})"
    )
    print(f"    spread        : {flat.spread:+.4%} (was {baseline['spread']:+.4%})")
    print("    The level moved and the spread did not, because a flat charge")
    print("    moves every bucket equally. A cost model that only ever did this")
    print("    would report that costs never matter.")

    # The number that does bite is the one charged against turnover.
    book = weights_from_buckets(bucket_panel(rank_panel(panel).panel, 5), 5)
    turnover = factor_turnover(book, TurnoverConvention.ONE_WAY)
    rebalances = len(book) / HORIZON

    print()
    print(
        f"  Charged against turnover ({turnover.mean_turnover:.4f} per period, {len(book)} books):"
    )
    print(f"  {'cost / trade':>14} {'drag per rebalance':>20} {'spread after':>14}")
    for cost in (0.0005, 0.0010, 0.0020):
        drag = 2.0 * cost * turnover.mean_turnover * HORIZON
        print(f"  {cost:>14.4f} {drag:>20.4%} {(baseline['spread'] - drag):>+14.4%}")
    print(
        f"  (gross book of {sum(abs(w) for w in next(iter(book.values())).values()):.1f}, "
        f"{rebalances:.0f} rebalances at horizon {HORIZON})"
    )
    print()
    print("  This is the crudest possible cost model and is deliberately so: it")
    print("  answers 'how big would costs have to be to erase this?', which has")
    print("  a single number for an answer. It is not an execution model --")
    print("  AlphaLab already has one, with a simulator, commissions and fill")
    print("  policies, and nothing here reimplements any of it.")

    # ------------------------------------------------------------------
    # Step 07 : Resampling
    # ------------------------------------------------------------------

    returns = [value for row in realized.rows.values() for value in row.values()]

    print()
    print("Step 07 - Block bootstrap and Monte Carlo")
    print(f"  realized returns  : {len(returns)}")
    for block in (1, 5, 20):
        positions = block_bootstrap_indices(len(returns), block, seed=SEED)
        drawn = sample_by(returns, positions)
        print(
            f"  block size {block:>2}     : mean {sum(drawn) / len(drawn):+.6f} "
            f"over {len(drawn)} draws"
        )
    print()
    print("  Blocks rather than single observations: an IID bootstrap destroys")
    print("  the serial dependence a time-series result rests on, and an")
    print("  interval built from it is an interval for a different series.")
    print("  (alphalab.research.bootstrap resamples a COMPLETED run's returns")
    print("  independently, which is the right thing there and the wrong thing")
    print("  here; the two are kept apart rather than merged.)")

    paths = monte_carlo_orders(len(returns), 5, seed=SEED)
    print()
    print(f"  Monte Carlo paths : {len(paths)}")
    print(f"  each a permutation: {all(sorted(p) == list(range(len(returns))) for p in paths)}")
    print(f"  all distinct      : {len(set(paths)) == len(paths)}")
    print(f"  reproducible      : {monte_carlo_orders(len(returns), 5, seed=SEED) == paths}")
    print("  Each path is drawn afresh from the original order, so path 5 can")
    print("  be reproduced without generating the four in front of it.")

    print()
    print("=" * 74)
    print(
        f"{len(runs) + len(data_runs) + len(missing_runs) + len(delay_runs)} perturbation "
        "runs, every one naming its baseline, its dataset and its seed."
    )
    print("The baseline itself was never altered: the frames and panels are")
    print("frozen dataclasses of tuples, so that is the type rather than a promise.")
    print("=" * 74)


if __name__ == "__main__":
    main()
