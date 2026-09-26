"""Point-in-time fundamentals: statements as they were knowable, never as restated.

One issuer, seven quarters published after the close, and one restatement.
Every query is asked at a research instant, and the tests pin the instants at
which each figure -- and each restatement -- becomes readable.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.alt_data import (
    BOOK_EQUITY,
    CASH,
    EARNINGS,
    REVENUE,
    SHARES_OUTSTANDING,
    TOTAL_DEBT,
    Aggregation,
    AltDataInputError,
    FiscalPeriod,
    FundamentalInput,
    FundamentalObservation,
    ObservationView,
    StatementType,
    VintagePolicy,
    build_observation_set,
    financial_ratios,
    fundamental_as_of,
    fundamental_inputs_as_of,
    latest_fundamental,
    restatements,
    statement_as_of,
    trailing_twelve_months,
    valuation_metrics,
    year_over_year_growth,
)
from alphalab.common import VisibilityRule
from tests.unit.alt_data.pit_harness import (
    BALANCE,
    INCOME,
    PUBLISHED,
    RESTATED_AT,
    SOURCE,
    fiscal_year,
    fundamental,
    issuer_records,
    issuer_set,
    ny,
    quarter,
)

PUB = VisibilityRule.PUBLICATION
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


def _view() -> ObservationView[FundamentalObservation]:
    return issuer_set().view(PUB)


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #


def test_a_fiscal_period_labels_itself_and_orders_its_quarters() -> None:
    assert quarter(2024, 2).label == "FY2024Q2"
    assert fiscal_year(2024).label == "FY2024"
    assert fiscal_year(2024).is_annual
    assert quarter(2024, 1).quarter_ordinal() == quarter(2023, 4).quarter_ordinal() + 1
    with pytest.raises(AltDataInputError, match="no quarter ordinal"):
        fiscal_year(2024).quarter_ordinal()


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ((2024, 5, 0.0, 1.0), "a quarter is 1 to 4"),
        ((2024, 0, 0.0, 1.0), "a quarter is 1 to 4"),
        ((2024.0, 1, 0.0, 1.0), "integer"),
        ((2024, 1, 5.0, 5.0), "not after it starts"),
    ],
)
def test_an_impossible_fiscal_period_is_refused(arguments: tuple[object, ...], match: str) -> None:
    with pytest.raises(AltDataInputError, match=match):
        FiscalPeriod(*arguments)  # type: ignore[arg-type]


def test_results_published_before_the_period_ends_are_refused() -> None:
    period = quarter(2024, 1)

    with pytest.raises(AltDataInputError, match="has not finished"):
        fundamental(
            "AAA", INCOME, "revenue", period, "1", period.end - 86_400.0, available_at=period.end
        )


def test_a_figure_available_before_it_was_published_is_refused() -> None:
    period = quarter(2024, 1)
    published = PUBLISHED[(2024, 1)]

    with pytest.raises(AltDataInputError, match="before it was published"):
        fundamental("AAA", INCOME, "revenue", period, "1", published, available_at=published - 60.0)


def test_a_figure_observed_at_anything_but_its_period_end_is_refused() -> None:
    record = fundamental("AAA", INCOME, "revenue", quarter(2024, 1), "1", PUBLISHED[(2024, 1)])

    with pytest.raises(AltDataInputError, match="period's end"):
        replace(record, stamp=replace(record.stamp, observed_at=record.stamp.observed_at - 1.0))


# --------------------------------------------------------------------------- #
# Reading as knowable
# --------------------------------------------------------------------------- #


def test_a_quarter_is_invisible_until_it_is_published() -> None:
    view = _view()
    published = PUBLISHED[(2024, 1)]

    before = fundamental_as_of(view, "AAA", INCOME, "revenue", "FY2024Q1", published - 1, AS_KNOWN)
    after = fundamental_as_of(view, "AAA", INCOME, "revenue", "FY2024Q1", published, AS_KNOWN)

    assert before is None, "the period ended in March and the figure did not exist until May"
    assert after is not None and after.value == Decimal("120")


def test_a_restatement_is_read_only_after_it_was_published() -> None:
    view = _view()

    before = fundamental_as_of(
        view, "AAA", INCOME, "revenue", "FY2024Q1", RESTATED_AT - 1, AS_KNOWN
    )
    after = fundamental_as_of(view, "AAA", INCOME, "revenue", "FY2024Q1", RESTATED_AT, AS_KNOWN)
    original = fundamental_as_of(
        view, "AAA", INCOME, "revenue", "FY2024Q1", RESTATED_AT + 1e7, ORIGINAL
    )

    assert before is not None and before.value == Decimal("120")
    assert after is not None and after.value == Decimal("104") and after.revision == 1
    assert original is not None and original.value == Decimal("120")


def test_the_latest_quarter_moves_forward_only_as_quarters_are_published() -> None:
    view = _view()

    at_june = latest_fundamental(
        view, "AAA", INCOME, "revenue", ny("2024-06-15 12:00"), AS_KNOWN, annual=False
    )
    at_september = latest_fundamental(
        view, "AAA", INCOME, "revenue", ny("2024-09-15 12:00"), AS_KNOWN, annual=False
    )

    assert at_june is not None and at_june.fiscal_period.label == "FY2024Q1"
    assert at_september is not None and at_september.fiscal_period.label == "FY2024Q2"
    assert latest_fundamental(view, "AAA", INCOME, "revenue", 0.0, AS_KNOWN, annual=None) is None
    assert latest_fundamental(view, "ZZZ", INCOME, "revenue", 1e12, AS_KNOWN, annual=None) is None


def test_a_statement_assembles_every_item_readable_at_the_instant() -> None:
    view = _view()

    statement = statement_as_of(view, "AAA", INCOME, "FY2024Q1", RESTATED_AT, AS_KNOWN)
    original = statement_as_of(view, "AAA", INCOME, "FY2024Q1", RESTATED_AT, ORIGINAL)

    assert statement is not None and original is not None
    assert sorted(statement.items) == ["eps_diluted", "net_income", "revenue"]
    assert statement.value("revenue") == Decimal("104")
    assert statement.restated_items == ("revenue",)
    assert original.value("revenue") == Decimal("120")
    assert original.restated_items == ()
    with pytest.raises(AltDataInputError, match="has no 'ebitda'"):
        statement.value("ebitda")
    assert statement_as_of(view, "AAA", INCOME, "FY2024Q1", 0.0, AS_KNOWN) is None


def test_items_that_disagree_about_their_period_are_refused() -> None:
    period = quarter(2024, 1)
    skewed = FiscalPeriod(2024, 1, period.start + 1.0, period.end)
    records = [
        fundamental("AAA", INCOME, "revenue", period, "1", PUBLISHED[(2024, 1)]),
        fundamental("AAA", INCOME, "net_income", skewed, "1", PUBLISHED[(2024, 1)]),
    ]
    view = build_observation_set("skew", records, SOURCE).view(PUB)

    with pytest.raises(AltDataInputError, match="disagree"):
        statement_as_of(view, "AAA", INCOME, "FY2024Q1", 1e12, AS_KNOWN)


def test_a_figure_with_unknown_availability_is_never_read() -> None:
    period = quarter(2024, 1)
    records = [
        fundamental("AAA", INCOME, "revenue", period, "120", PUBLISHED[(2024, 1)], known=False)
    ]
    view = build_observation_set("unknown", records, SOURCE).view(PUB)

    assert fundamental_as_of(view, "AAA", INCOME, "revenue", "FY2024Q1", 1e12, AS_KNOWN) is None


# --------------------------------------------------------------------------- #
# Trailing twelve months
# --------------------------------------------------------------------------- #


def test_trailing_twelve_months_sums_the_four_latest_consecutive_quarters() -> None:
    view = _view()

    ttm = trailing_twelve_months(view, "AAA", INCOME, "revenue", ny("2024-06-15 12:00"), AS_KNOWN)

    # FY2023Q2..FY2024Q1 as originally published: 105 + 110 + 115 + 120.
    assert ttm.value == Decimal("450")
    assert ttm.periods == ("FY2023Q2", "FY2023Q3", "FY2023Q4", "FY2024Q1")
    assert ttm.unit == "USD"
    assert len(ttm.record_ids) == 4
    assert ttm.reason == ""


def test_trailing_twelve_months_reads_the_restatement_only_once_published() -> None:
    view = _view()
    late = ny("2024-10-15 12:00")

    as_known = trailing_twelve_months(view, "AAA", INCOME, "revenue", late, AS_KNOWN)
    original = trailing_twelve_months(view, "AAA", INCOME, "revenue", late, ORIGINAL)

    # FY2023Q3..FY2024Q2: 110 + 115 + (120 restated to 104) + 125.
    assert as_known.value == Decimal("454")
    assert original.value == Decimal("470")


def test_fewer_than_four_quarters_is_undefined_with_a_reason() -> None:
    view = _view()

    ttm = trailing_twelve_months(view, "AAA", INCOME, "revenue", ny("2023-11-15 12:00"), AS_KNOWN)

    assert ttm.value is None
    assert "only 3 quarter(s)" in ttm.reason


def test_a_gap_in_the_quarters_is_undefined_rather_than_summed_across() -> None:
    records = [
        r
        for r in issuer_records()
        if not (r.line_item == "revenue" and r.fiscal_period.label == "FY2023Q4")
    ]
    view = build_observation_set("gap", records, SOURCE).view(PUB)

    ttm = trailing_twelve_months(view, "AAA", INCOME, "revenue", ny("2024-06-15 12:00"), AS_KNOWN)

    assert ttm.value is None
    assert "not consecutive" in ttm.reason


def test_trailing_twelve_months_of_a_balance_is_refused() -> None:
    with pytest.raises(AltDataInputError, match="level"):
        trailing_twelve_months(_view(), "AAA", BALANCE, "total_equity", 1e12, AS_KNOWN)
    with pytest.raises(AltDataInputError, match="cannot be summed"):
        FundamentalInput("x", BALANCE, "total_equity", Aggregation.TRAILING_TWELVE_MONTHS)


# --------------------------------------------------------------------------- #
# Inputs, valuation and ratios
# --------------------------------------------------------------------------- #


def test_inputs_are_read_with_their_units_sources_and_age() -> None:
    inputs = fundamental_inputs_as_of(_view(), "AAA", ny("2024-06-15 12:00"), AS_KNOWN, INPUTS)

    assert inputs.values[EARNINGS] == Decimal("11") + 12 + 13 + 14
    assert inputs.values[BOOK_EQUITY] == Decimal("404")
    assert inputs.units[SHARES_OUTSTANDING] == "shares"
    assert inputs.observed_at[BOOK_EQUITY] == quarter(2024, 1).end
    assert len(inputs.record_ids[EARNINGS]) == 4
    assert inputs.missing == {}


def test_a_missing_input_carries_its_reason() -> None:
    inputs = fundamental_inputs_as_of(
        _view(),
        "AAA",
        ny("2023-06-15 12:00"),
        AS_KNOWN,
        (*INPUTS, FundamentalInput("ebitda", INCOME, "ebitda", Aggregation.LATEST)),
    )

    assert "only 1 quarter(s)" in inputs.missing[EARNINGS]
    assert "no 'ebitda'" in inputs.missing["ebitda"]
    with pytest.raises(AltDataInputError, match="not defined"):
        inputs.require(EARNINGS)


def test_duplicate_or_absent_inputs_are_refused() -> None:
    with pytest.raises(AltDataInputError, match="share a name"):
        fundamental_inputs_as_of(_view(), "AAA", 1e12, AS_KNOWN, (INPUTS[0], INPUTS[0]))
    with pytest.raises(AltDataInputError, match="at least one"):
        fundamental_inputs_as_of(_view(), "AAA", 1e12, AS_KNOWN, ())


def test_valuation_metrics_from_point_in_time_fundamentals() -> None:
    as_of = ny("2024-06-15 12:00")
    inputs = fundamental_inputs_as_of(_view(), "AAA", as_of, AS_KNOWN, INPUTS)

    metrics = valuation_metrics(inputs, Decimal("25"), "USD/share", as_of - 3600.0)

    assert metrics.market_capitalization == Decimal("250")
    assert metrics.enterprise_value == Decimal("280")
    assert metrics.earnings_yield == Decimal("50") / Decimal("250")
    assert metrics.price_to_earnings == Decimal("5")
    assert metrics.book_to_market == Decimal("404") / Decimal("250")
    assert metrics.sales_to_price == Decimal("450") / Decimal("250")
    assert metrics.enterprise_value_to_sales == Decimal("280") / Decimal("450")
    assert metrics.enterprise_value_to_ebitda is None
    assert "ebitda" in metrics.undefined["enterprise_value_to_ebitda"]
    assert metrics.currency == "USD"
    assert len(metrics.record_ids) == 4 + 4 + 4


def test_a_price_observed_after_the_research_instant_is_refused() -> None:
    as_of = ny("2024-06-15 12:00")
    inputs = fundamental_inputs_as_of(_view(), "AAA", as_of, AS_KNOWN, INPUTS)

    with pytest.raises(AltDataInputError, match="look-ahead through the denominator"):
        valuation_metrics(inputs, Decimal("25"), "USD/share", as_of + 1.0)


def test_valuation_refuses_a_price_or_input_in_the_wrong_unit() -> None:
    as_of = ny("2024-06-15 12:00")
    inputs = fundamental_inputs_as_of(_view(), "AAA", as_of, AS_KNOWN, INPUTS)

    with pytest.raises(AltDataInputError, match="'<currency>/share'"):
        valuation_metrics(inputs, Decimal("25"), "USD", as_of)
    with pytest.raises(AltDataInputError, match="in 'USD'"):
        valuation_metrics(inputs, Decimal("25"), "EUR/share", as_of)
    with pytest.raises(AltDataInputError, match="is not a price"):
        valuation_metrics(inputs, Decimal("0"), "USD/share", as_of)


def test_losses_leave_the_multiple_undefined_and_the_yield_negative() -> None:
    records = [
        replace(r, value=-r.value) if r.line_item == "net_income" else r for r in issuer_records()
    ]
    view = build_observation_set("losses", records, SOURCE).view(PUB)
    as_of = ny("2024-06-15 12:00")
    inputs = fundamental_inputs_as_of(view, "AAA", as_of, AS_KNOWN, INPUTS)

    metrics = valuation_metrics(inputs, Decimal("25"), "USD/share", as_of)

    assert metrics.price_to_earnings is None
    assert "not positive" in metrics.undefined["price_to_earnings"]
    assert metrics.earnings_yield is not None and metrics.earnings_yield < 0


def test_ratios_are_computed_where_defined_and_explained_where_not() -> None:
    inputs = fundamental_inputs_as_of(_view(), "AAA", ny("2024-06-15 12:00"), AS_KNOWN, INPUTS)

    ratios = financial_ratios(inputs)

    assert ratios.net_margin == Decimal("50") / Decimal("450")
    assert ratios.return_on_equity == Decimal("50") / Decimal("404")
    assert ratios.debt_to_equity == Decimal("50") / Decimal("404")
    assert ratios.gross_margin is None
    assert "gross_profit" in ratios.undefined["gross_margin"]


def test_growth_compares_like_with_like_as_knowable_then() -> None:
    view = _view()

    growth = year_over_year_growth(
        view, "AAA", INCOME, "revenue", ny("2024-06-15 12:00"), AS_KNOWN, annual=False
    )
    after = year_over_year_growth(
        view, "AAA", INCOME, "revenue", RESTATED_AT, AS_KNOWN, annual=False
    )
    early = year_over_year_growth(
        view, "AAA", INCOME, "revenue", ny("2023-06-15 12:00"), AS_KNOWN, annual=False
    )

    assert growth.growth == Decimal("120") / Decimal("100") - 1
    # At the restatement instant FY2024Q2 is the latest quarter: 125 / 105 - 1.
    assert after.current is not None and after.current.fiscal_period.label == "FY2024Q2"
    assert after.growth == Decimal("125") / Decimal("105") - 1
    assert early.growth is None and "FY2022Q1" in early.reason


# --------------------------------------------------------------------------- #
# Restatement analysis, by name
# --------------------------------------------------------------------------- #


def test_restatements_compare_original_and_latest_across_the_set() -> None:
    found = restatements(issuer_set())

    assert len(found) == 1
    assert found[0].original.value == Decimal("120")
    assert found[0].latest.value == Decimal("104")
    assert found[0].change == Decimal("-16")
    assert found[0].revisions == 1


def test_an_income_item_is_not_a_balance_item() -> None:
    assert StatementType.INCOME_STATEMENT.is_duration
    assert StatementType.CASH_FLOW_STATEMENT.is_duration
    assert not StatementType.BALANCE_SHEET.is_duration


# --------------------------------------------------------------------------- #
# The aggregate timeline agrees with the point reads
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("policy", [AS_KNOWN, ORIGINAL])
@pytest.mark.parametrize("spec", INPUTS, ids=lambda spec: spec.name)
def test_the_aggregate_timeline_agrees_with_the_point_reads(
    policy: VintagePolicy, spec: FundamentalInput
) -> None:
    import bisect

    from alphalab.alt_data import aggregate_timeline

    view = _view()
    timeline = aggregate_timeline(view, "AAA", spec, policy)
    instants = [step.known_at for step in timeline]
    probes = [
        *sorted({*PUBLISHED.values(), RESTATED_AT}),
        ny("2023-01-15 12:00"),
        ny("2024-06-15 12:00"),
        ny("2024-10-15 12:00"),
        ny("2025-03-01 12:00"),
    ]

    for probe in probes:
        position = bisect.bisect_right(instants, probe)
        value = timeline[position - 1].value if position else None
        inputs = fundamental_inputs_as_of(view, "AAA", probe, policy, (spec,))
        assert value == inputs.values.get(spec.name), (spec.name, probe)


def test_a_per_share_unit_names_its_currency() -> None:
    from alphalab.alt_data import currency_of_per_share_unit

    assert currency_of_per_share_unit("JPY/share") == "JPY"
    for bad in ("JPY", "/share", "JPY/unit"):
        with pytest.raises(AltDataInputError, match="per-share unit"):
            currency_of_per_share_unit(bad)
