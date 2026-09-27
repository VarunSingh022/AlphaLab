"""
AlphaLab Examples
=================

Example 59 : Cross-Strategy Risk

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 18 (factors across the cross-section)
✓ Example 58 (a multi-strategy portfolio)

Topics
------

• Strategies correlated by their returns -- with the currency, period, sample
  and source of the returns stated -- and returns in two currencies refused
• Strategies compared by what they hold: shared names, overlap in the same
  and in opposite directions, and the similarity of their exposure vectors
• Factor crowding: how aligned and how concentrated the strategies are on each
  factor, from the factor library's own panels
• Common exposures by instrument, by currency and by sector
• Capital concentration, and the pools of capital strategies draw on together

What this shows
---------------

"Are these strategies diversified?" is several questions. Their returns can be
correlated while they hold nothing in common, and they can hold the same names
on opposite sides while their returns look independent. This example asks each
question separately and names the basis of every answer: a correlation of
returns says what the returns were measured in and over what sample; an
exposure similarity says it compares holdings; crowding says which factor model
it read. Every measure is taken within this portfolio -- AlphaLab has no view of
anyone else's positions, so crowding here means the portfolio's own strategies
leaning the same way.

Run

    python examples/59_cross_strategy_risk.py
"""

from decimal import Decimal

from _portfolio_world import (
    AS_OF,
    RATES,
    STRATEGY_RETURNS_SOURCE,
    banner,
    book,
    factor_loadings,
    refusal,
    section,
    sectors,
    strategy_returns,
)

from alphalab.analytics import (
    AnalyticsValidationError,
    CommonDimension,
    StrategyReturns,
    capital_concentration,
    capital_overlap,
    common_exposures,
    factor_crowding,
    strategy_overlap,
    strategy_return_correlation,
)
from alphalab.portfolio import value_book


def main() -> None:
    banner(59, "Cross-Strategy Risk")
    valuation = value_book(book(), reporting_currency="USD", rates=RATES, as_of=AS_OF)
    lines = valuation.lines

    # ----------------------------------------------------------------- #
    # 1. Correlation of returns, with its basis
    # ----------------------------------------------------------------- #

    section("1. Strategies correlated by their returns")
    series = strategy_returns()
    measured = [StrategyReturns(name, values, "USD", "1D") for name, values in series.items()]
    correlated = strategy_return_correlation(measured, source=STRATEGY_RETURNS_SOURCE)
    matrix = correlated.correlation
    basis = f"{matrix.currency}, per {matrix.period}, {matrix.observations} of them"
    print(f"  returns measured in {basis}")
    print(f"  from: {correlated.covariance.source}")
    print(f"  covariance {matrix.covariance_id[:16]}")
    print(f"  {'':10}" + "".join(f"{name:>10}" for name in matrix.assets))
    for index, name in enumerate(matrix.assets):
        print(f"  {name:<10}" + "".join(f"{value:10.3f}" for value in matrix.values[index]))
    in_euros = [
        StrategyReturns(entry.strategy_id, entry.returns, "EUR", "1D")
        if entry.strategy_id == "CARRY"
        else entry
        for entry in measured
    ]
    try:
        strategy_return_correlation(in_euros, source="CARRY measured in its account's currency")
    except AnalyticsValidationError as error:
        refusal("CARRY measured in euros, the others in dollars", error)

    # ----------------------------------------------------------------- #
    # 2. Overlap of holdings
    # ----------------------------------------------------------------- #

    section("2. Strategies compared by what they hold")
    for pair in strategy_overlap(lines):
        cosine = "n/a" if pair.cosine_similarity is None else f"{pair.cosine_similarity:+.3f}"
        in_common = ", ".join(pair.shared_instruments) or "nothing"
        print(f"  {pair.first} and {pair.second}: share {in_common}")
        same, opposing = pair.same_direction_overlap, pair.opposing_overlap
        print(
            f"    Jaccard {pair.jaccard:.3f}; same direction {same:,.2f} USD, "
            f"opposing {opposing:,.2f} USD"
        )
        print(
            f"    overlap is {100 * pair.first_overlap_share:.1f}% of {pair.first}'s gross and "
            f"{100 * pair.second_overlap_share:.1f}% of {pair.second}'s; cosine {cosine}"
        )
    print("  An exposure similarity compares holdings; it has no period and no sample.")

    # ----------------------------------------------------------------- #
    # 3. Factor crowding
    # ----------------------------------------------------------------- #

    section("3. Factor crowding inside the portfolio")
    loadings = factor_loadings()
    crowding = factor_crowding(lines, loadings, capital=valuation.nav)
    print(f"  factor model {crowding.loadings_id[:16]}: {loadings.source}")
    for factor in crowding.factors:
        exposures = ", ".join(f"{name} {value:+.5f}" for name, value in factor.by_strategy.items())
        alignment = "n/a" if factor.alignment is None else f"{factor.alignment:.3f}"
        spread = "n/a" if factor.concentration is None else f"{factor.concentration:.3f}"
        print(f"  {factor.factor}: {exposures}")
        print(
            f"    portfolio {factor.aggregate_exposure:+.5f}, gross {factor.gross_exposure:.5f}, "
            f"alignment {alignment}, concentration {spread}"
        )
        print(f"    leaning with the portfolio: {', '.join(factor.aligned_strategies) or 'none'}")
    for factor_pair in crowding.pairs:
        similarity = factor_pair.cosine_similarity
        cosine = "n/a" if similarity is None else f"{similarity:+.3f}"
        print(f"  {factor_pair.first} vs {factor_pair.second}: factor-exposure cosine {cosine}")
    print(f"  lineage of 'momentum': {loadings.lineage['momentum'][:68]}...")

    # ----------------------------------------------------------------- #
    # 4. Common exposures
    # ----------------------------------------------------------------- #

    section("4. Common exposures: by instrument, currency and sector")
    reports = (
        common_exposures(lines, by=CommonDimension.INSTRUMENT, classification=None),
        common_exposures(lines, by=CommonDimension.CURRENCY, classification=None),
        common_exposures(lines, by=CommonDimension.CLASSIFICATION, classification=sectors()),
    )
    for report in reports:
        shared = [bucket for bucket in report.buckets if bucket.shared]
        print(f"  by {report.label} (labels from: {report.source})")
        for bucket in shared:
            holders = ", ".join(
                f"{name} {value:+,.0f}" for name, value in bucket.by_strategy.items()
            )
            flag = "  <- opposite sides" if bucket.opposing else ""
            print(
                f"    {bucket.bucket:<12} net {bucket.net_exposure:>+14,.0f} USD: {holders}{flag}"
            )
        print(f"    ({len(report.buckets) - len(shared)} more held by one strategy only)")

    # ----------------------------------------------------------------- #
    # 5. Capital concentration and shared pools
    # ----------------------------------------------------------------- #

    section("5. Capital: how concentrated, and which pools are shared")
    capital = {entry.strategy_id: entry.nav for entry in valuation.strategies}
    concentration = capital_concentration(capital, currency="USD", dimension="strategy")
    for name, amount in concentration.amounts.items():
        print(f"  {name:<10}{amount:>16,} USD  {100 * concentration.shares[name]:5.1f}%")
    print(
        f"  Herfindahl {concentration.herfindahl:.3f}: like {concentration.effective_count:.2f} "
        f"equal strategies; largest {concentration.largest} "
        f"({100 * concentration.largest_share:.1f}%)"
    )
    pools = capital_overlap(
        {
            "MOMENTUM": {
                "BROKER-A/ACC-NY": Decimal("1800000"),
                "BROKER-B/ACC-TKY": Decimal("600000"),
            },
            "VALUE": {"BROKER-A/ACC-NY": Decimal("900000"), "BROKER-B/ACC-FRA": Decimal("230000")},
            "CARRY": {"BROKER-B/ACC-FRA": Decimal("850000"), "BROKER-B/ACC-TKY": Decimal("480000")},
        },
        currency="USD",
    )
    for pool in pools:
        users = ", ".join(f"{name} {amount:,}" for name, amount in pool.by_strategy.items())
        print(
            f"  {pool.pool:<18}{pool.total:>12,} USD  {users}{'  <- shared' if pool.shared else ''}"
        )
    print(
        "  A drawdown in one strategy reaches every other strategy that stands on the\n"
        "  same margin; the pools here are the example's accounts, named by identifier."
    )


if __name__ == "__main__":
    main()
