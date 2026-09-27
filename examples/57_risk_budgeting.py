"""
AlphaLab Examples
=================

Example 57 : Risk Budgeting

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 28 (risk decomposition)
✓ Example 56 (portfolio construction)

Topics
------

• One book, three strategies, three currencies, valued in dollars
• A risk model of returns measured in the reporting currency -- and one in
  another currency refused rather than mixed
• Sectors read from the instrument registry, countries from the caller's file
• Where the risk comes from along five dimensions: asset, strategy, sector,
  country and currency -- each a partition of the same lines, each summing
  to the portfolio's volatility
• Exposure, capital share and risk contribution, side by side
• Limits with a maximum, a minimum or a target, judged with a stated tolerance
• A currency's exposure in that currency, apart from its translation
• A regularized risk model, recorded as derived from the one it changed

What this shows
---------------

A strategy given a fifth of the capital can carry half the risk, and a book of
equal exposures can hold most of its risk in one name. A risk budget says how
much of the portfolio's risk each bucket may carry, and this example measures a
multi-currency book against one: every exposure line -- one strategy's holding
of one instrument -- gets its Euler contribution once, and every dimension
groups the same lines, so each dimension's buckets add up to the same
volatility. A breach is reported, with the bucket and the limit named; nothing
is rebalanced behind the reader's back.

Run

    python examples/57_risk_budgeting.py
"""

from _portfolio_world import (
    AS_OF,
    ASSET_ID,
    RATES,
    REGISTRY,
    SYMBOL_OF,
    SYMBOLS,
    banner,
    book,
    countries,
    covariance,
    refusal,
    section,
)

from alphalab.analytics import (
    AnalyticsValidationError,
    BudgetBasis,
    BudgetLimit,
    Classification,
    CovarianceMatrix,
    RiskBudget,
    RiskBudgetReport,
    RiskDimension,
    evaluate_risk_budget,
)
from alphalab.api import sector_classification
from alphalab.portfolio import BookValuation, value_book

BUDGET = RiskBudget(
    "fund risk budget, June 2024",
    (
        BudgetLimit(RiskDimension.ASSET, "ATLS", BudgetBasis.RELATIVE, 0.30, None, None),
        BudgetLimit(RiskDimension.ASSET, "GINZ", BudgetBasis.RELATIVE, 0.25, None, None),
        BudgetLimit(RiskDimension.STRATEGY, "MOMENTUM", BudgetBasis.RELATIVE, 0.60, None, 0.50),
        BudgetLimit(RiskDimension.STRATEGY, "CARRY", BudgetBasis.RELATIVE, None, 0.15, None),
        BudgetLimit(RiskDimension.SECTOR, "Technology", BudgetBasis.RELATIVE, 0.65, None, None),
        BudgetLimit(RiskDimension.COUNTRY, "US", BudgetBasis.ABSOLUTE, 0.0030, None, None),
        BudgetLimit(RiskDimension.CURRENCY, "JPY", BudgetBasis.RELATIVE, 0.20, None, None),
    ),
    tolerance=1e-6,
)


def registry_sectors() -> Classification:
    """The registry's sectors, re-keyed from canonical asset ids to this book's tickers.

    The book identifies instruments by ticker, the registry by ``asset_id``. The
    labels and the source are the registry's; only the keys change, and the
    re-keyed classification says so in its source.
    """

    read = sector_classification(REGISTRY, [ASSET_ID[symbol] for symbol in SYMBOLS], None)
    return Classification(
        "sector",
        {SYMBOL_OF[asset_id]: label for asset_id, label in read.labels.items()},
        f"{read.source}; keyed by ticker",
        read.as_of,
    )


def evaluate(
    valuation: BookValuation, model: CovarianceMatrix, sectors: Classification
) -> RiskBudgetReport:
    return evaluate_risk_budget(
        valuation.lines,
        capital=valuation.nav,
        reporting_currency="USD",
        covariance=model,
        budget=BUDGET,
        classifications=(sectors, countries()),
    )


def main() -> None:
    banner(57, "Risk Budgeting")

    # ----------------------------------------------------------------- #
    # 1. The book, in dollars
    # ----------------------------------------------------------------- #

    section("1. One book, three strategies, three currencies, valued in dollars")
    valuation = value_book(book(), reporting_currency="USD", rates=RATES, as_of=AS_OF)
    print(f"  book NAV     : {valuation.nav:>14,} USD  (what every weight is a fraction of)")
    print(f"  net exposure : {valuation.net_exposure:>14,} USD")
    print(f"  gross        : {valuation.gross_exposure:>14,} USD")
    print(f"  lines        : {len(valuation.lines)} (one per strategy and instrument)")
    print(f"  conversions  : {len(valuation.conversions)}, each at a recorded rate")

    # ----------------------------------------------------------------- #
    # 2. The risk model, and the currency it must be in
    # ----------------------------------------------------------------- #

    section("2. A risk model of returns measured in dollars")
    model = covariance()
    print(f"  covariance {model.covariance_id[:16]}: {model.currency}, per {model.period}")
    in_euros = CovarianceMatrix.from_rows(
        model.assets,
        model.values,
        currency="EUR",
        period="1D",
        source="the same numbers, relabelled",
        observations=None,
    )
    try:
        evaluate(valuation, in_euros, registry_sectors())
    except AnalyticsValidationError as error:
        refusal("a euro covariance for a dollar book", error)

    # ----------------------------------------------------------------- #
    # 3. Where the labels come from
    # ----------------------------------------------------------------- #

    section("3. Sectors from the instrument registry, countries from a file")
    sectors = registry_sectors()
    print(f"  sector  : {sectors.source}")
    print(f"  country : {countries().source}")
    for symbol in SYMBOLS:
        print(
            f"    {symbol}  {ASSET_ID[symbol][:8]}...  {sectors.label(symbol):<12}"
            f"{countries().label(symbol)}"
        )

    # ----------------------------------------------------------------- #
    # 4. Where the risk comes from
    # ----------------------------------------------------------------- #

    section("4. Where the risk comes from, along five dimensions")
    report = evaluate(valuation, model, sectors)
    print(f"  portfolio volatility: {100 * report.volatility:.4f}% of NAV a day")
    for dimension in report.dimensions:
        print()
        print(
            f"  {dimension.dimension.name:<9}{'net exposure':>16}{'capital':>9}"
            f"{'risk, bp':>10}{'share':>8}"
        )
        for bucket in dimension.buckets:
            share = f"{100 * bucket.capital_share:8.1f}%"
            print(
                f"    {bucket.bucket:<11}{bucket.net_exposure:>14,.0f}{share}"
                f"{1e4 * bucket.contribution:10.2f}{100 * bucket.share:7.1f}%"
            )
        print(
            f"    {'total':<11}{'':>14}{'':>9}{1e4 * dimension.total_contribution:10.2f}"
            f"   residual {dimension.residual:+.1e}"
        )
    momentum = report.dimension(RiskDimension.STRATEGY).bucket("MOMENTUM")
    assert momentum is not None
    print(
        f"\n  MOMENTUM holds {100 * momentum.capital_share:.0f}% of the capital as net exposure "
        f"and carries {100 * momentum.share:.0f}% of the risk."
    )

    # ----------------------------------------------------------------- #
    # 5. The budget, judged
    # ----------------------------------------------------------------- #

    section("5. The budget, judged with a stated tolerance")
    print(f"  budget {report.budget_id[:16]}, tolerance {BUDGET.tolerance:g}")
    for check in report.checks:
        limit = check.limit
        unit = "bp" if limit.basis is BudgetBasis.ABSOLUTE else "%"
        scale = 1e4 if limit.basis is BudgetBasis.ABSOLUTE else 100.0
        bounds = ", ".join(
            f"{word} {scale * value:g}{unit}"
            for word, value in (
                ("max", limit.maximum),
                ("min", limit.minimum),
                ("target", limit.target),
            )
            if value is not None
        )
        print(
            f"  {limit.dimension.name:<9}{limit.bucket:<11}{limit.basis.name:<9}"
            f"used {scale * check.used:6.2f}{unit:<3}({bounds}) -> {check.status.name}"
        )
    print(f"  breaches: {[f'{c.limit.dimension.name}/{c.limit.bucket}' for c in report.breaches]}")

    # ----------------------------------------------------------------- #
    # 6. A currency's exposure in that currency
    # ----------------------------------------------------------------- #

    section("6. Each currency bucket in its own currency, and translated")
    for bucket in report.dimension(RiskDimension.CURRENCY).buckets:
        assert bucket.native_exposure is not None
        print(
            f"  {bucket.bucket}: {bucket.native_exposure:>16,} {bucket.bucket}"
            f"  = {bucket.net_exposure:>14,} USD, {100 * bucket.share:5.1f}% of the risk"
        )
    print(
        "  The CURRENCY dimension is the risk of holdings denominated in each currency,\n"
        "  measured in dollars. It is not the risk of the exchange rate alone."
    )

    # ----------------------------------------------------------------- #
    # 7. A regularized risk model, recorded
    # ----------------------------------------------------------------- #

    section("7. A shrunk risk model is a new model, with its own identity")
    shrunk = model.with_diagonal_shrinkage(0.25)
    again = evaluate(valuation, shrunk, sectors)
    print(f"  shrunk covariance : {shrunk.covariance_id[:16]}")
    print(f"  derived from      : {str(shrunk.parent_id)[:16]} by '{shrunk.derivation}'")
    print(
        f"  volatility        : {100 * again.volatility:.4f}% (was {100 * report.volatility:.4f}%)"
    )
    print(f"  report identity   : {again.report_id[:16]} (was {report.report_id[:16]})")
    try:
        evaluate_risk_budget(
            valuation.lines,
            capital=valuation.nav,
            reporting_currency="USD",
            covariance=model,
            budget=BUDGET,
            classifications=(countries(),),
        )
    except AnalyticsValidationError as error:
        refusal("a sector limit with no sector classification", error)


if __name__ == "__main__":
    main()
