"""Point-in-time panels: what was known at each event, beside the label it earned (v4.3, GAP-13).

* **A row is an event's features at its start beside its label**, in start
  then subject order; an event a feature has no value for at its start is
  counted, never filled.
* **The counterfactual future** (plan I.3, mandatory for every dataset
  builder): changing every price after a boundary changes nothing known by it.
  The panel known by the boundary is the same panel, identity included, with
  the same weights and the same training set; the change still reached the
  rows it could.
* **Research's splits purge a panel**: in every fold of a purged
  cross-validation, no training row before the validation has a label reaching
  into it.
* **Rows are taken by when their label was known and by their start**, and
  weighed by their uniqueness among the rows taken.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from alphalab.factor_library import FeatureDefinition, FeatureField, FeatureKind, FeaturePanel
from alphalab.factor_library.compute import compute_panel
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.labels import (
    LabelSet,
    RollingVolatility,
    TripleBarrier,
    VerticalOutcome,
    average_uniqueness,
    triple_barrier_labels,
)
from alphalab.ml.point_in_time import PointInTimePanel, SampleWeights, point_in_time_panel
from alphalab.research import CVMethod, cross_validation_splits

DAY = 86400.0
SUBJECTS = ("A", "B", "C")
COUNT = 80
BOUNDARY = 40 * DAY
BARRIER = TripleBarrier(
    horizon=5,
    upper=1.5,
    lower=1.0,
    volatility=RollingVolatility(window=10),
    at_vertical=VerticalOutcome.SIGN,
)


def _draws(seed: int) -> Callable[[int], int]:
    state = seed

    def draw(modulus: int) -> int:
        nonlocal state
        state = (1103515245 * state + 12345) % 2**31
        return (state >> 8) % modulus

    return draw


def _paths(after: float | None = None) -> dict[str, list[float]]:
    """Walks in exact steps of ``k / 64``; ``after`` replaces every later step by another walk."""

    draw = _draws(911)
    other = _draws(3)
    paths: dict[str, list[float]] = {}
    for subject in SUBJECTS:
        price = 100.0
        values = []
        for index in range(COUNT):
            values.append(price)
            step = (draw(9) - 4) / 64
            if after is not None and index * DAY >= after:
                step = (other(17) - 8) / 64
            price = price * (1.0 + step)
        paths[subject] = values
    return paths


def _frame(paths: dict[str, list[float]]) -> ObservationFrame:
    stamps = tuple(float(i) * DAY for i in range(COUNT))
    return ObservationFrame(
        FeatureField.CLOSE,
        {s: ObservationSeries(s, stamps, tuple(values)) for s, values in paths.items()},
        "UTC",
        "prices@1",
    )


def _features(frame: ObservationFrame) -> dict[str, FeaturePanel]:
    return {
        "return_5": compute_panel(
            FeatureDefinition("return_5", FeatureKind.RETURN, FeatureField.CLOSE, window=5), frame
        ),
        "dispersion_15": compute_panel(
            FeatureDefinition("std_15", FeatureKind.ROLLING_STD, FeatureField.CLOSE, window=15),
            frame,
        ),
    }


def _panel(
    after: float | None = None,
) -> tuple[PointInTimePanel, LabelSet, dict[str, FeaturePanel]]:
    frame = _frame(_paths(after))
    labels = triple_barrier_labels(frame, BARRIER)
    features = _features(frame)
    return point_in_time_panel(features, labels), labels, features


def test_each_row_is_an_events_features_at_its_start_beside_its_label() -> None:
    panel, labels, features = _panel()
    assert panel.names == ("return_5", "dispersion_15")
    assert len(panel) + panel.missing == len(labels)
    assert panel.missing == 4 * len(SUBJECTS), "events on days 10 to 13 precede the 15-day std"
    assert {start for _, start, _ in panel.dropped} == {10 * DAY, 11 * DAY, 12 * DAY, 13 * DAY}
    by_event = {(label.subject, label.start): label for label in labels.labels}
    keys = list(zip(panel.starts, panel.subjects, strict=True))
    assert keys == sorted(keys)
    for index, row in enumerate(panel.features.rows):
        subject, start = panel.subjects[index], panel.starts[index]
        label = by_event[(subject, start)]
        assert (panel.ends[index], panel.labels[index], panel.outcomes[index]) == (
            label.end,
            label.value,
            label.outcome,
        )
        assert row == tuple(features[name].rows[start][subject] for name in panel.names)
    assert panel.lineage[-1].startswith("labels: triple barrier")
    assert all(label.end > label.start for label in labels.labels)


def test_the_counterfactual_future_moves_nothing_known_by_the_boundary() -> None:
    base, base_labels, base_features = _panel()
    changed, changed_labels, changed_features = _panel(after=BOUNDARY)
    assert base.panel_id != changed.panel_id, "the change must reach rows it can"
    assert base_labels.label_id != changed_labels.label_id
    known, known_changed = base.known_by(BOUNDARY), changed.known_by(BOUNDARY)
    assert len(known) > 50
    assert known.panel_id == known_changed.panel_id
    assert known.uniqueness() == known_changed.uniqueness()
    assert known.dropped == known_changed.dropped
    for weights in SampleWeights:
        assert (
            known.training_set(weights=weights).training_id
            == known_changed.training_set(weights=weights).training_id
        )
    for name, panel in base_features.items():
        for stamp, row in panel.rows.items():
            if stamp <= BOUNDARY:
                assert changed_features[name].rows[stamp] == row, (name, stamp)


def test_a_purged_split_from_research_leaves_no_training_label_reaching_its_validation() -> None:
    panel, _, _ = _panel()
    report = cross_validation_splits(
        panel.instants, 4, CVMethod.PURGED, panel.purge_policy(embargo_seconds=0.0)
    )
    assert report.folds
    purged = 0
    for fold in report.folds:
        opens = min(fold.validation)
        train = panel.at(fold.train)
        assert len(train) > 0
        for start, end in zip(train.starts, train.ends, strict=True):
            assert start >= opens or end < opens, (start, end, opens)
        purged += len(fold.purged)
    assert purged > 0, "the labels reach forward, so some instant was purged"


def test_an_instants_label_end_is_the_latest_of_its_rows() -> None:
    panel, _, _ = _panel()
    policy = panel.purge_policy(embargo_seconds=DAY)
    assert policy.embargo_seconds == DAY
    for instant in panel.instants:
        latest = max(e for s, e in zip(panel.starts, panel.ends, strict=True) if s == instant)
        assert policy.label_end(instant) == latest


def test_rows_are_taken_by_when_their_label_was_known_and_by_their_start() -> None:
    panel, _, _ = _panel()
    known = panel.known_by(BOUNDARY)
    assert all(end <= BOUNDARY for end in known.ends)
    assert len(known) == sum(1 for end in panel.ends if end <= BOUNDARY)
    chosen = panel.instants[:3]
    taken = panel.at(chosen)
    assert set(taken.starts) == set(chosen)
    assert len(taken) == sum(1 for start in panel.starts if start in chosen)
    assert taken.lineage == panel.lineage and taken.clocks == panel.clocks
    assert all(start in chosen for _, start, _ in taken.dropped)


def test_a_training_set_weighs_rows_equally_or_by_their_uniqueness_among_themselves() -> None:
    panel, _, _ = _panel()
    known = panel.known_by(BOUNDARY)
    equal = known.training_set(weights=SampleWeights.EQUAL)
    assert equal.weights is None and equal.features == known.features
    unique = known.training_set(weights=SampleWeights.UNIQUENESS)
    rows: Sequence[tuple[str, float, float]] = list(
        zip(known.subjects, known.starts, known.ends, strict=True)
    )
    assert unique.weights == average_uniqueness(rows, known.clocks)
    assert all(0.0 < weight <= 1.0 for weight in unique.weights or ())
    parent = dict(
        zip(zip(panel.subjects, panel.starts, strict=True), panel.uniqueness(), strict=True)
    )
    own = dict(zip(zip(known.subjects, known.starts, strict=True), known.uniqueness(), strict=True))
    assert any(own[key] != parent[key] for key in own), "weights are among the rows taken"
    with pytest.raises(MLInputError, match="SampleWeights"):
        known.training_set(weights="equal")  # type: ignore[arg-type]


def test_a_panels_identity_is_its_lineage_and_rows() -> None:
    panel, labels, features = _panel()
    assert panel.panel_id == point_in_time_panel(features, labels).panel_id
    renamed = point_in_time_panel(
        {"momentum": features["return_5"], "dispersion_15": features["dispersion_15"]}, labels
    )
    assert renamed.panel_id != panel.panel_id
    reordered = point_in_time_panel(dict(reversed(list(features.items()))), labels)
    assert reordered.panel_id != panel.panel_id and reordered.names == ("dispersion_15", "return_5")


def test_what_a_panel_cannot_join_is_refused() -> None:
    panel, labels, features = _panel()
    with pytest.raises(MLInputError, match="at least one feature"):
        point_in_time_panel({}, labels)
    with pytest.raises(MLInputError, match="not a FeaturePanel"):
        point_in_time_panel({"x": labels}, labels)  # type: ignore[dict-item]
    with pytest.raises(MLInputError, match="LabelSet"):
        point_in_time_panel(features, panel)  # type: ignore[arg-type]
    other = _frame(_paths())
    elsewhere = ObservationFrame(other.source_field, other.series, "America/New_York", "prices@1")
    with pytest.raises(MLInputError, match="join them in one zone"):
        point_in_time_panel(_features(elsewhere), labels)
    with pytest.raises(MLInputError, match="no label ends"):
        panel.known_by(-1.0).purge_policy(embargo_seconds=0.0)
