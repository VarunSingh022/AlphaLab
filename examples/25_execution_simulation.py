"""
AlphaLab Examples
=================

Example 25 : Execution Simulation

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 11 (unified backtest)

Topics
------

• The six cost roles, and the two ways a cost settles
• Why spread, slippage and impact move the price, and commission does not
• The ordering, and what changes if you get it wrong
• A quoted spread that refuses when the feed quoted nothing
• A tax that falls on one side only
• The itemization recomputed exactly from the configuration

What this shows
---------------

Before v3.3 a simulated fill carried two cost numbers and everything else an
institution pays was folded into one of them. `ExecutionCostModel` names six
roles and keeps them apart, and the two totals it produces are exactly the two
figures the execution report has always carried -- so the breakdown is honest
*and* the accounting identity is untouched.

Run

    python examples/25_execution_simulation.py
"""

from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.execution import (
    FREE,
    CostContext,
    CostSettlement,
    ExecutionCostModel,
    ExecutionSimulator,
    FillStatus,
    FixedHalfSpread,
    NoImpact,
    NoSlippage,
    NoSpread,
    NoTax,
    OrderInstruction,
    PercentageCommission,
    PercentageSlippage,
    PerTradeFee,
    ProportionalTax,
    QuotedHalfSpread,
    SquareRootImpact,
    itemized,
    reconciles,
)
from alphalab.execution.exceptions import ExecutionValidationError

REFERENCE = Decimal("50.00")
QUANTITY = Decimal("2000")
BID, ASK = Decimal("49.95"), Decimal("50.05")
SHOWN = Decimal("20000")


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def instruction(side: Side) -> OrderInstruction:
    return OrderInstruction(
        order_id="ORD-25",
        strategy_id="MOMENTUM",
        asset_id="AAPL",
        quantity=QUANTITY,
        price=REFERENCE,
        side=side,
        venue="SIM",
        currency="USD",
    )


def context(side: Side) -> CostContext:
    return CostContext(
        asset_id="AAPL",
        side=side,
        quantity=QUANTITY,
        reference_price=REFERENCE,
        currency="USD",
        venue="SIM",
        timestamp=1.0,
        bid=BID,
        ask=ASK,
        available_liquidity=SHOWN,
    )


#: Every role named, which is the point: nothing here is assumed.
MODEL = ExecutionCostModel(
    spread_model=QuotedHalfSpread(),
    slippage_model=PercentageSlippage(Decimal("0.0005")),
    impact_model=SquareRootImpact(Decimal("0.05")),
    commission_model=PercentageCommission(Decimal("0.0002")),
    fee_model=PerTradeFee(Decimal("1.25")),
    tax_model=ProportionalTax(Decimal("0.005"), frozenset({Side.BUY})),
)


def main() -> None:
    print("AlphaLab — Execution Simulation (v3.3)")
    print("=" * 52)
    print(f"Buying {QUANTITY} AAPL against a {BID}/{ASK} quote showing {SHOWN} units.")

    # ----------------------------------------------------------------- #
    rule("1. Six roles, itemized")

    costs = MODEL.quote(context(Side.BUY))
    for role, amount in itemized(costs, QUANTITY).items():
        settlement = (
            CostSettlement.PRICE_EMBEDDED
            if role in ("spread", "slippage", "impact")
            else CostSettlement.CASH_CHARGED
        )
        print(f"  {role:<11} {amount:>10.2f} USD   {settlement.name}")
    print(f"  {'-' * 40}")
    print(f"  {'all-in':<11} {costs.total(QUANTITY):>10.2f} USD")

    # ----------------------------------------------------------------- #
    rule("2. The two settlements never overlap")

    fill_price = MODEL.fill_price(context(Side.BUY), costs)
    print(f"  reference price         {REFERENCE}")
    print(f"  per-unit concession   + {costs.price_concession}   (spread + slippage + impact)")
    print(f"  fill price              {fill_price}")
    print(f"  cash charged            {costs.cash_charged} USD   (commission + fees + tax)")
    print()
    print("  The concession is paid *by paying more per share*. It is never also")
    print("  debited from cash -- that would be the same cost counted twice.")

    # ----------------------------------------------------------------- #
    rule("3. The report's two figures are exactly the six items")

    simulator = ExecutionSimulator(cost_model=MODEL)
    report = simulator.simulate_fill(
        instruction(Side.BUY),
        QUANTITY,
        REFERENCE,
        1.0,
        FillStatus.FULL_FILL,
        bid=BID,
        ask=ASK,
        available_liquidity=SHOWN,
    )
    print(f"  report.slippage    {report.slippage}   (the per-unit concession)")
    print(f"  report.commission  {report.commission}   (the one cash channel)")
    print(f"  reconciles         {reconciles(costs, report.slippage, report.commission)}")
    print()
    print("  The breakdown is not stored. It is a pure function of the")
    print("  configuration, so it recomputes exactly, for ever:")
    again = simulator.simulate_costs(
        instruction(Side.BUY), QUANTITY, REFERENCE, 1.0, BID, ASK, SHOWN
    )
    print(f"  recomputed == priced  {again == costs}")

    # ----------------------------------------------------------------- #
    rule("4. A sell pays the concession the other way")

    sell = MODEL.quote(context(Side.SELL))
    sell_price = MODEL.fill_price(context(Side.SELL), sell)
    print(f"  buy  fills at {fill_price}  (pays  {fill_price - REFERENCE} over)")
    print(f"  sell fills at {sell_price}  (gives {REFERENCE - sell_price} away)")
    print(f"  buy tax  {costs.tax} USD")
    print(f"  sell tax {sell.tax} USD   <- the tax was declared BUY-side only")

    # ----------------------------------------------------------------- #
    rule("5. Ordering is a decision, and it changes the number")

    # A fee on consideration is a fee on what was *paid*, not what was quoted.
    on_paid = ExecutionCostModel(
        FixedHalfSpread(Decimal("1.00")),
        NoSlippage(),
        NoImpact(),
        PercentageCommission(Decimal("0")),
        PerTradeFee(Decimal("0")),
        NoTax(),
    ).quote(context(Side.BUY))
    print(f"  half-spread assumed     {on_paid.spread}")
    print(f"  consideration           {(REFERENCE + on_paid.spread) * QUANTITY} USD")
    print(f"  (not                    {REFERENCE * QUANTITY} USD, which is the quote)")
    print()
    print("  The three cash roles are charged on the post-concession")
    print("  consideration. That is stated in alphalab.execution.costs and")
    print("  asserted in tests -- it is not left to the reader to infer.")

    # ----------------------------------------------------------------- #
    rule("6. A model refuses what it was not given")

    no_quote = CostContext("AAPL", Side.BUY, QUANTITY, REFERENCE, "USD", "SIM", 1.0)
    try:
        QuotedHalfSpread().half_spread(no_quote)
    except ExecutionValidationError as error:
        print(f"  QuotedHalfSpread on a feed with no quote:\n    {str(error)[:96]}...")
    try:
        SquareRootImpact(Decimal("0.05")).impact(no_quote)
    except ExecutionValidationError as error:
        print(f"\n  SquareRootImpact with no depth:\n    {str(error)[:96]}...")
    print()
    print("  Neither substitutes a figure. A spread nobody quoted was not")
    print("  observed, and an impact with no denominator is not a small number.")

    # ----------------------------------------------------------------- #
    rule("7. Free is an absence, stated once")

    free = FREE.quote(context(Side.BUY))
    print(f"  FREE.quote(...)  concession {free.price_concession}, cash {free.cash_charged}")
    print(
        f"  the six roles    {[type(m).__name__ for m in (NoSpread(), NoSlippage(), NoImpact())]}"
    )
    print("                   ... plus zero commission, NoFee, NoTax")
    print()
    print("  A frictionless backtest is a fine thing to want. What is not fine")
    print("  is getting one without having said so.")

    print("\n" + "=" * 52)
    print("Example 25 complete.")


if __name__ == "__main__":
    main()
