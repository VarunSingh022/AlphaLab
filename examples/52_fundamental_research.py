"""
AlphaLab Examples
=================

Example 52 : Fundamental Research, Point in Time

Difficulty : Intermediate

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 18 (factor research)
✓ Example 51 (alternative data with provenance)

Topics
------

• Statements as they were knowable: period end, filing, availability, restatement
• A date-only filing read at the next session open, never at midnight
• A restatement invisible until it was published, and the original still readable
• Trailing twelve months over consecutive quarters -- and refused for a balance
• Valuation and ratios with every undefined figure explained, never zeroed
• A price observed after the research instant refused as a look-ahead
• Restatement bias measured by name, as hindsight
• A point-in-time value factor across the universe

What this shows
---------------

A fundamentals database that stores one number per line item per period has
usually kept the latest restatement and stamped it with the period end, which
lets a backtest read figures before they were filed and corrections before they
were made. Here every figure carries its fiscal period, its filing and the
instant a researcher could read it, and every query takes an instant: the
statement for AAA's FY2023Q4 reads 0.65 before 13 May 2024 and 0.20 after it,
and a value factor computed on 10 May uses 0.65.

Run

    python examples/52_fundamental_research.py
"""

from datetime import date
from decimal import Decimal

from _point_in_time import (
    NYSE,
    SYMBOLS,
    banner,
    close_of,
    filing_rows,
    ingest_prices,
    label,
    section,
    vendor_file,
)

from alphalab.alt_data import (
    BOOK_EQUITY,
    CASH,
    EARNINGS,
    REVENUE,
    SHARES_OUTSTANDING,
    TOTAL_DEBT,
    Aggregation,
    AltDataInputError,
    FundamentalInput,
    StatementType,
    VintagePolicy,
    financial_ratios,
    fundamental_inputs_as_of,
    restatements,
    statement_as_of,
    trailing_twelve_months,
    valuation_metrics,
    year_over_year_growth,
)
from alphalab.api import (
    AvailabilityAtNextOpen,
    FundamentalColumns,
    TimestampReading,
    ingest_fundamentals,
    observation_source,
)
from alphalab.common import VisibilityRule
from alphalab.data.time import DateOnlyPolicy, TimestampFormat
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureScope,
    ResearchClock,
    SnapshotSpecification,
    align_prices,
    compute_panel,
    compute_value,
    divide_frames,
    fundamental_frame,
    fundamental_snapshot_as_of,
    observations_from_dataset,
)

PUB = VisibilityRule.PUBLICATION
INCOME = StatementType.INCOME_STATEMENT
BALANCE = StatementType.BALANCE_SHEET
AS_KNOWN = VintagePolicy.AS_KNOWN
ORIGINAL = VintagePolicy.ORIGINAL

INPUTS = (
    FundamentalInput(EARNINGS, INCOME, "net_income", Aggregation.TRAILING_TWELVE_MONTHS),
    FundamentalInput(REVENUE, INCOME, "revenue", Aggregation.TRAILING_TWELVE_MONTHS),
    FundamentalInput(BOOK_EQUITY, BALANCE, "total_equity", Aggregation.LATEST),
    FundamentalInput(SHARES_OUTSTANDING, BALANCE, "shares_diluted", Aggregation.LATEST),
    FundamentalInput(TOTAL_DEBT, BALANCE, "total_debt", Aggregation.LATEST),
    FundamentalInput(CASH, BALANCE, "cash", Aggregation.LATEST),
)


def main() -> None:
    banner(52, "Fundamental Research, Point in Time")

    section("1. Filings with a date and no time, read at the next open")
    rows = filing_rows()
    source = observation_source(
        vendor_file("filings.csv", rows, 1_717_000_300.0), "filings.quarterly", None
    )
    columns = FundamentalColumns(
        subject="issuer",
        statement="statement",
        line_item="item",
        fiscal_year="fy",
        fiscal_quarter="fq",
        period_start="start",
        period_end="end",
        value="value",
        unit="unit",
        published_at="filed",
        availability=AvailabilityAtNextOpen("filed", NYSE),
        revision="restatement",
        ingested_at_retrieval=False,
    )
    reading = TimestampReading(
        TimestampFormat.DATE_ONLY, "America/New_York", DateOnlyPolicy.END_OF_DAY
    )
    filings = ingest_fundamentals(rows, "filings", source, columns, reading).observations
    q1 = next(
        r
        for r in filings.records
        if r.subject == "AAA"
        and r.line_item == "eps_diluted"
        and r.fiscal_period.label == "FY2024Q1"
    )
    print(f"{len(filings)} figures, set version {filings.version}")
    print(
        f"AAA FY2024Q1 EPS: period ends {label(q1.fiscal_period.end)}, filed "
        f"{label(q1.published_at)}, knowable {label(q1.stamp.available_at or 0.0)}"
    )
    print(f"  basis {q1.stamp.basis.name}: {q1.stamp.rule}")

    section("2. A restatement, read only once it was published")
    view = filings.view(PUB)
    for day in (date(2024, 5, 10), date(2024, 5, 13)):
        at = close_of(day)
        known = statement_as_of(view, "AAA", INCOME, "FY2023Q4", at, AS_KNOWN)
        first = statement_as_of(view, "AAA", INCOME, "FY2023Q4", at, ORIGINAL)
        assert known is not None and first is not None
        print(
            f"{day}: FY2023Q4 EPS as known {known.value('eps_diluted')}, as first "
            f"published {first.value('eps_diluted')}, restated items {known.restated_items}"
        )

    section("3. Trailing twelve months, and what it refuses")
    for day in (date(2024, 4, 25), date(2024, 4, 26), date(2024, 5, 13)):
        ttm = trailing_twelve_months(view, "AAA", INCOME, "eps_diluted", close_of(day), AS_KNOWN)
        print(f"{day}: TTM EPS {ttm.value} over {ttm.periods}")
    try:
        trailing_twelve_months(
            view, "AAA", BALANCE, "total_equity", close_of(date(2024, 5, 13)), AS_KNOWN
        )
    except AltDataInputError as refusal:
        print(f"refused: {refusal}")
    growth = year_over_year_growth(
        view, "AAA", INCOME, "revenue", close_of(date(2024, 5, 13)), AS_KNOWN, annual=False
    )
    print(f"revenue growth FY2024Q1 over FY2023Q1: {growth.growth}")

    section("4. Valuation and ratios -- undefined figures explained")
    at = close_of(date(2024, 5, 1))
    inputs = fundamental_inputs_as_of(view, "AAA", at, AS_KNOWN, INPUTS)
    metrics = valuation_metrics(inputs, Decimal("25.00"), "USD/share", at - 3600.0)
    print(
        f"market cap {metrics.market_capitalization}, enterprise value {metrics.enterprise_value}"
    )
    print(f"earnings yield {metrics.earnings_yield:.6f}, P/E {metrics.price_to_earnings:.4f}")
    for name, reason in metrics.undefined.items():
        print(f"  {name}: undefined -- {reason}")
    ratios = financial_ratios(inputs)
    print(
        f"net margin {ratios.net_margin:.6f}, ROE {ratios.return_on_equity:.6f}, "
        f"debt/equity {ratios.debt_to_equity:.6f}"
    )
    for name, reason in ratios.undefined.items():
        print(f"  {name}: undefined -- {reason}")
    try:
        valuation_metrics(inputs, Decimal("25.00"), "USD/share", at + 60.0)
    except AltDataInputError as refusal:
        print(f"refused: {str(refusal)[:96]}...")

    section("5. Restatement bias, measured by name")
    for restatement in restatements(filings):
        print(
            f"{restatement.original.subject} {restatement.original.line_item} "
            f"{restatement.original.fiscal_period.label}: {restatement.original.value} -> "
            f"{restatement.latest.value} ({restatement.change:+})"
        )

    section("6. A point-in-time value factor across the universe")
    prices = ingest_prices()
    closes = observations_from_dataset(prices, FeatureField.CLOSE)
    eps = FundamentalInput("eps_ttm", INCOME, "eps_diluted", Aggregation.TRAILING_TWELVE_MONTHS)
    eps_frame = fundamental_frame(
        view, eps, ResearchClock.of_frame(closes), SYMBOLS, max_age_seconds=None, policy=AS_KNOWN
    )
    yields = divide_frames(eps_frame.frame, align_prices(closes, eps_frame), name="earnings_yield")
    ranked = compute_panel(
        FeatureDefinition(
            "earnings_yield_rank",
            FeatureKind.CROSS_SECTIONAL_RANK,
            FeatureField.OBSERVATION,
            scope=FeatureScope.CROSS_SECTIONAL,
        ),
        yields.frame,
    )
    instant = close_of(date(2024, 5, 10))
    print(f"ranks on {label(instant)}: {dict(sorted(ranked.cross_section(instant).items()))}")
    print(f"factor lineage: {ranked.dataset_version}")

    snapshot = fundamental_snapshot_as_of(
        view,
        "AAA",
        instant,
        Decimal("25.00"),
        "USD/share",
        instant,
        AS_KNOWN,
        SnapshotSpecification(
            earnings_per_share=eps,
            book_equity=FundamentalInput("book", BALANCE, "total_equity", Aggregation.LATEST),
            shares_outstanding=FundamentalInput(
                "shares", BALANCE, "shares_diluted", Aggregation.LATEST
            ),
            dividends_per_share=FundamentalInput(
                "dps_ttm", INCOME, "dps", Aggregation.TRAILING_TWELVE_MONTHS
            ),
        ),
    )
    value = compute_value(snapshot, "earnings_yield", 1, instant)
    print(
        f"AAA snapshot on 2024-05-10: EPS {snapshot.earnings_per_share}, "
        f"book/share {snapshot.book_value_per_share}, earnings yield {value.value:.4f}"
    )


if __name__ == "__main__":
    main()
