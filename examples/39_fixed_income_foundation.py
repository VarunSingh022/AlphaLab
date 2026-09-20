"""
AlphaLab Examples
=================

Example 39 : Fixed Income Foundation

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 31 (global market conventions)

Topics
------

• Cash flows generated backwards from maturity, which is how a bond is built
• Clean, dirty and accrued: three numbers, and the identity between them
• The yield inversion, and the prices it refuses
• Duration in years, convexity in years squared -- and why that matters
• Discount factors from an observed curve, under a named compounding
• What this is **not**: the boundary, stated

What this shows
---------------

This is a **foundation**, and the module says so rather than leaving it to a
release note. What is here is the arithmetic a fixed-rate bond with known coupon
dates admits exactly. What is deliberately absent is everything that needs a
model: credit, embedded options, floating coupons, and the bootstrapping of a
discount curve from traded instruments.

A bond quoted at 98.50 does not cost 98.50. The difference is not a rounding: a
5% coupon eleven months into its period is nearly five points of a hundred-point
price.

Run

    python examples/39_fixed_income_foundation.py
"""

from datetime import date
from decimal import Decimal

from alphalab.conventions import Compounding, DayCount
from alphalab.macro import (
    Bond,
    YieldCurve,
    YieldCurvePoint,
    accrued_interest,
    cash_flows,
    clean_price,
    convexity,
    dirty_price,
    discount_factor_at,
    macaulay_duration,
    modified_duration,
    yield_at_tenor,
    yield_from_clean_price,
)
from alphalab.macro.exceptions import MacroComputationError, MacroInputError

SETTLEMENT = date(2026, 4, 15)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def bond(
    coupon: float = 0.05,
    frequency: Compounding = Compounding.SEMI_ANNUAL,
    day_count: DayCount = DayCount.ACT_365_FIXED,
    maturity: date = date(2034, 1, 15),
) -> Bond:
    return Bond(
        face=Decimal("100"),
        coupon_rate=coupon,
        frequency=frequency,
        issue_date=date(2024, 1, 15),
        maturity=maturity,
        day_count=day_count,
        currency="USD",
    )


def main() -> None:
    print("=" * 68)
    print("Example 39 : Fixed Income Foundation")
    print("=" * 68)

    reference = bond()

    # ----------------------------------------------------------------- #
    rule("Cash flows, generated backwards from maturity")

    flows = cash_flows(reference)
    print(f"  {len(flows)} payments on a 5% semi-annual bond maturing {reference.maturity}")
    for flow in flows[:3]:
        print(f"    {flow.payment_date}   {flow.amount:>8}")
    print("    ...")
    for flow in flows[-2:]:
        marker = "  <- coupon + principal" if flow.is_principal else ""
        print(f"    {flow.payment_date}   {flow.amount:>8}{marker}")
    print()
    print("  Backwards, because the redemption date is fixed and the coupons")
    print("  count back from it -- which is what places a short or long first")
    print("  period where it actually falls.")

    # ----------------------------------------------------------------- #
    rule("Three numbers, and the identity between them")

    for settlement in (date(2026, 1, 15), date(2026, 4, 15), date(2026, 7, 14)):
        clean = clean_price(reference, 0.05, settlement)
        accrued = accrued_interest(reference, settlement)
        dirty = dirty_price(reference, 0.05, settlement)
        holds = clean + accrued == dirty
        print(
            f"  {settlement}  clean {clean:>14}  accrued {accrued:>12}  "
            f"dirty {dirty:>14}  identity {holds}"
        )
    print()
    print("  Nothing accrues on a coupon date -- the coupon was just paid. Three")
    print("  months in, it is a quarter of the period. The buyer pays the dirty")
    print("  price; most government markets quote the clean one.")

    # ----------------------------------------------------------------- #
    rule("The day count basis changes the accrual")

    for basis in DayCount:
        accrued = accrued_interest(bond(day_count=basis), SETTLEMENT)
        print(f"  {basis.name:<18} {accrued:>14}")
    print()
    print("  Same bond, same dates, three answers. The basis is required.")

    # ----------------------------------------------------------------- #
    rule("Price against yield")

    print(f"  {'yield':>8} {'clean price':>16} {'mod duration':>14} {'convexity':>12}")
    print("  " + "-" * 54)
    for rate in (0.03, 0.04, 0.05, 0.06, 0.08):
        print(
            f"  {rate:>8.2%} {clean_price(reference, rate, SETTLEMENT):>16} "
            f"{modified_duration(reference, rate, SETTLEMENT):>14.6f} "
            f"{convexity(reference, rate, SETTLEMENT):>12.6f}"
        )
    print()
    print("  A negative yield is real and is expressible -- it is a price above")
    print(f"  par: at -0.50%, {clean_price(reference, -0.005, SETTLEMENT)}.")

    # ----------------------------------------------------------------- #
    rule("The inversion")

    for rate in (0.03, 0.05, 0.0637, 0.09):
        price = clean_price(reference, rate, SETTLEMENT)
        recovered = yield_from_clean_price(reference, price, SETTLEMENT)
        print(f"  priced at {rate:>8.4%} -> {price:>16} -> recovered {recovered:>12.8%}")

    print()
    for label, price in (
        ("far above any reachable yield", Decimal("100000")),
        ("a defaulted bond", Decimal("0")),
    ):
        try:
            yield_from_clean_price(reference, price, SETTLEMENT)
            print(f"  {label:<30} NOT REFUSED")
        except (MacroComputationError, MacroInputError) as error:
            print(f"  {label:<30} {str(error).split('. ')[0][:40]}")

    # ----------------------------------------------------------------- #
    rule("Duration is years. Convexity is years squared.")

    zero = Bond(
        face=Decimal("100"),
        coupon_rate=0.0,
        frequency=Compounding.ANNUAL,
        issue_date=date(2024, 1, 15),
        maturity=date(2034, 1, 15),
        day_count=DayCount.ACT_365_FIXED,
        currency="USD",
    )
    on_a_coupon_date = date(2026, 1, 15)
    print(
        f"  zero-coupon, 8 years left : macaulay "
        f"{macaulay_duration(zero, 0.05, on_a_coupon_date):.6f} years"
    )
    print(
        f"  5% coupon, same maturity  : macaulay "
        f"{macaulay_duration(reference, 0.05, on_a_coupon_date):.6f} years"
    )
    print("  A zero's duration is exactly its maturity; coupons shorten it.")

    print()
    base = float(dirty_price(reference, 0.05, SETTLEMENT))
    actual = float(dirty_price(reference, 0.06, SETTLEMENT))
    duration = modified_duration(reference, 0.05, SETTLEMENT)
    curvature = convexity(reference, 0.05, SETTLEMENT)
    linear = base * (1 - duration * 0.01)
    quadratic = base * (1 - duration * 0.01 + 0.5 * curvature * 0.01**2)
    print(f"  price at 5%                        {base:>12.6f}")
    print(f"  price at 6%, actual                {actual:>12.6f}")
    print(f"  duration estimate                  {linear:>12.6f}  error {abs(linear - actual):.6f}")
    print(
        f"  duration + convexity estimate      {quadratic:>12.6f}  "
        f"error {abs(quadratic - actual):.6f}"
    )
    print()
    print("  That is why both are reported, and why they are never added: one is")
    print("  years and the other is years squared.")

    # ----------------------------------------------------------------- #
    rule("Discount factors from an observed curve")

    curve = YieldCurve(
        country="US",
        currency="USD",
        timestamp=0.0,
        points=(
            YieldCurvePoint(Decimal("0.25"), Decimal("0.0425")),
            YieldCurvePoint(Decimal("2"), Decimal("0.0400")),
            YieldCurvePoint(Decimal("10"), Decimal("0.0450")),
        ),
    )
    observed = yield_at_tenor(curve, Decimal("5"))
    print(f"  the 5-year yield, interpolated linearly: {observed}")
    for compounding in Compounding:
        factor = discount_factor_at(curve, Decimal("5"), compounding)
        print(f"    {compounding.name:<12} -> {factor:.8f}")
    print()
    print("  Same observed yield, five different discount factors. Nothing in the")
    print("  curve says which convention its yields were quoted under, so the")
    print("  caller states it.")

    outside = discount_factor_at(curve, Decimal("30"), Compounding.ANNUAL)
    print(f"\n  the 30-year tenor was never quoted: {outside}")
    print("  None, not an extrapolated factor.")

    # ----------------------------------------------------------------- #
    rule("What this is not")

    print("  A YieldCurve is a set of *observed* yields. It is not a bootstrapped")
    print("  discount curve, and v3.4 deliberately did not make it one:")
    print("  bootstrapping requires choosing an interpolation scheme over an")
    print("  incomplete set of quotes, and the choice changes every forward rate")
    print("  read off the result.")
    print()
    print("  Also absent, each because it needs a model whose choice is the")
    print("  researcher's:")
    for absent in (
        "credit spreads and default",
        "embedded calls and puts",
        "floating and inflation-linked coupons",
        "the 30E/360 and ACT/ACT ISDA day-count variants",
    ):
        print(f"    - {absent}")
    print()
    print("  ROADMAP.md records the boundary. This is a foundation, not a")
    print("  fixed-income engine, and calling it one would be the overclaim.")

    print("\n" + "=" * 68)
    print("Example 39 complete.")


if __name__ == "__main__":
    main()
