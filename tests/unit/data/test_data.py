"""Comprehensive tests validating strict Universal Data parsing, cleaning, and normalization."""

import pytest

from alphalab.data import (
    COLUMN_ALIASES,
    CleaningPolicy,
    DataAdapter,
    DataAssetClass,
    DatasetCleaned,
    DatasetMetadata,
    DuplicatePolicy,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    TimeFrequency,
    UniversalDataEngine,
    UniversalDataState,
    catalog_summary,
    dataset_lineage,
    dataset_summary,
    evaluate_bar_quality,
    normalize_prices,
    parse_raw_rows,
    quality_report,
    remove_duplicates,
    remove_invalid_ohlc,
    resample_bars,
)
from alphalab.data.exceptions import DataValidationError

#: A policy that permits everything cleaning can do. Stated here rather than
#: defaulted anywhere in the package: what may be altered is the data owner's
#: decision, and v3.1 removed the implicit one these tests used to rely on.
PERMISSIVE = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
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


def test_parse_raw_rows_refuses_rows_it_cannot_translate() -> None:
    """v3.1 change: rows that cannot become bars are refused, never dropped.

    Until v3.1 this returned an empty tuple and said nothing, so a caller who
    passed ten rows and received seven bars had no way to learn that three had
    gone or why. Every figure computed downstream was computed on data the
    caller had not seen. The refusal is the whole point; the message has to be
    actionable, so it names the path that *can* do a partial load.
    """

    raw = [
        {"Date": 1000.0, "Open": 10}  # Missing HLC
    ]

    with pytest.raises(DataValidationError) as error:
        parse_raw_rows("AAPL", raw)

    message = str(error.value)
    assert "HIGH" in message and "LOW" in message and "CLOSE" in message, (
        "the refusal names the roles nothing supplied"
    )


def test_parse_raw_rows_refuses_one_bad_row_among_good_ones() -> None:
    """And it refuses the whole call rather than quietly returning the rest."""

    raw: list[dict[str, object]] = [
        {"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"timestamp": 1001.0, "o": 10, "h": 12, "l": 9, "c": "n/a", "v": 100},
        {"timestamp": 1002.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
    ]

    with pytest.raises(DataValidationError) as error:
        parse_raw_rows("AAPL", raw)

    message = str(error.value)
    assert "1 of 3 rows" in message, "it says how much was at stake"
    assert "'n/a' is not a number" in message, "and which value was the problem"
    assert "row 2" in message, "and where it was"
    assert "ingest_rows" in message, "and how to load the other two anyway"


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
    """Cleaning derives a new version and leaves the original exactly as it was.

    Until v3.1 this replaced ``state.datasets["AAPL-1D"]`` in place, so the raw
    data stopped existing the moment anything was done to it and any evidence
    naming that id became unverifiable -- the id still resolved, to different
    numbers. Both versions now live in state, and ``lineage`` says which came
    from which.
    """

    raw = [
        {"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},  # Duplicate
    ]
    ds = UniversalDataEngine.load(generic_metadata, raw)
    s1 = UniversalDataEngine.ingest(base_state, ds, 1001.0)

    s2 = UniversalDataEngine.clean(s1, "AAPL-1D", PERMISSIVE, 1002.0)

    # The version that was read is untouched, duplicate included.
    assert len(s2.datasets["AAPL-1D"].records) == 2

    derived = [name for name in s2.datasets if name != "AAPL-1D"]
    assert len(derived) == 1, "cleaning derives exactly one new version"
    assert len(s2.datasets[derived[0]].records) == 1, "and that one has the duplicate removed"
    assert dataset_lineage(s2, derived[0]) == (derived[0], "AAPL-1D")

    cleaned = [e for e in s2.events if isinstance(e, DatasetCleaned)]
    assert len(cleaned) == 1
    assert cleaned[0].dataset_id == "AAPL-1D", "the event names the version that was read"
    assert cleaned[0].derived_dataset_id == derived[0], "and the one that was written"
    assert cleaned[0].records_removed == 1


def test_cleaning_the_same_dataset_twice_reaches_the_same_version(
    base_state: UniversalDataState, generic_metadata: DatasetMetadata
) -> None:
    """A derived identity is reproducible, which is what makes it an identity."""

    raw = [
        {"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
    ]
    ds = UniversalDataEngine.load(generic_metadata, raw)
    s1 = UniversalDataEngine.ingest(base_state, ds, 1001.0)

    first = UniversalDataEngine.clean(s1, "AAPL-1D", PERMISSIVE, 1002.0)
    second = UniversalDataEngine.clean(s1, "AAPL-1D", PERMISSIVE, 9999.0)

    assert set(first.datasets) == set(second.datasets)


def test_cleaning_a_clean_dataset_derives_nothing(
    base_state: UniversalDataState, generic_metadata: DatasetMetadata
) -> None:
    """A dataset nothing was done to is the dataset it came from."""

    raw = [{"timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}]
    ds = UniversalDataEngine.load(generic_metadata, raw)
    s1 = UniversalDataEngine.ingest(base_state, ds, 1001.0)

    s2 = UniversalDataEngine.clean(s1, "AAPL-1D", PERMISSIVE, 1002.0)

    assert list(s2.datasets) == ["AAPL-1D"]


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
    """Resampling derives a new version too, for the same reason cleaning does."""

    raw = [
        {"timestamp": 0.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"timestamp": 30.0, "o": 11, "h": 15, "l": 10, "c": 14, "v": 200},
    ]
    ds = UniversalDataEngine.load(generic_metadata, raw)
    s1 = UniversalDataEngine.ingest(base_state, ds, 1001.0)

    s2 = UniversalDataEngine.convert(s1, "AAPL-1D", 60.0, 1002.0)

    assert len(s2.datasets["AAPL-1D"].records) == 2, "the minute bars are still there"

    derived = [name for name in s2.datasets if name != "AAPL-1D"]
    assert len(derived) == 1
    assert len(s2.datasets[derived[0]].records) == 1, "aggregated into one 60s bucket"
    assert dataset_lineage(s2, derived[0]) == (derived[0], "AAPL-1D")


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


def test_adapter_refuses_an_asset_class_it_does_not_know() -> None:
    """Until v3.1 an unrecognised class silently became EQUITY.

    A vendor file of option quotes labelled ``"opt"`` was catalogued as
    equities, and nothing anywhere recorded that a substitution had happened.
    """

    with pytest.raises(DataValidationError) as error:
        DataAdapter.create_metadata("M-1", "Src", "opt", "daily", 0.0, 100.0)

    assert "OPTION" in str(error.value), "the refusal names the spellings it accepts"


def test_adapter_refuses_a_frequency_it_does_not_know() -> None:
    """And an unrecognised frequency silently became DAILY."""

    with pytest.raises(DataValidationError) as error:
        DataAdapter.create_metadata("M-1", "Src", "equity", "1min", 0.0, 100.0)

    assert "MINUTE" in str(error.value)


def test_remove_duplicates_keys_on_the_instrument_as_well_as_the_instant() -> None:
    """Two instruments printing in the same minute are not duplicates.

    Keyed on timestamp alone -- which this did before v3.1 -- a three-symbol
    daily file collapsed to one symbol, silently.
    """

    raw = [
        {"symbol": "AAPL", "timestamp": 1000.0, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"symbol": "MSFT", "timestamp": 1000.0, "o": 20, "h": 22, "l": 19, "c": 21, "v": 200},
        {"symbol": "SPY", "timestamp": 1000.0, "o": 30, "h": 32, "l": 29, "c": 31, "v": 300},
    ]
    bars = parse_raw_rows("FALLBACK", raw)

    assert len(remove_duplicates(bars)) == 3
    assert {bar.symbol for bar in remove_duplicates(bars)} == {"AAPL", "MSFT", "SPY"}
