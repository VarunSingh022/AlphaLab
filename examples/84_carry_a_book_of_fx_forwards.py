"""
AlphaLab Examples
=================

Example 84 : Carry a Book of FX Forwards

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 11 (a unified backtest)

Topics
------

• FX deals declared on their records: spot and one-month forwards, each value
  date counted by the pair's own rule over the calendars the run declares
• The account holds currencies, not pairs: every deal books two currencies,
  each pending until its value date
• A rollover as a swap: the near leg sells at spot for the forward's own value
  date, the far leg buys a month on, as one package
• Cash in each currency reconciled by hand, and the book valued in dollars at
  supplied rates

What this shows
---------------

Plan scenario S-05 (v4.3, ADR-0067). A dollar account holds a million
Australian and a million New Zealand dollars a month forward, and rolls each
forward on the day the pair's spot reaches its value date, through 2025. The
book is ``_fx_carry_book``'s, which states every name and number it turns on.

* A deal is booked as the currencies it exchanges, so the account holds
  Australian and New Zealand dollars, not units of a pair, and no position.
* Each value date follows both calendars: a holiday in either moves it.
* Each roll's near leg settles with the forward it closes, so the currency
  arrives and leaves on one day and the dollars the roll gains arrive with it.
* What the book earned is the forwards' discount, the carry, and the spot
  rates' moves, and the two add up to the dollars it gained.

The prices are **synthetic**: random-walk spot rates, and forwards carried a
month at stated interest rates. Nothing here says whether carry pays.
``tests/integration/test_fx_carry_end_to_end.py`` holds it.

Run

    python examples/84_carry_a_book_of_fx_forwards.py
"""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

from _fx_carry_book import (
    COMMISSION,
    INSTANTS,
    NOTIONAL,
    PAIRS,
    SPOTS,
    START_CASH,
    closing_rates,
    run,
    tally,
    unadjusted,
)

from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.settlement import VALUE_DATE
from alphalab.portfolio.types import TransactionType


def main() -> None:
    print("Example 84: a book of FX forwards, rolled as swaps through 2025\n")
    result, strategy = run()
    portfolio = result.state.portfolio
    legs = [t for t in portfolio.ledger.transactions if t.type is TransactionType.FX_LEG]
    print(f"{len(result.fills)} deals, {len(legs)} currency legs, and no position held\n")

    for pair in PAIRS:
        rolls = [roll for roll in strategy.rolls if roll.base == pair.base]
        print(f"{pair.base}/USD, {NOTIONAL:,} {pair.base} a month forward:")
        print(f"  {len(rolls)} rolls, each struck when spot reached the forward's value date")
        assert all(earlier.far == later.near for earlier, later in pairwise(rolls))
        moved = [
            (roll.struck, roll.far, unadjusted(roll.struck, pair))
            for roll in rolls
            if roll.far != unadjusted(roll.struck, pair)
        ]
        for struck, far, plain in moved:
            print(f"  struck {struck}: valued {far}, not {plain}, a holiday moving it")
        # What each forward earned: its discount to spot when dealt, and spot's move.
        fills = [
            f for f in result.fills if f.asset_id in (pair.spot.asset_id, pair.forward.asset_id)
        ]
        bought = [f for f in fills if f.asset_id == pair.forward.asset_id]
        sold = [f for f in fills if f.asset_id == pair.spot.asset_id]
        entry_spots = [SPOTS[pair.base][INSTANTS.index(f.filled_at)] for f in bought]
        exits = [f.price for f in sold] + [SPOTS[pair.base][-1]]
        carry = sum(
            ((spot - f.price) * NOTIONAL for spot, f in zip(entry_spots, bought, strict=True)),
            Decimal("0"),
        )
        moves = sum(
            ((exit_ - spot) * NOTIONAL for spot, exit_ in zip(entry_spots, exits, strict=True)),
            Decimal("0"),
        )
        print(f"  the forwards' discount earned {carry:,.2f} USD, spot's moves {moves:+,.2f} USD\n")

    by_hand = tally(result)
    print("cash in each currency, trade-date:")
    for currency, amount in by_hand.items():
        assert portfolio.cash.balance(currency) == amount
        print(f"  {currency} {amount:>16,.2f}   equal to the tally of the fills")
    print("\nat the year's end, each last forward is pending until its value date:")
    for pair in PAIRS:
        position = PortfolioEngine.cash_position(portfolio, pair.base, INSTANTS[-1])
        held = strategy.held[pair.base].isoformat()
        assert any(leg.metadata[VALUE_DATE] == held for leg in legs)
        print(f"  {pair.base} {position.unsettled_credits:,.2f} not yet received, due {held}")

    valuation = result.valuation_in("USD", closing_rates())
    paid = COMMISSION * len(result.fills)
    print(f"\nvalued at the year's last spot rates: {valuation.equity:,.2f} USD")
    print(f"from {START_CASH:,.2f}, after {paid:,.2f} of commission")
    print("Nothing here says whether carry pays: the prices are synthetic.")


if __name__ == "__main__":
    main()
