"""v3.7 point-in-time semantics: availability, visibility, effect, and the index.

The v2 helper, ``known_as_of``, is exercised by ``test_point_in_time.py`` and is
unchanged. These tests cover what v3.7 added beside it: a stamp that says *how*
an availability instant was established, two clocks for visibility, an effective
instant kept apart from both, and an index that answers "what was visible then?"
by bisection -- with every refusal that keeps those honest.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import pytest

from alphalab.common import (
    AlphaLabValidationError,
    AvailabilityBasis,
    PointInTimeIndex,
    PointInTimeStamp,
    VisibilityRule,
)

PUB = VisibilityRule.PUBLICATION
ING = VisibilityRule.INGESTION


@dataclass(frozen=True, slots=True)
class _Fact:
    """A minimal stamped record, unrelated to any AlphaLab domain type."""

    record_id: str
    stamp: PointInTimeStamp


# --------------------------------------------------------------------------- #
# The stamp
# --------------------------------------------------------------------------- #


def test_a_declared_stamp_is_visible_from_its_availability_instant_inclusive() -> None:
    stamp = PointInTimeStamp.declared(100.0, 150.0)

    assert not stamp.is_visible(149.999, PUB)
    assert stamp.is_visible(150.0, PUB), "a record known at t is visible at t"
    assert stamp.known_at(PUB) == 150.0
    assert stamp.verifiable


def test_an_unknown_availability_is_never_visible_under_either_rule() -> None:
    stamp = PointInTimeStamp.unknown(100.0, ingested_at=120.0)

    assert stamp.basis is AvailabilityBasis.UNKNOWN
    assert not stamp.verifiable
    assert stamp.known_at(PUB) is None
    assert stamp.known_at(ING) is None
    # Not even at the end of time: the observation instant is never substituted.
    assert not stamp.is_visible(1e18, PUB)
    assert not stamp.is_visible(1e18, ING)


def test_late_arriving_data_is_visible_on_publication_and_invisible_until_ingested() -> None:
    """Published at 150, backfilled into this system at 900."""

    stamp = PointInTimeStamp.declared(100.0, 150.0, ingested_at=900.0)

    assert stamp.is_visible(200.0, PUB)
    assert not stamp.is_visible(200.0, ING)
    assert not stamp.is_visible(899.0, ING)
    assert stamp.is_visible(900.0, ING)
    assert stamp.known_at(ING) == 900.0


def test_early_ingestion_never_makes_a_record_visible_before_publication() -> None:
    stamp = PointInTimeStamp.declared(100.0, 150.0, ingested_at=120.0)

    assert stamp.known_at(ING) == 150.0
    assert not stamp.is_visible(130.0, ING)


def test_a_record_with_no_ingestion_instant_is_never_visible_under_the_ingestion_rule() -> None:
    stamp = PointInTimeStamp.declared(100.0, 150.0)

    assert stamp.is_visible(200.0, PUB)
    assert stamp.known_at(ING) is None
    assert not stamp.is_visible(1e12, ING)


def test_the_effective_boundary_is_inclusive_and_separate_from_knowledge() -> None:
    """A split announced at 100 and effective at 250."""

    stamp = PointInTimeStamp.declared(100.0, 100.0, effective_at=250.0)

    assert stamp.is_visible(200.0, PUB), "known in January"
    assert not stamp.in_effect_at(200.0, PUB), "not in effect until February"
    assert not stamp.in_effect_at(249.999, PUB)
    assert stamp.in_effect_at(250.0, PUB)


def test_a_retroactive_effective_date_never_makes_a_fact_visible_early() -> None:
    """Announced at 500, effective (retroactively) from 100."""

    stamp = PointInTimeStamp.declared(500.0, 500.0, effective_at=100.0)

    assert not stamp.in_effect_at(300.0, PUB)
    assert stamp.in_effect_at(500.0, PUB)


def test_a_stamp_with_no_effective_instant_takes_effect_when_known() -> None:
    stamp = PointInTimeStamp.declared(10.0, 20.0)

    assert not stamp.in_effect_at(19.0, PUB)
    assert stamp.in_effect_at(20.0, PUB)


def test_a_derived_stamp_carries_its_rule() -> None:
    stamp = PointInTimeStamp.derived(100.0, 200.0, "date-only release; next session open")

    assert stamp.basis is AvailabilityBasis.DERIVED
    assert stamp.rule == "date-only release; next session open"
    assert stamp.is_visible(200.0, PUB)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {"observed_at": 1.0, "available_at": None, "basis": AvailabilityBasis.DECLARED},
            "UNKNOWN",
        ),
        (
            {"observed_at": 1.0, "available_at": 2.0, "basis": AvailabilityBasis.UNKNOWN},
            "DECLARED or DERIVED",
        ),
        (
            {"observed_at": 5.0, "available_at": 4.0, "basis": AvailabilityBasis.DECLARED},
            "Nothing is knowable before it happens",
        ),
        (
            {
                "observed_at": 5.0,
                "available_at": 6.0,
                "basis": AvailabilityBasis.DECLARED,
                "ingested_at": 4.0,
            },
            "cannot arrive before",
        ),
        (
            {"observed_at": 1.0, "available_at": 2.0, "basis": AvailabilityBasis.DERIVED},
            "must state the rule",
        ),
        (
            {
                "observed_at": 1.0,
                "available_at": 2.0,
                "basis": AvailabilityBasis.DECLARED,
                "rule": "lagged",
            },
            "Only a DERIVED instant",
        ),
        (
            {
                "observed_at": 1.0,
                "available_at": 2.0,
                "basis": AvailabilityBasis.DERIVED,
                "rule": "two\nlines",
            },
            "one line",
        ),
        (
            {"observed_at": math.nan, "available_at": None, "basis": AvailabilityBasis.UNKNOWN},
            "non-finite",
        ),
        (
            {"observed_at": 1.0, "available_at": math.inf, "basis": AvailabilityBasis.DECLARED},
            "non-finite",
        ),
        (
            {"observed_at": True, "available_at": None, "basis": AvailabilityBasis.UNKNOWN},
            "number of seconds",
        ),
    ],
)
def test_impossible_stamps_are_refused(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(AlphaLabValidationError, match=match):
        PointInTimeStamp(**kwargs)  # type: ignore[arg-type]


def test_a_non_finite_query_instant_is_refused() -> None:
    stamp = PointInTimeStamp.declared(1.0, 2.0)

    with pytest.raises(AlphaLabValidationError, match="non-finite"):
        stamp.is_visible(math.nan, PUB)


# --------------------------------------------------------------------------- #
# The index
# --------------------------------------------------------------------------- #


def _facts() -> list[_Fact]:
    return [
        _Fact("c", PointInTimeStamp.declared(0.0, 30.0)),
        _Fact("a", PointInTimeStamp.declared(0.0, 10.0)),
        _Fact("b", PointInTimeStamp.declared(5.0, 10.0)),
        _Fact("u", PointInTimeStamp.unknown(0.0)),
        _Fact("d", PointInTimeStamp.declared(0.0, 20.0, ingested_at=40.0)),
    ]


def test_the_index_answers_visibility_by_instant() -> None:
    index = PointInTimeIndex.build(_facts(), PUB)

    assert [fact.record_id for fact in index.visible_as_of(9.0)] == []
    assert [fact.record_id for fact in index.visible_as_of(10.0)] == ["a", "b"]
    assert [fact.record_id for fact in index.visible_as_of(25.0)] == ["a", "b", "d"]
    assert index.count_visible(30.0) == 4
    assert index.pending_at(10.0) == 2
    assert [fact.record_id for fact in index.unverified] == ["u"]
    assert len(index) == 5


def test_the_index_under_the_ingestion_rule_holds_back_a_backfill() -> None:
    index = PointInTimeIndex.build(_facts(), ING)

    # Nothing but "d" recorded an ingestion instant, so the rest cannot be placed.
    assert [fact.record_id for fact in index.records] == ["d"]
    assert index.known_instants == (40.0,)
    assert index.visible_as_of(39.0) == ()
    assert sorted(fact.record_id for fact in index.unverified) == ["a", "b", "c", "u"]


def test_same_instant_ordering_does_not_depend_on_input_order() -> None:
    facts = [_Fact(f"r{n:02d}", PointInTimeStamp.declared(0.0, 10.0)) for n in range(20)]
    reference = PointInTimeIndex.build(facts, PUB).records

    shuffled = facts[:]
    random.Random(7).shuffle(shuffled)
    assert PointInTimeIndex.build(shuffled, PUB).records == reference
    assert [fact.record_id for fact in reference] == sorted(f.record_id for f in facts)


def test_ties_on_knowledge_break_on_observation_then_identity() -> None:
    late_observed = _Fact("a", PointInTimeStamp.declared(9.0, 10.0))
    early_observed = _Fact("z", PointInTimeStamp.declared(1.0, 10.0))

    ordered = PointInTimeIndex.build([late_observed, early_observed], PUB).records

    assert ordered == (early_observed, late_observed)


def test_known_between_reads_only_what_arrived_in_the_interval() -> None:
    index = PointInTimeIndex.build(_facts(), PUB)

    assert [fact.record_id for fact in index.known_between(10.0, 30.0)] == ["d", "c"]
    assert index.known_between(10.0, 10.0) == ()
    assert index.next_known_instant(10.0) == 20.0
    assert index.next_known_instant(30.0) is None
    with pytest.raises(AlphaLabValidationError, match="backwards"):
        index.known_between(30.0, 10.0)


def test_a_record_handed_over_twice_is_refused() -> None:
    fact = _Fact("dup", PointInTimeStamp.declared(0.0, 1.0))

    with pytest.raises(AlphaLabValidationError, match="more than once"):
        PointInTimeIndex.build([fact, fact], PUB)


def test_a_hand_built_index_with_unordered_instants_is_refused() -> None:
    a = _Fact("a", PointInTimeStamp.declared(0.0, 1.0))
    b = _Fact("b", PointInTimeStamp.declared(0.0, 2.0))

    with pytest.raises(AlphaLabValidationError, match="not in order"):
        PointInTimeIndex(PUB, (b, a), (2.0, 1.0), ())
    with pytest.raises(AlphaLabValidationError, match="cannot be paired"):
        PointInTimeIndex(PUB, (a,), (1.0, 2.0), ())


def test_bisection_agrees_with_a_linear_scan_at_every_instant() -> None:
    """The index is an optimization of the rule, and must agree with the rule."""

    rng = random.Random(42)
    facts = []
    for number in range(300):
        observed = rng.uniform(0.0, 1000.0)
        facts.append(
            _Fact(f"f{number}", PointInTimeStamp.declared(observed, observed + rng.uniform(0, 50)))
        )
    index = PointInTimeIndex.build(facts, PUB)

    for instant in [rng.uniform(-10.0, 1100.0) for _ in range(200)]:
        scanned = {fact.record_id for fact in facts if fact.stamp.is_visible(instant, PUB)}
        assert {fact.record_id for fact in index.visible_as_of(instant)} == scanned
