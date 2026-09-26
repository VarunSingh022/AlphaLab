"""
AlphaLab Examples
=================

Example 50 : Event-Driven Research

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 16 (research access by dataset version)
✓ Example 32 (exchange calendars and sessions)

Topics
------

• One canonical event record for earnings, macro prints, corporate actions, news
• Four instants kept apart: occurred, knowable, in effect, ingested
• Where information falls in the trading day -- after the close, before the open
• What was visible at an instant, and a feed that never said when it delivered
• An event study anchored where the news could first be traded
• Excluded events reported with their reasons, and no p-value anywhere

What this shows
---------------

An earnings release at 16:05 is known at 16:05 and cannot be traded until the
next morning. A study that anchors on the announcement instant credits a
strategy with a reaction nobody could have traded, so every event here is
anchored at the first session open at or after the instant it became
*knowable*, and the anchor observation is the first close after that. A release
whose delivery the feed never recorded is ingested -- the record exists -- and
never anchored, because there is no instant to anchor it on. A correction is not
a second event.

Run

    python examples/50_event_driven_research.py
"""

from _point_in_time import (
    NYSE,
    banner,
    earnings_rows,
    ingest_prices,
    label,
    ny,
    section,
    vendor_file,
)

from alphalab.alt_data import PointInTimeError, place_in_session, place_record, surprise
from alphalab.api import (
    AvailabilityFromColumn,
    EventColumns,
    TimestampReading,
    ingest_events,
    observation_source,
)
from alphalab.common import PointInTimeStamp, VisibilityRule
from alphalab.data.time import TimestampFormat
from alphalab.factor_library import FeatureField, observations_from_dataset
from alphalab.research import (
    AbnormalReturnModel,
    EventStudyDefinition,
    EventWindow,
    event_study,
    event_study_metrics,
)

PUB = VisibilityRule.PUBLICATION


def main() -> None:
    banner(50, "Event-Driven Research")

    section("1. The calendar a vendor delivered, as a versioned set")
    rows = earnings_rows()
    source = observation_source(
        vendor_file("earnings.csv", rows, 1_717_000_100.0), "calendar.earnings", "2024.2"
    )
    columns = EventColumns(
        subject="ticker",
        event_type="kind",
        observed_at="announced",
        availability=AvailabilityFromColumn("delivered"),
        measurements=("actual", "consensus"),
        attributes=(),
        effective_at=None,
        revision="revision",
        ingested_at_retrieval=False,
    )
    reading = TimestampReading(TimestampFormat.ISO_8601_NAIVE, "America/New_York", None)
    events = ingest_events(rows, "earnings", source, columns, reading).observations
    print(f"set version   : {events.version}")
    digest = (source.content_hash or "")[:16]
    print(f"source        : {source.source_id} {source.version}, bytes {digest}...")
    print(f"records       : {len(events)}, with unknown availability: {events.unverified}")

    section("2. Where each release fell in New York's trading day")
    for record in sorted(events.records, key=lambda event: event.stamp.observed_at):
        if record.revision:
            continue
        surprised = surprise(record, "actual", "consensus")
        try:
            placement = place_record(record, PUB, NYSE)
        except PointInTimeError:
            print(
                f"{record.subject}  announced {label(record.stamp.observed_at)}  "
                f"surprise {surprised:+}  delivery never recorded -> never visible"
            )
            continue
        print(
            f"{record.subject}  announced {label(record.stamp.observed_at)}  surprise "
            f"{surprised:+}  {placement.timing.name:<16} tradable {label(placement.tradable_at)}"
        )

    section("3. Four instants, kept apart: a split announced, then effective")
    split = PointInTimeStamp.declared(
        ny("2024-04-02 08:00"), ny("2024-04-02 08:00"), effective_at=ny("2024-04-22 09:30")
    )
    for moment in ("2024-04-10 12:00", "2024-04-22 09:30"):
        at = ny(moment)
        print(
            f"at {moment}: known={split.is_visible(at, PUB)}  "
            f"in effect={split.in_effect_at(at, PUB)}"
        )
    backfill = PointInTimeStamp.declared(
        ny("2024-04-02 08:00"), ny("2024-04-02 08:00"), ingested_at=ny("2024-05-01 00:00")
    )
    at = ny("2024-04-15 12:00")
    print(
        f"a backfill on 2024-04-15: published-visible={backfill.is_visible(at, PUB)}  "
        f"ingested-visible={backfill.is_visible(at, VisibilityRule.INGESTION)}"
    )

    section("4. What was visible, instant by instant")
    view = events.view(PUB)
    for moment in ("2024-04-16 16:00", "2024-04-16 16:06", "2024-04-17 11:00", "2024-05-31 16:00"):
        selection = view.select(ny(moment))
        print(
            f"{moment}: visible {len(selection):>2}  pending {selection.pending:>2}  "
            f"unverified {selection.unverified}"
        )
    placement = place_in_session(ny("2024-04-20 10:00"), NYSE)
    print(f"a Saturday release: {placement.timing.name}, tradable {label(placement.tradable_at)}")

    section("5. The event study, anchored where the news could be traded")
    prices = ingest_prices()
    definition = EventStudyDefinition(
        name="earnings-reaction",
        event_types=("earnings.release",),
        window=EventWindow(before=3, after=3, estimation=10, gap=2),
        model=AbnormalReturnModel.MEAN_ADJUSTED,
        benchmark=None,
        visibility=PUB,
    )
    result = event_study(
        events, observations_from_dataset(prices, FeatureField.CLOSE), NYSE, definition
    )
    for outcome in result.outcomes:
        print(
            f"{outcome.subject}  known {label(outcome.known_at)}  anchor {label(outcome.anchor)}  "
            f"AR[0] {outcome.abnormal_returns[3]:+.4f}  CAR {outcome.cumulative:+.4f}"
        )
    print("average abnormal return by offset:")
    for offset, value in zip(result.offsets, result.average_abnormal, strict=True):
        print(f"  {offset:+d}: {value:+.5f}")
    print(
        f"mean CAR {result.mean_cumulative:+.4f}, median {result.median_cumulative:+.4f}, "
        f"positive {result.positive_share:.0%}, events sharing an anchor "
        f"{result.shared_anchor_events}"
    )
    print("excluded:")
    for excluded in result.excluded:
        print(f"  {excluded.subject}: {excluded.reason}")

    section("6. A result that names what it measured")
    print(f"study id  : {definition.study_id}")
    print(f"result id : {result.result_id}  (verifies: {result.verify()})")
    print(f"metrics   : {sorted(event_study_metrics(result))[:6]} ...")
    print("There is no t-statistic: releases cluster in calendar time, so a test that")
    print("assumes independent events overstates its confidence by an unknown amount.")


if __name__ == "__main__":
    main()
