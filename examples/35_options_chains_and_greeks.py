"""
AlphaLab Examples
=================

Example 35 : Options Chains, Greeks and Expiry

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 31 (global market conventions)

Topics
------

• A chain read by expiry, by type and by strike
• Greeks, and the four things the model does not do
• Multipliers that are not 100, and payoffs that scale with them
• Multi-leg strategies: net premium, net Greeks, legs that keep their direction
• Expiry: exercised, assigned, abandoned or worthless -- and the cash and
  underlying units each one moves

What this shows
---------------

A delta is not a property of a contract. It is the output of a model evaluated
under assumptions, and `ModelAssumptions` carries those assumptions so a figure
cannot travel without them.

At expiry, "what is this worth" and "what actually happened" are different
questions. The second one is where the sign errors live, and it is answered here
as two separate signed quantities: cash, and units of the underlying.

Run

    python examples/35_options_chains_and_greeks.py
"""

import textwrap
from datetime import UTC, datetime
from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.options import (
    BLACK_SCHOLES_MERTON,
    ExerciseStyle,
    ExpirationOutcome,
    ExpirationPolicy,
    OptionChain,
    OptionContract,
    OptionLeg,
    OptionStrategy,
    OptionType,
    SettlementStyle,
    black_scholes_greeks,
    black_scholes_price,
    by_expiry,
    calls,
    compute_payoff_at_expiry,
    expiries,
    moneyness,
    net_greeks,
    net_premium,
    occ_symbol,
    puts,
    resolve_expiration,
    resolve_strategy_expiration,
    strikes_for_expiry,
)

YEAR = 365.25 * 86400
VALUATION = datetime(2026, 1, 2, tzinfo=UTC).timestamp()
SPOT = Decimal("150")
RATE = 0.04
VOL = 0.25


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def option(
    strike: str,
    option_type: OptionType = OptionType.CALL,
    expiry: float = VALUATION + YEAR,
    multiplier: int = 100,
) -> OptionContract:
    return OptionContract(
        underlying_asset_id="AAPL",
        strike=Decimal(strike),
        expiry=expiry,
        option_type=option_type,
        style=ExerciseStyle.EUROPEAN,
        multiplier=multiplier,
    )


def main() -> None:
    print("=" * 68)
    print("Example 35 : Options Chains, Greeks and Expiry")
    print("=" * 68)

    chain = OptionChain(
        underlying_asset_id="AAPL",
        timestamp=VALUATION,
        contracts=tuple(
            option(strike, kind, expiry)
            for expiry in (VALUATION + YEAR / 2, VALUATION + YEAR)
            for strike in ("140", "150", "160")
            for kind in (OptionType.CALL, OptionType.PUT)
        ),
    )

    # ----------------------------------------------------------------- #
    rule("The chain")

    print(f"  {len(chain.contracts)} contracts on {chain.underlying_asset_id}")
    print(f"  calls: {len(calls(chain))}   puts: {len(puts(chain))}")
    for expiry in expiries(chain):
        years = (expiry - VALUATION) / YEAR
        print(
            f"  expiry {years:.2f}y : {len(by_expiry(chain, expiry))} contracts, "
            f"strikes {strikes_for_expiry(chain, expiry)}"
        )

    # ----------------------------------------------------------------- #
    rule("Greeks, and what the model assumes")

    print(
        f"  {'contract':<14} {'price':>9} {'delta':>9} {'gamma':>9} "
        f"{'theta/day':>10} {'vega':>9} {'rho':>9}"
    )
    print("  " + "-" * 74)
    for strike in ("140", "150", "160"):
        for kind in (OptionType.CALL, OptionType.PUT):
            contract = option(strike, kind)
            price = black_scholes_price(contract, SPOT, VOL, RATE, VALUATION)
            sensitivities = black_scholes_greeks(contract, SPOT, VOL, RATE, VALUATION)
            label = f"{strike} {kind.name.lower()}"
            print(
                f"  {label:<14} {price:>9} {sensitivities.delta:>9.4f} "
                f"{sensitivities.gamma:>9.5f} {sensitivities.theta:>10.4f} "
                f"{sensitivities.vega:>9.4f} {sensitivities.rho:>9.4f}"
            )

    print()
    print(f"  model      : {BLACK_SCHOLES_MERTON.model.name}")
    print(f"  identity   : {BLACK_SCHOLES_MERTON.identity}")
    print(f"  year basis : {BLACK_SCHOLES_MERTON.year_basis_days} days")
    for label, models in (
        ("early exercise", BLACK_SCHOLES_MERTON.prices_early_exercise),
        ("dividends", BLACK_SCHOLES_MERTON.models_dividends),
        ("a smile", BLACK_SCHOLES_MERTON.models_volatility_smile),
    ):
        print(f"  {label:<15}: {'modelled' if models else 'NOT modelled'}")
    print()
    print(
        textwrap.fill(
            BLACK_SCHOLES_MERTON.note, width=66, initial_indent="  ", subsequent_indent="  "
        )
    )

    # ----------------------------------------------------------------- #
    rule("A multiplier of 100 is a US equity convention, not a universal")

    for multiplier, label in ((100, "US equity"), (50, "Nifty index"), (10, "Eurostoxx")):
        contract = option("150", multiplier=multiplier)
        per_unit = black_scholes_price(contract, SPOT, VOL, RATE, VALUATION)
        strategy = OptionStrategy((OptionLeg(contract, Side.BUY, 1),))
        payoff = compute_payoff_at_expiry(strategy, Decimal("170"))
        print(
            f"  multiplier {multiplier:>4} ({label:<12}) premium/unit {per_unit}"
            f" -> per contract {per_unit * multiplier:>10}, payoff at 170 = {payoff:>7}"
        )
    print()
    print("  The multiplier is required on every contract as of v3.4. It scales")
    print("  every payoff, premium and Greek, so a wrong one is wrong by that")
    print("  factor with nothing refusing it.")

    # ----------------------------------------------------------------- #
    rule("A multi-leg strategy: a bull call spread")

    long_leg = OptionLeg(option("150"), Side.BUY, 2)
    short_leg = OptionLeg(option("160"), Side.SELL, 2)
    spread = OptionStrategy((long_leg, short_leg))

    prices = {
        occ_symbol(leg.contract): black_scholes_price(leg.contract, SPOT, VOL, RATE, VALUATION)
        for leg in spread.legs
    }
    by_leg = {
        occ_symbol(leg.contract): black_scholes_greeks(leg.contract, SPOT, VOL, RATE, VALUATION)
        for leg in spread.legs
    }

    for leg in spread.legs:
        symbol = occ_symbol(leg.contract)
        print(
            f"  {leg.side.name:<4} {leg.quantity} x strike {leg.contract.strike} "
            f"at {prices[symbol]} per unit"
        )
    premium = net_premium(spread, prices)
    combined = net_greeks(spread, by_leg)
    print(f"\n  net premium : {premium} (negative = paid out, a debit spread)")
    print(
        f"  net Greeks  : delta {combined.delta:.4f}  gamma {combined.gamma:.5f}  "
        f"vega {combined.vega:.4f}"
    )
    print("  Each leg is scaled by its own multiplier and its own signed quantity,")
    print("  so a short leg subtracts rather than being netted away first.")

    # ----------------------------------------------------------------- #
    rule("Payoff is capped above the short strike")

    for spot in ("140", "150", "155", "160", "200"):
        payoff = compute_payoff_at_expiry(spread, Decimal(spot))
        print(f"  spot {spot:>4}  gross payoff {payoff:>9}")

    # ----------------------------------------------------------------- #
    rule("Expiry: four outcomes, two signed quantities")

    cash = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    physical = ExpirationPolicy(SettlementStyle.PHYSICAL, exercise_in_the_money=True)
    abandoned = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=False)

    scenarios = (
        ("long call, ITM, cash", option("150"), Decimal("1"), Decimal("160"), cash),
        ("short call, ITM, cash", option("150"), Decimal("-1"), Decimal("160"), cash),
        ("long call, ITM, physical", option("150"), Decimal("1"), Decimal("160"), physical),
        ("short call, ITM, physical", option("150"), Decimal("-1"), Decimal("160"), physical),
        (
            "long put, ITM, physical",
            option("150", OptionType.PUT),
            Decimal("1"),
            Decimal("140"),
            physical,
        ),
        ("long call, OTM", option("150"), Decimal("1"), Decimal("140"), cash),
        ("long call, exactly ATM", option("150"), Decimal("1"), Decimal("150"), cash),
        ("long call, ITM, not exercised", option("150"), Decimal("1"), Decimal("160"), abandoned),
    )
    print(f"  {'scenario':<32} {'moneyness':<17} {'outcome':<19} {'cash':>10} {'units':>8}")
    print("  " + "-" * 90)
    for label, contract, quantity, settle, policy in scenarios:
        result = resolve_expiration(contract, quantity, settle, policy)
        print(
            f"  {label:<32} {result.moneyness.name:<17} {result.outcome.name:<19} "
            f"{result.cash_flow:>10} {result.underlying_units:>8}"
        )

    print()
    print("  Cash and underlying units are separate fields because they are")
    print("  separate dimensions. A long and a short of the same contract net to")
    print("  zero on both -- an exercise moves value between two parties.")

    long_side = resolve_expiration(option("150"), Decimal("3"), Decimal("160"), physical)
    short_side = resolve_expiration(option("150"), Decimal("-3"), Decimal("160"), physical)
    print(f"\n  long  3 : cash {long_side.cash_flow:>9}  units {long_side.underlying_units:>7}")
    print(f"  short 3 : cash {short_side.cash_flow:>9}  units {short_side.underlying_units:>7}")
    print(
        f"  net     : cash {long_side.cash_flow + short_side.cash_flow:>9}  "
        f"units {long_side.underlying_units + short_side.underlying_units:>7}"
    )

    # ----------------------------------------------------------------- #
    rule("A spread at expiry is two legs, never netted")

    results = resolve_strategy_expiration(spread, Decimal("155"), cash)
    for result in results:
        print(
            f"  {result.contract_symbol:<26} {result.contracts:>4} contracts  "
            f"{result.outcome.name:<19} cash {result.cash_flow}"
        )
    total = sum(result.cash_flow for result in results)
    print(f"\n  total {total}")
    print("  A spread assigned on one side and exercised on the other is a real")
    print("  event with two legs to book, and a netted zero would hide it.")

    assert results[0].outcome is ExpirationOutcome.EXERCISED
    assert moneyness(option("150"), Decimal("155")).name == "IN_THE_MONEY"

    print("\n" + "=" * 68)
    print("Example 35 complete.")


if __name__ == "__main__":
    main()
