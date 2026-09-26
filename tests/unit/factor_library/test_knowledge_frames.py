"""Knowledge frames: point-in-time external information in the shape features read.

The feature engine is unchanged by v3.7; what is tested here is that the frames
handed to it hold exactly what was knowable at each instant -- no later
publication, no later revision, no record whose availability was never
established -- and that everything a frame holds can be traced back to the
records behind it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.alt_data import (
    EARNINGS_PER_SHARE,
    Aggregation,
    ExternalObservation,
    FundamentalInput,
    FundamentalObservation,
    InformationEvent,
    ObservationSet,
    ObservationView,
    ReferencePeriod,
    VintagePolicy,
    build_observation_set,
    restrict_measured,
)
from alphalab.common import VisibilityRule
from alphalab.factor_library import (
    FactorInputError,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureScope,
    ResearchClock,
    SnapshotSpecification,
    align_prices,
    compute_carry,
    compute_feature,
    compute_panel,
    compute_quality,
    compute_value,
    divide_frames,
    event_frame,
    forward_returns,
    fundamental_frame,
    fundamental_snapshot_as_of,
    observation_frame,
)
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from tests.unit.alt_data.pit_harness import (
    BALANCE,
    INCOME,
    PUBLISHED,
    RESTATED_AT,
    SOURCE,
    event,
    fundamental,
    issuer_records,
    ny,
    observation,
    quarter,
)

PUB = VisibilityRule.PUBLICATION
ING = VisibilityRule.INGESTION
AS_KNOWN = VintagePolicy.AS_KNOWN
ORIGINAL = VintagePolicy.ORIGINAL
DAY = 86_400.0


def _macro_view(visibility: VisibilityRule = PUB) -> ObservationView[ExternalObservation]:
    """Two countries' CPI: printed on day 10 of the next month, revised on day 20."""

    records = []
    for index, country in enumerate(("US", "EU")):
        for month in range(4):
            start = month * 30 * DAY
            end = start + 29 * DAY
            period = ReferencePeriod(start, end, f"M{month}")
            printed = end + 10 * DAY
            records.append(
                observation(
                    country,
                    "cpi_yoy",
                    str(2 + month + index),
                    end,
                    printed,
                    period=period,
                    ingested_at=printed + DAY,
                )
            )
            if month == 1:
                records.append(
                    observation(
                        country,
                        "cpi_yoy",
                        str(9 + index),
                        end,
                        printed + 10 * DAY,
                        period=period,
                        revision=1,
                        ingested_at=printed + 11 * DAY,
                    )
                )
    records.append(observation("JP", "cpi_yoy", "1", 29 * DAY, None))
    return build_observation_set("cpi", records, SOURCE).view(visibility)


CLOCK = tuple(day * DAY for day in range(0, 130))
UTC_CLOCK = ResearchClock.of_instants(CLOCK, "UTC")


def _value_at(frame: ObservationFrame, subject: str, instant: float) -> float | None:
    row = frame.series.get(subject)
    if row is None:
        return None
    lookup = dict(zip(row.timestamps, row.values, strict=True))
    return lookup.get(instant)


# --------------------------------------------------------------------------- #
# Observations
# --------------------------------------------------------------------------- #


def test_a_value_is_absent_until_published_and_repeats_until_the_next() -> None:
    knowledge = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        UTC_CLOCK,
        ("US", "EU"),
        max_age_seconds=None,
        policy=AS_KNOWN,
    )
    frame = knowledge.frame

    assert _value_at(frame, "US", 38 * DAY) is None, "M0 ends day 29 and prints day 39"
    assert _value_at(frame, "US", 39 * DAY) == 2.0
    assert _value_at(frame, "US", 60 * DAY) == 2.0, "still the latest known on day 60"
    assert _value_at(frame, "US", 69 * DAY) == 3.0, "M1 printed on day 69"
    assert _value_at(frame, "US", 79 * DAY) == 9.0, "and revised on day 79"
    assert frame.source_field is FeatureField.OBSERVATION
    assert frame.dataset_version == knowledge.frame_id


def test_the_original_policy_never_reads_the_revision() -> None:
    frame = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        UTC_CLOCK,
        ("US",),
        max_age_seconds=None,
        policy=ORIGINAL,
    ).frame

    assert _value_at(frame, "US", 79 * DAY) == 3.0
    assert 9.0 not in frame.series["US"].values


def test_the_ingestion_rule_waits_for_the_backfill() -> None:
    frame = observation_frame(
        _macro_view(ING),
        "economic",
        "cpi_yoy",
        UTC_CLOCK,
        ("US",),
        max_age_seconds=None,
        policy=AS_KNOWN,
    ).frame

    assert _value_at(frame, "US", 39 * DAY) is None
    assert _value_at(frame, "US", 40 * DAY) == 2.0


def test_a_stale_figure_is_refused_by_the_stated_bound_and_counted() -> None:
    bounded = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        UTC_CLOCK,
        ("US",),
        max_age_seconds=20 * DAY,
        policy=AS_KNOWN,
    )

    # M0 describes day 29 and is printed on day 39: ten days old, within the bound.
    assert _value_at(bounded.frame, "US", 39 * DAY) == 2.0
    assert _value_at(bounded.frame, "US", 49 * DAY) == 2.0
    # From day 50 it is more than twenty days old, and M1 does not print until day 69.
    assert _value_at(bounded.frame, "US", 50 * DAY) is None
    assert _value_at(bounded.frame, "US", 69 * DAY) == 3.0
    # Each of M0, M1 and M2 is stale for the 19 days before the next print.
    assert bounded.stale == 3 * 19


def test_every_point_names_the_record_behind_it_and_the_gaps_are_counted() -> None:
    knowledge = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        UTC_CLOCK,
        ("US", "EU", "JP"),
        max_age_seconds=None,
        policy=AS_KNOWN,
    )

    us = knowledge.frame.series["US"]
    assert len(knowledge.sources["US"]) == len(us)
    assert all(len(ids) == 1 and len(ids[0]) == 64 for ids in knowledge.sources["US"])
    # JP's only record has no established availability: never read, always counted.
    assert "JP" not in knowledge.frame.series
    assert knowledge.unverified == 1
    assert knowledge.unknown >= len(CLOCK)


def test_nothing_a_frame_holds_depends_on_what_came_after_it() -> None:
    """Truncation: sampling a shorter clock gives the same values where they overlap,
    and publishing something new after the clock ends changes none of them."""

    full = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        UTC_CLOCK,
        ("US", "EU"),
        max_age_seconds=None,
        policy=AS_KNOWN,
    ).frame
    short = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        ResearchClock.of_instants(CLOCK[:75], "UTC"),
        ("US", "EU"),
        max_age_seconds=None,
        policy=AS_KNOWN,
    ).frame

    for subject in ("US", "EU"):
        for instant, value in zip(
            short.series[subject].timestamps, short.series[subject].values, strict=True
        ):
            assert _value_at(full, subject, instant) == value

    later = observation("US", "cpi_yoy", "99", 5 * DAY, 10_000 * DAY, revision=0)
    records = [*_macro_view().index.records, *_macro_view().index.unverified, later]
    extended = build_observation_set("cpi", records, SOURCE).view(PUB)
    after = observation_frame(
        extended,
        "economic",
        "cpi_yoy",
        UTC_CLOCK,
        ("US", "EU"),
        max_age_seconds=None,
        policy=AS_KNOWN,
    ).frame
    assert after.series["US"].values == full.series["US"].values


def test_the_frame_identity_moves_with_everything_that_decides_its_values() -> None:
    view = _macro_view()

    def frame_id(**changes: object) -> str:
        arguments: dict[str, object] = {
            "clock": UTC_CLOCK,
            "subjects": ("US", "EU"),
            "max_age_seconds": None,
            "policy": AS_KNOWN,
        }
        arguments.update(changes)
        return observation_frame(view, "economic", "cpi_yoy", **arguments).frame_id  # type: ignore[arg-type]

    reference = frame_id()
    assert reference == frame_id(subjects=("EU", "US")), "universe order is not identity"
    assert reference.startswith("economic.cpi_yoy@")
    for change in (
        {"clock": ResearchClock.of_instants(CLOCK[:-1], "UTC")},
        {"clock": ResearchClock.of_instants(CLOCK, "America/New_York")},
        {"clock": ResearchClock(CLOCK, "UTC", "prices@1")},
        {"subjects": ("US",)},
        {"max_age_seconds": 90 * DAY},
        {"policy": ORIGINAL},
    ):
        assert frame_id(**change) != reference, change
    assert (
        observation_frame(
            _macro_view(ING),
            "economic",
            "cpi_yoy",
            UTC_CLOCK,
            ("US", "EU"),
            max_age_seconds=None,
            policy=AS_KNOWN,
        ).frame_id
        != reference
    )


def test_a_feature_over_a_knowledge_frame_carries_lineage_to_the_set() -> None:
    knowledge = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        UTC_CLOCK,
        ("US", "EU"),
        max_age_seconds=None,
        policy=AS_KNOWN,
    )
    rank = FeatureDefinition(
        feature_id="cpi_rank",
        kind=FeatureKind.CROSS_SECTIONAL_RANK,
        source_field=FeatureField.OBSERVATION,
        scope=FeatureScope.CROSS_SECTIONAL,
    )

    panel = compute_panel(rank, knowledge.frame)
    series = compute_feature(rank, knowledge.frame)

    assert panel.dataset_version == knowledge.frame_id
    assert {row.dataset_version for row in series} == {knowledge.frame_id}
    assert all(row.lineage_id is not None for row in series)


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"subjects": ()}, "at least one subject"),
        ({"subjects": ("US", "US")}, "repeats"),
        ({"max_age_seconds": -1.0}, "staleness bound"),
        ({"clock": ResearchClock.of_instants((1.0, 2.0), "UTC")}, "Nothing about"),
    ],
)
def test_a_malformed_frame_request_is_refused(arguments: dict[str, object], match: str) -> None:
    request: dict[str, object] = {
        "clock": UTC_CLOCK,
        "subjects": ("US",),
        "max_age_seconds": None,
        "policy": AS_KNOWN,
    }
    request.update(arguments)
    with pytest.raises(FactorInputError, match=match):
        observation_frame(_macro_view(), "economic", "cpi_yoy", **request)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("instants", "match"),
    [
        ((), "at least one instant"),
        ((2.0, 1.0), "strictly increasing"),
        ((1.0, 1.0), "strictly increasing"),
        ((float("nan"),), "not finite"),
    ],
)
def test_a_malformed_research_clock_is_refused(instants: tuple[float, ...], match: str) -> None:
    with pytest.raises(FactorInputError, match=match):
        ResearchClock.of_instants(instants, "UTC")


# --------------------------------------------------------------------------- #
# Joining a knowledge frame to the prices it was sampled on
# --------------------------------------------------------------------------- #


def _price_frame(version: str | None = "prices@1") -> ObservationFrame:
    return ObservationFrame(
        FeatureField.CLOSE,
        {
            symbol: ObservationSeries(
                symbol, CLOCK, tuple(100.0 + day for day in range(len(CLOCK)))
            )
            for symbol in ("US", "EU")
        },
        "UTC",
        version,
    )


def test_prices_align_to_a_frame_sampled_on_their_clock() -> None:
    prices = _price_frame()
    knowledge = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        ResearchClock.of_frame(prices),
        ("US", "EU"),
        max_age_seconds=None,
        policy=AS_KNOWN,
    )

    aligned = align_prices(prices, knowledge)
    returns = forward_returns(aligned, 1)
    rank = FeatureDefinition(
        feature_id="cpi_rank",
        kind=FeatureKind.CROSS_SECTIONAL_RANK,
        source_field=FeatureField.OBSERVATION,
        scope=FeatureScope.CROSS_SECTIONAL,
    )
    panel = compute_panel(rank, knowledge.frame)

    assert knowledge.clock.dataset_version == "prices@1"
    assert aligned.dataset_version == knowledge.frame_id
    assert aligned.series == prices.series, "values unchanged; only the identity is the join"
    assert returns.dataset_version == panel.dataset_version


def test_prices_do_not_align_to_a_frame_sampled_elsewhere() -> None:
    prices = _price_frame()
    listed = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        UTC_CLOCK,
        ("US",),
        max_age_seconds=None,
        policy=AS_KNOWN,
    )
    elsewhere = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        ResearchClock.of_frame(_price_frame("other@1")),
        ("US",),
        max_age_seconds=None,
        policy=AS_KNOWN,
    )
    shorter = observation_frame(
        _macro_view(),
        "economic",
        "cpi_yoy",
        ResearchClock(CLOCK[:-1], "UTC", "prices@1"),
        ("US",),
        max_age_seconds=None,
        policy=AS_KNOWN,
    )

    with pytest.raises(FactorInputError, match="instants a caller listed"):
        align_prices(prices, listed)
    with pytest.raises(FactorInputError, match="never aligned"):
        align_prices(prices, elsewhere)
    with pytest.raises(FactorInputError, match="another clock"):
        align_prices(prices, shorter)


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


def _earnings() -> ObservationSet[InformationEvent]:
    return build_observation_set(
        "earnings",
        [
            event(
                "earnings.release",
                "AAA",
                10 * DAY,
                10 * DAY + 60,
                measurements={"actual": "1.5", "consensus": "1.2"},
            ),
            event(
                "earnings.release",
                "AAA",
                100 * DAY,
                100 * DAY + 60,
                measurements={"actual": "1.1"},
            ),
            event(
                "earnings.release",
                "BBB",
                20 * DAY,
                20 * DAY + 60,
                measurements={"actual": "2.0", "consensus": "2.5"},
            ),
        ],
        SOURCE,
    )


def test_an_event_measurement_is_refused_where_some_events_lack_it() -> None:
    with pytest.raises(FactorInputError, match="restrict_measured"):
        event_frame(
            _earnings().view(PUB),
            "earnings.release",
            "consensus",
            UTC_CLOCK,
            ("AAA", "BBB"),
            max_age_seconds=None,
            policy=AS_KNOWN,
        )


def test_a_restricted_event_set_samples_the_last_known_measurement() -> None:
    measured = restrict_measured(_earnings(), "consensus")
    knowledge = event_frame(
        measured.view(PUB),
        "earnings.release",
        "consensus",
        UTC_CLOCK,
        ("AAA", "BBB"),
        max_age_seconds=None,
        policy=AS_KNOWN,
    )

    assert measured.transformations[-1].operation == "restrict_measured"
    assert _value_at(knowledge.frame, "AAA", 10 * DAY) is None, "released at 10d + 60s"
    assert _value_at(knowledge.frame, "AAA", 11 * DAY) == 1.2
    assert _value_at(knowledge.frame, "AAA", 120 * DAY) == 1.2
    assert _value_at(knowledge.frame, "BBB", 21 * DAY) == 2.5
    assert knowledge.selection == "earnings.release:consensus"


# --------------------------------------------------------------------------- #
# Ratios
# --------------------------------------------------------------------------- #


def _frame(values: dict[str, list[float]], version: str | None = "v@1") -> ObservationFrame:
    return ObservationFrame(
        source_field=FeatureField.OBSERVATION,
        series={
            symbol: ObservationSeries(symbol, tuple(float(i) for i in range(len(row))), tuple(row))
            for symbol, row in values.items()
        },
        timezone_name="UTC",
        dataset_version=version,
    )


def test_two_frames_divide_where_both_hold_a_value() -> None:
    earnings = _frame({"AAA": [2.0, 2.0, 3.0], "BBB": [1.0, 0.0]}, "eps@1")
    prices = _frame({"AAA": [20.0, 40.0, 30.0], "BBB": [10.0, 0.0], "CCC": [5.0]}, "px@1")

    ratio = divide_frames(earnings, prices, name="earnings_yield")

    assert ratio.frame.series["AAA"].values == (0.1, 0.05, 0.1)
    assert ratio.frame.series["BBB"].values == (0.1,)
    assert ratio.undefined == 1, "BBB's second price is zero: undefined, not infinite"
    assert "CCC" not in ratio.frame.series
    assert ratio.frame.dataset_version is not None
    assert ratio.frame.dataset_version.startswith("earnings_yield@")


def test_a_ratio_of_one_dataset_keeps_its_identity() -> None:
    ratio = divide_frames(_frame({"A": [2.0]}, "same@1"), _frame({"A": [4.0]}, "same@1"), name="r")

    assert ratio.frame.dataset_version == "same@1"


def test_an_untraceable_input_makes_an_untraceable_ratio() -> None:
    ratio = divide_frames(_frame({"A": [1.0]}, None), _frame({"A": [2.0]}), name="r")

    assert ratio.frame.dataset_version is None


def test_a_ratio_refuses_two_zones_or_no_overlap() -> None:
    other_zone = ObservationFrame(FeatureField.OBSERVATION, {}, "Asia/Tokyo", None)
    with pytest.raises(FactorInputError, match="reported in"):
        divide_frames(_frame({"A": [1.0]}), other_zone, name="r")
    with pytest.raises(FactorInputError, match="share no"):
        divide_frames(_frame({"A": [1.0]}), _frame({"B": [1.0]}), name="r")
    with pytest.raises(FactorInputError, match="without '@'"):
        divide_frames(_frame({"A": [1.0]}), _frame({"A": [1.0]}), name="a@b")


# --------------------------------------------------------------------------- #
# Fundamentals
# --------------------------------------------------------------------------- #


def _fundamental_view() -> ObservationView[FundamentalObservation]:
    records = issuer_records("AAA") + issuer_records("BBB", scale=2)
    return build_observation_set("fundamentals", records, SOURCE).view(PUB)


TTM_REVENUE = FundamentalInput("revenue", INCOME, "revenue", Aggregation.TRAILING_TWELVE_MONTHS)


def test_a_fundamental_frame_reads_twelve_months_as_knowable_at_each_instant() -> None:
    clock = (ny("2024-06-03 16:00"), RESTATED_AT - 60.0, RESTATED_AT + 3600.0)

    knowledge = fundamental_frame(
        _fundamental_view(),
        TTM_REVENUE,
        ResearchClock.of_instants(clock, "America/New_York"),
        ("AAA", "BBB"),
        max_age_seconds=None,
        policy=AS_KNOWN,
    )

    # June: FY2023Q2..FY2024Q1 = 105+110+115+120. Before the restatement FY2024Q2 is
    # in (110+115+120+125 = 470); after it, FY2024Q1 reads 104 (454).
    assert knowledge.frame.series["AAA"].values == (450.0, 470.0, 454.0)
    assert knowledge.frame.series["BBB"].values == (900.0, 940.0, 908.0)
    assert knowledge.selection == "income_statement.revenue:trailing_twelve_months"
    assert all(len(ids) == 4 for ids in knowledge.sources["AAA"])


def test_the_original_policy_frame_ignores_the_restatement() -> None:
    knowledge = fundamental_frame(
        _fundamental_view(),
        TTM_REVENUE,
        ResearchClock.of_instants((RESTATED_AT + 3600.0,), "America/New_York"),
        ("AAA",),
        max_age_seconds=None,
        policy=ORIGINAL,
    )

    assert knowledge.frame.series["AAA"].values == (470.0,)


def test_a_fundamental_frame_of_twelve_months_of_a_balance_is_refused() -> None:
    bad = FundamentalInput("equity", BALANCE, "total_equity", Aggregation.LATEST)
    object.__setattr__(bad, "aggregation", Aggregation.TRAILING_TWELVE_MONTHS)

    with pytest.raises(FactorInputError, match="level"):
        fundamental_frame(
            _fundamental_view(),
            bad,
            ResearchClock.of_instants((RESTATED_AT,), "UTC"),
            ("AAA",),
            max_age_seconds=None,
            policy=AS_KNOWN,
        )


SNAPSHOT = SnapshotSpecification(
    earnings_per_share=FundamentalInput(
        EARNINGS_PER_SHARE, INCOME, "eps_diluted", Aggregation.TRAILING_TWELVE_MONTHS
    ),
    book_equity=FundamentalInput("book_equity", BALANCE, "total_equity", Aggregation.LATEST),
    shares_outstanding=FundamentalInput(
        "shares_outstanding", BALANCE, "shares_diluted", Aggregation.LATEST
    ),
    dividends_per_share=FundamentalInput("dividends_per_share", INCOME, "dps", Aggregation.LATEST),
)


def _with_dividends() -> ObservationView[FundamentalObservation]:
    records = issuer_records("AAA")
    for (year, number), published in sorted(PUBLISHED.items()):
        records.append(
            fundamental(
                "AAA",
                INCOME,
                "dps",
                quarter(year, number),
                "0.25",
                published,
                unit="USD/share",
            )
        )
    return build_observation_set("with_dividends", records, SOURCE).view(PUB)


def test_a_snapshot_is_built_from_what_was_knowable_and_feeds_the_style_factors() -> None:
    as_of = ny("2024-06-03 16:00")

    snapshot = fundamental_snapshot_as_of(
        _with_dividends(),
        "AAA",
        as_of,
        Decimal("20"),
        "USD/share",
        as_of - 60.0,
        AS_KNOWN,
        SNAPSHOT,
    )

    # EPS FY2023Q2..FY2024Q1 = 1.10 + 1.20 + 1.30 + 1.40; equity 404 over 10 shares.
    assert snapshot.earnings_per_share == Decimal("5.00")
    assert snapshot.book_value_per_share == Decimal("40.4")
    assert snapshot.dividend_per_share == Decimal("0.25")
    assert snapshot.timestamp == as_of
    assert compute_value(snapshot, "ey", 1, as_of).value == pytest.approx(0.25)
    assert compute_quality(snapshot, "roe", 1, as_of).value == pytest.approx(5.0 / 40.4)
    assert compute_carry(snapshot, "dy", 1, as_of).value == pytest.approx(0.0125)


def test_a_snapshot_refuses_a_future_price_missing_inputs_and_wrong_units() -> None:
    as_of = ny("2024-06-03 16:00")
    view = _with_dividends()

    with pytest.raises(FactorInputError, match="after the snapshot instant"):
        fundamental_snapshot_as_of(
            view, "AAA", as_of, Decimal("20"), "USD/share", as_of + 1, AS_KNOWN, SNAPSHOT
        )
    with pytest.raises(FactorInputError, match="no complete snapshot"):
        fundamental_snapshot_as_of(
            view,
            "AAA",
            ny("2023-06-01 12:00"),
            Decimal("20"),
            "USD/share",
            ny("2023-06-01 12:00"),
            AS_KNOWN,
            SNAPSHOT,
        )
    with pytest.raises(FactorInputError, match="needs"):
        fundamental_snapshot_as_of(
            view, "AAA", as_of, Decimal("20"), "EUR/share", as_of, AS_KNOWN, SNAPSHOT
        )
    with pytest.raises(FactorInputError, match="per-share unit"):
        fundamental_snapshot_as_of(
            view, "AAA", as_of, Decimal("20"), "USD", as_of, AS_KNOWN, SNAPSHOT
        )
    with pytest.raises(FactorInputError, match="not a price"):
        fundamental_snapshot_as_of(
            view, "AAA", as_of, Decimal("0"), "USD/share", as_of, AS_KNOWN, SNAPSHOT
        )


def test_a_snapshot_with_unknown_availability_is_incomplete() -> None:
    records = [
        fundamental(
            "AAA", INCOME, "eps_diluted", quarter(2024, 1), "1", PUBLISHED[(2024, 1)], known=False
        )
    ]
    view = build_observation_set("unknown", records, SOURCE).view(PUB)

    with pytest.raises(FactorInputError, match="no complete snapshot"):
        fundamental_snapshot_as_of(
            view, "AAA", 1e10, Decimal("1"), "USD/share", 1e10, AS_KNOWN, SNAPSHOT
        )
