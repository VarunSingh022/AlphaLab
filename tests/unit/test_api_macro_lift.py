"""The macro engine's releases reach a strategy as point-in-time observations (v4.1.1, FUT-008).

``alphalab.macro`` keeps what a release describes and when it was published
apart; ``observations_from_indicator`` carries both -- and the vintage, numbered
by release order -- into the form the delivery path reads, so a macro-engine
series is scheduled without being re-ingested.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.alt_data import ObservationSource, delivery_schedule
from alphalab.api import observations_from_indicator
from alphalab.common.point_in_time import VisibilityRule
from alphalab.data import DataValidationError
from alphalab.macro import IndicatorMetadata, IndicatorObservation, known_as_of
from alphalab.macro.enums import Frequency

CPI = IndicatorMetadata("US_CPI_YOY", "CPI, year on year", "US", Frequency.MONTHLY, "%")
SOURCE = ObservationSource("macro.bls", "2024.06", "c" * 64, 1_718_000_000.0)
MARCH, APRIL = 1_709_251_200.0, 1_711_929_600.0
RELEASES = (
    IndicatorObservation("US_CPI_YOY", MARCH, 1_712_752_200.0, Decimal("3.5")),
    IndicatorObservation("US_CPI_YOY", MARCH, 1_713_961_800.0, Decimal("3.4"), is_revision=True),
    IndicatorObservation("US_CPI_YOY", APRIL, 1_715_779_800.0, Decimal("3.7")),
)


def test_each_release_is_observed_at_its_period_and_known_at_its_release() -> None:
    obs_set = observations_from_indicator(CPI, RELEASES, SOURCE)
    schedule = delivery_schedule(obs_set, VisibilityRule.PUBLICATION)

    assert [d.known_at for d in schedule] == [r.release_date for r in RELEASES]
    assert [(d.record.revision, d.record.value) for d in schedule] == [
        (0, Decimal("3.5")),
        (1, Decimal("3.4")),
        (0, Decimal("3.7")),
    ]
    first = schedule.deliveries[0].record
    assert (first.subject, first.metric, first.unit, first.category) == (
        "US",
        "us_cpi_yoy",
        "%",
        "economic",
    )
    assert first.stamp.observed_at == MARCH
    assert schedule.unverified == 0


def test_what_is_known_at_any_instant_agrees_with_the_macro_engine() -> None:
    obs_set = observations_from_indicator(CPI, RELEASES, SOURCE)
    schedule = delivery_schedule(obs_set, VisibilityRule.PUBLICATION)
    for at in (1_712_752_199.0, 1_712_752_200.0, 1_713_961_800.0, 1_716_000_000.0):
        engine = known_as_of(tuple(r for r in RELEASES if r.reference_period == MARCH), at)
        lifted = [
            d.record for d in schedule if d.known_at <= at and d.record.stamp.observed_at == MARCH
        ]
        expected = None if engine is None else engine.value
        assert (lifted[-1].value if lifted else None) == expected, at


def test_the_set_is_identified_by_its_content() -> None:
    first = observations_from_indicator(CPI, RELEASES, SOURCE)
    again = observations_from_indicator(CPI, tuple(reversed(RELEASES)), SOURCE)
    revised = observations_from_indicator(
        CPI,
        (*RELEASES[:2], IndicatorObservation("US_CPI_YOY", APRIL, 1_715_779_800.0, Decimal("3.8"))),
        SOURCE,
    )
    assert first.version == again.version
    assert first.version != revised.version


@pytest.mark.parametrize(
    ("releases", "message"),
    [
        ((IndicatorObservation("US_GDP", MARCH, MARCH, Decimal("1")),), "is not one of"),
        ((RELEASES[1],), "marked is_revision=True"),
        (
            (
                RELEASES[0],
                IndicatorObservation("US_CPI_YOY", MARCH, 1_713_961_800.0, Decimal("3.4")),
            ),
            "is_revision=False",
        ),
        ((RELEASES[0], RELEASES[0]), "two releases"),
        ((), "No release"),
    ],
    ids=["other-indicator", "first-marked-revision", "later-not-marked", "same-instant", "empty"],
)
def test_a_series_that_contradicts_itself_is_refused(
    releases: tuple[IndicatorObservation, ...], message: str
) -> None:
    with pytest.raises(DataValidationError, match=message):
        observations_from_indicator(CPI, releases, SOURCE)
