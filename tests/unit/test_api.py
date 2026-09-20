"""The application-facing API: a file in, a dataset with lineage out.

This is the surface a host platform calls. What it has to get right is not
cleverness but *honesty*: the dataset it returns must be able to say where it
came from, and it must refuse rather than invent when it cannot.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from alphalab.api import (
    DataRequest,
    clean_dataset,
    convention_from_spec,
    future_contract_from_spec,
    ingest_csv,
    ingest_rows,
    inspect_csv,
    normalize_records,
    option_contract_from_spec,
    select,
    to_market_dataset,
    validate_dataset,
)
from alphalab.conventions import (
    LotSpecification,
    SettlementBasis,
    SettlementRule,
    TickSchedule,
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
from alphalab.data.assets import EquitySpec, FutureSpec, OptionSpec
from alphalab.futures import futures_symbol
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.options.enums import ExerciseStyle, OptionType

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


# --------------------------------------------------------------------------- #
# The wire/domain contract join (v3.4)
# --------------------------------------------------------------------------- #


def _future_spec(**overrides: object) -> FutureSpec:
    fields: dict[str, object] = {
        "symbol": "CLZ6",
        "currency": "USD",
        "exchange": "XCME",
        "root": "CL",
        "expiry": 1_766_188_800.0,
        "multiplier": 1000.0,
        "tick_size": 0.01,
        "contract_month": 1_764_547_200.0,
    }
    fields.update(overrides)
    return FutureSpec(**fields)  # type: ignore[arg-type]


def test_a_future_spec_lifts_into_a_contract_that_opens_a_position() -> None:
    contract = future_contract_from_spec(_future_spec())
    assert contract.underlying_asset_id == "CL"
    assert contract.multiplier == 1000
    assert contract.tick_size == Decimal("0.01")
    assert contract.currency == "USD"
    assert futures_symbol(contract) == "CL_202512"


def test_the_float_to_decimal_conversion_carries_no_binary_artefact() -> None:
    """``Decimal(0.1)`` is 0.1000000000000000055…, and a tick size holding that
    would put every price off its own grid."""

    contract = future_contract_from_spec(_future_spec(tick_size=0.1, multiplier=50.0))
    assert contract.tick_size == Decimal("0.1")
    assert Decimal("100.3") % contract.tick_size == Decimal("0")


def test_a_spec_with_no_contract_month_is_refused_rather_than_derived() -> None:
    with pytest.raises(DataValidationError, match="never named"):
        future_contract_from_spec(_future_spec(contract_month=None))


def test_a_settlement_currency_can_differ_from_the_series_label() -> None:
    """ADR-0019's four currency roles: a statement, not a default."""

    assert future_contract_from_spec(_future_spec()).currency == "USD"
    assert future_contract_from_spec(_future_spec(), currency="EUR").currency == "EUR"


def test_an_option_spec_lifts_into_a_contract_keeping_its_non_us_terms() -> None:
    spec = OptionSpec(
        symbol="NKO",
        currency="JPY",
        exchange="XOSE",
        underlying_symbol="NK225",
        strike=38000.0,
        expiry=1_766_188_800.0,
        option_type=OptionType.CALL,
        multiplier=1000.0,
        style=ExerciseStyle.EUROPEAN,
    )
    contract = option_contract_from_spec(spec)
    assert contract.multiplier == 1000
    assert contract.style is ExerciseStyle.EUROPEAN
    assert contract.strike == Decimal("38000.0")


def test_a_convention_takes_the_three_facts_a_spec_cannot_carry() -> None:
    """Calendar, tick grid and lot grid are arguments: no price series states
    them, and a default would be one market's convention as a universal."""

    convention = convention_from_spec(
        _future_spec(),
        calendar_id="XCME",
        tick=TickSchedule.flat(Decimal("0.01")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
    )
    assert convention.venue == "XCME"
    assert convention.quote_currency == convention.settlement_currency == "USD"
    assert convention.multiplier == Decimal("1000.0")
    assert convention.calendar_id == "XCME"


def test_an_equity_spec_takes_a_multiplier_of_one_stated_rather_than_assumed() -> None:
    convention = convention_from_spec(
        EquitySpec(symbol="AAPL", currency="USD", exchange="XNAS"),
        calendar_id="XNAS",
        tick=TickSchedule.flat(Decimal("0.01")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 1),
    )
    assert convention.multiplier == Decimal("1")
    assert convention.settlement.label == "T+1 trading days"


def test_a_quanto_settlement_currency_is_carried_through() -> None:
    convention = convention_from_spec(
        _future_spec(currency="JPY"),
        calendar_id="XCME",
        tick=TickSchedule.flat(Decimal("5")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
        settlement_currency="USD",
    )
    assert convention.quote_currency == "JPY"
    assert convention.settlement_currency == "USD"
    assert not convention.settles_in_quote_currency


def test_the_join_lives_above_both_and_data_still_imports_neither_engine() -> None:
    """The reason it is in ``alphalab.api``: ``data`` importing either engine
    would close a package cycle and put a standalone engine on the ingestion
    path."""

    import ast
    import inspect
    import pathlib

    from alphalab.data import assets

    imported = {
        node.module
        for node in ast.walk(ast.parse(pathlib.Path(inspect.getfile(assets)).read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not [n for n in imported if n.startswith("alphalab.futures")]
    assert not [n for n in imported if n.startswith("alphalab.portfolio")]
    assert not [n for n in imported if n.startswith("alphalab.conventions")]
