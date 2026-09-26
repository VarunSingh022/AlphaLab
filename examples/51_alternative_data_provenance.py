"""
AlphaLab Examples
=================

Example 51 : Alternative Data with Provenance and Point-in-Time Semantics

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 15 (data ingestion)
✓ Example 17 (feature engineering)

Topics
------

• A source named by identity, version and the exact bytes it was read from
• Any category of data -- news, weather, web -- with no class to write
• Availability declared, derived by a stated rule, or honestly unknown
• Late-arriving data: what the world knew against what this system knew
• Revisions as vintages, read as they were knowable
• A set version that changes with the data, and lineage through restrictions
• A single-timestamp wire record lifted only with its timestamp's meaning declared
• A knowledge frame on the price clock, joined to prices and measured by IC

What this shows
---------------

An alternative-data figure is only usable in a backtest if you can say when it
became knowable. This example ingests a daily news score whose vendor delivers
each observation an hour after it is taken -- an availability *derived* by a
stated rule -- alongside a weather series that says nothing about availability,
which is ingested, counted and never read. A knowledge frame then samples the
news score on the price panel's own clock, and the join to the prices is checked
rather than assumed before an information coefficient is measured.

Run

    python examples/51_alternative_data_provenance.py
"""

from decimal import Decimal

from _point_in_time import (
    SESSIONS,
    SYMBOLS,
    banner,
    close_of,
    ingest_prices,
    label,
    section,
    sentiment_rows,
    vendor_file,
)

from alphalab.alt_data import (
    ExternalObservation,
    ReferencePeriod,
    VintagePolicy,
    build_observation_set,
    known_by,
    restrict_subjects,
    verify_observation_set,
)
from alphalab.api import (
    AvailabilityAfterLag,
    AvailabilityNotDeclared,
    ObservationColumns,
    TimestampReading,
    WireTimestamp,
    ingest_observations,
    lift_wire_records,
    observation_source,
)
from alphalab.common import PointInTimeStamp, VisibilityRule
from alphalab.data.feed import AlternativeDataRecord
from alphalab.data.time import TimestampFormat
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureScope,
    ResearchClock,
    align_prices,
    compute_panel,
    forward_returns,
    information_coefficient,
    observation_frame,
    observations_from_dataset,
)

PUB = VisibilityRule.PUBLICATION
READING = TimestampReading(TimestampFormat.ISO_8601_NAIVE, "America/New_York", None)


def main() -> None:
    banner(51, "Alternative Data with Provenance")

    section("1. A source: identity, version, and the bytes")
    rows = sentiment_rows()
    raw = vendor_file("news_scores.csv", rows, 1_717_000_200.0)
    source = observation_source(raw, "news.sentiment", "3.1")
    print(f"source {source.source_id} version {source.version}")
    print(f"content digest {source.content_hash}")
    print(f"retrieved at {label(raw.retrieved_at)} (recorded, never part of an identity)")

    section("2. Availability derived by a stated rule")
    columns = ObservationColumns(
        category="sentiment",
        unit="score",
        subject="ticker",
        metric="metric",
        value="value",
        observed_at="observed",
        availability=AvailabilityAfterLag(3600.0),
        effective_at=None,
        revision=None,
        period=None,
        ingested_at_retrieval=True,
    )
    news = ingest_observations(rows, "news", source, columns, READING).observations
    first = news.records[0]
    print(f"{len(news)} observations, set version {news.version}")
    available = first.stamp.available_at or 0.0
    print(f"observed {label(first.stamp.observed_at)}, available {label(available)}")
    print(f"basis {first.stamp.basis.name}: {first.stamp.rule}")

    section("3. A source that says nothing about availability")
    weather_rows = [
        {
            "station": "KNYC",
            "metric": "heating_degree_days",
            "value": "12.5",
            "day": "2024-03-04 00:00",
        },
        {
            "station": "KNYC",
            "metric": "heating_degree_days",
            "value": "10.0",
            "day": "2024-03-05 00:00",
        },
    ]
    weather_source = observation_source(
        vendor_file("weather.csv", weather_rows, 1_717_000_300.0), "weather.degree_days", None
    )
    weather = ingest_observations(
        weather_rows,
        "weather",
        weather_source,
        ObservationColumns(
            category="weather",
            unit="degree_days",
            subject="station",
            metric="metric",
            value="value",
            observed_at="day",
            availability=AvailabilityNotDeclared(),
            effective_at=None,
            revision=None,
            period=None,
            ingested_at_retrieval=False,
        ),
        READING,
    ).observations
    visible = len(weather.view(PUB).select(1e12))
    print(
        f"{len(weather)} records ingested, {weather.unverified} with unknown availability, "
        f"{visible} ever visible to research"
    )

    section("4. Late-arriving data: two clocks")
    backfill = news.view(VisibilityRule.INGESTION)
    at = close_of(SESSIONS[10])
    print(
        f"at {label(at)}: published-visible {len(news.view(PUB).select(at))}, "
        f"ingested-visible {len(backfill.select(at))} -- the file was read on "
        f"{label(raw.retrieved_at)}, so this system knew none of it earlier"
    )

    section("5. Revisions are vintages, read as they were knowable")
    period = ReferencePeriod(0.0, 100.0, "2024-05")
    vintages = [
        ExternalObservation(
            category="economic",
            subject="US",
            metric="cpi_yoy",
            value=Decimal(value),
            unit="percent",
            stamp=PointInTimeStamp.declared(100.0, available),
            source=source,
            period=period,
            revision=revision,
        )
        for revision, (value, available) in enumerate((("3.1", 150.0), ("3.3", 300.0)))
    ]
    cpi = build_observation_set("cpi", vintages, source).view(PUB)
    key = vintages[0].vintage_key
    for at in (200.0, 300.0):
        known = cpi.vintage_as_of(key, at, VintagePolicy.AS_KNOWN)
        original = cpi.vintage_as_of(key, at, VintagePolicy.ORIGINAL)
        print(
            f"at {at:.0f}: as known {known.value if known else None}, "
            f"as first published {original.value if original else None}"
        )

    section("6. Set identity and lineage")
    only_aaa = restrict_subjects(news, ["AAA"])
    frozen = known_by(news, close_of(SESSIONS[20]), PUB)
    print(f"restricted to AAA : {only_aaa.version}")
    print(f"  parent          : {only_aaa.parent_version}")
    steps = [step.operation for step in frozen.transformations]
    print(f"known by session 20: {len(frozen)} records, steps {steps}")
    print(f"all verify: {all(verify_observation_set(s) for s in (news, only_aaa, frozen))}")

    section("7. A wire record, lifted with its timestamp's meaning declared")
    wire = [AlternativeDataRecord("AAA", close_of(SESSIONS[3]), "desk", 0.4)]
    as_period = lift_wire_records(wire, "wire", source, WireTimestamp.OBSERVATION, "score")
    as_release = lift_wire_records(wire, "wire", source, WireTimestamp.AVAILABILITY, "score")
    print(f"timestamp is the period only : basis {as_period.records[0].stamp.basis.name}")
    print(f"timestamp is the release     : basis {as_release.records[0].stamp.basis.name}")

    section("8. A knowledge frame on the price clock, joined and measured")
    prices = ingest_prices()
    closes = observations_from_dataset(prices, FeatureField.CLOSE)
    knowledge = observation_frame(
        news.view(PUB),
        "sentiment",
        "news_score",
        ResearchClock.of_frame(closes),
        SYMBOLS,
        max_age_seconds=4 * 86_400.0,
        policy=VintagePolicy.AS_KNOWN,
    )
    first_close = knowledge.frame.series["AAA"].timestamps[0]
    print(f"frame {knowledge.frame_id}")
    print(f"sampled on {knowledge.clock.dataset_version}")
    print(f"the 18:00 score, delivered 19:00, first reaches the close of {label(first_close)}")
    rank = FeatureDefinition(
        "news_rank",
        FeatureKind.CROSS_SECTIONAL_RANK,
        FeatureField.OBSERVATION,
        scope=FeatureScope.CROSS_SECTIONAL,
    )
    panel = compute_panel(rank, knowledge.frame)
    returns = forward_returns(align_prices(closes, knowledge), 1)
    ic = information_coefficient(panel, returns, minimum_assets=5)
    print(
        f"rank IC {ic.mean_rank:+.4f} over {ic.instants_measured} instants "
        f"({ic.observations} observations); hit rate {ic.hit_rate:.2f}"
    )


if __name__ == "__main__":
    main()
