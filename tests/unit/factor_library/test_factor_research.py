"""Factor research: ranking, neutralization, IC, decay, turnover and exposure.

Every one of these is a place where an implementation can look right and be
subtly wrong, so each is checked against a case whose answer is known by
construction rather than against its own output.

The recurring shape: a factor is *built* to have a property -- a perfect
ordering, a pure group effect, a known beta -- and the diagnostic is required
to recover exactly that. A perfect predictor must report a rank IC of 1.0; a
factor that is entirely a sector effect must be exactly zero after group
neutralization; a book that never changes must report zero turnover.
"""

import math

import pytest

from alphalab.common.statistics import RankMethod, TieBreak
from alphalab.data.feed import Bar as WireBar
from alphalab.factor_library import (
    FactorInputError,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeaturePanel,
    FeatureScope,
    ObservationFrame,
    TurnoverConvention,
    bucket_panel,
    compute_panel,
    factor_decay,
    factor_exposure,
    factor_turnover,
    forward_returns,
    information_coefficient,
    neutralize_beta,
    neutralize_group,
    neutralize_mean,
    observations_from_records,
    percentile_rank_panel,
    rank_panel,
    weights_from_buckets,
)

DAY = 86400.0
START = 1_735_689_600.0


def _records(paths: dict[str, list[float]]) -> list[WireBar]:
    return [
        WireBar(symbol, START + index * DAY, close, close, close, close, 1000.0)
        for symbol, closes in paths.items()
        for index, close in enumerate(closes)
    ]


def _frame(paths: dict[str, list[float]], version: str = "ds@v1") -> ObservationFrame:
    return observations_from_records(_records(paths), FeatureField.CLOSE, "UTC", version)


def _panel_of(rows: dict[float, dict[str, float]]) -> FeaturePanel:
    """A panel built directly, for tests about cross-sectional operations.

    The definition is a real one so the panel is a real panel; only the values
    are chosen rather than computed, which is what lets a test state the answer
    in advance.
    """

    definition = FeatureDefinition("f", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=1)
    return FeaturePanel(
        definition=definition,
        dataset_version="ds@v1",
        timezone_name="UTC",
        rows={stamp: dict(row) for stamp, row in sorted(rows.items())},
        symbols=tuple(sorted({asset for row in rows.values() for asset in row})),
    )


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


def test_ranking_runs_from_one_upward_and_records_its_tie_method() -> None:
    panel = _panel_of({START: {"A": 3.0, "B": 1.0, "C": 2.0}})

    ranked = rank_panel(panel, RankMethod.AVERAGE)

    assert ranked.panel.cross_section(START) == {"A": 3.0, "B": 1.0, "C": 2.0}
    assert ranked.counts == {START: 3}
    assert "rank(method=AVERAGE" in ranked.panel.lineage


def test_percentile_ranks_are_comparable_across_universe_sizes() -> None:
    """The property a raw rank does not have, asserted on two sizes at once."""

    panel = _panel_of(
        {
            START: {"A": 1.0, "B": 2.0, "C": 3.0},
            START + DAY: {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0},
        }
    )

    ranked = percentile_rank_panel(panel)

    assert ranked.panel.cross_section(START)["A"] == 0.0
    assert ranked.panel.cross_section(START)["C"] == 1.0
    assert ranked.panel.cross_section(START + DAY)["A"] == 0.0
    assert ranked.panel.cross_section(START + DAY)["E"] == 1.0


def test_a_thin_cross_section_is_omitted_and_the_thinning_is_visible() -> None:
    panel = _panel_of({START: {"A": 1.0, "B": 2.0, "C": 3.0}, START + DAY: {"A": 1.0, "B": 2.0}})

    ranked = rank_panel(panel, minimum_assets=3)

    assert set(ranked.counts) == {START}
    assert ranked.minimum_cross_section == 3
    assert START + DAY not in ranked.panel.rows


def test_ranking_refuses_when_no_instant_is_large_enough() -> None:
    panel = _panel_of({START: {"A": 1.0, "B": 2.0}})
    with pytest.raises(FactorInputError, match="No instant in the panel held at least 5"):
        rank_panel(panel, minimum_assets=5)


def test_bucketing_puts_the_extremes_in_the_end_buckets() -> None:
    panel = _panel_of({START: {f"S{index}": float(index) for index in range(10)}})

    bucketed = bucket_panel(panel, 5, TieBreak.LOW)
    section = bucketed.cross_section(START)

    assert section["S0"] == 0.0
    assert section["S9"] == 4.0
    assert sorted(section.values()) == [0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0]


def test_a_transform_chain_is_recorded_in_order() -> None:
    panel = _panel_of({START: {f"S{index}": float(index) for index in range(6)}})

    chained = bucket_panel(rank_panel(panel).panel, 3)

    assert [step.operation for step in chained.transforms] == ["rank", "bucket"]
    assert chained.lineage.startswith(panel.definition.feature_version)
    assert "rank(" in chained.lineage and "bucket(" in chained.lineage


# --------------------------------------------------------------------------- #
# Neutralization
# --------------------------------------------------------------------------- #


def test_mean_neutralization_leaves_a_cross_section_summing_to_zero() -> None:
    panel = _panel_of({START: {"A": 1.0, "B": 2.0, "C": 6.0}})

    report = neutralize_mean(panel)
    section = report.panel.cross_section(START)

    assert sum(section.values()) == pytest.approx(0.0)
    assert section["A"] == pytest.approx(-2.0)
    assert report.instants_neutralized == 1
    assert report.mean_absolute_removed == pytest.approx(3.0)


def test_group_neutralization_removes_a_pure_group_effect_exactly() -> None:
    """A factor that *is* its sector reads exactly zero afterwards."""

    panel = _panel_of({START: {"A": 5.0, "B": 5.0, "C": 9.0, "D": 9.0}})
    groups = {"A": "tech", "B": "tech", "C": "energy", "D": "energy"}

    report = neutralize_group(panel, groups)

    assert all(value == pytest.approx(0.0) for value in report.panel.cross_section(START).values())


def test_group_neutralization_keeps_within_group_differences() -> None:
    panel = _panel_of({START: {"A": 4.0, "B": 6.0, "C": 10.0, "D": 20.0}})
    groups = {"A": "tech", "B": "tech", "C": "energy", "D": "energy"}

    section = neutralize_group(panel, groups).panel.cross_section(START)

    assert section["A"] == pytest.approx(-1.0)
    assert section["B"] == pytest.approx(1.0)
    assert section["C"] == pytest.approx(-5.0)
    assert section["D"] == pytest.approx(5.0)


def test_an_asset_with_no_group_is_refused_rather_than_pooled() -> None:
    panel = _panel_of({START: {"A": 1.0, "B": 2.0}})
    with pytest.raises(FactorInputError, match="does not name"):
        neutralize_group(panel, {"A": "tech"})


def test_a_group_of_one_is_skipped_rather_than_set_to_zero() -> None:
    """Demeaning a singleton deletes the asset; it does not neutralize it."""

    panel = _panel_of({START: {"A": 1.0, "B": 2.0, "C": 9.0}})
    groups = {"A": "tech", "B": "tech", "C": "lonely"}

    section = neutralize_group(panel, groups).panel.cross_section(START)

    assert set(section) == {"A", "B"}
    assert "C" not in section


def test_beta_neutralization_removes_exactly_the_fitted_exposure() -> None:
    """A factor that is a perfect linear function of the exposure reads zero."""

    exposures = {START: {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}}
    panel = _panel_of({START: {asset: 3.0 + 2.0 * x for asset, x in exposures[START].items()}})

    report = neutralize_beta(panel, exposures)

    assert all(
        value == pytest.approx(0.0, abs=1e-12)
        for value in report.panel.cross_section(START).values()
    )
    assert report.instants_neutralized == 1


def test_beta_neutralization_keeps_the_part_the_exposure_does_not_explain() -> None:
    exposures = {START: {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}}
    panel = _panel_of({START: {"A": 5.0, "B": 7.0, "C": 9.0, "D": 20.0}})

    section = neutralize_beta(panel, exposures).panel.cross_section(START)

    assert sum(section.values()) == pytest.approx(0.0, abs=1e-12)
    assert section["D"] > 0.0, "the outlier keeps its unexplained part"


def test_beta_neutralization_refuses_a_missing_exposure() -> None:
    panel = _panel_of({START: {"A": 1.0, "B": 2.0, "C": 3.0}})

    with pytest.raises(FactorInputError, match="exposures do not"):
        neutralize_beta(panel, {START: {"A": 1.0, "B": 2.0}})
    with pytest.raises(FactorInputError, match="holds a cross-section at"):
        neutralize_beta(panel, {START + DAY: {"A": 1.0, "B": 2.0, "C": 3.0}})


def test_a_two_asset_beta_fit_is_refused_because_its_residuals_are_zero() -> None:
    panel = _panel_of({START: {"A": 1.0, "B": 2.0}})
    with pytest.raises(FactorInputError, match="two-point fit"):
        neutralize_beta(panel, {START: {"A": 1.0, "B": 2.0}}, minimum_assets=2)


def test_a_constant_exposure_is_skipped_rather_than_divided_by_zero() -> None:
    panel = _panel_of(
        {START: {"A": 1.0, "B": 2.0, "C": 3.0}, START + DAY: {"A": 1.0, "B": 5.0, "C": 9.0}}
    )
    exposures = {
        START: {"A": 1.0, "B": 1.0, "C": 1.0},
        START + DAY: {"A": 1.0, "B": 2.0, "C": 3.0},
    }

    report = neutralize_beta(panel, exposures)

    assert report.instants_neutralized == 1
    assert report.instants_skipped == 1


def test_the_neutralization_transform_is_recorded() -> None:
    panel = _panel_of({START: {"A": 1.0, "B": 2.0, "C": 3.0}})
    assert "neutralize_mean" in neutralize_mean(panel).panel.lineage


# --------------------------------------------------------------------------- #
# Information coefficient
# --------------------------------------------------------------------------- #


def _perfect_panel_and_returns(
    instants: int = 6, assets: int = 6
) -> tuple[FeaturePanel, ObservationFrame]:
    """A factor whose ordering exactly matches the following period's return."""

    paths: dict[str, list[float]] = {}
    for index in range(assets):
        growth = 1.0 + 0.01 * (index + 1)
        paths[f"S{index}"] = [100.0 * growth**step for step in range(instants)]

    frame = _frame(paths)
    definition = FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=1)
    return compute_panel(definition, frame), frame


def test_a_perfect_predictor_reports_a_rank_ic_of_one() -> None:
    panel, frame = _perfect_panel_and_returns()

    result = information_coefficient(panel, forward_returns(frame, 1), minimum_assets=3)

    assert result.mean_rank == pytest.approx(1.0)
    assert result.hit_rate == pytest.approx(1.0)
    assert result.instants_measured > 0
    assert result.observations == result.instants_measured * 6


def test_the_ic_reports_the_sample_it_was_measured_on() -> None:
    panel, frame = _perfect_panel_and_returns()

    result = information_coefficient(panel, forward_returns(frame, 1), minimum_assets=3)

    assert result.horizon == 1
    assert result.minimum_assets == 3
    assert result.dataset_version == "ds@v1"
    assert len(result.per_instant_rank) == result.instants_measured


def test_a_cross_section_below_the_threshold_is_counted_not_averaged_in() -> None:
    panel, frame = _perfect_panel_and_returns(assets=4)

    result = information_coefficient(panel, forward_returns(frame, 1), minimum_assets=10)

    assert result.instants_measured == 0
    assert result.instants_skipped > 0
    assert result.mean_rank is None
    assert result.mean_pearson is None
    assert result.hit_rate is None
    assert not result.is_measurable


def test_a_two_asset_ic_is_refused_because_it_is_always_plus_or_minus_one() -> None:
    panel, frame = _perfect_panel_and_returns()
    with pytest.raises(FactorInputError, match="perfectly correlated"):
        information_coefficient(panel, forward_returns(frame, 1), minimum_assets=2)


def test_correlating_across_two_datasets_is_refused() -> None:
    panel, _ = _perfect_panel_and_returns()
    other = _frame({f"S{index}": [100.0, 101.0, 102.0] for index in range(6)}, "ds@v2")

    with pytest.raises(FactorInputError, match="measure one dataset's factor"):
        information_coefficient(panel, forward_returns(other, 1), minimum_assets=3)


def test_a_constant_cross_section_is_unmeasurable_rather_than_zero() -> None:
    """Every asset scoring the same is undefined, and is counted as skipped."""

    panel, frame = _perfect_panel_and_returns()
    flat = _panel_of({stamp: dict.fromkeys(panel.symbols, 1.0) for stamp in panel.timestamps})
    realized = forward_returns(frame, 1)

    result = information_coefficient(flat, realized, minimum_assets=3)

    assert result.instants_measured == 0
    assert result.instants_skipped == len(flat.timestamps)


# --------------------------------------------------------------------------- #
# Forward returns
# --------------------------------------------------------------------------- #


def test_a_forward_return_looks_exactly_the_horizon_ahead() -> None:
    frame = _frame({"A": [100.0, 110.0, 121.0, 133.1]})

    realized = forward_returns(frame, 2)

    assert realized.cross_section(START)["A"] == pytest.approx(121.0 / 100.0 - 1.0)
    assert realized.cross_section(START + DAY)["A"] == pytest.approx(133.1 / 110.0 - 1.0)
    assert realized.timestamps == (START, START + DAY)


def test_the_tail_of_the_sample_has_no_forward_return_and_says_how_much() -> None:
    frame = _frame({"A": [1.0, 2.0, 3.0, 4.0, 5.0]})

    realized = forward_returns(frame, 2)

    assert len(realized) == 3
    assert realized.unrealized_instants == 2


def test_a_zero_horizon_is_refused() -> None:
    frame = _frame({"A": [1.0, 2.0, 3.0]})
    with pytest.raises(FactorInputError, match="horizon of zero is the present"):
        forward_returns(frame, 0)


def test_a_horizon_longer_than_the_sample_is_refused() -> None:
    frame = _frame({"A": [1.0, 2.0, 3.0]})
    with pytest.raises(FactorInputError, match="no forward return at that horizon"):
        forward_returns(frame, 5)


# --------------------------------------------------------------------------- #
# Decay
# --------------------------------------------------------------------------- #


def test_a_decay_profile_measures_every_horizon_on_its_own_sample() -> None:
    panel, frame = _perfect_panel_and_returns(instants=12)

    profile = factor_decay(panel, frame, [1, 2, 4], minimum_assets=3)

    assert sorted(profile.horizons) == [1, 2, 4]
    counts = profile.instants_by_horizon
    assert counts[1] > counts[2] > counts[4], "a longer horizon realizes fewer instants"


def test_a_persistently_correct_factor_never_turns_negative() -> None:
    panel, frame = _perfect_panel_and_returns(instants=12)

    profile = factor_decay(panel, frame, [1, 2, 4], minimum_assets=3)

    assert profile.first_negative_horizon is None
    assert all(value == pytest.approx(1.0) for value in profile.mean_rank_by_horizon.values())


def test_a_repeated_horizon_is_refused() -> None:
    panel, frame = _perfect_panel_and_returns()
    with pytest.raises(FactorInputError, match="repeat one"):
        factor_decay(panel, frame, [1, 1])


def test_a_decay_across_two_datasets_is_refused() -> None:
    panel, _ = _perfect_panel_and_returns()
    other = _frame({f"S{index}": [100.0, 101.0, 102.0] for index in range(6)}, "ds@v2")
    with pytest.raises(FactorInputError, match="across two datasets"):
        factor_decay(panel, other, [1])


# --------------------------------------------------------------------------- #
# Turnover
# --------------------------------------------------------------------------- #


def test_a_book_that_never_changes_has_zero_turnover() -> None:
    book = {"A": 0.5, "B": 0.5}
    result = factor_turnover({START: book, START + DAY: book, START + 2 * DAY: book})

    assert result.mean_turnover == 0.0
    assert result.periods_measured == 2
    assert result.entries == 0
    assert result.exits == 0


def test_a_complete_swap_is_one_under_the_one_way_convention() -> None:
    """Out of A into B: |0-1| + |1-0| = 2 absolute, 1 one-way."""

    weights = {START: {"A": 1.0}, START + DAY: {"B": 1.0}}

    assert factor_turnover(weights, TurnoverConvention.ONE_WAY).mean_turnover == 1.0
    assert factor_turnover(weights, TurnoverConvention.ABSOLUTE_CHANGE).mean_turnover == 2.0


def test_an_asset_leaving_the_book_counts_as_a_trade() -> None:
    """Treating it as absent would report a factor that churns as never trading."""

    weights = {START: {"A": 0.5, "B": 0.5}, START + DAY: {"A": 1.0}}

    result = factor_turnover(weights, TurnoverConvention.ABSOLUTE_CHANGE)

    assert result.mean_turnover == pytest.approx(1.0)
    assert result.exits == 1
    assert result.entries == 0


def test_turnover_needs_a_transition_to_measure() -> None:
    with pytest.raises(FactorInputError, match="single book has not traded"):
        factor_turnover({START: {"A": 1.0}})


def test_a_long_short_book_is_dollar_neutral_by_construction() -> None:
    panel = _panel_of({START: {f"S{index}": float(index) for index in range(10)}})
    bucketed = bucket_panel(panel, 5)

    book = weights_from_buckets(bucketed, 5)[START]

    assert sum(book.values()) == pytest.approx(0.0)
    assert sum(abs(weight) for weight in book.values()) == pytest.approx(2.0)


def test_weights_from_buckets_refuses_a_panel_bucketed_differently() -> None:
    panel = _panel_of({START: {f"S{index}": float(index) for index in range(10)}})
    bucketed = bucket_panel(panel, 5)

    with pytest.raises(FactorInputError, match="was not bucketed into 3"):
        weights_from_buckets(bucketed, 3)


# --------------------------------------------------------------------------- #
# Exposure
# --------------------------------------------------------------------------- #


def test_exposure_reports_net_and_gross_separately_by_group() -> None:
    """A group that is net flat because it is long and short is still exposed."""

    weights = {START: {"A": 1.0, "B": -1.0, "C": 0.5, "D": -0.5}}
    groups = {"A": "tech", "B": "tech", "C": "energy", "D": "energy"}

    report = factor_exposure(weights, groups)

    assert report.by_group["tech"] == pytest.approx(0.0)
    assert report.gross_by_group["tech"] == pytest.approx(2.0)
    assert report.largest_group == "tech"
    assert report.net_exposure == pytest.approx(0.0)
    assert report.gross_exposure == pytest.approx(3.0)


def test_concentration_reads_one_for_a_single_name_book() -> None:
    report = factor_exposure({START: {"A": 1.0}})
    assert report.concentration == pytest.approx(1.0)


def test_a_mean_exposure_averages_over_every_instant_not_only_held_ones() -> None:
    """Averaging only over held instants would report position size, not exposure."""

    weights = {START: {"A": 1.0}, START + DAY: {"B": 1.0}}

    report = factor_exposure(weights)

    assert report.by_asset["A"] == pytest.approx(0.5)
    assert report.by_asset["B"] == pytest.approx(0.5)


def test_exposure_refuses_an_ungrouped_asset() -> None:
    with pytest.raises(FactorInputError, match="does not name"):
        factor_exposure({START: {"A": 1.0, "B": 1.0}}, {"A": "tech"})


def test_the_whole_chain_composes_from_a_dataset() -> None:
    """Panel to ranks to buckets to weights to turnover and exposure."""

    paths = {
        f"S{index}": [100.0 * (1.0 + 0.005 * index) ** step for step in range(15)]
        for index in range(10)
    }
    frame = _frame(paths)
    panel = compute_panel(
        FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=5), frame
    )

    bucketed = bucket_panel(rank_panel(panel).panel, 5)
    weights = weights_from_buckets(bucketed, 5)
    turnover = factor_turnover(weights)
    exposure = factor_exposure(weights, dict.fromkeys(panel.symbols, "all"))

    assert turnover.periods_measured == len(weights) - 1
    assert exposure.net_exposure == pytest.approx(0.0)
    assert math.isfinite(turnover.mean_turnover)


def test_a_cross_sectional_definition_composes_with_ranking() -> None:
    paths = {f"S{index}": [100.0 + index, 101.0 + index, 102.0 + index] for index in range(5)}
    frame = _frame(paths)
    definition = FeatureDefinition(
        "xs",
        FeatureKind.CROSS_SECTIONAL_ZSCORE,
        FeatureField.CLOSE,
        scope=FeatureScope.CROSS_SECTIONAL,
    )

    panel = compute_panel(definition, frame)

    assert sum(panel.cross_section(START).values()) == pytest.approx(0.0, abs=1e-12)
    assert rank_panel(panel).counts[START] == 5
