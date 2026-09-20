"""
AlphaLab Examples
=================

Example 31 : Global Market Conventions

Difficulty : Intermediate

Estimated Time : 8 minutes

Prerequisites
-------------

✓ Example 14 (multi-currency settlement)

Topics
------

• One instrument's conventions, declared with nothing defaulted
• Tick size is a price; tick value is money; they are separate types
• Lot sizes, and a partial lot refused rather than rounded
• The multiplier applied exactly once
• Quote currency and settlement currency are two fields

What this shows
---------------

The same number means different things in different markets, and none of the
differences is visible in the number. A `MarketConvention` is where they are
declared, and every field is required -- an instrument nobody described cannot
be constructed at all, which is the only version of this that helps.

Run

    python examples/31_global_market_conventions.py
"""

from decimal import Decimal

from alphalab.conventions import (
    ConventionInputError,
    ConventionViolationError,
    LotSpecification,
    MarketConvention,
    RoundingDirection,
    SettlementBasis,
    SettlementRule,
    TickBand,
    TickSchedule,
    contract_notional,
    round_to_tick,
)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> None:
    print("=" * 68)
    print("Example 31 : Global Market Conventions")
    print("=" * 68)

    # ----------------------------------------------------------------- #
    rule("Three markets, three sets of conventions")

    us_equity = MarketConvention(
        venue="XNAS",
        calendar_id="XNAS",
        quote_currency="USD",
        settlement_currency="USD",
        multiplier=Decimal("1"),
        tick=TickSchedule.flat(Decimal("0.01")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 1),
    )
    india_derivative = MarketConvention(
        venue="XNSE",
        calendar_id="XNSE",
        quote_currency="INR",
        settlement_currency="INR",
        multiplier=Decimal("1"),
        tick=TickSchedule.flat(Decimal("0.05")),
        lot=LotSpecification(lot_size=Decimal("50"), minimum_quantity=Decimal("50")),
        settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
    )
    crude_future = MarketConvention(
        venue="XCME",
        calendar_id="XCME",
        quote_currency="USD",
        settlement_currency="USD",
        multiplier=Decimal("1000"),
        tick=TickSchedule.flat(Decimal("0.01")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
    )

    header = f"  {'venue':<8} {'quote':<6} {'mult':>8} {'tick':>8} {'lot':>6}  settlement"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for convention in (us_equity, india_derivative, crude_future):
        print(
            f"  {convention.venue:<8} {convention.quote_currency:<6} "
            f"{convention.multiplier:>8} "
            f"{convention.tick.tick_size_at(Decimal('100')):>8} "
            f"{convention.lot.lot_size:>6}  {convention.settlement.label}"
        )

    # ----------------------------------------------------------------- #
    rule("A tick size is a price. A tick value is money.")

    for convention, label in (
        (us_equity, "one share"),
        (india_derivative, "one unit"),
        (crude_future, "1,000 barrels"),
    ):
        value = convention.tick_value_at(Decimal("100"))
        print(
            f"  {convention.venue:<8} tick {value.tick_size} x multiplier {value.multiplier:>6}"
            f" = {value.amount:>10} {value.currency}   ({label})"
        )
    print()
    print("  The two are separate fields on separate types, so a risk figure")
    print("  cannot add a price increment to an amount of money.")

    # ----------------------------------------------------------------- #
    rule("A tiered grid, which is the normal shape outside the US")

    tiered = TickSchedule(
        (
            TickBand(upper_bound=Decimal("100"), tick_size=Decimal("0.01")),
            TickBand(upper_bound=Decimal("1000"), tick_size=Decimal("0.05")),
            TickBand(upper_bound=None, tick_size=Decimal("0.10")),
        )
    )
    for price in ("50.00", "99.99", "100.00", "750.00", "5000.00"):
        size = tiered.tick_size_at(Decimal(price))
        print(f"  price {price:>9}  ->  tick {size}")

    # ----------------------------------------------------------------- #
    rule("Rounding onto the grid is a direction, never a default")

    off_grid = Decimal("750.234")
    for direction in RoundingDirection:
        print(f"  {direction.name:<8} -> {round_to_tick(off_grid, Decimal('0.05'), direction)}")
    print()
    print("  A buy limit rounded up crosses further into the book; a sell limit")
    print("  rounded up may never fill. Nothing chooses on the caller's behalf.")

    # ----------------------------------------------------------------- #
    rule("A partial lot is refused, not rounded")

    lot = india_derivative.lot
    for quantity in ("100", "175", "25"):
        try:
            lot.require(Decimal(quantity))
            print(f"  {quantity:>5} units  accepted")
        except ConventionViolationError as error:
            print(f"  {quantity:>5} units  refused: {str(error)[:58]}")

    # ----------------------------------------------------------------- #
    rule("The multiplier, applied exactly once")

    notional = contract_notional(crude_future, Decimal("5"), Decimal("75.50"))
    print(f"  {notional.quantity} contracts at {notional.price} per barrel")
    print(f"    x multiplier {notional.multiplier}")
    print(f"    = {notional.amount} {notional.currency}")
    print(f"    controlling {notional.underlying_units} barrels")
    print()
    print("  Notional and underlying units are separate fields: one is money and")
    print("  one is a quantity, and this is the only place in AlphaLab that a")
    print("  contract count is multiplied by a multiplier.")

    # ----------------------------------------------------------------- #
    rule("Quote currency and settlement currency are two fields")

    quanto = MarketConvention(
        venue="XCME",
        calendar_id="XCME",
        quote_currency="JPY",
        settlement_currency="USD",
        multiplier=Decimal("5"),
        tick=TickSchedule.flat(Decimal("5")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
    )
    for convention in (crude_future, quanto):
        needs = "no" if convention.settles_in_quote_currency else "YES -- an FX rate is required"
        print(
            f"  {convention.venue:<6} quoted in {convention.quote_currency}, settles in "
            f"{convention.settlement_currency}  -> conversion needed: {needs}"
        )

    # ----------------------------------------------------------------- #
    rule("Nothing is defaulted")

    try:
        MarketConvention(  # type: ignore[call-arg]
            venue="XNAS",
            calendar_id="XNAS",
            quote_currency="USD",
            settlement_currency="USD",
            multiplier=Decimal("1"),
            tick=TickSchedule.flat(Decimal("0.01")),
            lot=LotSpecification.single_units(),
        )
        print("  NOT REFUSED")
    except TypeError as error:
        print(f"  omitting the settlement rule -> TypeError: {error}")

    try:
        MarketConvention(
            venue="XNAS",
            calendar_id="XNAS",
            quote_currency="  ",
            settlement_currency="USD",
            multiplier=Decimal("1"),
            tick=TickSchedule.flat(Decimal("0.01")),
            lot=LotSpecification.single_units(),
            settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
        )
        print("  NOT REFUSED")
    except ConventionInputError as error:
        print(f"  a blank currency          -> {str(error)[:60]}")

    print()
    print("  AlphaLab ships no venue's tick table, no lot schedule and no holiday")
    print("  list. An exchange revises them; a table baked in here would be wrong")
    print("  within a year while looking authoritative.")

    print("\n" + "=" * 68)
    print("Example 31 complete.")


if __name__ == "__main__":
    main()
