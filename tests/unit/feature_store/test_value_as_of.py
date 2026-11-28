"""Feature Store's as-of reads: what a read at an instant would have returned (v4.3, GAP-13).

* **A value is known from the later of its computation and its write.** A read
  at an instant returns the last value known by then -- a later write winning
  a tie -- and nothing before the first; a backfill written late never answers
  for an instant before its write. :func:`latest_value` is unchanged.
* **The timeline is the store's, functional and complete**: every approved
  write, in order; a rejected one never; an earlier state untouched by a later
  write.
* **The as-of dataset is point-in-time** (the counterfactual future, plan
  I.3): every write after the instant -- new values, backfills, a new asset --
  changes nothing in the dataset built as of it, while the latest-value
  builder, documented as not point-in-time, moves.
"""

from __future__ import annotations

import pytest

from alphalab.feature_store import (
    FeatureMetadata,
    FeatureRegistry,
    FeatureStoreEngine,
    FeatureStoreState,
    FeatureType,
    FeatureValue,
    FeatureValueStore,
    FeatureValueType,
    KnownFeatureValue,
    latest_value,
    value_as_of,
    value_timeline,
)
from alphalab.ml import build_dataset_as_of, build_dataset_from_feature_store
from alphalab.ml.exceptions import MLInputError


def _metadata(feature_id: str) -> FeatureMetadata:
    return FeatureMetadata(
        feature_id=feature_id,
        name=feature_id,
        version=1,
        feature_type=FeatureType.DERIVED,
        value_type=FeatureValueType.FLOAT,
        owner="research",
        description="a test feature",
    )


def _store() -> FeatureStoreState:
    state = FeatureStoreEngine.initialize("AS-OF")
    for feature_id in ("momentum", "forward"):
        state = FeatureRegistry.register(state, _metadata(feature_id), 0.0)
    return state


def _write(
    state: FeatureStoreState,
    feature_id: str,
    asset: str,
    value: float,
    computed: float,
    written: float,
) -> FeatureStoreState:
    state, decision = FeatureValueStore.write(
        state, FeatureValue(feature_id, 1, asset, value, computed), written
    )
    assert decision.approved
    return state


def test_a_read_at_an_instant_returns_the_last_value_known_by_it() -> None:
    state = _store()
    state = _write(state, "momentum", "A", 1.0, computed=10.0, written=10.0)
    state = _write(state, "momentum", "A", 2.0, computed=20.0, written=20.0)
    state = _write(state, "momentum", "A", 3.0, computed=30.0, written=30.0)
    read = [value_as_of(state, "momentum", 1, "A", t) for t in (5.0, 10.0, 25.0, 30.0, 99.0)]
    assert [None if v is None else v.value for v in read] == [None, 1.0, 2.0, 3.0, 3.0]
    late = _write(state, "momentum", "A", 1.5, computed=15.0, written=40.0)
    assert value_as_of(late, "momentum", 1, "A", 35.0) == FeatureValue(
        "momentum", 1, "A", 3.0, 30.0
    )
    backfill = value_as_of(late, "momentum", 1, "A", 40.0)
    assert backfill is not None and backfill.value == 1.5, (
        "known at its write, 40, the latest known"
    )
    early = _write(late, "momentum", "A", 4.0, computed=50.0, written=45.0)
    assert value_as_of(early, "momentum", 1, "A", 47.0) == backfill, "computed after the read"
    tied = _write(early, "momentum", "A", 5.0, computed=40.0, written=40.0)
    tie = value_as_of(tied, "momentum", 1, "A", 40.0)
    assert tie is not None and tie.value == 5.0, "of two known at once, the later write"
    assert latest_value(tied, "momentum", 1, "A") == FeatureValue("momentum", 1, "A", 5.0, 40.0)


def test_the_timeline_holds_every_approved_write_in_order_and_no_rejected_one() -> None:
    state = _store()
    first = _write(state, "momentum", "A", 1.0, computed=10.0, written=12.0)
    second = _write(first, "momentum", "A", 2.0, computed=20.0, written=20.0)
    rejected, decision = FeatureValueStore.write(
        second, FeatureValue("unregistered", 1, "A", 3.0, 30.0), 30.0
    )
    assert not decision.approved
    assert tuple(value_timeline(rejected, "momentum", 1, "A")) == (
        KnownFeatureValue(FeatureValue("momentum", 1, "A", 1.0, 10.0), 12.0),
        KnownFeatureValue(FeatureValue("momentum", 1, "A", 2.0, 20.0), 20.0),
    )
    assert tuple(value_timeline(rejected, "unregistered", 1, "A")) == ()
    assert len(value_timeline(first, "momentum", 1, "A")) == 1, "an earlier state is untouched"
    assert value_timeline(state, "momentum", 1, "B") == ()


def _research_store() -> FeatureStoreState:
    state = _store()
    for asset, momentum, forward in (("A", 0.5, 0.25), ("B", -0.25, -0.125), ("C", 0.75, 0.5)):
        state = _write(state, "momentum", asset, momentum, computed=10.0, written=10.0)
        state = _write(state, "forward", asset, forward, computed=12.0, written=12.0)
    return state


def test_the_counterfactual_future_moves_nothing_in_the_dataset_as_of_an_instant() -> None:
    assets = ("A", "B", "C", "D")
    base = _research_store()
    later = base
    later = _write(later, "momentum", "A", 9.0, computed=20.0, written=20.0)
    later = _write(later, "momentum", "B", 8.0, computed=11.0, written=30.0)  # a backfill
    later = _write(later, "momentum", "D", 1.0, computed=21.0, written=21.0)
    later = _write(later, "forward", "D", 1.0, computed=21.0, written=21.0)
    for state in (base, later):
        dataset = build_dataset_as_of(state, (("momentum", 1),), ("forward", 1), assets, as_of=15.0)
        assert dataset.asset_ids == ("A", "B", "C")
        assert dataset.x == ((0.5,), (-0.25,), (0.75,))
        assert dataset.y == (0.25, -0.125, 0.5)
    moved = build_dataset_from_feature_store(later, (("momentum", 1),), ("forward", 1), assets)
    assert moved.asset_ids == ("A", "B", "C", "D") and moved.x[0] == (9.0,), (
        "the latest-value builder is not point-in-time"
    )


def test_an_asset_not_yet_known_is_dropped_and_none_known_is_refused() -> None:
    state = _write(_research_store(), "momentum", "D", 1.0, computed=13.0, written=13.0)
    partial = build_dataset_as_of(state, (("momentum", 1),), ("forward", 1), ("A", "D"), as_of=14.0)
    assert partial.asset_ids == ("A",), "D's target was never known"
    with pytest.raises(MLInputError, match="No asset has complete data"):
        build_dataset_as_of(state, (("momentum", 1),), ("forward", 1), ("A", "B"), as_of=11.0)
    with pytest.raises(MLInputError, match="cannot be empty"):
        build_dataset_as_of(state, (), ("forward", 1), ("A",), as_of=20.0)
