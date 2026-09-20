"""
AlphaLab Examples
=================

Example 36 : Implied Volatility and the Volatility Surface

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 35 (options chains and Greeks)

Topics
------

• The inversion, and that it recovers the volatility it was priced at
• The five ways a quoted price has no implied volatility -- each refused
• A surface built from a chain, with every refusal reported
• Smile: a slice at one expiry
• Term structure: only the expiries that actually quote the strike

What this shows
---------------

Given a price, find the volatility that reproduces it. The Black-Scholes value
is strictly increasing in volatility, so when an answer exists it is unique --
which is the easy half.

The hard half is that a quoted price frequently has **no** answer, and the
failure is quiet. Returning a number anyway -- a clamp, a fallback, the last
iterate of a solver that never converged -- produces a surface with fabricated
points in exactly the corners a trader looks at. Every one of those raises here.

Run

    python examples/36_implied_volatility_surface.py
"""

from datetime import UTC, datetime
from decimal import Decimal

from alphalab.options import (
    ExerciseStyle,
    ImpliedVolatilityError,
    OptionChain,
    OptionContract,
    OptionType,
    black_scholes_price,
    black_scholes_value,
    implied_volatility,
    occ_symbol,
    surface_expiries,
    surface_from_chain,
    surface_slice,
    term_structure,
)

YEAR = 365.25 * 86400
VALUATION = datetime(2026, 1, 2, tzinfo=UTC).timestamp()
SPOT = Decimal("150")
RATE = 0.04


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def option(strike: str, expiry: float = VALUATION + YEAR) -> OptionContract:
    return OptionContract(
        underlying_asset_id="AAPL",
        strike=Decimal(strike),
        expiry=expiry,
        option_type=OptionType.CALL,
        style=ExerciseStyle.EUROPEAN,
        multiplier=100,
    )


def main() -> None:
    print("=" * 68)
    print("Example 36 : Implied Volatility and the Volatility Surface")
    print("=" * 68)

    # ----------------------------------------------------------------- #
    rule("The round trip")

    contract = option("150")
    print(f"  {'priced at':>10} {'price':>10} {'recovered':>12} {'steps':>7} {'residual':>12}")
    print("  " + "-" * 56)
    for volatility in (0.05, 0.15, 0.25, 0.60, 1.20):
        price = black_scholes_price(contract, SPOT, volatility, RATE, VALUATION)
        recovered = implied_volatility(contract, price, SPOT, RATE, VALUATION)
        print(
            f"  {volatility:>10.4f} {price:>10} {recovered.value:>12.8f} "
            f"{recovered.iterations:>7} {recovered.residual:>12.3g}"
        )
    print()
    print("  The solver evaluates the same expression the pricer rounds, so this")
    print("  is one formula inverted rather than a second implementation of it.")

    # ----------------------------------------------------------------- #
    rule("The result carries the model it was inverted under")

    recovered = implied_volatility(
        contract, black_scholes_price(contract, SPOT, 0.25, RATE, VALUATION), SPOT, RATE, VALUATION
    )
    print(f"  volatility  : {recovered.value:.8f}")
    print(f"  vega        : {recovered.vega:.4f}  (price change per 1.0 of volatility)")
    print(f"  assumptions : {recovered.assumptions.identity}")
    print()
    print("  An implied volatility is only meaningful against the model that")
    print("  implied it, so the two travel together.")

    # ----------------------------------------------------------------- #
    rule("Five ways a quoted price has no implied volatility")

    import math

    floor = float(SPOT) - 150.0 * math.exp(-RATE)
    one_day = 86400.0

    def near_expiry(strike: str, seconds: float) -> OptionContract:
        return OptionContract(
            "AAPL",
            Decimal(strike),
            VALUATION + seconds,
            OptionType.CALL,
            ExerciseStyle.EUROPEAN,
            100,
        )

    refusals = (
        ("quoted at the floor", contract, Decimal(str(round(floor, 6)))),
        ("quoted below the floor", contract, Decimal("0.01")),
        ("quoted above the ceiling", contract, Decimal("150")),
        ("unreachable at any volatility", near_expiry("400", 3600.0), Decimal("1e-20")),
        ("vega has collapsed", near_expiry("250", one_day), Decimal("1e-12")),
    )
    for label, target_contract, price in refusals:
        try:
            implied_volatility(target_contract, price, SPOT, RATE, VALUATION)
            print(f"  {label:<31} NOT REFUSED")
        except ImpliedVolatilityError as error:
            first = str(error).split(". ")[0]
            print(f"  {label:<31} {first[:48]}")

    print()
    print("  Each of these is routine in a real chain: a deep wing quoted at")
    print("  intrinsic, a far strike a day from expiry quoted at a fraction of a")
    print("  cent. A number in any of them would look like a measurement and be")
    print("  one only of the arithmetic.")

    # ----------------------------------------------------------------- #
    rule("A surface built from a chain -- and what it could not invert")

    #: A smile: the wings are quoted at higher volatility than the body, which is
    #: what a real chain looks like and what a single-volatility model cannot
    #: produce on its own.
    smile = {
        "120": 0.34,
        "130": 0.30,
        "140": 0.27,
        "150": 0.25,
        "160": 0.26,
        "170": 0.29,
        "180": 0.33,
    }
    expiries_declared = (VALUATION + YEAR / 2, VALUATION + YEAR)
    contracts = tuple(option(strike, expiry) for expiry in expiries_declared for strike in smile)
    chain = OptionChain("AAPL", VALUATION, contracts)

    prices: dict[str, Decimal] = {}
    for candidate in contracts:
        years = (candidate.expiry - VALUATION) / YEAR
        # A little term structure: the shorter expiry is quoted two points higher.
        volatility = smile[f"{candidate.strike:.0f}"] + (0.02 if years < 0.75 else 0.0)
        prices[occ_symbol(candidate)] = Decimal(
            str(black_scholes_value(candidate, float(SPOT), volatility, RATE, years))
        )

    # One wing is quoted at its intrinsic value, which no volatility reproduces.
    broken = occ_symbol(option("120", expiries_declared[0]))
    prices[broken] = Decimal("30.00")
    # And one contract simply has no quote.
    unquoted = occ_symbol(option("180", expiries_declared[1]))
    del prices[unquoted]

    surface, refused = surface_from_chain(chain, prices, SPOT, RATE, VALUATION)
    print(f"  contracts in the chain : {len(chain.contracts)}")
    print(f"  points on the surface  : {len(surface.points)}")
    print(f"  refused                : {len(refused)}")
    print(
        f"  the two account for every contract: "
        f"{len(surface.points) + len(refused) == len(chain.contracts)}"
    )
    print()
    for refusal in refused:
        print(f"    {refusal.contract_symbol:<26} strike {refusal.strike:>6.0f}")
        print(f"      {refusal.reason[:62]}")

    print()
    print("  A chain always contains some of these. Dropping them silently is how")
    print("  a surface comes to look complete while its corners are missing.")

    # ----------------------------------------------------------------- #
    rule("The smile at one expiry")

    for expiry in surface_expiries(surface):
        years = (expiry - VALUATION) / YEAR
        sliced = surface_slice(surface, expiry)
        print(f"  expiry {years:.2f}y")
        print("    " + "  ".join(f"{strike:>6.0f}" for strike in sliced.strikes))
        print("    " + "  ".join(f"{vol:>6.4f}" for vol in sliced.volatilities))

    print()
    print("  Interpolation runs along strikes and never across expiries: variance")
    print("  accumulates with time, so averaging two maturities' volatilities is")
    print("  wrong by an amount that grows with the gap between them.")

    # ----------------------------------------------------------------- #
    rule("Term structure at one strike")

    for strike in (150.0, 180.0):
        structure = term_structure(surface, strike)
        rendered = ", ".join(
            f"{(expiry - VALUATION) / YEAR:.2f}y={vol:.4f}" for expiry, vol in structure
        )
        print(f"  strike {strike:>6.0f} : {rendered or '(no expiry quotes this strike)'}")
    print()
    print("  Strike 180 appears at one expiry only -- the other was not quoted --")
    print("  so its term structure is short rather than interpolated.")

    print("\n" + "=" * 68)
    print("Example 36 complete.")


if __name__ == "__main__":
    main()
