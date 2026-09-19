"""The application-facing API: a file in, a dataset with lineage out.

This is the surface a host platform calls. What it has to get right is not
cleverness but *honesty*: the dataset it returns must be able to say where it
came from, and it must refuse rather than invent when it cannot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alphalab.api import (
    DataRequest,
    clean_dataset,
    ingest_csv,
    ingest_rows,
    inspect_csv,
    normalize_records,
    select,
    to_market_dataset,
    validate_dataset,
)
from alphalab.data import (
    CleaningPolicy,
    DataAssetClass,
    DataValidationError,
    DuplicatePolicy,
    IngestionRequest,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    PriceBasis,
    RecordType,
    SourceKind,
    TimeFrequency,
    TimestampFormat,
    raw_source_from_bytes,
)
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy

SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "data" / "sample_ohlcv.csv"
RETRIEVED_AT = 1_726_000_000.0

POLICY = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)

NORMALIZATION = NormalizationPolicy(venue="XNYS", currency="USD", timeframe=TimeFrame.D1)


def _request(name: str = "SAMPLE", **overrides: object) -> IngestionRequest:
    fields: dict[str, object] = {
        "name": name,
        "source": raw_source_from_bytes(
            SourceKind.LOCAL_FILE, "placeholder", b"", RETRIEVED_AT, "text/csv"
        ),
        "frequency": TimeFrequency.DAILY,
        "asset_class": DataAssetClass.EQUITY,
        "cleaning_policy": POLICY,
        "price_basis": PriceBasis.RAW,
    }
    fields.update(overrides)
    return IngestionRequest(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The dry run
# --------------------------------------------------------------------------- #


def test_inspecting_a_file_commits_to_nothing_and_reports_everything() -> None:
    source, table, detection = inspect_csv(SAMPLE, RETRIEVED_AT)

    assert source.kind is SourceKind.LOCAL_FILE
    assert source.byte_count == SAMPLE.stat().st_size
    assert len(source.content_hash) == 64

    assert len(table.rows) == 90
    assert table.malformed == ()

    assert detection.is_resolved is True
    assert detection.record_type is RecordType.BAR
    assert detection.timestamp_format is TimestampFormat.ISO_8601_AWARE


def test_inspection_reports_the_columns_it_did_not_recognise() -> None:
    _, _, detection = inspect_csv(SAMPLE, RETRIEVED_AT)

    assert detection.unmapped_columns == ("dataset_id",), (
        "carried, not discarded -- it is the file's own metadata column"
    )


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def test_ingesting_a_csv_records_the_file_that_was_actually_read() -> None:
    """The request's placeholder source is replaced, so a caller cannot record
    provenance that does not match the bytes ingested."""

    result = ingest_csv(SAMPLE, _request())
    provenance = result.dataset.require_provenance()

    assert provenance.source.location == str(SAMPLE)
    assert provenance.source.byte_count == SAMPLE.stat().st_size
    assert provenance.source.location != "placeholder"


def test_an_ingested_dataset_carries_its_records_and_a_quality_report() -> None:
    result = ingest_csv(SAMPLE, _request())

    assert len(result.dataset.records) == 90
    assert result.quality.row_count == 90
    assert result.quality.valid_rows == 90
    assert result.quality.rejected_count == 0
    assert result.quality.quality_score == 100.0
    assert result.dataset.quality == result.quality.summarize()


def test_rows_in_memory_and_the_same_rows_in_a_file_agree() -> None:
    """Two paths that agreed only approximately would make a dataset's identity
    depend on how it was delivered."""

    rows = [
        {
            "symbol": "AAPL",
            "timestamp": "2025-01-02T16:00:00Z",
            "open": "1",
            "high": "2",
            "low": "0.5",
            "close": "1.5",
            "volume": "10",
        },
        {
            "symbol": "AAPL",
            "timestamp": "2025-01-03T16:00:00Z",
            "open": "1",
            "high": "2",
            "low": "0.5",
            "close": "1.6",
            "volume": "11",
        },
    ]
    source = raw_source_from_bytes(
        SourceKind.IN_MEMORY, "fixture", b"rows", RETRIEVED_AT, "application/json"
    )

    result = ingest_rows(rows, _request(source=source))

    assert len(result.dataset.records) == 2
    assert result.dataset.require_provenance().source.kind is SourceKind.IN_MEMORY


def test_ingesting_no_rows_is_refused() -> None:
    with pytest.raises(DataValidationError):
        ingest_rows([], _request())


def test_an_ingestion_reports_its_assumptions() -> None:
    rows = [
        {"timestamp": "1735833600", "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
    ]
    source = raw_source_from_bytes(
        SourceKind.IN_MEMORY, "fixture", b"rows", RETRIEVED_AT, "text/csv"
    )

    result = ingest_rows(rows, _request(symbol="AAPL", source=source))

    joined = " ".join(result.assumptions)
    assert "Unix seconds" in joined, "the numeric-timestamp convention is stated"
    assert "no column identifies the instrument" in joined


# --------------------------------------------------------------------------- #
# The stages
# --------------------------------------------------------------------------- #


def test_validating_a_clean_dataset_finds_nothing() -> None:
    assert validate_dataset(ingest_csv(SAMPLE, _request()).dataset) == ()


def test_cleaning_reports_the_data_as_it_was_found_not_as_it_ended_up() -> None:
    """Reporting quality after cleaning would make every dataset look clean."""

    dataset = ingest_csv(SAMPLE, _request()).dataset
    records, report = clean_dataset(dataset, POLICY)

    assert len(records) == 90
    assert report.row_count == 90


def test_normalizing_lifts_wire_floats_into_canonical_decimals() -> None:
    from decimal import Decimal

    dataset = ingest_csv(SAMPLE, _request()).dataset
    lifted = normalize_records(dataset.records[:1], NORMALIZATION)

    assert isinstance(lifted[0].close, Decimal)  # type: ignore[union-attr]
    assert lifted[0].close == Decimal("151.20")  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# Research access
# --------------------------------------------------------------------------- #


def test_a_selection_names_the_dataset_it_came_from() -> None:
    dataset = ingest_csv(SAMPLE, _request()).dataset

    selection = select(dataset, DataRequest(symbols=("AAPL",)))

    assert len(selection) == 30
    assert {record.symbol for record in selection.records} == {"AAPL"}
    assert selection.dataset_version == dataset.dataset_version


def test_a_time_range_bounds_the_selection() -> None:
    dataset = ingest_csv(SAMPLE, _request()).dataset
    stamps = sorted({record.timestamp for record in dataset.records})

    selection = select(dataset, DataRequest(start=stamps[1], end=stamps[3]))

    assert {record.timestamp for record in selection.records} == {stamps[1], stamps[2]}


def test_a_point_in_time_cut_excludes_what_was_not_yet_knowable() -> None:
    dataset = ingest_csv(SAMPLE, _request()).dataset
    stamps = sorted({record.timestamp for record in dataset.records})

    selection = select(dataset, DataRequest(as_of=stamps[0]))

    assert all(record.timestamp <= stamps[0] for record in selection.records)
    assert len(selection) == 3, "one bar for each of the three instruments"


def test_asking_for_a_basis_the_dataset_is_not_on_is_refused() -> None:
    """Converting would need corporate actions a selection does not have."""

    dataset = ingest_csv(SAMPLE, _request()).dataset

    with pytest.raises(DataValidationError) as error:
        select(dataset, DataRequest(price_basis=PriceBasis.TOTAL_RETURN))

    assert "PriceBasis.RAW" in str(error.value)
    assert "ingest the dataset on the basis you need" in str(error.value)


def test_a_request_naming_the_matching_basis_is_allowed() -> None:
    dataset = ingest_csv(SAMPLE, _request()).dataset

    assert len(select(dataset, DataRequest(price_basis=PriceBasis.RAW))) == 90


# --------------------------------------------------------------------------- #
# The join to a run
# --------------------------------------------------------------------------- #


def test_the_market_dataset_carries_the_derived_version_as_its_identity() -> None:
    dataset = ingest_csv(SAMPLE, _request()).dataset

    market = to_market_dataset(dataset, NORMALIZATION)

    assert market.dataset_id == dataset.dataset_version
    assert len(market) == 90


def test_the_market_dataset_is_chronological_across_instruments() -> None:
    """The source is grouped by instrument; a run needs one ordered stream."""

    dataset = ingest_csv(SAMPLE, _request()).dataset

    market = to_market_dataset(dataset, NORMALIZATION)
    stamps = [record.timestamp for record in market.records]

    assert stamps == sorted(stamps)
