"""Regime detection: declared rules, persistence, and a state that reconstructs.

Covers the three rule shapes, the persistence rule, resumption from a recorded
state producing exactly what one pass produces, truncation leaving every label
unchanged, the profile of what each regime held, and lineage from computed
features. Labels are chosen by the tests; nothing here knows what "bull" means.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureSeries,
    compute_feature,
)
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.persistence.serializer import serialize
from alphalab.research import (
    REGIME_DEFINITION_SCHEME,
    CompositeRule,
    RegimeDefinition,
    ResearchValidationError,
    ThresholdRule,
    TrailingQuantileRule,
    canonical_regime_key,
    classify_regimes,
    initial_regime_state,
    regime_profile,
    regime_series_from_features,
)

VOL = ThresholdRule("vol", (0.8, 1.2), ("low_vol", "normal", "high_vol"))
TREND = ThresholdRule("trend", (0.0,), ("bear", "bull"))


def _signal(values: list[float], start: float = 0.0) -> list[tuple[float, float]]:
    return [(start + index, value) for index, value in enumerate(values)]


def _single(rule: ThresholdRule | TrailingQuantileRule, persistence: int = 1) -> RegimeDefinition:
    return RegimeDefinition("model", rule, persistence)


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def test_a_threshold_rule_puts_a_value_at_a_cut_point_in_the_interval_above() -> None:
    series = classify_regimes(
        _single(VOL), {"vol": _signal([0.5, 0.8, 1.19, 1.2, 3.0])}, input_id=None
    )

    assert series.raw_labels == ("low_vol", "normal", "normal", "high_vol", "high_vol")


def test_a_trailing_quantile_waits_for_its_window_and_ranks_within_it() -> None:
    rule = TrailingQuantileRule("vol", 4, (0.5,), ("calm", "turbulent"))

    series = classify_regimes(
        _single(rule), {"vol": _signal([1.0, 2.0, 3.0, 4.0, 0.5, 5.0])}, input_id=None
    )

    # Ranks: 4 of 4 at/below 4.0 -> 1.0; 1 of 4 at/below 0.5 -> 0.25; 4 of 4 -> 1.0.
    assert series.raw_labels == (None, None, None, "turbulent", "calm", "turbulent")
    assert _single(rule).warmup == 3


def test_a_composite_combines_components_through_a_total_table() -> None:
    rule = CompositeRule.of(
        (TREND, ThresholdRule("vol", (1.0,), ("calm", "turbulent"))),
        {
            ("bear", "calm"): "risk_off",
            ("bear", "turbulent"): "crisis",
            ("bull", "calm"): "risk_on",
            ("bull", "turbulent"): "risk_off",
        },
    )
    model = RegimeDefinition("macro", rule, 1)

    series = classify_regimes(
        model,
        {"trend": _signal([1.0, 1.0, -1.0, -1.0]), "vol": _signal([0.5, 2.0, 0.5, 2.0])},
        input_id=None,
    )

    assert series.raw_labels == ("risk_on", "risk_off", "risk_off", "crisis")
    assert model.labels == ("crisis", "risk_off", "risk_on")
    assert model.signals == ("trend", "vol")


def test_a_composite_table_that_leaves_a_combination_unnamed_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="every combination"):
        CompositeRule.of(
            (TREND, ThresholdRule("vol", (1.0,), ("calm", "turbulent"))),
            {("bull", "calm"): "risk_on"},
        )
    with pytest.raises(ResearchValidationError, match="at least two components"):
        CompositeRule.of((TREND,), {("bull",): "x", ("bear",): "y"})


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: ThresholdRule("vol", (), ("a",)), "at least one cut point"),
        (lambda: ThresholdRule("vol", (1.0, 1.0), ("a", "b", "c")), "not increasing"),
        (lambda: ThresholdRule("vol", (1.0,), ("a",)), "needs exactly one"),
        (lambda: ThresholdRule("vol", (math.inf,), ("a", "b")), "not finite"),
        (lambda: ThresholdRule("vol", (1.0,), ("a", " b")), "padded"),
        (lambda: ThresholdRule("1vol", (1.0,), ("a", "b")), "not a signal name"),
        (lambda: TrailingQuantileRule("vol", 1, (0.5,), ("a", "b")), "at least 2"),
        (lambda: TrailingQuantileRule("vol", 5, (1.5,), ("a", "b")), "strictly between 0 and 1"),
        (lambda: RegimeDefinition("m", VOL, 0), "at least one observation"),
        (lambda: RegimeDefinition("a@b", VOL, 1), "without '@'"),
    ],
)
def test_an_incoherent_rule_or_model_is_refused(build: object, match: str) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        build()  # type: ignore[operator]


# --------------------------------------------------------------------------- #
# Persistence and transitions
# --------------------------------------------------------------------------- #


FLICKER = [0.5, 0.5, 0.5, 1.5, 0.5, 1.5, 1.5, 1.5, 0.5, 0.5, 0.5]


def test_persistence_one_switches_at_once_and_records_every_transition() -> None:
    series = classify_regimes(_single(VOL), {"vol": _signal(FLICKER)}, input_id=None)

    assert series.regimes == tuple(series.raw_labels)
    assert [(t.index, t.previous, t.current) for t in series.transitions] == [
        (0, None, "low_vol"),
        (3, "low_vol", "high_vol"),
        (4, "high_vol", "low_vol"),
        (5, "low_vol", "high_vol"),
        (8, "high_vol", "low_vol"),
    ]


def test_persistence_ignores_a_flicker_and_confirms_a_sustained_move() -> None:
    series = classify_regimes(_single(VOL, persistence=3), {"vol": _signal(FLICKER)}, input_id=None)

    # The one-observation high at index 3 never confirms; three in a row from 5 do.
    assert series.regimes == (
        None,
        None,
        "low_vol",
        "low_vol",
        "low_vol",
        "low_vol",
        "low_vol",
        "high_vol",
        "high_vol",
        "high_vol",
        "low_vol",
    )
    assert [(t.at, t.previous, t.current) for t in series.transitions] == [
        (2.0, None, "low_vol"),
        (7.0, "low_vol", "high_vol"),
        (10.0, "high_vol", "low_vol"),
    ]


def test_resuming_from_a_recorded_state_reproduces_one_pass_exactly() -> None:
    """The reconstructable-state property, at every split point."""

    model = RegimeDefinition(
        "resumable", TrailingQuantileRule("vol", 3, (0.34, 0.67), ("low", "mid", "high")), 2
    )
    values = [1.0, 3.0, 2.0, 5.0, 4.0, 0.5, 0.7, 6.0, 6.5, 1.0, 2.0, 2.5]
    whole = classify_regimes(model, {"vol": _signal(values)}, input_id="in@1")

    for split in range(1, len(values)):
        head = classify_regimes(model, {"vol": _signal(values[:split])}, input_id="in@1")
        tail = classify_regimes(
            model,
            {"vol": _signal(values[split:], start=float(split))},
            input_id="in@1",
            initial=head.final_state,
        )
        assert head.regimes + tail.regimes == whole.regimes, split
        assert head.transitions + tail.transitions == whole.transitions, split
        assert tail.final_state == whole.final_state, split
        assert tail.final_state.state_id == whole.final_state.state_id


def test_no_label_depends_on_a_later_observation() -> None:
    model = RegimeDefinition(
        "pit", TrailingQuantileRule("vol", 4, (0.5,), ("calm", "turbulent")), 2
    )
    values = [float((index * 7) % 11) for index in range(40)]
    whole = classify_regimes(model, {"vol": _signal(values)}, input_id=None)

    for cut in range(1, len(values)):
        prefix = classify_regimes(model, {"vol": _signal(values[:cut])}, input_id=None)
        assert prefix.regimes == whole.regimes[:cut]
        assert prefix.raw_labels == whole.raw_labels[:cut]


def test_a_state_from_another_model_or_resumed_backwards_is_refused() -> None:
    head = classify_regimes(_single(VOL), {"vol": _signal([1.0, 2.0])}, input_id=None)
    other = RegimeDefinition("other", VOL, 1)

    with pytest.raises(ResearchValidationError, match="belongs to"):
        classify_regimes(
            other, {"vol": _signal([1.0], start=5.0)}, input_id=None, initial=head.final_state
        )
    with pytest.raises(ResearchValidationError, match="forwards in time"):
        classify_regimes(
            _single(VOL),
            {"vol": _signal([1.0], start=1.0)},
            input_id=None,
            initial=head.final_state,
        )


@pytest.mark.parametrize(
    ("signals", "match"),
    [
        ({}, "reads"),
        ({"vol": [(0.0, 1.0)], "extra": [(0.0, 1.0)]}, "reads"),
        ({"vol": [(1.0, 1.0), (0.0, 1.0)]}, "strictly increasing"),
        ({"vol": [(0.0, math.nan)]}, "non-finite"),
        ({"vol": []}, "no observations"),
    ],
)
def test_malformed_signals_are_refused(
    signals: dict[str, list[tuple[float, float]]], match: str
) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        classify_regimes(_single(VOL), signals, input_id=None)


def test_misaligned_signals_are_refused_rather_than_paired_across_clocks() -> None:
    rule = CompositeRule.of(
        (TREND, ThresholdRule("vol", (1.0,), ("calm", "turbulent"))),
        {
            ("bear", "calm"): "a",
            ("bear", "turbulent"): "b",
            ("bull", "calm"): "c",
            ("bull", "turbulent"): "d",
        },
    )

    with pytest.raises(ResearchValidationError, match="different instants"):
        classify_regimes(
            RegimeDefinition("m", rule, 1),
            {"trend": _signal([1.0, 1.0]), "vol": _signal([1.0, 1.0], start=0.5)},
            input_id=None,
        )


# --------------------------------------------------------------------------- #
# Identity and serialization
# --------------------------------------------------------------------------- #


def test_the_definition_identity_moves_with_every_parameter() -> None:
    reference = _single(VOL, persistence=2).definition_id

    assert reference == _single(VOL, persistence=2).definition_id
    assert reference.startswith("model@")
    for changed in (
        _single(VOL, persistence=3),
        _single(replace(VOL, cutoffs=(0.8, 1.3))),
        _single(replace(VOL, labels=("calm", "normal", "high_vol"))),
        RegimeDefinition("renamed", VOL, 2),
    ):
        assert changed.definition_id != reference


def test_the_canonical_regime_rendering_is_pinned() -> None:
    assert canonical_regime_key(_single(VOL, persistence=2)) == "\n".join(
        [
            REGIME_DEFINITION_SCHEME,
            "name='model'",
            "persistence=2",
            "threshold(signal='vol',cutoffs=(0.8, 1.2),labels=('low_vol', 'normal', 'high_vol'))",
        ]
    )


def test_a_series_and_its_state_serialize_as_deterministic_json() -> None:
    series = classify_regimes(_single(VOL, 2), {"vol": _signal(FLICKER)}, input_id="in@1")

    payload = serialize(series)

    assert json.loads(payload)["final_state"]["regime"] == "low_vol"
    assert serialize(series) == payload
    assert len(series.series_id) == 64
    assert series.initial_state_id == initial_regime_state(_single(VOL, 2)).state_id


# --------------------------------------------------------------------------- #
# What each regime held
# --------------------------------------------------------------------------- #


def test_the_profile_counts_spells_returns_and_transitions() -> None:
    series = classify_regimes(_single(VOL), {"vol": _signal(FLICKER)}, input_id=None)
    returns = {
        float(index): (0.01 if value > 1.0 else -0.01) for index, value in enumerate(FLICKER)
    }

    profile = regime_profile(series, returns)
    stats = {entry.label: entry for entry in profile.statistics}

    assert profile.classified == 11 and profile.unclassified == 0
    assert stats["high_vol"].observations == 4 and stats["high_vol"].spells == 2
    assert stats["low_vol"].observations == 7 and stats["low_vol"].spells == 3
    assert stats["high_vol"].mean_return == pytest.approx(0.01)
    assert stats["high_vol"].mean_duration == pytest.approx(2.0)
    assert stats["low_vol"].mean_duration == pytest.approx(7 / 3)
    rows: dict[str, float] = {}
    for transition in profile.transitions:
        rows[transition.previous] = rows.get(transition.previous, 0.0) + transition.probability
    assert all(total == pytest.approx(1.0) for total in rows.values())


def test_a_profile_without_returns_reports_no_return_figures() -> None:
    series = classify_regimes(_single(VOL), {"vol": _signal([0.5, 0.5])}, input_id=None)

    (only,) = regime_profile(series, None).statistics

    assert only.mean_return is None and only.return_std is None
    assert only.return_observations == 0


def test_a_series_that_confirmed_nothing_has_no_profile() -> None:
    rule = TrailingQuantileRule("vol", 5, (0.5,), ("calm", "turbulent"))
    series = classify_regimes(_single(rule), {"vol": _signal([1.0, 2.0])}, input_id=None)

    with pytest.raises(ResearchValidationError, match="confirmed no regime"):
        regime_profile(series, None)


def test_labels_by_instant_omit_what_was_not_confirmed() -> None:
    series = classify_regimes(_single(VOL, persistence=3), {"vol": _signal(FLICKER)}, input_id=None)

    labels = series.labels_by_instant()

    assert 0.0 not in labels and 1.0 not in labels
    assert labels[2.0] == "low_vol"
    assert len(labels) == 9


# --------------------------------------------------------------------------- #
# From features, with lineage
# --------------------------------------------------------------------------- #


def _feature_series(version: str | None) -> FeatureSeries:
    closes = [100.0 * (1.0 + 0.01 * ((index * 13) % 7 - 3)) for index in range(80)]
    frame = ObservationFrame(
        FeatureField.CLOSE,
        {"SPX": ObservationSeries("SPX", tuple(float(i) for i in range(80)), tuple(closes))},
        "UTC",
        version,
    )
    definition = FeatureDefinition(
        feature_id="vol_regime",
        kind=FeatureKind.VOLATILITY_REGIME,
        source_field=FeatureField.CLOSE,
        window=5,
        parameters={"long_window": 20},
    )
    return compute_feature(definition, frame)[0]


def test_a_regime_over_a_feature_names_the_features_lineage() -> None:
    model = _single(ThresholdRule("vol", (1.0,), ("calm", "turbulent")))

    traced = regime_series_from_features(model, {"vol": _feature_series("prices@1")})
    untraced = regime_series_from_features(model, {"vol": _feature_series(None)})

    assert traced.input_id is not None
    assert untraced.input_id is None
    assert traced.regimes == untraced.regimes
    assert traced.series_id != untraced.series_id
