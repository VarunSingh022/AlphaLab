"""
AlphaLab Examples
=================

Example 38 : Crypto Perpetuals, Funding and 24/7 Coverage

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 31 (global market conventions)

Topics
------

• Venue differences declared, not assumed: funding interval, fees, price source
• Funding instants follow a venue's own anchor, not a global one
• Accrual against the venue's mark, refused when a mark is missing
• Maker rebates and taker fees, in the same sign convention as funding
• A 24/7 clock is not 24/7 data: coverage, and the gaps it reports

What this shows
---------------

The same pair on two exchanges is genuinely two instruments. This example runs
two venues side by side and they disagree about almost everything: how often
funding is charged, what a trade costs, which price positions mark against, and
how small an order is accepted.

The hardest part of a continuous venue is that a missing observation is
invisible -- every instant is a trading instant, so an outage, a delisting and a
quiet period all look like a gap between two timestamps. This reports the gaps
and creates nothing.

**AlphaLab holds no exchange adapter, no API client and no credential.** Every
figure below is metadata declared by this example.

Run

    python examples/38_crypto_perpetuals_and_funding.py
"""

from decimal import Decimal

from alphalab.crypto import (
    CryptoInputError,
    CryptoInstrument,
    FeeSchedule,
    FundingRate,
    FundingRateHistory,
    InstrumentType,
    LiquidityRole,
    PriceSource,
    VenueSpecification,
    accrued_funding,
    annualized_funding_rate,
    compute_liquidation_price,
    coverage,
    cross_venue_dispersion,
    crypto_symbol,
    funding_instants,
    observation_gaps,
    trading_fee,
)
from alphalab.data.calendar import MarketCalendar
from alphalab.portfolio.types import PositionSide

HOUR = 3600.0
DAY = 24 * HOUR

VENUE_A = VenueSpecification(
    venue="venue-a",
    funding_interval_hours=8,
    fees=FeeSchedule(maker_bps=Decimal("-1"), taker_bps=Decimal("5")),
    price_source=PriceSource.INDEX,
    minimum_notional=Decimal("5"),
    settlement_asset="USDT",
)
VENUE_B = VenueSpecification(
    venue="venue-b",
    funding_interval_hours=1,
    fees=FeeSchedule(maker_bps=Decimal("2"), taker_bps=Decimal("7")),
    price_source=PriceSource.MARK,
    minimum_notional=Decimal("10"),
    settlement_asset="USDC",
)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> None:
    print("=" * 68)
    print("Example 38 : Crypto Perpetuals, Funding and 24/7 Coverage")
    print("=" * 68)

    # ----------------------------------------------------------------- #
    rule("Two venues, declared")

    print(
        f"  {'':<9} {'funding':>9} {'maker':>8} {'taker':>8} {'marks against':>15} "
        f"{'min':>7} {'settles':>9}"
    )
    print("  " + "-" * 70)
    for venue in (VENUE_A, VENUE_B):
        print(
            f"  {venue.venue:<9} {venue.funding_interval_hours:>7}h "
            f"{venue.fees.maker_bps:>7}bp {venue.fees.taker_bps:>6}bp "
            f"{venue.price_source.name:>15} {venue.minimum_notional:>7} "
            f"{venue.settlement_asset:>9}"
        )
    print()
    print(
        f"  intervals per year: venue-a {VENUE_A.funding_intervals_per_year}, "
        f"venue-b {VENUE_B.funding_intervals_per_year}"
    )
    print("  Eight hours is the most common convention. It is a convention, not")
    print("  a rule, and annualizing one venue's rate on the other's interval is")
    print("  wrong by that ratio.")

    # ----------------------------------------------------------------- #
    rule("A 24/7 calendar is a real calendar with a real timezone")

    continuous = MarketCalendar.continuous(VENUE_A.venue, "UTC")
    print(
        f"  {continuous.calendar_id}: continuous={continuous.is_continuous}, "
        f"zone={continuous.timezone_name}"
    )
    sunday_night = 1_773_360_000.0
    print(f"  open at a Sunday 04:00 UTC: {continuous.is_open(sunday_night)}")
    print(f"  the local day it belongs to: {continuous.trading_day_of(sunday_night)}")
    print()
    print("  The zone still decides which local day an instant falls in, which")
    print("  matters for daily bars -- so it is required, not assumed to be UTC.")

    # ----------------------------------------------------------------- #
    rule("Funding instants follow the venue's own anchor")

    midnight_anchor = funding_instants(0.0, DAY, VENUE_A.funding_interval_hours, anchor=0.0)
    offset_anchor = funding_instants(0.0, DAY, VENUE_A.funding_interval_hours, anchor=HOUR)
    print(f"  8h interval, anchored 00:00 : {[int(t / HOUR) for t in midnight_anchor]} (hours)")
    print(f"  8h interval, anchored 01:00 : {[int(t / HOUR) for t in offset_anchor]} (hours)")
    hourly = funding_instants(0.0, 6 * HOUR, VENUE_B.funding_interval_hours, anchor=0.0)
    print(f"  1h interval, six hours      : {[int(t / HOUR) for t in hourly]} (hours)")
    print()
    print("  The anchor is required. Assuming one would charge funding at times")
    print("  the venue did not.")

    # ----------------------------------------------------------------- #
    rule("Accrual against the venue's mark")

    instrument = CryptoInstrument(
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=InstrumentType.PERPETUAL,
        exchange=VENUE_A.venue,
        contract_size=Decimal("1"),
    )
    symbol = crypto_symbol(instrument)
    rates = ("0.0001", "0.00025", "-0.00005")
    marks = ("60000", "61500", "59800")
    history = FundingRateHistory(
        instrument_symbol=symbol,
        rates=tuple(
            FundingRate(symbol, Decimal(rate), instant, VENUE_A.funding_interval_hours)
            for rate, instant in zip(rates, midnight_anchor, strict=True)
        ),
    )
    mark_by_instant = dict(zip(midnight_anchor, (Decimal(m) for m in marks), strict=True))

    summary = accrued_funding(history, Decimal("1.5"), mark_by_instant, instrument.contract_size)
    print(f"  instrument: {summary.instrument_symbol}")
    print(f"  {'hour':>5} {'rate':>11} {'mark':>9} {'notional':>11} {'payment':>11}")
    print("  " + "-" * 52)
    for accrual in summary.accruals:
        print(
            f"  {int(accrual.timestamp / HOUR):>5} {accrual.rate:>11} "
            f"{accrual.mark_price:>9} {accrual.notional:>11} {accrual.payment:>11}"
        )
    print("  " + "-" * 52)
    print(f"  {'total':>39} {summary.total:>11} {instrument.quote_asset}")
    print()
    print("  Positive funding: a long pays. Negative: a long receives. The third")
    print("  interval is negative, and the payment flips sign with it.")
    print(f"  annualized (uniform 8h): {annualized_funding_rate(history):.6f}")

    try:
        accrued_funding(
            history,
            Decimal("1.5"),
            {midnight_anchor[0]: Decimal("60000")},
            instrument.contract_size,
        )
        print("  NOT REFUSED")
    except CryptoInputError as error:
        print(f"\n  a missing mark: {str(error).split('. ')[0][:60]}")

    # ----------------------------------------------------------------- #
    rule("Fees are the same sign convention as funding")

    notional = Decimal("90000")
    for venue in (VENUE_A, VENUE_B):
        for role in LiquidityRole:
            fee = trading_fee(venue, notional, role)
            direction = "received" if fee > 0 else "paid"
            print(
                f"  {venue.venue:<9} {role.name:<6} on {notional} -> "
                f"{fee:>9} {venue.settlement_asset} ({direction})"
            )
    print()
    print("  Both are cash flows to the holder, so a fee and a funding payment")
    print("  add without either being negated first. venue-a rebates the maker;")
    print("  venue-b does not, and a strategy assuming one would be wrong in sign.")

    # ----------------------------------------------------------------- #
    rule("The same pair, two venues, two prices")

    dispersion = cross_venue_dispersion(
        {
            "venue-a": Decimal("60120.5"),
            "venue-b": Decimal("60098.0"),
            "venue-c": Decimal("60135.0"),
        }
    )
    print(f"  cheapest : {dispersion.lowest_venue} at {dispersion.lowest}")
    print(f"  dearest  : {dispersion.highest_venue} at {dispersion.highest}")
    print(f"  spread   : {dispersion.spread} (a price)")
    print(f"  relative : {dispersion.relative_spread:.8f} (a fraction)")
    print()
    print("  Neither venue is the reference. A price and a fraction are separate")
    print("  fields so they cannot be compared to each other.")

    try:
        cross_venue_dispersion({"venue-a": Decimal("60120.5")})
        print("  NOT REFUSED")
    except CryptoInputError as error:
        print(f"  one venue alone: {str(error).split('.')[0][:60]}")

    # ----------------------------------------------------------------- #
    rule("Liquidation, and what the formula does not include")

    for leverage in ("5", "10", "25"):
        for side in (PositionSide.LONG, PositionSide.SHORT):
            price = compute_liquidation_price(
                Decimal("60000"), side, Decimal(leverage), Decimal("0.005")
            )
            print(f"  {side.name:<5} at {leverage:>2}x -> {price:>12}")
    print()
    print("  Isolated margin, ignoring fees and funding accrued since entry. A")
    print("  simplification, not a substitute for a venue's own engine -- and the")
    print("  mark it is compared against must be the venue's, not the last trade.")

    # ----------------------------------------------------------------- #
    rule("A 24/7 clock is not 24/7 data")

    minute = 60.0
    window_end = 6 * HOUR
    complete = [index * minute for index in range(int(window_end / minute))]
    outage = [stamp for stamp in complete if not 2 * HOUR <= stamp < 2.5 * HOUR]
    outage = [stamp for stamp in outage if stamp != 5 * HOUR]

    for label, stamps in (("complete", complete), ("with an outage", outage)):
        report = coverage(stamps, 0.0, window_end, minute)
        ratio = "n/a" if report.coverage is None else f"{report.coverage:.4f}"
        print(
            f"  {label:<15} expected {report.expected:>4}  observed {report.observed:>4}  "
            f"missing {report.missing:>3}  coverage {ratio}"
        )

    print()
    print("  gaps found:")
    for gap in observation_gaps(outage, minute):
        print(
            f"    after {int(gap.after / minute):>4}m, before {int(gap.before / minute):>4}m"
            f"  -> {gap.seconds:>7.0f}s, {gap.missing} expected observation(s) absent"
        )
    print()
    print("  A gap is reported as a *count* of absences, never as timestamps --")
    print("  naming them would come within one step of producing rows for them.")
    print("  There is no fill, no forward-carry and no interpolation here.")

    empty = coverage([], 0.0, 30.0, minute)
    print(f"\n  a window implying no observations: coverage={empty.coverage}")
    print("  None, not 1.0 and not 0.0 -- an unmeasurable statistic is None.")

    print("\n" + "=" * 68)
    print("Example 38 complete.")
    print("(Every venue figure above is declared in this file. AlphaLab holds no")
    print(" exchange adapter, no API client and no credential.)")


if __name__ == "__main__":
    main()
