"""Generic observations, their source identity, and the identity derived from both.

The v3.7 alternative-data foundation: an observation of any category with a
declared unit, a point-in-time stamp and a revision number, read from a source
that names itself, its version and the bytes it came from.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.alt_data import (
    OBSERVATION_KEY_SCHEME,
    AltDataInputError,
    DataProvenance,
    ExternalObservation,
    NewsSentiment,
    ObservationSource,
    ReferencePeriod,
    canonical_observation_key,
    observation_from_release,
)
from alphalab.common import AvailabilityBasis, PointInTimeStamp
from alphalab.macro import IndicatorObservation
from tests.unit.alt_data.pit_harness import SOURCE, SOURCE_V2, observation

# --------------------------------------------------------------------------- #
# Source identity
# --------------------------------------------------------------------------- #


def test_a_source_names_itself_its_version_and_its_bytes() -> None:
    assert SOURCE.byte_provenance
    assert SOURCE.record_lines == ("source='panel.test_feed'", "source_version='2024.1'")
    assert SOURCE.set_lines[-1] == f"content={'a' * 64!r}"


def test_an_undeclared_version_and_absent_bytes_are_recorded_as_absent() -> None:
    source = ObservationSource("panel.memory", None, None, None)

    assert not source.byte_provenance
    assert source.record_lines == ("source='panel.memory'", "source_version=None")


def test_vendor_quality_rides_along_and_enters_no_record_identity() -> None:
    quality = DataProvenance("an analyst's note", "weekly passes", Decimal("0.7"))
    with_quality = replace(SOURCE, quality=quality)

    plain = observation("US", "cpi_yoy", "3.1", 100.0, 150.0)
    rated = observation("US", "cpi_yoy", "3.1", 100.0, 150.0, source=with_quality)

    assert rated.record_id == plain.record_id


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"source_id": "Panel.Feed"}, "dotted lowercase identifier"),
        ({"source_id": "panel feed"}, "dotted lowercase identifier"),
        ({"version": "  "}, "cannot be empty"),
        ({"version": "2024\n1"}, "line break"),
        ({"content_hash": "ab cd"}, "whitespace"),
        ({"retrieved_at": float("nan")}, "finite"),
    ],
)
def test_a_malformed_source_is_refused(kwargs: dict[str, object], match: str) -> None:
    fields: dict[str, object] = {
        "source_id": "panel.feed",
        "version": "1",
        "content_hash": "c" * 64,
        "retrieved_at": 1.0,
    }
    fields.update(kwargs)
    with pytest.raises(AltDataInputError, match=match):
        ObservationSource(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The observation
# --------------------------------------------------------------------------- #


def test_the_category_vocabulary_is_open() -> None:
    """A weather index needs a name, not a new class."""

    weather = observation("KORD", "heating_degree_days", "12.5", 100.0, 110.0, category="weather")
    web = observation("AAA", "site_visits", "1200", 100.0, 110.0, category="web")

    assert weather.category == "weather"
    assert web.series_key == ("panel.test_feed", "'2024.1'", "web", "AAA", "site_visits")


def test_the_identity_is_derived_from_content_and_reproduces() -> None:
    first = observation("US", "cpi_yoy", "3.1", 100.0, 150.0)
    second = observation("US", "cpi_yoy", "3.1", 100.0, 150.0)

    assert first.record_id == second.record_id
    assert len(first.record_id) == 64
    assert first == second


@pytest.mark.parametrize(
    "change",
    [
        {"value": "3.2"},
        {"available_at": 151.0},
        {"revision": 1},
        {"ingested_at": 900.0},
        {"effective_at": 200.0},
        {"source": SOURCE_V2},
        {"category": "inflation"},
    ],
)
def test_every_input_that_changes_research_changes_the_identity(change: dict[str, object]) -> None:
    base = {"subject": "US", "metric": "cpi_yoy", "value": "3.1", "observed_at": 100.0}
    reference = observation(**base, available_at=150.0)  # type: ignore[arg-type]
    arguments: dict[str, object] = {**base, "available_at": 150.0, **change}

    changed = observation(**arguments)  # type: ignore[arg-type]

    assert changed.record_id != reference.record_id


def test_a_value_keeps_its_spelling_in_its_identity() -> None:
    """The v3.6 rule: ``10`` and ``10.0`` are two spellings, and two identities."""

    assert (
        observation("US", "m", "1.50", 1.0, 2.0).record_id
        != observation("US", "m", "1.5", 1.0, 2.0).record_id
    )


def test_the_canonical_rendering_is_pinned() -> None:
    """Changing this re-identifies every observation in existence; it needs an ADR."""

    obs = observation(
        "US",
        "cpi_yoy",
        "3.1",
        100.0,
        150.0,
        period=ReferencePeriod(50.0, 100.0, "2024-05"),
    )

    assert canonical_observation_key(obs) == "\n".join(
        [
            OBSERVATION_KEY_SCHEME,
            "category='economic'",
            "subject='US'",
            "metric='cpi_yoy'",
            "value='3.1'",
            "unit='percent'",
            "revision=0",
            "period='2024-05'[50.0,100.0]",
            "source='panel.test_feed'",
            "source_version='2024.1'",
            "observed_at=100.0",
            "available_at=150.0",
            "basis=DECLARED",
            "effective_at=None",
            "ingested_at=None",
            "rule=''",
        ]
    )


def test_the_vintage_key_uses_the_period_or_the_observation_instant() -> None:
    periodic = observation("US", "m", "1", 90.0, 150.0, period=ReferencePeriod(50.0, 100.0, "P1"))
    instant = observation("US", "m", "1", 90.0, 150.0)

    assert periodic.vintage_key[-1] == "P1"
    assert instant.vintage_key[-1] == "90.0"


def test_an_observation_is_immutable() -> None:
    obs = observation("US", "m", "1", 1.0, 2.0)

    with pytest.raises(FrozenInstanceError):
        obs.value = Decimal("2")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"category": "Economic"}, "identifier"),
        ({"metric": ""}, "identifier"),
        ({"subject": " US"}, "surrounding whitespace"),
        ({"unit": ""}, "cannot be empty"),
        ({"value": Decimal("NaN")}, "finite"),
        ({"value": 3.1}, "Decimal"),
        ({"revision": -1}, "revision"),
        ({"revision": True}, "revision"),
    ],
)
def test_a_malformed_observation_is_refused(change: dict[str, Any], match: str) -> None:
    obs = observation("US", "cpi_yoy", "3.1", 1.0, 2.0)
    with pytest.raises(AltDataInputError, match=match):
        replace(obs, **change)


def test_an_observation_outside_its_own_period_is_refused() -> None:
    with pytest.raises(AltDataInputError, match="outside the period"):
        observation("US", "m", "1", 500.0, 600.0, period=ReferencePeriod(0.0, 100.0, "P"))


def test_a_backwards_or_unlabelled_period_is_refused() -> None:
    with pytest.raises(AltDataInputError, match="before it starts"):
        ReferencePeriod(10.0, 5.0, "P")
    with pytest.raises(AltDataInputError, match="cannot be empty"):
        ReferencePeriod(1.0, 5.0, "")


# --------------------------------------------------------------------------- #
# Lifting what already exists
# --------------------------------------------------------------------------- #


def test_a_v1_news_record_lifts_with_its_release_as_a_declared_instant() -> None:
    news = NewsSentiment(
        asset_id="AAA",
        headline="beat",
        published_at=100.0,
        release_date=160.0,
        sentiment_score=Decimal("0.4"),
        source=DataProvenance("desk", "headlines", Decimal("0.9")),
    )

    lifted = observation_from_release(
        news,
        category="sentiment",
        subject=news.asset_id,
        metric="article_score",
        value=news.sentiment_score,
        unit="score",
        source=SOURCE,
        revision=0,
    )

    assert lifted.stamp == PointInTimeStamp.declared(100.0, 160.0)
    assert lifted.stamp.basis is AvailabilityBasis.DECLARED
    assert lifted.value == Decimal("0.4")


def test_a_macro_indicator_lifts_through_the_same_protocol() -> None:
    cpi = IndicatorObservation("US_CPI", 100.0, 200.0, Decimal("3.1"))

    lifted = observation_from_release(
        cpi,
        category="economic",
        subject="US",
        metric="cpi_yoy",
        value=cpi.value,
        unit="percent",
        source=SOURCE,
        revision=0,
    )

    assert isinstance(lifted, ExternalObservation)
    assert lifted.stamp.observed_at == 100.0
    assert lifted.stamp.available_at == 200.0
