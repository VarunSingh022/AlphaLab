"""
AlphaLab Examples
=================

Example 28 : Risk Decomposition

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 27 (portfolio attribution)

Topics
------

• Three VaR methods, and why the method has to be named
• The Euler decomposition: contributions that sum to the volatility
• A hedge that contributes negative risk, and keeps the sign
• Concentration, leverage, beta, correlation, liquidity, tail
• Degenerate samples that refuse rather than returning zero

What this shows
---------------

"VaR at 95%" is not a number until the method is stated. `VaRPolicy` carries the
method and confidence together so a figure cannot travel without them. And a
decomposition is only a decomposition if the parts sum to the whole --
`risk_contributions` does, exactly, which is the property that makes it worth
reading.

Run

    python examples/28_risk_decomposition.py
"""

from decimal import Decimal

from alphalab.analytics import (
    PositionRisk,
    VaRMethod,
    VaRPolicy,
    concentration,
    correlation_matrix,
    decompose,
    gross_weights,
    leverage,
    portfolio_volatility,
    risk_contributions,
    tail_ratio,
)
from alphalab.analytics.exceptions import AnalyticsValidationError

#: Deterministic, hand-written return series -- no RNG, so this example prints
#: the same numbers on every machine and every run.
GROWTH = (
    0.012,
    -0.008,
    0.021,
    -0.015,
    0.006,
    0.019,
    -0.024,
    0.011,
    0.003,
    -0.009,
    0.016,
    -0.013,
    0.008,
    0.002,
    -0.006,
    0.014,
    -0.011,
    0.007,
    -0.004,
    0.010,
)
DEFENSIVE = (
    0.004,
    0.002,
    0.005,
    -0.001,
    0.003,
    0.004,
    -0.002,
    0.003,
    0.001,
    -0.001,
    0.005,
    -0.002,
    0.004,
    0.001,
    -0.001,
    0.003,
    -0.002,
    0.002,
    0.000,
    0.003,
)
HEDGE = (
    -0.010,
    0.007,
    -0.018,
    0.013,
    -0.005,
    -0.016,
    0.020,
    -0.009,
    -0.002,
    0.008,
    -0.014,
    0.011,
    -0.007,
    -0.001,
    0.005,
    -0.012,
    0.009,
    -0.006,
    0.003,
    -0.008,
)

BOOK = (
    PositionRisk("GROWTH", Decimal("5200000"), "USD", GROWTH),
    PositionRisk("DEFENSIVE", Decimal("2600000"), "USD", DEFENSIVE),
    PositionRisk("HEDGE", Decimal("-1800000"), "USD", HEDGE),
)
EQUITY = Decimal("6000000")


def portfolio_returns() -> tuple[float, ...]:
    weights = gross_weights(BOOK)
    return tuple(
        weights["GROWTH"] * g + weights["DEFENSIVE"] * d + weights["HEDGE"] * h
        for g, d, h in zip(GROWTH, DEFENSIVE, HEDGE, strict=True)
    )


def equity_curve(returns: tuple[float, ...]) -> tuple[float, ...]:
    curve = [float(EQUITY)]
    for value in returns:
        curve.append(curve[-1] * (1.0 + value))
    return tuple(curve)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> None:
    print("AlphaLab — Risk Decomposition (v3.3)")
    print("=" * 62)

    returns = portfolio_returns()
    curve = equity_curve(returns)

    print("Book (one currency; a mixed one is refused, not converted):")
    for position in BOOK:
        print(f"  {position.asset_id:<10} {position.market_value:>12,} {position.currency}")
    print(f"  {'equity':<10} {EQUITY:>12,} USD")

    # ----------------------------------------------------------------- #
    rule("1. The method is part of the figure")

    for method in VaRMethod:
        policy = VaRPolicy(method, 0.95)
        print(
            f"  {policy.identity():<22} VaR {policy.var(returns):.6f}"
            f"   CVaR {policy.cvar(returns):.6f}"
        )
    print()
    print("  Three legitimate methods, three different answers from one sample.")
    print("  HISTORICAL makes no distributional claim and cannot exceed the")
    print("  worst observed loss. GAUSSIAN extrapolates and understates a fat")
    print("  tail. CORNISH_FISHER adjusts for skew and kurtosis.")
    print("  Losses are reported as positive magnitudes, stated once.")

    # ----------------------------------------------------------------- #
    rule("2. The decomposition sums to what it decomposes")

    volatility = portfolio_volatility(BOOK)
    contributions = risk_contributions(BOOK)
    print(f"  portfolio volatility (per period)  {volatility:.8f}")
    print()
    for asset_id, contribution in contributions.items():
        share = contribution / volatility * 100.0
        print(f"    {asset_id:<10} {contribution:>12.8f}   {share:>7.2f}% of total risk")
    total = sum(contributions.values())
    print(f"    {'sum':<10} {total:>12.8f}")
    print(f"\n  sum == volatility: {abs(total - volatility) < 1e-15}")
    print("  The Euler property. Without it this is a list, not a decomposition.")
    print()
    print(f"  HEDGE contributes {contributions['HEDGE']:.8f} -- negative, and kept.")
    print("  A position that hedges the rest genuinely removes risk; clamping")
    print("  it at zero would misstate both it and everything else's share.")

    # ----------------------------------------------------------------- #
    rule("3. Concentration and leverage")

    spread = concentration(BOOK)
    exposure = leverage(BOOK, EQUITY)
    print(f"  Herfindahl index      {spread.herfindahl:.4f}")
    print(f"  effective positions   {spread.effective_positions:.2f}  (of 3 actual)")
    print(f"  largest              {spread.largest_asset_id} at {spread.largest_weight:.2%}")
    print()
    print(f"  gross exposure       {exposure.gross_exposure:>12,} USD")
    print(f"  net exposure         {exposure.net_exposure:>12,} USD")
    print(f"  gross leverage       {exposure.gross_leverage:>12.4f}x")
    print(f"  net leverage         {exposure.net_leverage:>12.4f}x")
    print(f"  long / short         {exposure.long_exposure:,} / {exposure.short_exposure:,}")

    # ----------------------------------------------------------------- #
    rule("4. Correlation")

    matrix = correlation_matrix(BOOK)
    names = list(matrix)
    print("              " + "".join(f"{name:>12}" for name in names))
    for first in names:
        row = "".join(f"{matrix[first][second]:>12.4f}" for second in names)
        print(f"  {first:<11}{row}")
    print("\n  Derived from the covariance matrix, so the two can never disagree.")

    # ----------------------------------------------------------------- #
    rule("5. Everything at once, under one policy")

    result = decompose(
        BOOK,
        returns,
        EQUITY,
        curve,
        VaRPolicy(VaRMethod.CORNISH_FISHER, 0.99),
        benchmark=GROWTH,
        loadings={
            "GROWTH": {"momentum": 0.85, "quality": -0.20},
            "DEFENSIVE": {"quality": 0.60},
            "HEDGE": {"momentum": -0.75},
        },
        units={
            "GROWTH": Decimal("29000"),
            "DEFENSIVE": Decimal("41000"),
            "HEDGE": Decimal("22000"),
        },
        average_daily_volume={
            "GROWTH": Decimal("1400000"),
            "DEFENSIVE": Decimal("3100000"),
            "HEDGE": Decimal("95000"),
        },
        participation_limit=Decimal("0.15"),
    )
    print(f"  policy                {result.policy.identity()}")
    print(f"  volatility            {result.volatility:.6f}")
    print(
        f"  VaR / CVaR            {result.value_at_risk:.6f} / "
        f"{result.conditional_value_at_risk:.6f}"
    )
    print(f"  tail ratio            {result.tail_ratio:.4f}  (CVaR / VaR)")
    print(f"  beta vs GROWTH        {result.beta:.4f}")
    print(f"  max drawdown          {result.max_drawdown:.6f}")
    print(
        f"  factor exposure       { {k: round(v, 4) for k, v in result.factor_exposure.items()} }"
    )
    assert result.liquidity is not None
    print(
        f"  worst to liquidate    {result.liquidity.worst_asset_id} at "
        f"{result.liquidity.worst_days:.2f} days "
        f"(at {result.liquidity.participation_limit:.0%} of ADV)"
    )

    # ----------------------------------------------------------------- #
    rule("6. Unmeasured is not zero")

    bare = decompose(BOOK, returns, EQUITY, curve, VaRPolicy(VaRMethod.HISTORICAL, 0.95))
    print(f"  no benchmark supplied  -> beta            {bare.beta}")
    print(f"  no loadings supplied   -> factor exposure {bare.factor_exposure}")
    print(f"  no volumes supplied    -> liquidity       {bare.liquidity}")
    print()
    print("  None, not 0.0. A beta of zero is a measurement; None is the")
    print("  absence of one, and they must not look alike in a report.")

    # ----------------------------------------------------------------- #
    rule("7. Degenerate samples refuse")

    flat = (
        PositionRisk("A", Decimal("100"), "USD", GROWTH),
        PositionRisk("FLAT", Decimal("100"), "USD", (0.01,) * len(GROWTH)),
    )
    try:
        correlation_matrix(flat)
    except AnalyticsValidationError as error:
        print(f"  a constant series:\n    {str(error)[:110]}...")

    try:
        leverage(BOOK, Decimal("0"))
    except AnalyticsValidationError as error:
        print(f"\n  leverage against no capital:\n    {str(error)[:110]}...")

    try:
        VaRPolicy(VaRMethod.CORNISH_FISHER, 0.95).var((0.01, -0.02, 0.03))
    except AnalyticsValidationError as error:
        print(f"\n  Cornish-Fisher on 3 observations:\n    {str(error)[:110]}...")

    print()
    ratio = tail_ratio(VaRPolicy(VaRMethod.HISTORICAL, 0.95), returns)
    print(f"  tail ratio is always >= 1: {ratio:.4f}")
    print("  A risk report that turned an undefined measurement into 0.0 would")
    print("  report a riskless portfolio. That is the worst possible placeholder.")

    print("\n" + "=" * 62)
    print("Example 28 complete.")


if __name__ == "__main__":
    main()
