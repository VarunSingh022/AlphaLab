"""Expected against paper against live: alignment, tolerance and missing data.

Three properties carry the module, and each has a section below: the alignment
is declared rather than guessed, a missing value never becomes a zero, and money
is compared per currency and never summed across two.
"""

from decimal import Decimal

import pytest

from alphalab.core.enums import Side
from alphalab.lifecycle import (
    MONETARY_METRICS,
    AlignmentKey,
    ComparisonMetric,
    ComparisonOutcome,
    ComparisonSource,
    FillObservation,
    LifecycleInputError,
    RunObservations,
    Tolerance,
    TradeObservation,
    compare_expected_paper_live,
    compare_runs,
)
from alphalab.portfolio.amounts import CurrencyAmounts

TOLERANCES = {
    ComparisonMetric.TRADE_QUANTITY: Tolerance(absolute=Decimal("0")),
    ComparisonMetric.TRADE_PRICE: Tolerance(absolute=Decimal("0.02")),
    ComparisonMetric.FILL_QUANTITY: Tolerance(absolute=Decimal("0")),
    ComparisonMetric.FILL_PRICE: Tolerance(absolute=Decimal("0.02")),
    ComparisonMetric.SLIPPAGE: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.EXECUTION_LATENCY: Tolerance(absolute=Decimal("0.5")),
    ComparisonMetric.REALIZED_PNL: Tolerance(absolute=Decimal("1.00")),
    ComparisonMetric.EXPOSURE: Tolerance(relative=Decimal("0.01")),
}


def trade(key: str, quantity: str = "10", price: str = "100.00") -> TradeObservation:
    return TradeObservation(key, "asset-a", Side.BUY, Decimal(quantity), Decimal(price), 1.0)


def fill(
    key: str,
    quantity: str = "10",
    price: str = "100.00",
    slippage: str | None = "0.01",
    latency: str | None = "0.10",
) -> FillObservation:
    return FillObservation(
        key=key,
        asset_id="asset-a",
        side=Side.BUY,
        quantity=Decimal(quantity),
        price=Decimal(price),
        commission=Decimal("1.00"),
        filled_at=1.0,
        slippage=None if slippage is None else Decimal(slippage),
        latency_seconds=None if latency is None else Decimal(latency),
    )


def run(source: ComparisonSource, **overrides: object) -> RunObservations:
    arguments: dict[str, object] = {
        "source": source,
        "trades": (trade("k1"),),
        "fills": (fill("k1"),),
        "realized_pnl": CurrencyAmounts.single(Decimal("250.00"), "USD"),
        "exposure": CurrencyAmounts.single(Decimal("1000.00"), "USD"),
    }
    arguments.update(overrides)
    return RunObservations(**arguments)  # type: ignore[arg-type]


class TestMatching:
    def test_two_identical_runs_agree_exactly(self) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED), run(ComparisonSource.PAPER), TOLERANCES
        )
        assert result.agrees
        assert result.material == ()
        assert result.missing == ()
        assert result.incomparable == ()
        assert all(entry.outcome is ComparisonOutcome.EXACT_MATCH for entry in result.entries)

    def test_a_small_difference_inside_tolerance_is_distinguished_from_an_exact_match(
        self,
    ) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, fills=(fill("k1", price="100.01"),)),
            TOLERANCES,
        )
        price = result.entries_for(ComparisonMetric.FILL_PRICE)[0]
        assert price.outcome is ComparisonOutcome.WITHIN_TOLERANCE
        assert result.agrees

    def test_a_difference_beyond_tolerance_is_material_and_says_by_how_much(self) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, fills=(fill("k1", price="100.50"),)),
            TOLERANCES,
        )
        price = result.entries_for(ComparisonMetric.FILL_PRICE)[0]
        assert price.outcome is ComparisonOutcome.MATERIAL_DIFFERENCE
        assert price.expected == Decimal("100.00")
        assert price.observed == Decimal("100.50")
        assert "0.50" in price.detail
        assert not result.agrees


class TestMissingData:
    def test_a_trade_only_the_observed_side_has_is_missing_expected(self) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED, trades=()),
            run(ComparisonSource.LIVE),
            TOLERANCES,
        )
        entry = result.entries_for(ComparisonMetric.TRADE_QUANTITY)[0]
        assert entry.outcome is ComparisonOutcome.MISSING_EXPECTED
        assert entry.expected is None
        assert entry.observed == Decimal("10")

    def test_a_trade_only_the_reference_side_has_is_missing_observed(self) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, trades=()),
            TOLERANCES,
        )
        entry = result.entries_for(ComparisonMetric.TRADE_QUANTITY)[0]
        assert entry.outcome is ComparisonOutcome.MISSING_OBSERVED
        assert entry.observed is None

    def test_a_missing_value_is_never_turned_into_a_zero(self) -> None:
        """The property the whole outcome vocabulary exists for."""

        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, trades=()),
            TOLERANCES,
        )
        for entry in result.missing:
            assert entry.expected is None or entry.observed is None
            assert Decimal("0") not in (entry.expected, entry.observed)

    def test_a_missing_value_never_reads_as_a_match(self) -> None:
        """P&L and exposure were supplied on both sides and still match; the
        trade and fill metrics the observed side did not observe do not."""

        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, fills=None, trades=None),
            TOLERANCES,
        )
        assert not result.agrees
        unobserved = [entry for entry in result.entries if entry.metric not in MONETARY_METRICS]
        assert unobserved
        assert all(not entry.is_matched for entry in unobserved)

    def test_an_unobserved_side_is_reported_as_not_comparable_with_a_reason(self) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, fills=None),
            TOLERANCES,
        )
        entries = result.entries_for(ComparisonMetric.FILL_QUANTITY)
        assert len(entries) == 1
        assert entries[0].outcome is ComparisonOutcome.NOT_COMPARABLE
        assert "did not observe fills" in entries[0].detail

    def test_slippage_a_venue_does_not_measure_is_missing_rather_than_zero(self) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, fills=(fill("k1", slippage=None),)),
            TOLERANCES,
        )
        entry = result.entries_for(ComparisonMetric.SLIPPAGE)[0]
        assert entry.outcome is ComparisonOutcome.MISSING_OBSERVED
        assert entry.observed is None
        assert "does not measure slippage" in entry.detail

    def test_latency_a_simulator_does_not_measure_is_missing_rather_than_zero(self) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED, fills=(fill("k1", latency=None),)),
            run(ComparisonSource.LIVE),
            TOLERANCES,
        )
        entry = result.entries_for(ComparisonMetric.EXECUTION_LATENCY)[0]
        assert entry.outcome is ComparisonOutcome.MISSING_EXPECTED
        assert entry.expected is None


class TestTolerancesAreExplicit:
    def test_a_metric_with_no_tolerance_is_not_comparable(self) -> None:
        partial = {ComparisonMetric.TRADE_QUANTITY: Tolerance(absolute=Decimal("0"))}
        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.PAPER, fills=(fill("k1", price="200.00"),)),
            partial,
        )
        price = result.entries_for(ComparisonMetric.FILL_PRICE)[0]
        assert price.outcome is ComparisonOutcome.NOT_COMPARABLE
        assert "no tolerance was stated" in price.detail
        assert not result.agrees

    def test_an_exact_match_still_needs_a_tolerance_to_be_called_a_match(self) -> None:
        """Equality is not a default; it is reported as incomparable."""

        result = compare_runs(run(ComparisonSource.EXPECTED), run(ComparisonSource.PAPER), {})
        assert all(entry.outcome is ComparisonOutcome.NOT_COMPARABLE for entry in result.entries)

    def test_missing_is_reported_before_a_missing_tolerance_is_looked_for(self) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED, trades=()), run(ComparisonSource.LIVE), {}
        )
        entry = result.entries_for(ComparisonMetric.TRADE_QUANTITY)[0]
        assert entry.outcome is ComparisonOutcome.MISSING_EXPECTED


class TestDimensions:
    def test_money_is_compared_per_currency_and_never_summed(self) -> None:
        left = run(
            ComparisonSource.EXPECTED,
            realized_pnl=CurrencyAmounts({"USD": Decimal("100.00"), "JPY": Decimal("5000.00")}),
        )
        right = run(
            ComparisonSource.LIVE,
            realized_pnl=CurrencyAmounts({"USD": Decimal("100.00"), "JPY": Decimal("5000.00")}),
        )
        result = compare_runs(left, right, TOLERANCES)
        entries = result.entries_for(ComparisonMetric.REALIZED_PNL)
        assert [entry.key for entry in entries] == ["JPY", "USD"]
        assert all(entry.outcome is ComparisonOutcome.EXACT_MATCH for entry in entries)

    def test_a_currency_absent_from_a_supplied_accumulation_is_a_real_zero(self) -> None:
        """``CurrencyAmounts`` says an unbooked currency is absent, not missing."""

        left = run(
            ComparisonSource.EXPECTED, realized_pnl=CurrencyAmounts.single(Decimal("0.00"), "USD")
        )
        right = run(
            ComparisonSource.LIVE,
            realized_pnl=CurrencyAmounts({"USD": Decimal("0.00"), "JPY": Decimal("0.40")}),
        )
        result = compare_runs(left, right, TOLERANCES)
        jpy = next(
            entry
            for entry in result.entries_for(ComparisonMetric.REALIZED_PNL)
            if entry.key == "JPY"
        )
        assert jpy.expected == Decimal("0.00")
        assert jpy.outcome is ComparisonOutcome.WITHIN_TOLERANCE

    def test_an_absent_accumulation_is_missing_rather_than_empty(self) -> None:
        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, realized_pnl=None),
            TOLERANCES,
        )
        entries = result.entries_for(ComparisonMetric.REALIZED_PNL)
        assert len(entries) == 1
        assert entries[0].outcome is ComparisonOutcome.NOT_COMPARABLE
        assert entries[0].expected is None and entries[0].observed is None

    def test_a_pnl_difference_in_one_currency_does_not_offset_another(self) -> None:
        left = run(
            ComparisonSource.EXPECTED,
            realized_pnl=CurrencyAmounts({"USD": Decimal("100.00"), "JPY": Decimal("-100.00")}),
        )
        right = run(
            ComparisonSource.LIVE,
            realized_pnl=CurrencyAmounts({"USD": Decimal("200.00"), "JPY": Decimal("-200.00")}),
        )
        result = compare_runs(left, right, TOLERANCES)
        material = [entry.key for entry in result.material]
        assert sorted(material) == ["JPY", "USD"]

    def test_the_monetary_metrics_are_the_two_keyed_by_currency(self) -> None:
        assert MONETARY_METRICS == (ComparisonMetric.REALIZED_PNL, ComparisonMetric.EXPOSURE)

    def test_latency_is_a_decimal_of_seconds(self) -> None:
        observation = fill("k1", latency="1.25")
        assert observation.latency_seconds == Decimal("1.25")
        assert isinstance(observation.latency_seconds, Decimal)


class TestAlignment:
    def test_records_line_up_by_key_regardless_of_order(self) -> None:
        left = run(ComparisonSource.EXPECTED, trades=(trade("a"), trade("b")))
        right = run(ComparisonSource.LIVE, trades=(trade("b"), trade("a")))
        result = compare_runs(left, right, TOLERANCES)
        assert all(
            entry.outcome is ComparisonOutcome.EXACT_MATCH
            for entry in result.entries_for(ComparisonMetric.TRADE_QUANTITY)
        )

    def test_a_key_used_twice_is_refused_rather_than_silently_dropped(self) -> None:
        with pytest.raises(LifecycleInputError, match="share the alignment key"):
            compare_runs(
                run(ComparisonSource.EXPECTED, trades=(trade("a"), trade("a"))),
                run(ComparisonSource.LIVE),
                TOLERANCES,
            )

    def test_two_records_under_one_key_naming_different_instruments_are_incomparable(
        self,
    ) -> None:
        other = TradeObservation("k1", "asset-z", Side.BUY, Decimal("10"), Decimal("100.00"), 1.0)
        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, trades=(other,)),
            TOLERANCES,
        )
        entry = result.entries_for(ComparisonMetric.TRADE_QUANTITY)[0]
        assert entry.outcome is ComparisonOutcome.NOT_COMPARABLE
        assert "different instruments" in entry.detail

    def test_two_records_under_one_key_on_opposite_sides_are_incomparable(self) -> None:
        other = TradeObservation("k1", "asset-a", Side.SELL, Decimal("10"), Decimal("100.00"), 1.0)
        result = compare_runs(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.LIVE, trades=(other,)),
            TOLERANCES,
        )
        assert all(
            entry.outcome is ComparisonOutcome.NOT_COMPARABLE
            for entry in result.entries_for(ComparisonMetric.TRADE_PRICE)
        )

    def test_a_blank_key_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="key cannot be empty"):
            TradeObservation("", "a", Side.BUY, Decimal("1"), Decimal("1"), 1.0)
        with pytest.raises(LifecycleInputError, match="key cannot be empty"):
            FillObservation(" ", "a", Side.BUY, Decimal("1"), Decimal("1"), Decimal("0"), 1.0)

    def test_there_are_exactly_two_alignment_modes(self) -> None:
        assert set(AlignmentKey) == {AlignmentKey.ORDER_ID, AlignmentKey.ASSET_AND_TIME}


class TestPairsAndThreeWay:
    def test_a_source_cannot_be_compared_against_itself(self) -> None:
        with pytest.raises(LifecycleInputError, match="against itself"):
            compare_runs(run(ComparisonSource.PAPER), run(ComparisonSource.PAPER), TOLERANCES)

    def test_the_three_pairs_are_produced_and_labelled(self) -> None:
        result = compare_expected_paper_live(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.PAPER),
            run(ComparisonSource.LIVE),
            TOLERANCES,
        )
        assert result.expected_vs_paper.left is ComparisonSource.EXPECTED
        assert result.expected_vs_paper.right is ComparisonSource.PAPER
        assert result.expected_vs_live.right is ComparisonSource.LIVE
        assert result.paper_vs_live.left is ComparisonSource.PAPER
        assert result.agrees

    def test_a_three_way_comparison_locates_where_a_divergence_started(self) -> None:
        """Live disagrees with both; paper agrees with the backtest."""

        result = compare_expected_paper_live(
            run(ComparisonSource.EXPECTED),
            run(ComparisonSource.PAPER),
            run(ComparisonSource.LIVE, fills=(fill("k1", price="105.00"),)),
            TOLERANCES,
        )
        assert result.expected_vs_paper.agrees
        assert not result.expected_vs_live.agrees
        assert not result.paper_vs_live.agrees
        assert not result.agrees
        assert len(result.material) == 2

    def test_mislabelled_sources_are_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="cannot report which side is which"):
            compare_expected_paper_live(
                run(ComparisonSource.EXPECTED),
                run(ComparisonSource.LIVE),
                run(ComparisonSource.LIVE),
                TOLERANCES,
            )

    def test_the_left_source_is_the_expected_side_even_for_paper_vs_live(self) -> None:
        result = compare_runs(
            run(ComparisonSource.PAPER, trades=()),
            run(ComparisonSource.LIVE),
            TOLERANCES,
        )
        entry = result.entries_for(ComparisonMetric.TRADE_QUANTITY)[0]
        assert entry.outcome is ComparisonOutcome.MISSING_EXPECTED


class TestDeterminism:
    def test_the_same_inputs_produce_an_equal_comparison(self) -> None:
        left, right = run(ComparisonSource.EXPECTED), run(ComparisonSource.LIVE)
        assert compare_runs(left, right, TOLERANCES) == compare_runs(left, right, TOLERANCES)

    def test_entries_are_ordered_by_metric_then_key(self) -> None:
        left = run(ComparisonSource.EXPECTED, trades=(trade("z"), trade("a"), trade("m")))
        right = run(ComparisonSource.LIVE, trades=(trade("m"), trade("z"), trade("a")))
        result = compare_runs(left, right, TOLERANCES)
        order = list(ComparisonMetric)
        pairs = [(order.index(entry.metric), entry.key) for entry in result.entries]
        assert pairs == sorted(pairs)

    def test_comparing_changes_neither_side(self) -> None:
        left, right = run(ComparisonSource.EXPECTED), run(ComparisonSource.LIVE)
        before_left, before_right = left, right
        compare_runs(left, right, TOLERANCES)
        assert left == before_left and right == before_right
