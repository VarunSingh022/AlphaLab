"""
AlphaLab Examples
=================

Example 27 : Portfolio Attribution

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 11 (unified backtest)

Topics
------

• Nine dimensions, and which three AlphaLab already knows
• Metadata that must be supplied, and what happens when it is not
• Reconciliation: the parts summing to the portfolio result
• Currency attribution, and why it deliberately does not total
• Factor attribution and the residual that makes it honest
• Execution attribution as negative contributions

What this shows
---------------

Attribution is only useful if it reconciles and only honest if it refuses to
invent. `attribute()` reports each dimension with an `Availability` beside it: a
dimension nothing was supplied for comes back **empty**, never as one `UNKNOWN`
bucket holding the whole P&L.

Run

    python examples/27_portfolio_attribution.py
"""

from decimal import Decimal

from alphalab.analytics import (
    AttributionDimension,
    Availability,
    TradeFacts,
    TradeRecord,
    attribute,
)
from alphalab.core.contribution import StrategyContribution

#: Six fills from a two-strategy book. Two strategies asked for the first order,
#: which is why its P&L is split rather than assigned.
TRADES = (
    TradeRecord(
        "T1",
        "AAPL",
        "Technology",
        Decimal("1250.00"),
        Decimal("90000"),
        7200.0,
        (
            StrategyContribution("MOMENTUM", Decimal("70")),
            StrategyContribution("MEANREV", Decimal("30")),
        ),
    ),
    TradeRecord(
        "T2",
        "MSFT",
        "Technology",
        Decimal("-430.00"),
        Decimal("64000"),
        3600.0,
        (StrategyContribution("MOMENTUM", Decimal("100")),),
    ),
    TradeRecord(
        "T3",
        "XOM",
        "Energy",
        Decimal("880.00"),
        Decimal("42000"),
        10800.0,
        (StrategyContribution("MEANREV", Decimal("100")),),
    ),
    TradeRecord(
        "T4",
        "SAP",
        "Technology",
        Decimal("-215.00"),
        Decimal("31000"),
        5400.0,
        (StrategyContribution("MOMENTUM", Decimal("100")),),
    ),
)

#: What the execution path knew, plus what an operator supplied. Country and
#: broker are the operator's: AlphaLab holds neither.
FACTS = {
    "T1": TradeFacts(
        "T1",
        currency="USD",
        venue="XNAS",
        broker="PRIME-A",
        country="US",
        factor_pnl={"momentum": Decimal("900.00"), "value": Decimal("150.00")},
        execution_costs={
            "commission": Decimal("18.00"),
            "spread": Decimal("22.50"),
            "impact": Decimal("41.00"),
        },
    ),
    "T2": TradeFacts(
        "T2",
        currency="USD",
        venue="XNAS",
        broker="PRIME-A",
        country="US",
        factor_pnl={"momentum": Decimal("-380.00")},
        execution_costs={
            "commission": Decimal("12.80"),
            "spread": Decimal("16.00"),
            "impact": Decimal("25.00"),
        },
    ),
    "T3": TradeFacts(
        "T3",
        currency="USD",
        venue="XNYS",
        broker="PRIME-B",
        country="US",
        factor_pnl={"value": Decimal("710.00")},
        execution_costs={
            "commission": Decimal("8.40"),
            "spread": Decimal("10.50"),
            "impact": Decimal("14.00"),
        },
    ),
    "T4": TradeFacts(
        "T4",
        currency="EUR",
        venue="XETR",
        broker="PRIME-B",
        country="DE",
        factor_pnl={"momentum": Decimal("-190.00")},
        execution_costs={
            "commission": Decimal("6.20"),
            "spread": Decimal("7.75"),
            "impact": Decimal("9.00"),
        },
    ),
}


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def show(report, dimension: AttributionDimension) -> None:  # type: ignore[no-untyped-def]
    built = report.dimensions[dimension]
    mark = "reconciles" if built.reconciles else "does not total"
    coverage = f"{built.covered}/{built.total}"
    print(f"  {dimension.name:<10} {built.availability.name:<12} {coverage}  {mark}")
    for label, amount in built.buckets.items():
        print(f"      {label:<14} {amount:>12,.2f}")
    if built.uncovered:
        print(f"      (no metadata for: {', '.join(built.uncovered)})")


def main() -> None:
    print("AlphaLab — Portfolio Attribution (v3.3)")
    print("=" * 62)

    report = attribute(TRADES, FACTS)
    print(f"Portfolio realized P&L: {report.realized_pnl:,.2f}")
    print(f"Settlement currencies seen: {report.currencies}")
    if len(report.currencies) > 1:
        print("  ^ more than one, so that total mixes units. Read CURRENCY below.")

    # ----------------------------------------------------------------- #
    rule("1. The three AlphaLab already knows")

    for dimension in (
        AttributionDimension.STRATEGY,
        AttributionDimension.ASSET,
        AttributionDimension.SECTOR,
    ):
        show(report, dimension)
    print()
    print("  STRATEGY comes from the contribution ledger, so T1's P&L is split")
    print("  70/30 between the two strategies that actually asked for it --")
    print("  not assigned to a fabricated 'ALLOC-NETTED'.")

    # ----------------------------------------------------------------- #
    rule("2. The ones an operator has to supply")

    for dimension in (
        AttributionDimension.COUNTRY,
        AttributionDimension.VENUE,
        AttributionDimension.BROKER,
    ):
        show(report, dimension)
    print()
    print("  VENUE is on every execution report. COUNTRY and BROKER are not:")
    print("  AlphaLab has no security master, and 'venue' is the venue an order")
    print("  executed at, which is not a broker. Both were supplied here.")

    # ----------------------------------------------------------------- #
    rule("3. What happens when they are not supplied")

    bare = attribute(TRADES)
    for dimension in (AttributionDimension.COUNTRY, AttributionDimension.BROKER):
        built = bare.dimensions[dimension]
        print(f"  {dimension.name:<10} {built.availability.name:<12} buckets={built.buckets}")
    print()
    print("  Empty, and labelled NO_METADATA. Not one 'UNKNOWN' bucket holding")
    print(f"  the whole {report.realized_pnl:,.2f} while looking like a breakdown.")

    # ----------------------------------------------------------------- #
    rule("4. Currency is dimensionally coherent, so it does not total")

    show(report, AttributionDimension.CURRENCY)
    print()
    print("  These buckets are in *different currencies*. Adding them needs a")
    print("  rate, and AlphaLab does not invent one (ADR-0020). So the dimension")
    print("  reports AVAILABLE and reconciles=False, which is the true statement.")

    # ----------------------------------------------------------------- #
    rule("5. Factor attribution, with the residual that makes it reconcile")

    show(report, AttributionDimension.FACTOR)
    factor = report.dimensions[AttributionDimension.FACTOR]
    explained = sum(v for k, v in factor.buckets.items() if k != "residual")
    print()
    print(f"  the caller's factor model explained {explained:,.2f}")
    print(f"  what it did not explain             {factor.buckets['residual']:,.2f}")
    print("  Recorded, not discarded. A breakdown that dropped the residual")
    print("  would claim the model accounted for P&L it never touched.")

    # ----------------------------------------------------------------- #
    rule("6. Execution costs, as the negative contributions they are")

    show(report, AttributionDimension.EXECUTION)
    execution = report.dimensions[AttributionDimension.EXECUTION]
    print()
    print(f"  total execution drag {sum(execution.buckets.values()):,.2f}")
    print("  Costs are not a partition of P&L, so this dimension does not")
    print("  reconcile to it -- and says so rather than appearing to.")

    # ----------------------------------------------------------------- #
    rule("7. Reconciliation, checked")

    for dimension, built in report.dimensions.items():
        if built.availability is Availability.AVAILABLE and built.reconciles:
            total = sum(built.buckets.values())
            agrees = total == report.realized_pnl
            print(f"  {dimension.name:<10} sum = {total:>12,.2f}  == portfolio  {agrees}")

    print("\n" + "=" * 62)
    print("Example 27 complete.")


if __name__ == "__main__":
    main()
