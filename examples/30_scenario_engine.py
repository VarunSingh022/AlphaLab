"""
AlphaLab Examples
=================

Example 30 : The Scenario Engine

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 29 (portfolio stress testing)

Topics
------

• One contract, applied to four different kinds of portfolio
• Immutability: applying returns, it never mutates
• Determinism and derived identity, stable across processes
• Inspectability: the base state, the shocked state, and the reach
• Unsupported fields refused rather than skipped

What this shows
---------------

Example 29 stressed one book. This one shows *why the contract is generic*: the
same `Scenario` object is applied to a hand-built book, a long/short book, a
multi-currency book and a single-name book without knowing anything about where
any of them came from. That is only possible because a scenario takes a
`ScenarioState` -- a flat projection anything holding positions can produce --
rather than naming a portfolio class.

Run

    python examples/30_scenario_engine.py
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal

from alphalab.scenario import (
    Scenario,
    ScenarioExposure,
    ScenarioState,
    Shock,
    ShockKind,
    UnsupportedShockError,
    everything,
    from_positions,
    of_currency,
    of_sector,
    scenario,
)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


#: One scenario object, built once, applied to everything below.
CRASH = scenario("systemic_crash", price_shock=Decimal("-0.22"))


def simple_book() -> ScenarioState:
    """Built from four-tuples -- the minimum a scenario needs."""

    return from_positions(
        [
            ("AAPL", Decimal("500"), Decimal("185.00"), "USD"),
            ("MSFT", Decimal("300"), Decimal("410.00"), "USD"),
        ],
        "USD",
    )


def long_short_book() -> ScenarioState:
    return from_positions(
        [
            ("AAPL", Decimal("500"), Decimal("185.00"), "USD"),
            ("SPY", Decimal("-400"), Decimal("505.00"), "USD"),
        ],
        "USD",
    )


def multi_currency_book() -> ScenarioState:
    return from_positions(
        [
            ("AAPL", Decimal("500"), Decimal("185.00"), "USD"),
            ("SAP", Decimal("400"), Decimal("142.00"), "EUR"),
            ("SONY", Decimal("900"), Decimal("3100.00"), "JPY"),
        ],
        "USD",
        rates={"EUR": Decimal("1.08"), "JPY": Decimal("0.0065")},
    )


def single_name_book() -> ScenarioState:
    return from_positions([("AAPL", Decimal("500"), Decimal("185.00"), "USD")], "USD")


def rich_book() -> ScenarioState:
    """Carries volatility, liquidity and sector, so every shock kind applies."""

    return ScenarioState(
        exposures=(
            ScenarioExposure(
                "AAPL",
                Decimal("500"),
                Decimal("185.00"),
                "USD",
                volatility=0.24,
                available_liquidity=Decimal("900000"),
                sector="Technology",
            ),
            ScenarioExposure(
                "XOM",
                Decimal("800"),
                Decimal("108.00"),
                "USD",
                volatility=0.29,
                available_liquidity=Decimal("450000"),
                sector="Energy",
            ),
        ),
        base_currency="USD",
        rates={},
    )


def main() -> None:
    print("AlphaLab — The Scenario Engine (v3.3)")
    print("=" * 68)

    # ----------------------------------------------------------------- #
    rule("1. One scenario, four different books")

    print(f"  scenario: {CRASH.name}  ({CRASH.shocks[0].kind.name} {CRASH.shocks[0].magnitude})")
    print(f"  identity: {CRASH.identity()[:40]}...\n")
    print(f"  {'book':<22} {'before':>16} {'after':>16} {'change':>9}")
    for label, book in (
        ("long only", simple_book()),
        ("long / short", long_short_book()),
        ("multi-currency", multi_currency_book()),
        ("single name", single_name_book()),
        ("with vol + liquidity", rich_book()),
    ):
        result = CRASH.apply(book)
        print(
            f"  {label:<22} {result.base_value:>16,.2f} {result.shocked_value:>16,.2f} "
            f"{result.relative_change:>9.2%}"
        )
    print()
    print("  The scenario knows nothing about any of them. It reads a")
    print("  ScenarioState, which is a flat projection -- so a backtest book, a")
    print("  live book, an optimizer target and a spreadsheet all work the same.")

    # ----------------------------------------------------------------- #
    rule("2. A long/short book nets, and the engine shows both legs")

    result = CRASH.apply(long_short_book())
    for asset_id, change in result.change_by_asset.items():
        direction = "loses" if change < 0 else "gains"
        print(f"    {asset_id:<6} {direction} {abs(change):>12,.2f} USD")
    print(
        f"    {'net':<6}        {abs(result.change):>12,.2f} USD "
        f"({'loss' if result.change < 0 else 'gain'})"
    )

    # ----------------------------------------------------------------- #
    rule("3. Applying returns; it never mutates")

    book = rich_book()
    before = book.value()
    for magnitude in ("-0.05", "-0.30", "-0.70", "-0.99"):
        scenario("s", price_shock=Decimal(magnitude)).apply(book)
    print(f"  applied four scenarios; base value {before:,.2f} -> {book.value():,.2f}")
    print(f"  unchanged: {before == book.value()}")
    print()
    try:
        book.exposures[0].price = Decimal("1.00")  # type: ignore[misc]
    except FrozenInstanceError:
        print("  ScenarioExposure is frozen -- a shocked state is a new object,")
        print("  never an edit. Scenario leakage is structurally impossible.")

    # ----------------------------------------------------------------- #
    rule("4. Chaining, when that is what you want")

    chained = CRASH.apply(CRASH.apply(rich_book()).shocked_state)
    once = CRASH.apply(rich_book())
    print(f"  applied once   {once.shocked_value:,.2f}")
    print(f"  applied twice  {chained.shocked_value:,.2f}")
    print(
        f"  twice == once * 0.78: {chained.shocked_value == (once.shocked_value * Decimal('0.78'))}"
    )
    print()
    print("  apply_all() measures each scenario against the same base; feeding")
    print("  a shocked state back in is how you compound deliberately.")

    # ----------------------------------------------------------------- #
    rule("5. Determinism and derived identity")

    first = scenario("crash", price_shock=Decimal("-0.30"), volatility_shock=Decimal("1.5"))
    second = scenario("crash", price_shock=Decimal("-0.30"), volatility_shock=Decimal("1.5"))
    print(f"  two identical scenarios -> same identity: {first.identity() == second.identity()}")
    print(
        f"  a different magnitude   -> different:     "
        f"{first.identity() != scenario('crash', price_shock=Decimal('-0.31')).identity()}"
    )
    print(
        f"  applying twice gives the same result:     "
        f"{first.apply(rich_book()) == first.apply(rich_book())}"
    )
    print()
    print("  SHA-256 over the canonical rendering -- derived from content, never")
    print("  minted and never taken from a clock, so it reproduces in any process.")

    # ----------------------------------------------------------------- #
    rule("6. Inspectability")

    composed = Scenario(
        "targeted",
        (
            Shock(ShockKind.PRICE, Decimal("-0.30"), of_sector("Energy")),
            Shock(ShockKind.VOLATILITY, Decimal("1.20"), everything()),
        ),
    )
    inspected = composed.apply(rich_book())
    print(f"  name        {inspected.scenario_name}")
    print(f"  shocks      {[shock.identity() for shock in composed.shocks]}")
    print(f"  reached     {inspected.reached}   (per shock, in order)")
    print(f"  base value  {inspected.base_value:,.2f}")
    print(f"  after       {inspected.shocked_value:,.2f}")
    print(f"  per asset   { {k: str(v) for k, v in inspected.change_by_asset.items()} }")
    print()
    print("  The result carries both states, so a caller holding only the result")
    print("  can still see exactly what it was measured against.")

    # ----------------------------------------------------------------- #
    rule("7. Unsupported fields are refused, not skipped")

    plain = simple_book()
    for label, built in (
        (
            "volatility shock on a book with no volatility",
            scenario("v", volatility_shock=Decimal("1.5")),
        ),
        (
            "liquidity shock on a book with no liquidity",
            scenario("l", liquidity_shock=Decimal("-0.5")),
        ),
        (
            "sector shock on a book with no sectors",
            Scenario("s", (Shock(ShockKind.PRICE, Decimal("-0.3"), of_sector("Tech")),)),
        ),
        (
            "FX shock on the reporting currency",
            Scenario("f", (Shock(ShockKind.FX, Decimal("-0.2"), of_currency("USD")),)),
        ),
    ):
        try:
            built.apply(plain)
            print(f"  {label:<46} NOT REFUSED")
        except UnsupportedShockError:
            print(f"  {label:<46} UnsupportedShockError")
    print()
    print("  Skipping a leg would report a smaller loss under the scenario's own")
    print("  name, and nothing downstream could tell. So it raises instead.")

    print("\n" + "=" * 68)
    print("Example 30 complete.")


if __name__ == "__main__":
    main()
