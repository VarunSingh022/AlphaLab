"""Versioned observation sets and what was visible in them, instant by instant.

Every case the v3.7 point-in-time requirement names is here: future publication
refused, late-arriving data, revised information, effective-date boundaries,
same-instant ordering, data unavailable at the requested instant, missing
availability metadata, and deterministic ordering -- asserted on real sets
rather than on the helpers beneath them.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from alphalab.alt_data import (
    OBSERVATION_SET_KEY_SCHEME,
    AltDataInputError,
    ObservationSet,
    ObservationSource,
    PointInTimeError,
    ReferencePeriod,
    SetTransformation,
    VintagePolicy,
    build_observation_set,
    canonical_set_key,
    known_by,
    latest_vintages,
    original_vintages,
    originals_only,
    restrict_observed,
    restrict_subjects,
    verify_observation_set,
)
from alphalab.common import VisibilityRule
from tests.unit.alt_data.pit_harness import SOURCE, SOURCE_V2, event, observation

PUB = VisibilityRule.PUBLICATION
ING = VisibilityRule.INGESTION
MAY = ReferencePeriod(0.0, 100.0, "2024-05")
JUNE = ReferencePeriod(101.0, 200.0, "2024-06")


def _cpi_records() -> list:  # type: ignore[type-arg]
    """May CPI, first printed at 150 and revised at 300; June printed at 250."""

    return [
        observation("US", "cpi_yoy", "3.1", 100.0, 150.0, period=MAY),
        observation("US", "cpi_yoy", "3.3", 100.0, 300.0, period=MAY, revision=1),
        observation("US", "cpi_yoy", "3.0", 200.0, 250.0, period=JUNE),
        observation("US", "cpi_yoy", "2.9", 200.0, None, period=JUNE, revision=1),
    ]


def _cpi_set() -> ObservationSet:  # type: ignore[type-arg]
    return build_observation_set("cpi", _cpi_records(), SOURCE)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_a_set_version_is_derived_and_does_not_depend_on_arrival_order() -> None:
    records = _cpi_records()
    shuffled = records[:]
    random.Random(3).shuffle(shuffled)

    first = build_observation_set("cpi", records, SOURCE)
    second = build_observation_set("cpi", shuffled, SOURCE)

    assert first.version == second.version
    assert first.version.startswith("cpi@")
    assert first.records == second.records
    assert verify_observation_set(first)


def test_the_set_version_names_the_bytes_and_the_source_version() -> None:
    other_bytes = replace(SOURCE, content_hash="f" * 64)
    moved = [replace(record, source=other_bytes) for record in _cpi_records()]

    assert build_observation_set("cpi", moved, other_bytes).version != _cpi_set().version
    # The records themselves are the same records: bytes are the set's provenance.
    assert [record.record_id for record in moved] == [r.record_id for r in _cpi_records()]


def test_the_canonical_set_rendering_is_pinned() -> None:
    step = SetTransformation("restrict_subjects", "subjects=['US']", 4, 2)
    key = canonical_set_key("cpi", "k", SOURCE, "cpi@parent", (step,), ("b", "a"))

    assert key == "\n".join(
        [
            OBSERVATION_SET_KEY_SCHEME,
            "name='cpi'",
            "kind='k'",
            "source='panel.test_feed'",
            "source_version='2024.1'",
            f"content={'a' * 64!r}",
            "parent='cpi@parent'",
            "transformations",
            "'restrict_subjects':\"subjects=['US']\":4->2",
            "records",
            "a",
            "b",
        ]
    )


def test_a_tampered_set_does_not_verify() -> None:
    obs_set = _cpi_set()
    altered = replace(obs_set, version="cpi@" + "0" * 64)

    assert not verify_observation_set(altered)


# --------------------------------------------------------------------------- #
# Refusals at construction
# --------------------------------------------------------------------------- #


def test_an_empty_set_is_refused() -> None:
    with pytest.raises(AltDataInputError, match="no records"):
        build_observation_set("empty", [], SOURCE)


def test_a_record_from_another_source_is_refused() -> None:
    foreign = observation("US", "cpi_yoy", "3.1", 100.0, 150.0, source=SOURCE_V2)

    with pytest.raises(AltDataInputError, match="comes from"):
        build_observation_set("cpi", [*_cpi_records(), foreign], SOURCE)


def test_records_of_two_kinds_are_refused() -> None:
    mixed = [*_cpi_records(), event("macro.cpi", "US", 100.0, 150.0)]

    with pytest.raises(AltDataInputError, match="given a"):
        build_observation_set("mixed", mixed, SOURCE)


def test_one_record_twice_is_refused() -> None:
    record = _cpi_records()[0]

    with pytest.raises(AltDataInputError, match="appears twice"):
        build_observation_set("cpi", [record, record], SOURCE)


def test_two_different_values_at_one_revision_are_refused() -> None:
    first = observation("US", "cpi_yoy", "3.1", 100.0, 150.0, period=MAY)
    rival = observation("US", "cpi_yoy", "3.4", 100.0, 160.0, period=MAY)

    with pytest.raises(AltDataInputError, match="claim revision 0"):
        build_observation_set("cpi", [first, rival], SOURCE)


def test_a_revision_published_before_its_original_is_refused() -> None:
    original = observation("US", "cpi_yoy", "3.1", 100.0, 300.0, period=MAY)
    early = observation("US", "cpi_yoy", "3.3", 100.0, 150.0, period=MAY, revision=1)

    with pytest.raises(AltDataInputError, match="cannot be published ahead"):
        build_observation_set("cpi", [original, early], SOURCE)


def test_a_name_with_an_at_sign_is_refused() -> None:
    with pytest.raises(AltDataInputError, match="'@'"):
        build_observation_set("cpi@2024", _cpi_records(), SOURCE)


def test_a_hand_built_set_out_of_order_is_refused() -> None:
    obs_set = _cpi_set()

    with pytest.raises(AltDataInputError, match="not in"):
        replace(obs_set, records=tuple(reversed(obs_set.records)))


# --------------------------------------------------------------------------- #
# What was visible, and when
# --------------------------------------------------------------------------- #


def test_future_publication_is_invisible() -> None:
    view = _cpi_set().view(PUB)

    selection = view.select(149.0)

    assert selection.records == ()
    assert selection.pending == 3
    assert selection.unverified == 1


def test_revised_information_is_read_as_it_was_knowable_at_each_instant() -> None:
    view = _cpi_set().view(PUB)
    may = ("panel.test_feed", "'2024.1'", "economic", "US", "cpi_yoy", "2024-05")

    assert view.vintage_as_of(may, 149.0, VintagePolicy.AS_KNOWN) is None
    first = view.vintage_as_of(may, 200.0, VintagePolicy.AS_KNOWN)
    revised = view.vintage_as_of(may, 300.0, VintagePolicy.AS_KNOWN)
    original = view.vintage_as_of(may, 900.0, VintagePolicy.ORIGINAL)

    assert first is not None and str(first.value) == "3.1"
    assert revised is not None and str(revised.value) == "3.3", "the revision, once published"
    assert original is not None and str(original.value) == "3.1", "originals stay readable"


def test_the_latest_figure_is_the_newest_period_not_the_newest_publication() -> None:
    """At 300 the May revision is the most recent *publication*; June is the newest period."""

    view = _cpi_set().view(PUB)
    series = ("panel.test_feed", "'2024.1'", "economic", "US", "cpi_yoy")

    latest = view.latest_in_series(series, 300.0, VintagePolicy.AS_KNOWN)

    assert latest is not None
    assert latest.period == JUNE
    assert str(latest.value) == "3.0", "June's revision has no established availability"


def test_missing_availability_metadata_is_never_selected() -> None:
    view = _cpi_set().view(PUB)
    june = ("panel.test_feed", "'2024.1'", "economic", "US", "cpi_yoy", "2024-06")

    known = view.vintage_as_of(june, 10_000.0, VintagePolicy.AS_KNOWN)

    assert known is not None and known.revision == 0
    assert _cpi_set().unverified == 1
    assert all(record.stamp.verifiable for record in view.select(10_000.0).records)


def test_data_unavailable_at_the_requested_instant_is_refused_with_its_arrival() -> None:
    view = _cpi_set().view(PUB)
    may = ("panel.test_feed", "'2024.1'", "economic", "US", "cpi_yoy", "2024-05")

    with pytest.raises(PointInTimeError, match=r"becomes knowable at 150\.0"):
        view.require_vintage(may, 120.0, VintagePolicy.AS_KNOWN)


def test_a_figure_whose_availability_was_never_established_is_refused_as_such() -> None:
    only_unknown = build_observation_set(
        "unknown", [observation("US", "m", "1", 10.0, None)], SOURCE
    )
    view = only_unknown.view(PUB)
    key = only_unknown.records[0].vintage_key

    with pytest.raises(PointInTimeError, match="never established"):
        view.require_vintage(key, 1e12, VintagePolicy.AS_KNOWN)


def test_an_absent_figure_is_refused_as_absent() -> None:
    view = _cpi_set().view(PUB)

    with pytest.raises(PointInTimeError, match="holds no figure"):
        view.require_vintage(("nope",), 1.0, VintagePolicy.AS_KNOWN)


def test_late_arriving_data_waits_for_its_arrival_under_the_ingestion_rule() -> None:
    backfilled = build_observation_set(
        "backfill",
        [
            observation("US", "m", "1", 100.0, 150.0, ingested_at=160.0),
            observation("US", "m", "2", 200.0, 250.0, ingested_at=900.0),
        ],
        SOURCE,
    )

    assert len(backfilled.view(PUB).select(300.0)) == 2
    assert len(backfilled.view(ING).select(300.0)) == 1
    assert len(backfilled.view(ING).select(900.0)) == 2


def test_same_instant_records_are_selected_together_in_a_stable_order() -> None:
    records = [observation(f"S{n}", "m", "1", 10.0, 50.0) for n in range(8)]
    shuffled = records[:]
    random.Random(11).shuffle(shuffled)

    first = build_observation_set("tie", records, SOURCE).view(PUB).select(50.0)
    second = build_observation_set("tie", shuffled, SOURCE).view(PUB).select(50.0)

    assert len(first) == 8, "a record known at t is visible at t, and so are its peers"
    assert first.records == second.records


def test_effective_date_boundaries_are_separate_from_knowledge() -> None:
    split = event(
        "corporate_action.split",
        "AAA",
        100.0,
        100.0,
        measurements={"ratio": "2"},
        effective_at=250.0,
    )
    view = build_observation_set("actions", [split], SOURCE).view(PUB)

    known_now = view.select(200.0).records

    assert known_now == (split,)
    assert not split.stamp.in_effect_at(200.0, PUB)
    assert split.stamp.in_effect_at(250.0, PUB)


def test_the_vintage_policies_choose_one_record_per_figure() -> None:
    records = _cpi_records()[:3]

    assert [str(r.value) for r in latest_vintages(records)] == ["3.3", "3.0"]
    assert [str(r.value) for r in original_vintages(records)] == ["3.1", "3.0"]


def test_a_selection_offers_both_vintage_readings() -> None:
    selection = _cpi_set().view(PUB).select(300.0)

    assert [str(r.value) for r in selection.vintages(VintagePolicy.AS_KNOWN)] == ["3.3", "3.0"]
    assert [str(r.value) for r in selection.vintages(VintagePolicy.ORIGINAL)] == ["3.1", "3.0"]


def test_an_unknown_series_is_refused_by_name() -> None:
    view = _cpi_set().view(PUB)

    with pytest.raises(AltDataInputError, match="holds no series"):
        view.visible_in_series(("x",), 1.0)


# --------------------------------------------------------------------------- #
# Derived sets and lineage
# --------------------------------------------------------------------------- #


def test_a_restriction_derives_a_new_version_and_records_its_lineage() -> None:
    parent = build_observation_set(
        "mixed",
        [observation("US", "m", "1", 1.0, 2.0), observation("EU", "m", "2", 1.0, 2.0)],
        SOURCE,
    )

    child = restrict_subjects(parent, ["US"])

    assert child.version != parent.version
    assert child.parent_version == parent.version
    assert child.transformations == (
        SetTransformation("restrict_subjects", "subjects=['US']", 2, 1),
    )
    assert child.subjects == ("US",)
    assert verify_observation_set(child)
    # Applied twice, the same restriction derives the same identity.
    assert restrict_subjects(parent, ["US"]).version == child.version


def test_a_point_in_time_snapshot_is_a_derived_set() -> None:
    snapshot = known_by(_cpi_set(), 260.0, PUB)

    assert [str(record.value) for record in snapshot.records] == ["3.1", "3.0"]
    assert snapshot.transformations[-1].operation == "known_by"
    assert snapshot.parent_version == _cpi_set().version


def test_restrictions_compose_and_record_every_step() -> None:
    step_one = restrict_observed(_cpi_set(), 0.0, 150.0)
    step_two = originals_only(step_one)

    assert [t.operation for t in step_two.transformations] == [
        "restrict_observed",
        "originals_only",
    ]
    assert [str(record.value) for record in step_two.records] == ["3.1"]
    assert step_two.parent_version == step_one.version


def test_a_restriction_that_leaves_nothing_is_refused() -> None:
    with pytest.raises(AltDataInputError, match="leaves set"):
        restrict_subjects(_cpi_set(), ["JP"])
    with pytest.raises(AltDataInputError, match="leaves set"):
        known_by(_cpi_set(), 1.0, PUB)
    with pytest.raises(AltDataInputError, match="empty"):
        restrict_observed(_cpi_set(), 5.0, 5.0)
    with pytest.raises(AltDataInputError, match="at least one subject"):
        restrict_subjects(_cpi_set(), [])


def test_a_set_names_its_subjects_and_length() -> None:
    obs_set = _cpi_set()

    assert obs_set.subjects == ("US",)
    assert len(obs_set) == 4
    assert len(obs_set.record_ids) == 4


def test_a_source_with_no_bytes_still_forms_a_set_and_says_so() -> None:
    memory = ObservationSource("panel.memory", None, None, None)
    obs_set = build_observation_set(
        "memory", [observation("US", "m", "1", 1.0, 2.0, source=memory)], memory
    )

    assert not obs_set.source.byte_provenance
    assert verify_observation_set(obs_set)


# --------------------------------------------------------------------------- #
# The timeline agrees with the rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("policy", [VintagePolicy.AS_KNOWN, VintagePolicy.ORIGINAL])
def test_the_latest_timeline_agrees_with_latest_in_series_at_every_instant(
    policy: VintagePolicy,
) -> None:
    """An incremental answer is only an optimization if it gives the same answer."""

    import bisect

    rng = random.Random(5)
    records = []
    for month in range(24):
        observed = 100.0 * (month + 1)
        first_print = observed + rng.uniform(5.0, 30.0)
        records.append(
            observation(
                "US",
                "cpi_yoy",
                f"{rng.uniform(1, 5):.2f}",
                observed,
                first_print,
                period=ReferencePeriod(observed - 50.0, observed, f"M{month:02d}"),
            )
        )
        if month % 3 == 0:
            records.append(
                observation(
                    "US",
                    "cpi_yoy",
                    f"{rng.uniform(1, 5):.2f}",
                    observed,
                    first_print + rng.uniform(1.0, 400.0),
                    period=ReferencePeriod(observed - 50.0, observed, f"M{month:02d}"),
                    revision=1,
                )
            )
    view = build_observation_set("cpi", records, SOURCE).view(PUB)
    series = view.series_keys[0]
    timeline = view.latest_timeline(series, policy)
    instants = [entry[0] for entry in timeline]

    for probe in [rng.uniform(0.0, 3000.0) for _ in range(300)]:
        position = bisect.bisect_right(instants, probe)
        from_timeline = timeline[position - 1][1] if position else None
        assert from_timeline == view.latest_in_series(series, probe, policy)


@pytest.mark.parametrize("visibility", [PUB, ING])
def test_reading_one_figure_agrees_with_everything_visible_at_every_instant(
    visibility: VisibilityRule,
) -> None:
    """The per-figure index answers exactly as a scan of the whole selection would.

    Figures with up to three vintages, some of unknown availability, revisions
    arriving with, shortly after or long after the one they revise, and records
    ingested as they were observed, as they became available, or later -- read
    at every probe under both policies, against a reference built from
    :meth:`ObservationView.select`.
    """

    rng = random.Random(37)
    records = []
    for month in range(18):
        observed = 100.0 * (month + 1)
        period = ReferencePeriod(observed - 50.0, observed, f"M{month:02d}")
        available = observed + rng.uniform(1.0, 20.0)
        for revision in range(rng.randint(1, 3)):
            records.append(
                observation(
                    "US",
                    "cpi_yoy",
                    f"{rng.uniform(1, 5):.2f}",
                    observed,
                    None if rng.random() < 0.15 else available,
                    period=period,
                    revision=revision,
                    ingested_at=rng.choice(
                        (observed, available, available + 60.0, available + 700.0)
                    ),
                )
            )
            available += rng.choice((0.0, 15.0, 400.0))
    view = build_observation_set("figures", records, SOURCE).view(visibility)
    keys = sorted({record.vintage_key for record in records})

    for probe in [float(instant) for instant in range(0, 3_000, 9)]:
        visible = view.select(probe).records
        for key in keys:
            mine = [record for record in visible if record.vintage_key == key]
            originals = [record for record in mine if record.revision == 0]
            as_known = max(mine, key=lambda record: record.revision) if mine else None
            assert view.vintage_as_of(key, probe, VintagePolicy.AS_KNOWN) == as_known
            original = originals[0] if originals else None
            assert view.vintage_as_of(key, probe, VintagePolicy.ORIGINAL) == original
