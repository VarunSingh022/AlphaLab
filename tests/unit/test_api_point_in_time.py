"""The application-facing point-in-time ingestion: rows in, versioned sets out.

Rows are read the way an application hands them over -- strings from a CSV --
with the bytes they came from recorded through a
:class:`~alphalab.data.source.RawSource`. Every row becomes a record or comes
back as a :class:`~alphalab.data.validation.RowRejection` with its reason, and
every availability rule is exercised, including the one a source that states
nothing must declare.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.alt_data import (
    AltDataInputError,
    StatementType,
    VintagePolicy,
)
from alphalab.api import (
    AvailabilityAfterLag,
    AvailabilityAtNextOpen,
    AvailabilityFromColumn,
    AvailabilityNotDeclared,
    EventColumns,
    FundamentalColumns,
    ObservationColumns,
    TimestampReading,
    WireTimestamp,
    ingest_events,
    ingest_fundamentals,
    ingest_observations,
    lift_wire_records,
    observation_source,
)
from alphalab.common import AvailabilityBasis, VisibilityRule
from alphalab.data.exceptions import DataValidationError
from alphalab.data.feed import Bar, EconomicEvent, FundamentalRecord
from alphalab.data.source import SourceKind, raw_source_from_bytes
from alphalab.data.time import DateOnlyPolicy, TimestampFormat
from alphalab.data.validation import FindingKind
from tests.unit.alt_data.pit_harness import NYSE, ny

RAW = raw_source_from_bytes(
    SourceKind.VENDOR_FILE, "macro.csv", b"subject,metric,...", 1_730_000_000.0, "text/csv", "utf-8"
)
SOURCE = observation_source(RAW, "panel.macro", "2024.1")
AWARE = TimestampReading(TimestampFormat.ISO_8601_AWARE, None, None)


def _columns(**changes: object) -> ObservationColumns:
    arguments: dict[str, object] = {
        "category": "economic",
        "unit": "percent",
        "subject": "country",
        "metric": "series",
        "value": "value",
        "observed_at": "period_end",
        "availability": AvailabilityFromColumn("released"),
        "effective_at": None,
        "revision": "vintage",
        "period": None,
        "ingested_at_retrieval": False,
    }
    arguments.update(changes)
    return ObservationColumns(**arguments)  # type: ignore[arg-type]


ROWS = [
    {
        "country": "US",
        "series": "cpi_yoy",
        "value": "3.1",
        "period_end": "2024-04-30T23:59:59+00:00",
        "released": "2024-05-15T12:30:00+00:00",
        "vintage": "0",
    },
    {
        "country": "US",
        "series": "cpi_yoy",
        "value": "3.3",
        "period_end": "2024-04-30T23:59:59+00:00",
        "released": "2024-06-12T12:30:00+00:00",
        "vintage": "1",
    },
    {
        "country": "EU",
        "series": "cpi_yoy",
        "value": "2.4",
        "period_end": "2024-04-30T23:59:59+00:00",
        "released": "",
        "vintage": "0",
    },
]


def test_a_source_names_the_exact_bytes_it_was_read_from() -> None:
    assert SOURCE.content_hash == RAW.content_hash
    assert SOURCE.retrieved_at == RAW.retrieved_at
    assert SOURCE.byte_provenance


def test_rows_become_a_versioned_set_with_unknown_availability_stated() -> None:
    ingestion = ingest_observations(ROWS, "cpi", SOURCE, _columns(), AWARE)
    records = {(r.subject, r.revision): r for r in ingestion.observations.records}

    assert ingestion.rejected == ()
    assert ingestion.observations.version.startswith("cpi@")
    assert records[("US", 1)].stamp.available_at == ny("2024-06-12 08:30")
    assert records[("EU", 0)].stamp.basis is AvailabilityBasis.UNKNOWN
    assert ingestion.observations.unverified == 1
    assert ingest_observations(ROWS, "cpi", SOURCE, _columns(), AWARE).observations.version == (
        ingestion.observations.version
    )


def test_every_unreadable_row_comes_back_with_its_line_and_reason() -> None:
    bad = [
        {**ROWS[0], "value": "three"},
        {**ROWS[0], "period_end": "yesterday"},
        {**ROWS[0], "country": ""},
        {**ROWS[0], "released": "2024-04-01T00:00:00+00:00"},
        {**ROWS[0], "value": "NaN"},
        ROWS[0],
        ROWS[0],
    ]

    ingestion = ingest_observations(bad, "cpi", SOURCE, _columns(), AWARE)
    kinds = [rejection.findings[0].kind for rejection in ingestion.rejected]

    assert kinds == [
        FindingKind.NON_NUMERIC,
        FindingKind.UNPARSEABLE_TIMESTAMP,
        FindingKind.MISSING_VALUE,
        FindingKind.INCONSISTENT_RECORD,
        FindingKind.NON_FINITE,
        FindingKind.DUPLICATE_TIMESTAMP,
    ]
    assert [rejection.line_number for rejection in ingestion.rejected] == [1, 2, 3, 4, 5, 7]
    assert len(ingestion.observations) == 1


def test_a_row_set_with_nothing_usable_is_refused() -> None:
    with pytest.raises(DataValidationError, match="No row"):
        ingest_observations([{**ROWS[0], "value": "x"}], "cpi", SOURCE, _columns(), AWARE)


def test_two_values_for_one_revision_are_refused_rather_than_one_dropped() -> None:
    rival = {**ROWS[0], "value": "3.9", "released": "2024-05-16T12:30:00+00:00"}

    with pytest.raises(AltDataInputError, match="claim revision 0"):
        ingest_observations([ROWS[0], rival], "cpi", SOURCE, _columns(), AWARE)


def test_a_declared_lag_derives_availability_and_records_its_rule() -> None:
    ingestion = ingest_observations(
        ROWS, "cpi", SOURCE, _columns(availability=AvailabilityAfterLag(86_400.0)), AWARE
    )

    stamp = ingestion.observations.records[0].stamp
    assert stamp.basis is AvailabilityBasis.DERIVED
    assert stamp.available_at == stamp.observed_at + 86_400.0
    assert "declared lag" in stamp.rule
    with pytest.raises(DataValidationError, match="non-negative"):
        ingest_observations(
            ROWS, "cpi", SOURCE, _columns(availability=AvailabilityAfterLag(-1.0)), AWARE
        )


def test_a_source_that_states_nothing_is_ingested_and_never_visible() -> None:
    ingestion = ingest_observations(
        ROWS, "cpi", SOURCE, _columns(availability=AvailabilityNotDeclared()), AWARE
    )

    assert ingestion.observations.unverified == 3
    assert len(ingestion.observations.view(VisibilityRule.PUBLICATION).select(1e12)) == 0


def test_the_retrieval_instant_stamps_ingestion_for_the_ingestion_rule() -> None:
    ingestion = ingest_observations(
        ROWS, "cpi", SOURCE, _columns(ingested_at_retrieval=True), AWARE
    )
    view = ingestion.observations.view(VisibilityRule.INGESTION)

    assert all(r.stamp.ingested_at == RAW.retrieved_at for r in ingestion.observations.records)
    assert len(view.select(RAW.retrieved_at - 1.0)) == 0
    assert len(view.select(RAW.retrieved_at)) == 2


def test_periods_are_read_when_declared() -> None:
    rows = [{**ROWS[0], "start": "2024-04-01T00:00:00+00:00", "label": "2024-04"}]

    ingestion = ingest_observations(
        rows, "cpi", SOURCE, _columns(period=("start", "period_end", "label")), AWARE
    )

    (record,) = ingestion.observations.records
    assert record.period is not None and record.period.label == "2024-04"


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


EVENT_COLUMNS = EventColumns(
    subject="ticker",
    event_type="kind",
    observed_at="announced",
    availability=AvailabilityFromColumn("delivered"),
    measurements=("actual", "consensus"),
    attributes=("period",),
    effective_at=None,
    revision=None,
    ingested_at_retrieval=False,
)


def test_event_rows_carry_measurements_attributes_and_their_absence() -> None:
    rows = [
        {
            "ticker": "AAA",
            "kind": "earnings.release",
            "announced": "2024-05-07T20:05:00+00:00",
            "delivered": "2024-05-07T20:06:00+00:00",
            "actual": "1.52",
            "consensus": "1.45",
            "period": "FY2024Q1",
        },
        {
            "ticker": "BBB",
            "kind": "earnings.release",
            "announced": "2024-05-08T11:00:00+00:00",
            "delivered": "2024-05-08T11:00:00+00:00",
            "actual": "0.90",
            "consensus": "",
            "period": "",
        },
    ]

    ingestion = ingest_events(rows, "earnings", SOURCE, EVENT_COLUMNS, AWARE)
    by_subject = {event.subject: event for event in ingestion.observations.records}

    assert by_subject["AAA"].measurements == {
        "actual": Decimal("1.52"),
        "consensus": Decimal("1.45"),
    }
    assert by_subject["AAA"].attributes == {"period": "FY2024Q1"}
    assert set(by_subject["BBB"].measurements) == {"actual"}
    assert by_subject["BBB"].attributes == {}


# --------------------------------------------------------------------------- #
# Fundamentals
# --------------------------------------------------------------------------- #


FUNDAMENTAL_COLUMNS = FundamentalColumns(
    subject="issuer",
    statement="statement",
    line_item="item",
    fiscal_year="fy",
    fiscal_quarter="fq",
    period_start="start",
    period_end="end",
    value="value",
    unit="unit",
    published_at="filed",
    availability=AvailabilityAtNextOpen("filed", NYSE),
    revision="restatement",
    ingested_at_retrieval=False,
)
DATES = TimestampReading(TimestampFormat.DATE_ONLY, "America/New_York", DateOnlyPolicy.END_OF_DAY)


def _filing(**changes: str) -> dict[str, str]:
    row = {
        "issuer": "AAA",
        "statement": "INCOME_STATEMENT",
        "item": "revenue",
        "fy": "2024",
        "fq": "1",
        "start": "2024-01-01",
        "end": "2024-03-31",
        "value": "120",
        "unit": "USD",
        "filed": "2024-05-07",
        "restatement": "0",
    }
    row.update(changes)
    return row


def test_a_date_only_filing_becomes_available_at_the_next_open() -> None:
    ingestion = ingest_fundamentals([_filing()], "filings", SOURCE, FUNDAMENTAL_COLUMNS, DATES)

    (record,) = ingestion.observations.records
    assert record.statement is StatementType.INCOME_STATEMENT
    assert record.stamp.basis is AvailabilityBasis.DERIVED
    assert record.stamp.available_at == ny("2024-05-08 09:30"), "filed on the 7th, read at its end"
    assert "XNYS" in record.stamp.rule


def test_restatements_ingest_as_vintages_readable_as_knowable() -> None:
    rows = [_filing(), _filing(value="104", filed="2024-09-10", restatement="1")]

    ingestion = ingest_fundamentals(rows, "filings", SOURCE, FUNDAMENTAL_COLUMNS, DATES)
    view = ingestion.observations.view(VisibilityRule.PUBLICATION)
    vintage = ingestion.observations.records[0].vintage_key
    before = view.vintage_as_of(vintage, ny("2024-09-10 12:00"), VintagePolicy.AS_KNOWN)
    after = view.vintage_as_of(vintage, ny("2024-09-11 12:00"), VintagePolicy.AS_KNOWN)

    assert before is not None and str(before.value) == "120"
    assert after is not None and str(after.value) == "104"


def test_impossible_filings_are_rejected_with_reasons() -> None:
    rows = [
        _filing(statement="PROFIT_AND_LOSS"),
        _filing(filed="2024-03-15"),
        _filing(fq="five"),
        _filing(),
    ]

    ingestion = ingest_fundamentals(rows, "filings", SOURCE, FUNDAMENTAL_COLUMNS, DATES)
    kinds = [rejection.findings[0].kind for rejection in ingestion.rejected]

    assert kinds == [
        FindingKind.INCONSISTENT_RECORD,
        FindingKind.INCONSISTENT_RECORD,
        FindingKind.NON_NUMERIC,
    ]
    assert len(ingestion.observations) == 1


# --------------------------------------------------------------------------- #
# Wire records
# --------------------------------------------------------------------------- #


def test_a_wire_record_lifts_with_its_timestamp_meaning_declared() -> None:
    records = [FundamentalRecord("AAA", 1_714_000_000.0, "revenue", 120.0)]

    as_available = lift_wire_records(records, "wire", SOURCE, WireTimestamp.AVAILABILITY, "USD")
    as_observed = lift_wire_records(records, "wire", SOURCE, WireTimestamp.OBSERVATION, "USD")

    assert as_available.records[0].stamp.available_at == 1_714_000_000.0
    assert as_observed.records[0].stamp.basis is AvailabilityBasis.UNKNOWN
    assert as_observed.version != as_available.version


def test_an_economic_event_lifts_its_actual_print() -> None:
    (record,) = lift_wire_records(
        [EconomicEvent("US", 1.0, "nonfarm_payrolls", 272.0, 180.0, 165.0)],
        "wire",
        SOURCE,
        WireTimestamp.AVAILABILITY,
        "thousands",
    ).records

    assert record.category == "economic"
    assert str(record.value) == "272.0"


def test_wire_records_that_cannot_be_lifted_are_refused() -> None:
    with pytest.raises(DataValidationError, match="no wire records"):
        lift_wire_records([], "wire", SOURCE, WireTimestamp.AVAILABILITY, "USD")
    with pytest.raises(DataValidationError, match="carries no external observation"):
        lift_wire_records(
            [Bar("AAA", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)],
            "wire",
            SOURCE,
            WireTimestamp.AVAILABILITY,
            "USD",
        )
    with pytest.raises(AltDataInputError, match="identifier"):
        lift_wire_records(
            [FundamentalRecord("AAA", 1.0, "Total Revenue", 1.0)],
            "wire",
            SOURCE,
            WireTimestamp.AVAILABILITY,
            "USD",
        )
