"""
AlphaLab Examples
=================

Example 26 : Capacity Modelling

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 25 (execution simulation)

Topics
------

• Capacity as a liquidity question, not a return-series question
• The two binding constraints, and which name binds first
• Sensitivity to capital, participation, turnover, liquidity and impact
• The assumptions travelling with the figure
• Refusals: no default participation limit, no default turnover

What this shows
---------------

"How much can this strategy trade?" has no answer without liquidity. This model
connects capital, position size, ADV, turnover, participation and market impact,
reports the capital at which a *named* constraint binds, and names the asset
that bound it. Every assumption is required, because each moves the answer by
orders of magnitude and none has a universal value.

Run

    python examples/26_capacity_modelling.py
"""

from decimal import Decimal

from alphalab.execution import (
    AssetLiquidity,
    CapacityModel,
    ImpactModel,
    LinearImpact,
    NoImpact,
    SquareRootImpact,
    capacity_curve,
)
from alphalab.execution.exceptions import ExecutionValidationError

#: A three-name book: one deep, one ordinary, one thin. Equal weights, so the
#: only thing that differs between them is the liquidity behind them.
UNIVERSE = (
    AssetLiquidity("MEGACAP", Decimal("180"), Decimal("12000000"), Decimal("0.34"), "USD", "SIM"),
    AssetLiquidity("MIDCAP", Decimal("60"), Decimal("900000"), Decimal("0.33"), "USD", "SIM"),
    AssetLiquidity("SMALLCAP", Decimal("14"), Decimal("120000"), Decimal("0.33"), "USD", "SIM"),
)

CEILING = Decimal("1e12")


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def money(value: Decimal) -> str:
    return f"{value:>16,.0f}"


def model(
    participation: str = "0.10",
    turnover: str = "0.25",
    impact: ImpactModel | None = None,
    budget: str | None = "0.0015",
) -> CapacityModel:
    return CapacityModel(
        participation_limit=Decimal(participation),
        turnover=Decimal(turnover),
        impact_model=SquareRootImpact(Decimal("0.04")) if impact is None else impact,
        impact_budget=None if budget is None else Decimal(budget),
        search_ceiling=CEILING,
    )


def main() -> None:
    print("AlphaLab — Capacity Modelling (v3.3)")
    print("=" * 62)
    print("Three names, equally weighted. Only their liquidity differs.\n")
    for asset in UNIVERSE:
        print(
            f"  {asset.asset_id:<9} price {asset.price:>7}  "
            f"ADV {asset.average_daily_volume:>10,}  "
            f"daily notional {money(asset.daily_notional)}"
        )

    # ----------------------------------------------------------------- #
    rule("1. Where the book binds, and on what")

    result = model().capacity(UNIVERSE, 0.0)
    print(f"  capacity          {money(result.capacity)} USD")
    print(f"  binding name      {result.binding_asset_id}")
    print(f"  binding constraint {result.constraint.name}")
    print()
    print("  Per name:")
    for item in result.per_asset:
        impact = "unevaluated" if item.impact_capacity is None else money(item.impact_capacity)
        print(
            f"    {item.asset_id:<9} participation {money(item.participation_capacity)}"
            f"   impact {impact}   -> {item.constraint.name}"
        )
    print()
    print("  The minimum, not the mean: the first name to bind caps the book.")

    # ----------------------------------------------------------------- #
    rule("2. The assumptions travel with the figure")

    for key, value in result.assumptions.items():
        print(f"    {key:<20} {value}")
    print()
    print("  A capacity number without these is not a capacity number.")

    # ----------------------------------------------------------------- #
    rule("3. Sensitivity — what actually moves the answer")

    print("  participation limit (impact constraint off):")
    for limit in ("0.01", "0.05", "0.10", "0.25"):
        built = model(participation=limit, impact=NoImpact(), budget=None)
        print(f"    {limit:>6} of ADV   {money(built.capacity(UNIVERSE, 0.0).capacity)} USD")

    print("\n  turnover (participation fixed at 10%):")
    for turnover in ("0.05", "0.25", "1.00"):
        built = model(turnover=turnover, impact=NoImpact(), budget=None)
        print(f"    {turnover:>6} per period {money(built.capacity(UNIVERSE, 0.0).capacity)} USD")

    print("\n  impact assumption (budget fixed at 15 bps):")
    impacts: tuple[tuple[str, ImpactModel], ...] = (
        ("SquareRoot 0.02", SquareRootImpact(Decimal("0.02"))),
        ("SquareRoot 0.04", SquareRootImpact(Decimal("0.04"))),
        ("Linear     0.04", LinearImpact(Decimal("0.04"))),
    )
    for label, impact_model in impacts:
        built = model(impact=impact_model)
        print(f"    {label:<16} {money(built.capacity(UNIVERSE, 0.0).capacity)} USD")

    print("\n  liquidity (SMALLCAP's ADV doubled):")
    deeper = (
        UNIVERSE[0],
        UNIVERSE[1],
        AssetLiquidity("SMALLCAP", Decimal("14"), Decimal("240000"), Decimal("0.33"), "USD", "SIM"),
    )
    print(f"    as given       {money(model().capacity(UNIVERSE, 0.0).capacity)} USD")
    print(f"    ADV doubled    {money(model().capacity(deeper, 0.0).capacity)} USD")

    # ----------------------------------------------------------------- #
    rule("4. How hard the book presses as capital grows")

    print("        capital      worst participation   impact (fraction of price)")
    for capital, participation, impact_fraction in capacity_curve(
        model(),
        UNIVERSE,
        [Decimal("1e6"), Decimal("5e6"), Decimal("2e7"), Decimal("1e8")],
        0.0,
    ):
        print(
            f"    {capital:>13,.0f}          {participation:>8.4f}"
            f"            {impact_fraction:>10.6f}"
        )
    print()
    print("  The worst name, not the average: the average is not what binds.")

    # ----------------------------------------------------------------- #
    rule("5. Nothing is assumed")

    try:
        CapacityModel(
            participation_limit=Decimal("0.1"),
            turnover=Decimal("0"),
            impact_model=NoImpact(),
            impact_budget=None,
            search_ceiling=CEILING,
        )
    except ExecutionValidationError as error:
        print(f"  turnover of zero:\n    {error}")

    try:
        AssetLiquidity("DEAD", Decimal("10"), Decimal("0"), Decimal("1"), "USD", "SIM")
    except ExecutionValidationError as error:
        print(f"\n  a name that trades nothing:\n    {str(error)[:120]}...")

    print()
    print("  And there is no default participation limit, no default turnover")
    print("  and no default impact budget anywhere in this module. Passing")
    print("  impact_budget=None reports the constraint as 'unevaluated' rather")
    print("  than quietly evaluating it against a number nobody chose.")

    print("\n" + "=" * 62)
    print("Example 26 complete.")


if __name__ == "__main__":
    main()
