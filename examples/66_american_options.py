"""
AlphaLab Examples
=================

Example 66 : American Options on a Lattice, and a Term Structure

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 36 (options pricing, Greeks and implied volatility)

Topics
------

• An American put on a Cox-Ross-Rubinstein lattice, converging step by step to
  a published table -- and the early-exercise premium over the European value
• A call that early exercise is worth nothing on, and one a cash dividend makes
  it worth something on
• Greeks from the lattice, and a lattice refused when it is too coarse to carry
  the rate
• Implied volatility inverted through the lattice an American quote was priced
  on, against the European formula that misreads it
• Volatility between two quoted expiries, linear in total variance, and a
  calendar arbitrage refused rather than smoothed

What this shows
---------------

Until v3.13 every option was priced by Black-Scholes-Merton, so an American
contract was valued as if it could not be exercised early and a discrete
dividend could not be stated at all. A lattice states both, together with the
number of steps it was computed on -- part of the model's identity, because a
price on 5 steps and a price on 500 are different numbers. Hull's textbook table
for this put is reproduced to the third decimal.

Run

    python examples/66_american_options.py
"""

import textwrap
from decimal import Decimal

from alphalab.options import (
    BinomialLattice,
    CashDividend,
    ExpiryInterpolation,
    OptionContract,
    OptionInputError,
    OptionPricingError,
    VolatilitySurface,
    VolPoint,
    binomial_greeks,
    binomial_value,
    black_scholes_value,
    dividend_yield,
    implied_vol_across_expiries,
    implied_volatility,
)
from alphalab.options.enums import ExerciseStyle, OptionType

#: The valuation instant (1 January 2026, 00:00 UTC) and the pricers' year.
NOW = 1_767_225_600.0
YEAR = 365.25 * 86_400.0
NOTHING_PAID = dividend_yield(0.0)


def rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def refused(label: str, error: Exception) -> None:
    print(f"  {label}: refused --")
    print(textwrap.fill(str(error), width=74, initial_indent="    ", subsequent_indent="    "))


def contract(kind: OptionType, style: ExerciseStyle, strike: str, years: float) -> OptionContract:
    return OptionContract("UNDERLYING", Decimal(strike), NOW + years * YEAR, kind, style, 100)


def main() -> None:
    print("=" * 72)
    print("AlphaLab Example 66 : American Options on a Lattice, and a Term Structure")
    print("=" * 72)

    # ----------------------------------------------------------------- #
    rule("An American put, step by step (S = K = 50, r = 10%, sigma = 40%, 5 months)")

    put = contract(OptionType.PUT, ExerciseStyle.AMERICAN, "50", 5 / 12)
    european = contract(OptionType.PUT, ExerciseStyle.EUROPEAN, "50", 5 / 12)
    for steps in (5, 30, 50, 100, 500):
        lattice = BinomialLattice(steps, ())
        american = binomial_value(put, 50.0, 0.40, 0.10, NOW, carry=NOTHING_PAID, lattice=lattice)
        on_lattice = binomial_value(
            european, 50.0, 0.40, 0.10, NOW, carry=NOTHING_PAID, lattice=lattice
        )
        print(
            f"  {steps:>3} steps: American {american:.3f}   European {on_lattice:.3f}   "
            f"early exercise {american - on_lattice:.3f}"
        )
    closed_form = black_scholes_value(european, 50.0, 0.40, 0.10, 5 / 12, carry=NOTHING_PAID)
    print(f"  Black-Scholes-Merton European: {closed_form:.3f}")
    print()
    print("  Hull's table reads 4.488, 4.263, 4.272, 4.278, 4.283. The European")
    print("  value on the lattice converges to the closed form; the American one")
    print("  stays above it by what the right to exercise early is worth.")

    # ----------------------------------------------------------------- #
    rule("Early exercise of a call: worth nothing, until a dividend")

    lattice = BinomialLattice(200, ())
    american_call = contract(OptionType.CALL, ExerciseStyle.AMERICAN, "100", 1.0)
    european_call = contract(OptionType.CALL, ExerciseStyle.EUROPEAN, "100", 1.0)
    plain = binomial_value(
        american_call, 100.0, 0.25, 0.05, NOW, carry=NOTHING_PAID, lattice=lattice
    ) - binomial_value(european_call, 100.0, 0.25, 0.05, NOW, carry=NOTHING_PAID, lattice=lattice)
    print(f"  no dividend          : American - European = {plain:.6f}")

    dividend = BinomialLattice(200, (CashDividend(NOW + 0.99 * YEAR, Decimal("15")),))
    with_dividend_american = binomial_value(
        american_call, 100.0, 0.25, 0.05, NOW, carry=NOTHING_PAID, lattice=dividend
    )
    with_dividend_european = binomial_value(
        european_call, 100.0, 0.25, 0.05, NOW, carry=NOTHING_PAID, lattice=dividend
    )
    print(
        f"  15.00 paid just before expiry: American {with_dividend_american:.4f}   "
        f"European {with_dividend_european:.4f}"
    )
    print("  Exercising the day before the stock goes ex captures the dividend the")
    print("  European holder forgoes. The lattice states its method: escrowed --")
    print("  the spot carried is the spot less the dividends' present value.")

    # ----------------------------------------------------------------- #
    rule("Greeks from the lattice, and a lattice too coarse to carry the rate")

    greeks = binomial_greeks(
        put, Decimal("50"), 0.40, 0.10, NOW, carry=NOTHING_PAID, lattice=BinomialLattice(500, ())
    )
    print(f"  delta {greeks.delta:+.4f}   gamma {greeks.gamma:.4f}   vega {greeks.vega:.4f}")
    print(f"  theta {greeks.theta:+.4f} a day   rho {greeks.rho:+.4f}")
    try:
        binomial_value(
            put, 50.0, 0.01, 0.50, NOW, carry=NOTHING_PAID, lattice=BinomialLattice(2, ())
        )
        print("  NOT REFUSED")
    except OptionPricingError as error:
        refused("2 steps, 1% volatility, a 50% rate", error)

    # ----------------------------------------------------------------- #
    rule("Implied volatility through the lattice the quote was priced on")

    quote_lattice = BinomialLattice(300, ())
    priced = binomial_value(put, 50.0, 0.30, 0.10, NOW, carry=NOTHING_PAID, lattice=quote_lattice)
    quoted = Decimal(f"{priced:.4f}")
    through_lattice = implied_volatility(
        put, quoted, Decimal("50"), 0.10, NOW, carry=NOTHING_PAID, lattice=quote_lattice
    )
    as_european = implied_volatility(put, quoted, Decimal("50"), 0.10, NOW, carry=NOTHING_PAID)
    print(f"  an American put quoted at {quoted} (priced at 30% volatility)")
    print(f"  inverted on the lattice : {through_lattice.value:.4f}")
    print(f"  inverted by the formula : {as_european.value:.4f}  (the early-exercise")
    print("                            premium misread as volatility)")

    # ----------------------------------------------------------------- #
    rule("Between two quoted expiries: linear in total variance")

    three_months, one_year = NOW + 0.25 * YEAR, NOW + 1.0 * YEAR
    surface = VolatilitySurface(
        "UNDERLYING",
        NOW,
        (VolPoint(100.0, three_months, 0.30), VolPoint(100.0, one_year, 0.24)),
    )
    for months in (3, 6, 9, 12):
        sigma = implied_vol_across_expiries(
            surface,
            100.0,
            NOW + months / 12 * YEAR,
            method=ExpiryInterpolation.TOTAL_VARIANCE_LINEAR,
        )
        print(f"  {months:>2} months: {sigma:.4f}")
    inverted = VolatilitySurface(
        "UNDERLYING",
        NOW,
        (VolPoint(100.0, three_months, 0.40), VolPoint(100.0, NOW + 0.30 * YEAR, 0.20)),
    )
    for label, target, chosen in (
        ("beyond the last expiry", NOW + 2 * YEAR, surface),
        ("total variance falling", NOW + 0.27 * YEAR, inverted),
    ):
        try:
            implied_vol_across_expiries(
                chosen, 100.0, target, method=ExpiryInterpolation.TOTAL_VARIANCE_LINEAR
            )
            print("  NOT REFUSED")
        except OptionInputError as error:
            refused(label, error)
    print()
    print("  Total variance must not fall as expiry lengthens: if it does, a calendar")
    print("  spread is free money, and the surface says so instead of averaging it away.")


if __name__ == "__main__":
    main()
