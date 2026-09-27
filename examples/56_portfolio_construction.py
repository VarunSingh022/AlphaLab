"""
AlphaLab Examples
=================

Example 56 : Portfolio Construction

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 06 (the v1 portfolio optimizer)
✓ Example 28 (risk decomposition)

Topics
------

• One risk model: a covariance with a source, a period, a currency and an identity
• Minimum variance, mean-variance, maximum diversification and risk parity,
  each an optimization over the same stated constraints
• Risk parity with budgets stated per asset, and a budget that does not add up
• Robust mean-variance: the worst case over an uncertainty set for the forecast
• A factor-neutral book with a sector cap, gross and turnover limits and a
  dollar cap on one name -- and which of them bind
• Constraints that cannot all hold: INFEASIBLE, the conflict named, no weights
• Black-Litterman: an equilibrium prior, one view, a posterior, and weights
• Identities that name every input, whatever order they were listed in

What this shows
---------------

A portfolio is the answer to a stated question -- an objective, a risk model,
constraints -- and AlphaLab states all three before it answers. Every method
below is solved *over* its constraints by one numerical method and returns the
evidence that its answer is optimal: which constraints bind, how far any is
violated, how stationary the solution is. When the constraints cannot all hold
the answer is ``INFEASIBLE``, with the constraints that conflict named, and no
weights at all -- never an unconstrained answer quietly relaxed.

Run

    python examples/56_portfolio_construction.py
"""

from decimal import Decimal

from _portfolio_world import (
    DAYS,
    SYMBOLS,
    banner,
    covariance,
    factor_loadings,
    header,
    refusal,
    row,
    section,
    sectors,
    wrapped,
)

from alphalab.analytics import portfolio_factor_exposures
from alphalab.portfolio_optimizer import (
    BlackLittermanModel,
    BoxUncertainty,
    ConstraintSet,
    ConstructionInputError,
    ConstructionObjective,
    ConstructionProblem,
    ConstructionResult,
    EllipsoidalUncertainty,
    EquilibriumPrior,
    ExpectedReturns,
    ExposureRange,
    FactorBound,
    GroupBound,
    InvestorView,
    MaximumDiversification,
    MeanVariance,
    MinimumVariance,
    NotionalLimits,
    OptimizationError,
    RiskParity,
    RobustMeanVariance,
    SolverSettings,
    TurnoverLimit,
    WeightBounds,
    black_litterman,
    construct,
    view_variance_from_prior,
)

COVARIANCE = covariance()
SETTINGS = SolverSettings(
    feasibility_tolerance=1e-10, convergence_tolerance=1e-10, max_iterations=5_000
)
FULLY_INVESTED = ExposureRange.exactly(1.0)
CAPPED = ConstraintSet(FULLY_INVESTED, WeightBounds.long_only(0.30))

#: A house forecast of daily returns in dollars: the example's numbers, sourced.
FORECAST = ExpectedReturns(
    {
        "ATLS": 0.00060,
        "BRCK": 0.00030,
        "CEDR": 0.00050,
        "DUNE": 0.00010,
        "ELBE": 0.00030,
        "FALK": 0.00040,
        "GINZ": 0.00040,
        "HAKO": 0.00020,
    },
    currency="USD",
    period="1D",
    source="house forecast, June 2024",
)


def solve(objective: ConstructionObjective, constraints: ConstraintSet) -> ConstructionResult:
    return construct(ConstructionProblem(COVARIANCE, objective, constraints, SETTINGS))


def main() -> None:
    banner(56, "Portfolio Construction")

    # ----------------------------------------------------------------- #
    # 1. The risk model every construction below reads
    # ----------------------------------------------------------------- #

    section("1. One risk model, stated")
    definiteness = COVARIANCE.definiteness()
    print(f"  covariance   : {COVARIANCE.covariance_id[:16]}")
    print(f"  of           : {len(COVARIANCE.assets)} names, {COVARIANCE.observations} returns")
    print(f"  measured in  : {COVARIANCE.currency}, per {COVARIANCE.period}")
    print(f"  source       : {COVARIANCE.source}")
    print(
        f"  definiteness : {definiteness.kind.name}, rank {definiteness.rank}, "
        f"smallest / largest Cholesky pivot {definiteness.pivot_ratio:.3f}"
    )
    print(header())
    print(
        row(
            "volatility, % a day", {name: COVARIANCE.volatility(name) for name in SYMBOLS}, digits=2
        )
    )

    # ----------------------------------------------------------------- #
    # 2. Four objectives, one constraint set
    # ----------------------------------------------------------------- #

    section("2. Four objectives, fully invested, long only, 30% a name at most")
    results = {
        "minimum variance": solve(MinimumVariance(), CAPPED),
        "mean-variance": solve(MeanVariance(FORECAST, risk_aversion=4.0), CAPPED),
        "max diversification": solve(MaximumDiversification(), CAPPED),
        "risk parity": solve(RiskParity.equal(), CAPPED),
    }
    print(header("weight, %"))
    for name, result in results.items():
        print(row(name, result.require_weights()))
    print()
    print(f"  {'':<20}{'vol %/day':>10}{'forecast bp/day':>16}")
    for name, result in results.items():
        diagnostics = result.diagnostics
        assert diagnostics.volatility is not None
        weights = result.require_weights()
        forecast = sum(weights[asset] * FORECAST.values[asset] for asset in SYMBOLS)
        print(f"  {name:<20}{100 * diagnostics.volatility:10.3f}{1e4 * forecast:16.2f}")
    for name, result in results.items():
        binding = [label for label in result.diagnostics.binding if label != "net exposure = 1.0"]
        print(wrapped(f"binding, {name}", binding))
    ratio = results["max diversification"].diagnostics.diversification_ratio
    assert ratio is not None
    print(f"  diversification ratio of the maximum-diversification book: {ratio:.3f}")
    print()
    print("  Each result's share of its own risk (Euler contributions, summing to 100%):")
    print(header("risk share, %"))
    for name in ("minimum variance", "risk parity"):
        risk = results[name].diagnostics.risk
        assert risk is not None
        print(row(name, risk.relative))
    print(
        "  The forecast return is computed here for every method so the four can be\n"
        "  compared on it; only mean-variance was asked to maximize it."
    )

    # ----------------------------------------------------------------- #
    # 3. Risk budgets stated per asset
    # ----------------------------------------------------------------- #

    section("3. Risk parity with budgets stated per asset")
    technology = set(sectors().members("Technology"))
    budgets = {name: Decimal("0.15") if name in technology else Decimal("0.10") for name in SYMBOLS}
    budgeted = solve(
        RiskParity(budgets), ConstraintSet(FULLY_INVESTED, WeightBounds.long_only(None))
    )
    risk = budgeted.diagnostics.risk
    assert risk is not None
    print(header())
    print(row("budget, %", {name: float(budgets[name]) for name in SYMBOLS}))
    print(row("risk share, %", risk.relative))
    print(row("weight, %", budgeted.require_weights()))
    print(f"  largest gap between share and budget: {budgeted.diagnostics.budget_deviation:.1e}")
    try:
        RiskParity({**budgets, "HAKO": Decimal("0.09")})
    except ConstructionInputError as error:
        refusal("budgets summing to 0.99", error)

    # ----------------------------------------------------------------- #
    # 4. Robust mean-variance
    # ----------------------------------------------------------------- #

    section("4. Robust mean-variance: the worst case of an uncertain forecast")
    plain = results["mean-variance"]
    ellipsoid = EllipsoidalUncertainty.of_sample_mean(COVARIANCE, DAYS, radius=1.0)
    robust = solve(RobustMeanVariance(FORECAST, 4.0, ellipsoid), CAPPED)
    box = BoxUncertainty({name: COVARIANCE.volatility(name) / DAYS**0.5 for name in SYMBOLS})
    boxed = solve(RobustMeanVariance(FORECAST, 4.0, box), CAPPED)
    print(f"  uncertainty : the forecast's sampling error over {DAYS} days, C / {DAYS}")
    print(
        f"                {ellipsoid.omega.derivation}, from {str(ellipsoid.omega.parent_id)[:16]}"
    )
    print(header("weight, %"))
    print(row("mean-variance", plain.require_weights()))
    print(row("robust, ellipsoid", robust.require_weights()))
    print(row("robust, box", boxed.require_weights()))
    print(row("minimum variance", results["minimum variance"].require_weights()))
    for name, result in (("robust, ellipsoid", robust), ("robust, box", boxed)):
        worst = result.diagnostics.worst_case_return
        assert worst is not None and result.diagnostics.expected_return is not None
        print(
            f"  {name:<18}: forecast return {1e4 * result.diagnostics.expected_return:5.2f} bp, "
            f"worst case in the set {1e4 * worst:5.2f} bp"
        )
    print("  Doubting the forecast moves the answer toward minimum variance, by a stated amount.")

    # ----------------------------------------------------------------- #
    # 5. A factor-neutral, constrained rebalance
    # ----------------------------------------------------------------- #

    section("5. Momentum-neutral, sector-capped, from the book held today")
    loadings = factor_loadings()
    held = dict.fromkeys(SYMBOLS, 1.0 / len(SYMBOLS))
    constraints = ConstraintSet(
        net_exposure=FULLY_INVESTED,
        bounds=WeightBounds.uniform(-0.10, 0.30),
        max_gross_exposure=1.30,
        groups=(GroupBound(sectors(), "Technology", None, 0.40),),
        factors=(FactorBound.neutral(loadings, "momentum"),),
        turnover=TurnoverLimit(held, 0.60),
        notional_limits=NotionalLimits(Decimal("1000000"), {"DUNE": Decimal("50000")}),
    )
    rebalanced = solve(MeanVariance(FORECAST, risk_aversion=10.0), constraints)
    diagnostics = rebalanced.diagnostics
    print(f"  loadings     : {loadings.loadings_id[:16]}, {loadings.source}")
    print(header())
    print(row("momentum vs mean, %", {n: loadings.loading(n, "momentum") for n in SYMBOLS}))
    print(row("held, %", held))
    print(row("rebalanced, %", rebalanced.require_weights()))
    exposures = portfolio_factor_exposures(rebalanced.require_weights(), loadings)
    momentum = diagnostics.factor_exposures["momentum"]
    print(f"  {'momentum exposure':<20}: {momentum:+.1e}  (held neutral)")
    print(f"  {'volatility exposure':<20}: {exposures['volatility']:+.5f}  (not constrained)")
    for label, exposure in diagnostics.group_exposures.items():
        print(f"  {label:<20}: {100 * exposure:6.2f}%")
    assert diagnostics.gross_exposure is not None and diagnostics.turnover is not None
    print(f"  {'gross exposure':<20}: {diagnostics.gross_exposure:.4f}  (limit 1.30)")
    print(f"  {'turnover':<20}: {diagnostics.turnover:.4f}  (limit 0.60)")
    dune = 1e6 * abs(rebalanced.require_weights()["DUNE"])
    print(f"  {'DUNE notional':<20}: {dune:,.0f} of 1,000,000  (limit 50,000)")
    print(wrapped("binding", diagnostics.binding))
    print(
        f"  largest violation {diagnostics.max_violation:.1e}, "
        f"stationarity {diagnostics.stationarity:.1e}: the evidence it is optimal"
    )

    # ----------------------------------------------------------------- #
    # 6. Constraints that cannot all hold
    # ----------------------------------------------------------------- #

    section("6. Constraints that cannot all hold")
    impossible = solve(
        MinimumVariance(), ConstraintSet(FULLY_INVESTED, WeightBounds.long_only(0.10))
    )
    print(f"  8 names at most 10% each, fully invested -> {impossible.status.name}")
    print(wrapped("conflict", impossible.diagnostics.conflict))
    print(f"  weights  : {impossible.weights}")
    try:
        impossible.require_weights()
    except OptimizationError as error:
        print(f"  require_weights() -> {type(error).__name__}: {str(error).split('. ')[0]}.")

    # ----------------------------------------------------------------- #
    # 7. Black-Litterman
    # ----------------------------------------------------------------- #

    section("7. Black-Litterman: an equilibrium prior, one view, a posterior")
    market_values = {
        "ATLS": Decimal("950"),
        "BRCK": Decimal("180"),
        "CEDR": Decimal("620"),
        "DUNE": Decimal("140"),
        "ELBE": Decimal("95"),
        "FALK": Decimal("210"),
        "GINZ": Decimal("160"),
        "HAKO": Decimal("70"),
    }
    prior = EquilibriumPrior(market_values, currency="USD", risk_aversion=2.5)
    exposures = {"ATLS": 1.0, "DUNE": -1.0}
    view = InvestorView(
        "ATLS beats DUNE by 2 bp a day",
        exposures,
        expected_return=0.0002,
        variance=view_variance_from_prior(exposures, COVARIANCE, tau=0.05, scale=1.0),
    )
    posterior = black_litterman(BlackLittermanModel(COVARIANCE, prior, 0.05, (view,)))
    print(f"  model    : {posterior.model_id[:16]}")
    print(header("bp a day"))
    print(row("prior (equilibrium)", posterior.prior_returns.values, scale=1e4))
    print(row("posterior", posterior.posterior_returns.values, scale=1e4))
    for diagnostic in posterior.views:
        print(
            f"  view '{diagnostic.name}': stated {1e4 * diagnostic.stated:.1f} bp, "
            f"prior implied {1e4 * diagnostic.prior_implied:.2f} bp, "
            f"posterior {1e4 * diagnostic.posterior_implied:.2f} bp"
        )
    unconstrained = ConstraintSet(FULLY_INVESTED, WeightBounds.unbounded())
    at_prior = solve(MeanVariance(posterior.prior_returns, 2.5), unconstrained)
    at_posterior = solve(MeanVariance(posterior.posterior_returns, 2.5), unconstrained)
    capped_posterior = solve(MeanVariance(posterior.posterior_returns, 2.5), CAPPED)
    print(header("weight, %"))
    print(row("market", prior.market_weights()))
    print(row("MV, prior", at_prior.require_weights()))
    print(row("MV, posterior", at_posterior.require_weights()))
    print(row("MV, posterior, 30%", capped_posterior.require_weights()))
    market = prior.market_weights()
    gap = max(abs(at_prior.require_weights()[n] - market[n]) for n in SYMBOLS)
    tilted = [n for n in SYMBOLS if abs(at_posterior.require_weights()[n] - market[n]) > 1e-9]
    print(f"  on the prior, mean-variance returns the market weights (largest gap {gap:.1e});")
    print(f"  on the posterior it moves only the names in the view: {', '.join(tilted)}")

    # ----------------------------------------------------------------- #
    # 8. Identities
    # ----------------------------------------------------------------- #

    section("8. Identities name every input, not the order they were listed in")
    caps = (
        GroupBound(sectors(), "Technology", None, 0.45),
        GroupBound(sectors(), "Industrials", None, 0.45),
    )
    first = solve(
        MinimumVariance(), ConstraintSet(FULLY_INVESTED, WeightBounds.long_only(0.30), groups=caps)
    )
    listed_backwards = ConstraintSet(
        ExposureRange(1.0, 1.0), WeightBounds(0.0, 0.30), groups=tuple(reversed(caps))
    )
    again = solve(MinimumVariance(), listed_backwards)
    print(f"  problem              : {first.problem_id[:16]}")
    print(f"  result               : {first.result_id[:16]}")
    print(f"  caps listed backwards: {again.problem_id[:16]}  (identical result: {again == first})")
    looser = solve(
        MinimumVariance(),
        ConstraintSet(FULLY_INVESTED, WeightBounds.long_only(0.35), groups=caps),
    )
    print(f"  a 35% cap instead    : {looser.problem_id[:16]}  (a different problem)")


if __name__ == "__main__":
    main()
