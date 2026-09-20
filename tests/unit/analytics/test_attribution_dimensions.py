"""The six dimensions v3.3 adds, and what each does when its metadata is absent."""

from decimal import Decimal

import pytest

from alphalab.analytics.attribution import (
    AttributionDimension,
    Availability,
    TradeFacts,
    TradeRecord,
    attribute,
    calculate_attribution,
)
from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.core.contribution import StrategyContribution


def trades() -> tuple[TradeRecord, ...]:
    return (
        TradeRecord(
            "T1",
            "AAPL",
            "Tech",
            Decimal("100.00"),
            Decimal("5000"),
            3600.0,
            (StrategyContribution("MOM", Decimal("60")), StrategyContribution("MR", Decimal("40"))),
        ),
        TradeRecord(
            "T2",
            "SAP",
            None,
            Decimal("-40.00"),
            Decimal("2000"),
            1800.0,
            (StrategyContribution("MOM", Decimal("100")),),
        ),
    )


def facts() -> dict[str, TradeFacts]:
    return {
        "T1": TradeFacts(
            "T1",
            currency="USD",
            venue="XNAS",
            broker="BRK-A",
            country="US",
            execution_costs={"commission": Decimal("2.00"), "spread": Decimal("0.50")},
        ),
        "T2": TradeFacts("T2", currency="USD", venue="XETR", broker="BRK-B", country="DE"),
    }


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "dimension",
    [
        AttributionDimension.STRATEGY,
        AttributionDimension.ASSET,
        AttributionDimension.COUNTRY,
        AttributionDimension.VENUE,
        AttributionDimension.BROKER,
    ],
)
def test_a_fully_covered_partitioning_dimension_reconciles(
    dimension: AttributionDimension,
) -> None:
    report = attribute(trades(), facts())
    built = report.dimensions[dimension]

    assert built.availability is Availability.AVAILABLE
    assert built.reconciles
    assert sum(built.buckets.values()) == report.realized_pnl


def test_the_strategy_split_is_the_existing_authority_s_split() -> None:
    """Not re-derived here: attribute() calls split_realized_pnl."""

    report = attribute(trades(), facts())
    legacy = calculate_attribution(trades())

    assert report.dimensions[AttributionDimension.STRATEGY].buckets == legacy.pnl_by_strategy


def test_the_asset_breakdown_matches_the_existing_authority() -> None:
    report = attribute(trades(), facts())
    legacy = calculate_attribution(trades())

    assert report.dimensions[AttributionDimension.ASSET].buckets == legacy.pnl_by_asset


# --------------------------------------------------------------------------- #
# Absent metadata is reported, never fabricated
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "dimension",
    [
        AttributionDimension.COUNTRY,
        AttributionDimension.CURRENCY,
        AttributionDimension.VENUE,
        AttributionDimension.BROKER,
        AttributionDimension.FACTOR,
        AttributionDimension.EXECUTION,
    ],
)
def test_a_dimension_with_no_metadata_is_empty_rather_than_bucketed_as_unknown(
    dimension: AttributionDimension,
) -> None:
    built = attribute(trades()).dimensions[dimension]

    assert built.availability is Availability.NO_METADATA
    assert built.buckets == {}
    assert built.covered == 0
    assert not built.reconciles
    assert "UNKNOWN" not in built.buckets


def test_partial_metadata_covers_what_it_has_and_names_what_it_does_not() -> None:
    partial = {"T1": TradeFacts("T1", country="US")}
    built = attribute(trades(), partial).dimensions[AttributionDimension.COUNTRY]

    assert built.availability is Availability.PARTIAL
    assert built.buckets == {"US": Decimal("100.00")}
    assert built.uncovered == ("T2",)
    assert not built.reconciles


def test_an_unknown_sector_stays_out_of_the_sector_breakdown() -> None:
    built = attribute(trades(), facts()).dimensions[AttributionDimension.SECTOR]

    assert built.availability is Availability.PARTIAL
    assert set(built.buckets) == {"Tech"}
    assert built.uncovered == ("T2",)


def test_a_trade_with_no_contributions_is_not_attributed_to_a_fabricated_strategy() -> None:
    orphan = (TradeRecord("T9", "AAPL", None, Decimal("10.00"), Decimal("100"), None, ()),)
    built = attribute(orphan).dimensions[AttributionDimension.STRATEGY]

    assert built.buckets == {}
    assert built.uncovered == ("T9",)


# --------------------------------------------------------------------------- #
# Currency is dimensionally coherent, and therefore does not total
# --------------------------------------------------------------------------- #


def test_currency_buckets_are_kept_separate_and_never_summed() -> None:
    mixed = {
        "T1": TradeFacts("T1", currency="USD"),
        "T2": TradeFacts("T2", currency="EUR"),
    }
    report = attribute(trades(), mixed)
    built = report.dimensions[AttributionDimension.CURRENCY]

    assert built.availability is Availability.AVAILABLE
    assert built.buckets == {"EUR": Decimal("-40.00"), "USD": Decimal("100.00")}
    assert not built.reconciles, "currency buckets are in different units and do not total"
    assert report.currencies == ("EUR", "USD")


def test_a_single_currency_book_reports_one_currency() -> None:
    assert attribute(trades(), facts()).currencies == ("USD",)


# --------------------------------------------------------------------------- #
# Factor and execution
# --------------------------------------------------------------------------- #


def test_factor_attribution_carries_the_unexplained_residual_so_it_reconciles() -> None:
    supplied = {
        "T1": TradeFacts("T1", factor_pnl={"momentum": Decimal("70.00")}),
        "T2": TradeFacts("T2", factor_pnl={"momentum": Decimal("-30.00")}),
    }
    report = attribute(trades(), supplied)
    built = report.dimensions[AttributionDimension.FACTOR]

    assert built.buckets["momentum"] == Decimal("40.00")
    assert built.buckets["residual"] == Decimal("20.00")
    assert built.reconciles
    assert sum(built.buckets.values()) == report.realized_pnl


def test_a_factor_model_that_explains_everything_leaves_a_zero_residual() -> None:
    supplied = {
        "T1": TradeFacts("T1", factor_pnl={"momentum": Decimal("100.00")}),
        "T2": TradeFacts("T2", factor_pnl={"momentum": Decimal("-40.00")}),
    }
    built = attribute(trades(), supplied).dimensions[AttributionDimension.FACTOR]

    assert built.buckets["residual"] == Decimal("0.00")


def test_execution_costs_are_reported_as_negative_contributions() -> None:
    built = attribute(trades(), facts()).dimensions[AttributionDimension.EXECUTION]

    assert built.buckets == {"commission": Decimal("-2.00"), "spread": Decimal("-0.50")}
    assert not built.reconciles, "costs are not a partition of P&L"


# --------------------------------------------------------------------------- #
# Validation and determinism
# --------------------------------------------------------------------------- #


def test_a_duplicated_trade_is_refused_because_the_total_would_double_with_it() -> None:
    doubled = (*trades(), trades()[0])

    with pytest.raises(AnalyticsValidationError, match="Duplicate trade ids"):
        attribute(doubled)


def test_only_the_requested_dimensions_are_computed() -> None:
    report = attribute(trades(), facts(), [AttributionDimension.ASSET])

    assert set(report.dimensions) == {AttributionDimension.ASSET}


def test_attribution_is_deterministic() -> None:
    assert attribute(trades(), facts()) == attribute(trades(), facts())


def test_buckets_are_ordered_so_two_runs_render_identically() -> None:
    built = attribute(trades(), facts()).dimensions[AttributionDimension.VENUE]

    assert list(built.buckets) == sorted(built.buckets)


def test_an_empty_trade_list_reports_no_metadata_rather_than_failing() -> None:
    report = attribute(())

    assert report.realized_pnl == Decimal("0.00")
    for built in report.dimensions.values():
        assert built.availability is Availability.NO_METADATA
        assert built.buckets == {}
