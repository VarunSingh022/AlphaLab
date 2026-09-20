"""
AlphaLab Examples
=================

Example 37 : FX Research

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 14 (multi-currency settlement)

Topics
------

• Quotation direction, carried rather than inferred
• Cross rates: derived deliberately, and only through a named currency
• Forwards from covered interest parity, with both rates and the day count
  stated
• Carry, and why it has the opposite sign to forward points
• The look-ahead guard: a rate that was not yet true is refused
• Currency attribution: what the assets did, and what the currency did

What this shows
---------------

`alphalab.portfolio.fx` is the rate authority and has been since v2.16: no
default rate, no fallback of 1.0, no triangulation and no implicit inversion.
v3.4 adds research on top of those types without defining a second rate type,
and closes the other side of time -- a rate dated *after* the instant it is
read at is now a refusal, not just a stale one.

**AlphaLab ships no FX rate.** Every rate below is declared by this example.

Run

    python examples/37_fx_research.py
"""

from datetime import date
from decimal import Decimal

from alphalab.conventions import DayCount
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.fx import (
    FutureDatedRateError,
    FxRate,
    FxRates,
    MissingRateError,
    StaleRateError,
)
from alphalab.portfolio.fx_research import (
    ForwardTerms,
    carry_rate,
    covered_forward_rate,
    currency_attribution,
    currency_exposures,
    forward_points,
    hedge_notional,
)
from alphalab.portfolio.position import Position

OPEN = 0.0
CLOSE = 86400.0


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> None:
    print("=" * 68)
    print("Example 37 : FX Research")
    print("=" * 68)

    table = FxRates.of(
        [
            FxRate("EUR", "USD", Decimal("1.0850"), OPEN, "declared-by-this-example"),
            FxRate("USD", "JPY", Decimal("151.20"), OPEN, "declared-by-this-example"),
            FxRate("GBP", "USD", Decimal("1.2700"), OPEN, "declared-by-this-example"),
        ]
    )

    # ----------------------------------------------------------------- #
    rule("Direction is part of the rate")

    for base, quote in table.pairs:
        held = table.rate_for(base, quote)
        assert held is not None
        print(f"  one {base} buys {held.rate} {quote}   (source: {held.source!r})")

    print()
    print("  There is no convention that settles which way a pair is quoted, so")
    print("  every rate states it. Reading one backwards is not a small error.")

    try:
        table.convert(Decimal("100"), "USD", "EUR", as_of=OPEN)
        print("  NOT REFUSED")
    except MissingRateError as error:
        print(f"\n  USD -> EUR from a EUR/USD rate: {str(error).split('.')[1].strip()[:56]}")

    inverted = table.with_inverses()
    reverse = inverted.rate_for("USD", "EUR")
    assert reverse is not None
    print(f"  with_inverses() mints it: {reverse.rate:.6f}, derived={reverse.derived}")

    # ----------------------------------------------------------------- #
    rule("A cross is a deliberate act, through a named currency")

    try:
        table.convert(Decimal("100"), "EUR", "JPY", as_of=OPEN)
        print("  NOT REFUSED")
    except MissingRateError:
        print("  convert(EUR -> JPY)          refused: the table triangulates nothing")

    via_usd = table.cross_rate("EUR", "JPY", via="USD")
    print(f"  cross_rate(EUR, JPY, via=USD) {via_usd.rate}  derived={via_usd.derived}")
    print(f"    source: {via_usd.source}")

    wider = table.with_rate(FxRate("GBP", "JPY", Decimal("192.50"), OPEN, "declared"))
    via_gbp_table = wider.with_rate(FxRate("EUR", "GBP", Decimal("0.8543"), OPEN, "declared"))
    via_gbp = via_gbp_table.cross_rate("EUR", "JPY", via="GBP")
    print(f"  cross_rate(EUR, JPY, via=GBP) {via_gbp.rate}")
    print()
    print("  Two routes, two numbers. Which one was used is a fact a result has")
    print("  to carry, which is why `via` is required rather than chosen here.")

    # ----------------------------------------------------------------- #
    rule("Forwards: covered interest parity, with every input stated")

    spot = table.rate_for("EUR", "USD")
    assert spot is not None
    terms = ForwardTerms(
        value_date=date(2026, 7, 2),
        spot_date=date(2026, 1, 6),
        base_rate=0.0250,
        quote_rate=0.0425,
        basis=DayCount.ACT_360,
    )
    forward = covered_forward_rate(spot, terms)
    print(f"  spot EUR/USD        {spot.rate}")
    print(f"  EUR deposit rate    {terms.base_rate:.4%}")
    print(f"  USD deposit rate    {terms.quote_rate:.4%}")
    print(f"  day count           {terms.basis.name}  ({terms.years:.6f} years)")
    print(f"  forward EUR/USD     {forward.rate:.6f}   derived={forward.derived}")
    print(f"  forward points      {forward_points(spot, forward):+.6f} USD per EUR")
    print(f"  carry (long EUR)    {carry_rate(terms):+.4%} per year")
    print()
    print("  The euro yields less than the dollar, so it trades at a forward")
    print("  premium and the carry to being long it is negative. Forward points")
    print("  and carry have opposite signs, and each says which it is.")

    higher_yielding = ForwardTerms(
        value_date=date(2026, 7, 2),
        spot_date=date(2026, 1, 6),
        base_rate=0.0650,
        quote_rate=0.0425,
        basis=DayCount.ACT_360,
    )
    flipped = covered_forward_rate(spot, higher_yielding)
    print(
        f"  with EUR at 6.50%:  forward {flipped.rate:.6f} (a discount), "
        f"carry {carry_rate(higher_yielding):+.4%}"
    )

    other_basis = ForwardTerms(
        value_date=date(2026, 7, 2),
        spot_date=date(2026, 1, 6),
        base_rate=0.0250,
        quote_rate=0.0425,
        basis=DayCount.ACT_365_FIXED,
    )
    print(
        f"  the same rates on ACT/365F: forward {covered_forward_rate(spot, other_basis).rate:.6f}"
    )
    print("  -- which is why the basis is a required field and not a detail.")

    # ----------------------------------------------------------------- #
    rule("Both directions of time")

    dated = FxRates.of(
        [FxRate("EUR", "USD", Decimal("1.0850"), 100.0, "declared")], max_age_seconds=60.0
    )
    for label, instant in (("too early", 50.0), ("in window", 130.0), ("too late", 500.0)):
        try:
            converted = dated.convert(Decimal("100"), "EUR", "USD", as_of=instant)
            print(f"  read at {instant:>6.0f} ({label:<9}) -> {converted.converted}")
        except FutureDatedRateError:
            print(f"  read at {instant:>6.0f} ({label:<9}) -> FutureDatedRateError (look-ahead)")
        except StaleRateError:
            print(f"  read at {instant:>6.0f} ({label:<9}) -> StaleRateError")
    print()
    print("  `max_age_seconds` has bounded how *old* a rate may be since v2.16.")
    print("  v3.4 closes the other side: a stale rate is visibly old, and a")
    print("  future-dated one is a look-ahead that produces a confident currency")
    print("  return the position could not have earned.")

    # ----------------------------------------------------------------- #
    rule("Currency exposure, and a stated hedge ratio")

    book = {
        "AAPL": Position(
            "AAPL", Decimal("100"), Decimal("200"), Decimal("205"), Decimal("0"), "USD", OPEN
        ),
        "SAP": Position(
            "SAP", Decimal("500"), Decimal("140"), Decimal("146"), Decimal("0"), "EUR", OPEN
        ),
        "7203": Position(
            "7203", Decimal("-200"), Decimal("2800"), Decimal("2750"), Decimal("0"), "JPY", OPEN
        ),
    }
    exposures = currency_exposures(book)
    for currency in exposures.currencies:
        amount = exposures.of(currency)
        print(f"  {currency}  {amount:>14}")
    print()
    print("  One figure per currency, never a total: summing them would be the")
    print("  figure ADR-0020 removed.")

    print()
    for ratio in ("0", "0.5", "1", "1.2"):
        hedged = hedge_notional(exposures.of("EUR"), Decimal(ratio))
        print(f"  hedge ratio {ratio:>4} -> sell {hedged} EUR forward")
    try:
        hedge_notional(exposures.of("EUR"), Decimal("-1"))
        print("  NOT REFUSED")
    except PortfolioError as error:
        print(f"  hedge ratio   -1 -> {str(error)[:56]}")

    # ----------------------------------------------------------------- #
    rule("Currency attribution: the assets, and the currency")

    opening_rates = FxRates.of(
        [
            FxRate("EUR", "USD", Decimal("1.0850"), OPEN, "open"),
            FxRate("JPY", "USD", Decimal("0.006614"), OPEN, "open"),
        ]
    )
    closing_rates = FxRates.of(
        [
            FxRate("EUR", "USD", Decimal("1.1100"), CLOSE, "close"),
            FxRate("JPY", "USD", Decimal("0.006450"), CLOSE, "close"),
        ]
    )
    report = currency_attribution(
        opening={"USD": Decimal("20000"), "EUR": Decimal("70000"), "JPY": Decimal("-560000")},
        closing={"USD": Decimal("20500"), "EUR": Decimal("73000"), "JPY": Decimal("-550000")},
        opening_rates=opening_rates,
        closing_rates=closing_rates,
        reporting_currency="USD",
        opening_timestamp=OPEN,
        closing_timestamp=CLOSE,
    )

    print(f"  reporting currency: {report.reporting_currency}")
    print(
        f"  {'ccy':<5} {'open(local)':>13} {'close(local)':>13} "
        f"{'local':>12} {'currency':>12} {'total':>12}"
    )
    print("  " + "-" * 72)
    for entry in report.by_currency:
        print(
            f"  {entry.currency:<5} {entry.opening_local:>13} {entry.closing_local:>13} "
            f"{entry.local_return:>12} {entry.currency_return:>12} {entry.total:>12}"
        )
    print("  " + "-" * 72)
    print(
        f"  {'':<5} {'':>13} {'':>13} {report.local_return:>12} "
        f"{report.currency_return:>12} {report.total:>12}"
    )
    print()
    print(f"  rounding carried: {report.rounding}")
    print()
    print("  The decomposition is an identity, not an approximation:")
    print("    (V1 - V0) * r0  +  V1 * (r1 - r0)  ==  V1 * r1 - V0 * r0")
    print("  There is no residual and no unexplained remainder.")
    print()
    print("  Read the euro row: 3,255 of the 5,080 came from the assets and 1,825")
    print("  from the euro strengthening. A single figure would have reported one")
    print("  number and attributed all of it to the manager.")
    print()
    print("  The yen row is a short position in a currency that weakened, so both")
    print("  components are gains -- the leg shrank and what remains is worth less")
    print("  to owe. The USD row has no currency component at all, because the")
    print("  reporting currency cannot move against itself.")

    print("\n" + "=" * 68)
    print("Example 37 complete.")
    print("(Every rate above is declared in this file. AlphaLab ships none.)")


if __name__ == "__main__":
    main()
