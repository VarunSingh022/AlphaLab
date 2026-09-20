"""
AlphaLab Examples
=================

Example 29 : Portfolio Stress Testing

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 28 (risk decomposition)

Topics
------

• Synthetic scenarios, where the caller supplies the magnitude
• Historical scenarios, which ship as contracts and refuse without data
• 2008, 2020 and 2022 as *definitions*, not as invented numbers
• Scoping a shock to a sector, an asset or a currency
• Composing shocks, and why order matters

What this shows
---------------

A synthetic scenario carries no claim about the world: the caller said "minus
ten percent". A historical one *is* a claim, and AlphaLab has no market data to
back it -- so `CRISIS_2008` ships as a `ScenarioDefinition` naming the window and
the observations it needs, and refuses to apply until a caller supplies them
from a real dataset.

Run

    python examples/29_portfolio_stress_testing.py
"""

from decimal import Decimal

from alphalab.scenario import (
    COMMODITY_SHOCK_2022,
    COVID_CRASH_2020,
    CRISIS_2008,
    HISTORICAL_SCENARIOS,
    RATES_REPRICING_2022,
    ScenarioExposure,
    ScenarioState,
    ScenarioValidationError,
    apply_all,
    commodity_shock,
    flash_crash,
    fx_shock,
    of_assets,
    rate_shock,
    scenario,
    sector_shock,
)

BOOK = ScenarioState(
    exposures=(
        ScenarioExposure(
            "AAPL",
            Decimal("12000"),
            Decimal("185.00"),
            "USD",
            volatility=0.24,
            available_liquidity=Decimal("900000"),
            sector="Technology",
        ),
        ScenarioExposure(
            "XOM",
            Decimal("30000"),
            Decimal("108.00"),
            "USD",
            volatility=0.29,
            available_liquidity=Decimal("450000"),
            sector="Energy",
        ),
        ScenarioExposure(
            "SAP",
            Decimal("9000"),
            Decimal("142.00"),
            "EUR",
            volatility=0.26,
            available_liquidity=Decimal("180000"),
            sector="Technology",
        ),
        ScenarioExposure(
            "TLT",
            Decimal("-15000"),
            Decimal("92.00"),
            "USD",
            volatility=0.15,
            available_liquidity=Decimal("700000"),
            sector="Rates",
        ),
    ),
    base_currency="USD",
    rates={"EUR": Decimal("1.08")},
)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def show(result) -> None:  # type: ignore[no-untyped-def]
    print(
        f"  {result.scenario_name:<28} {result.base_value:>14,.2f} -> "
        f"{result.shocked_value:>14,.2f}   {result.relative_change:>8.2%}"
    )


def main() -> None:
    print("AlphaLab — Portfolio Stress Testing (v3.3)")
    print("=" * 72)
    print("Book, valued in USD (the EUR leg needs a rate, and was given one):\n")
    for exposure in BOOK.exposures:
        value = exposure.market_value * BOOK.rate_for(exposure.currency)
        print(
            f"  {exposure.asset_id:<6} {exposure.quantity:>8,} @ {exposure.price:>7} "
            f"{exposure.currency}  {exposure.sector:<11} {value:>14,.2f} USD"
        )
    print(f"\n  {'total':<6} {'':>8} {'':>10}  {'':<11} {BOOK.value():>14,.2f} USD")

    # ----------------------------------------------------------------- #
    rule("1. Synthetic scenarios — the caller states the magnitude")

    print(f"  {'scenario':<28} {'before':>14} -> {'after':>14}   {'change':>8}")
    for result in apply_all(
        (
            flash_crash(Decimal("-0.10")),
            flash_crash(Decimal("-0.25")),
            sector_shock(Decimal("-0.35"), "Energy"),
            sector_shock(Decimal("-0.20"), "Technology"),
            commodity_shock(Decimal("-0.30"), "XOM"),
            rate_shock(Decimal("-0.12"), of_assets("TLT")),
            fx_shock(Decimal("-0.15"), "EUR"),
        ),
        BOOK,
    ):
        show(result)
    print()
    print("  Each is measured against the *unshocked* book, not against the")
    print("  previous scenario's output. That is what a comparison means.")

    # ----------------------------------------------------------------- #
    rule("2. Where the loss came from")

    detailed = sector_shock(Decimal("-0.35"), "Energy").apply(BOOK)
    for asset_id, change in detailed.change_by_asset.items():
        print(f"    {asset_id:<6} {change:>14,.2f} USD")
    print(f"    {'total':<6} {detailed.change:>14,.2f} USD")
    print(f"\n  reached {detailed.reached[0]} of {len(BOOK.exposures)} exposures")
    print("  Only the Energy name. TLT's short leg is untouched because the")
    print("  shock was scoped, not because it was netted away.")

    # ----------------------------------------------------------------- #
    rule("3. An FX shock moves the rate, not the price")

    fx = fx_shock(Decimal("-0.15"), "EUR").apply(BOOK)
    print(f"  EUR/USD rate   {BOOK.rates['EUR']} -> {fx.shocked_state.rates['EUR']}")
    sap_before = next(e for e in BOOK.exposures if e.asset_id == "SAP")
    sap_after = next(e for e in fx.shocked_state.exposures if e.asset_id == "SAP")
    print(f"  SAP local price {sap_before.price} -> {sap_after.price}   (unchanged)")
    print(
        f"  SAP in USD      {sap_before.market_value * BOOK.rates['EUR']:,.2f} -> "
        f"{sap_after.market_value * fx.shocked_state.rates['EUR']:,.2f}"
    )
    print()
    print("  A euro holding does not fall in euros when the euro falls. Shocking")
    print("  the price instead would have moved both, which is a different event.")

    # ----------------------------------------------------------------- #
    rule("4. Composition — and order matters")

    crisis = (
        flash_crash(Decimal("-0.18"))
        .then(scenario("liquidity_dry_up", liquidity_shock=Decimal("-0.75")))
        .then(scenario("vol_spike", volatility_shock=Decimal("1.40")))
    )
    result = crisis.apply(BOOK)
    show(result)
    print(f"  identity   {result.scenario_identity[:32]}...")
    print(f"  reached    {result.reached}  (one count per shock, in order)")
    worst = min(result.shocked_state.exposures, key=lambda e: e.available_liquidity or 0)
    print(
        f"  thinnest after the dry-up: {worst.asset_id} at {worst.available_liquidity:,.0f} units"
    )
    print()
    print(f"  base book untouched: {BOOK.value():,.2f} USD")
    print("  Every type here is frozen and every transformation is a replace(),")
    print("  so scenario leakage into the base book is structurally impossible.")

    # ----------------------------------------------------------------- #
    rule("5. Historical scenarios are contracts, not numbers")

    print("  Shipped definitions:\n")
    for name, definition in HISTORICAL_SCENARIOS.items():
        print(f"    {name:<22} window {definition.window}")
        print(f"      requires: {', '.join(definition.required_keys())}")
    print()
    try:
        CRISIS_2008.realize({})
    except ScenarioValidationError as error:
        first = str(error).split("\n")[0]
        print(f"  CRISIS_2008.realize({{}}):\n    {first}")
    print()
    print("  AlphaLab ships no market data. A hard-coded -0.37 for 2008 would be")
    print("  an invention that looks measured -- and wrong by however much your")
    print("  universe differed from whatever index the number came from.")

    # ----------------------------------------------------------------- #
    rule("6. Supply the observations, and it applies")

    print("  Measured by the caller from their own dataset over 2008-09-15..2008-12-31:")
    observed = {
        "equity_price": Decimal("-0.38"),
        "volatility": Decimal("1.90"),
        "liquidity": Decimal("-0.55"),
    }
    for key, value in observed.items():
        print(f"    {key:<14} {value}")
    realized = CRISIS_2008.realize(observed)
    result = realized.apply(BOOK)
    print()
    show(result)
    print(f"  identity   {result.scenario_identity[:32]}...")
    print()
    print("  The definitions for 2020 and 2022 work identically:")
    for definition in (COVID_CRASH_2020, RATES_REPRICING_2022, COMMODITY_SHOCK_2022):
        print(f"    {definition.name:<22} needs {len(definition.required_keys())} observation(s)")

    # ----------------------------------------------------------------- #
    rule("7. A scenario that touches nothing is an error")

    try:
        sector_shock(Decimal("-0.40"), "Utilities").apply(BOOK)
    except Exception as error:
        print(f"  {type(error).__name__}:\n    {str(error)[:130]}...")
    print()
    print("  Reporting 'no loss' for a scenario scoped to a sector the book does")
    print("  not hold would be a true statement about the wrong question.")

    print("\n" + "=" * 72)
    print("Example 29 complete.")


if __name__ == "__main__":
    main()
