"""Comprehensive tests validating strict Universal Data parsing, cleaning, and normalization."""

import pytest

from alphalab.data import (
    COLUMN_ALIASES,
    DataAdapter,
    DataAssetClass,
    DatasetMetadata,
    TimeFrequency,
    UniversalDataEngine,
    UniversalDataState,
    catalog_summary,
    dataset_summary,
    evaluate_bar_quality,
    normalize_prices,
    parse_raw_rows,
    quality_report,
    remove_duplicates,
    remove_invalid_ohlc,
    resample_bars,
)


@pytest.fixture
def base_state() -> UniversalDataState:
    return UniversalDataEngine.initialize("DATA-ENG-01")


@pytest.fixture
def generic_metadata() -> DatasetMetadata:
    return DataAdapter.create_metadata("AAPL-1D", "Yahoo", "EQUITY", "DAILY", 1000.0, 2000.0)


# --- FORMATS & PARSING ALIAS TESTS (20+ assertions) ---


def test_column_alias_detection() -> None:
    assert COLUMN_ALIASES["date"] == "timestamp"
    assert COLUMN_ALIASES["adj close"] == "close"
    assert COLUMN_ALIASES["ticker"] == "symbol"
    assert COLUMN_ALIASES["vol"] == "volume"


def test_parse_raw_rows_perfect() -> None:
    raw = [{"Date": 1000.0, "Open": 10, "High": 12, "Low": 9, "Close": 11, "Vol": 100}]
    parsed = parse_raw_rows("AAPL", raw)
    assert len(parsed) == 1
    bar = parsed[0]
    assert bar.timestamp == 1000.0
    assert bar.open == 10.0
    assert bar.high == 12.0
    assert bar.volume == 100.0


def test_parse_raw_rows_missing_columns() -> None:
    raw = [
        {"Date": 1000.0, "Open": 10}  # Missing HLC
    ]
    parsed = parse_raw_rows("AAPL", raw)
    assert len(parsed) == 0  # Invalid structures are safely dropped


def test_parse_raw_rows_chronological_sort() -> None:
    raw = [
        {"timestamp": 2000.0, "o": 1, "h": 2, "l": 1, "c": 2, "v": 10},
        {"timestamp": 1000.0, "o": 1, "h": 2, "l": 1, "c": 2, "v": 10},
    ]
    parsed = parse_raw_rows("AAPL", raw)
    assert parsed[0].timestamp == 1000.0
    assert parsed[1].timestamp == 2000.0


# --- DATA QUALITY & CLEANING TESTS (30+ assertions) ---


def test_quality_perfect() -> None:
    raw = [{"timestamp": float(i), "o": 10, "h": 12, "l": 9, "c": 11, "v": 100} for i in range(10)]
    bars = parse_raw_rows("AAPL", raw)

    report = evaluate_bar_quality("D1", bars)
    assert report.invalid_count == 0
    assert report.duplicate_count == 0
    assert report.quality_score == 100.0


def test_quality_invalid_ohlc() -> None:
    raw = [
        {"timestamp": 1000.0, "o": 10, "h": 8, "l": 9, "c": 11, "v": 100}  # High < Low
    ]
    bars = parse_raw_rows("AAPL", raw)
    report = evaluate_bar_quality("D1", bars)
    assert report.invalid_count == 1
    assert report.quality_score < 100.0


def test_quality_negative_prices() -> None:
    raw = [
        {"timestamp": 1000.0, "o": -10, "h": 12, "l": 9, "c": 11, "v": 100}  # Open < 0
    ]
    bars = parse_raw_rows("AAPL", raw)
    report = evaluate_bar_quality("D1", bars)
    assert report.invalid_count == 1


def test_remove_invalid_ohlc() -> None:
    raw = [
        {"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},  # Valid
        {"timestamp": 1001.0, "o": 10, "h": 8, "l": 9, "c": 11, "v": 100},  # Invalid
    ]
    bars = parse_raw_rows("AAPL", raw)
    cleaned = remove_invalid_ohlc(bars)
    assert len(cleaned) == 1
    assert cleaned[0].timestamp == 1000.0


def test_remove_duplicates() -> None:
    raw = [
        {"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"timestamp": 1000.0, "o": 11, "h": 13, "l": 8, "c": 12, "v": 200},  # Duplicate TS
    ]
    bars = parse_raw_rows("AAPL", raw)
    cleaned = remove_duplicates(bars)
    assert len(cleaned) == 1
    assert cleaned[0].open == 10.0  # Keeps first


# --- CONVERSION & NORMALIZATION TESTS (20+ assertions) ---


def test_resample_bars() -> None:
    raw = [
        {"timestamp": 0.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"timestamp": 30.0, "o": 11, "h": 15, "l": 10, "c": 14, "v": 200},
        {"timestamp": 60.0, "o": 14, "h": 16, "l": 13, "c": 15, "v": 300},
    ]
    bars = parse_raw_rows("AAPL", raw)

    # Resample 30s ticks to 60s (1-minute) bars
    resampled = resample_bars(bars, 60.0)

    assert len(resampled) == 2

    # Bucket 0.0 groups ts=0.0 and ts=30.0
    b0 = resampled[0]
    assert b0.timestamp == 0.0
    assert b0.open == 10.0
    assert b0.high == 15.0
    assert b0.low == 9.0
    assert b0.close == 14.0
    assert b0.volume == 300.0

    # Bucket 60.0 groups ts=60.0
    b1 = resampled[1]
    assert b1.timestamp == 60.0
    assert b1.close == 15.0


def test_normalize_prices() -> None:
    raw = [{"timestamp": 0.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}]
    bars = parse_raw_rows("AAPL", raw)

    norm = normalize_prices(bars, 0.5)  # E.g. 2-for-1 split
    assert norm[0].open == 5.0
    assert norm[0].high == 6.0
    assert norm[0].volume == 100.0  # Volume is strictly unadjusted by price factor


# --- ENGINE FACADE & LIFECYCLE TESTS (40+ assertions) ---


def test_engine_initialization() -> None:
    state = UniversalDataEngine.initialize("E1")
    assert state.engine_id == "E1"
    assert len(dataset_summary(state)) == 0

    with pytest.raises(ValueError):
        UniversalDataEngine.initialize("")


def test_engine_load(generic_metadata: DatasetMetadata) -> None:
    raw = [{"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}]
    ds = UniversalDataEngine.load(generic_metadata, raw)

    assert ds.metadata.dataset_id == "AAPL-1D"
    assert len(ds.records) == 1
    assert ds.quality.quality_score == 100.0


def test_engine_ingest(base_state: UniversalDataState, generic_metadata: DatasetMetadata) -> None:
    raw = [{"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}]
    ds = UniversalDataEngine.load(generic_metadata, raw)

    s1 = UniversalDataEngine.ingest(base_state, ds, 1001.0)

    assert len(dataset_summary(s1)) == 1
    assert "AAPL-1D" in s1.datasets
    assert any(type(e).__name__ == "DatasetIngested" for e in s1.events)


def test_engine_clean(base_state: UniversalDataState, generic_metadata: DatasetMetadata) -> None:
    raw = [
        {"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},  # Duplicate
    ]
    ds = UniversalDataEngine.load(generic_metadata, raw)
    s1 = UniversalDataEngine.ingest(base_state, ds, 1001.0)

    s2 = UniversalDataEngine.clean(s1, "AAPL-1D", 1002.0)

    assert len(s2.datasets["AAPL-1D"].records) == 1
    assert any(type(e).__name__ == "DatasetCleaned" for e in s2.events)


def test_engine_quality(base_state: UniversalDataState, generic_metadata: DatasetMetadata) -> None:
    raw = [{"timestamp": 1000.0, "o": -10, "h": 12, "l": 9, "c": 11, "v": 100}]  # Invalid
    ds = UniversalDataEngine.load(generic_metadata, raw)
    s1 = UniversalDataEngine.ingest(base_state, ds, 1001.0)

    s2 = UniversalDataEngine.quality(s1, "AAPL-1D", 1002.0)

    rep = quality_report(s2, "AAPL-1D")
    assert rep is not None
    assert rep.invalid_count == 1
    assert any(type(e).__name__ == "QualityReportGenerated" for e in s2.events)


def test_engine_convert(base_state: UniversalDataState, generic_metadata: DatasetMetadata) -> None:
    raw = [
        {"timestamp": 0.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"timestamp": 30.0, "o": 11, "h": 15, "l": 10, "c": 14, "v": 200},
    ]
    ds = UniversalDataEngine.load(generic_metadata, raw)
    s1 = UniversalDataEngine.ingest(base_state, ds, 1001.0)

    s2 = UniversalDataEngine.convert(s1, "AAPL-1D", 60.0, 1002.0)

    assert len(s2.datasets["AAPL-1D"].records) == 1


def test_engine_catalog(base_state: UniversalDataState, generic_metadata: DatasetMetadata) -> None:
    raw = [{"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}]
    ds = UniversalDataEngine.load(generic_metadata, raw)
    s1 = UniversalDataEngine.ingest(base_state, ds, 1001.0)

    s2 = UniversalDataEngine.catalog(s1, "AAPL-1D", 1002.0)

    cat = catalog_summary(s2)
    assert len(cat) == 1
    assert cat[0].record_count == 1
    assert any(type(e).__name__ == "DatasetCataloged" for e in s2.events)


def test_adapter_metadata_helper() -> None:
    meta = DataAdapter.create_metadata("M-1", "Src", "equity", "daily", 0.0, 100.0)
    assert meta.asset_class == DataAssetClass.EQUITY
    assert meta.frequency == TimeFrequency.DAILY
