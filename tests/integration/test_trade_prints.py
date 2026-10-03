"""Trade prints from a flat file, read as declared and never as guessed (ledger FEA-004).

A table of prints has the same header shape as a partial bar -- ``price`` is a
close to the alias table -- so a print is read only through a declared
:class:`~alphalab.data.schema.TradeColumns`. These tests hold the declaration to
its promises: nothing undeclared is read as a trade, the declaration is part of
the dataset's identity, the print's identifier and aggressor flag reach the
execution path and the snapshot, and two prints at one instant are two prints.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

import pytest

from alphalab.api import backtest, ingest_rows, normalize_records, to_market_dataset
from alphalab.data.cleaning import (
    REFUSE_EVERYTHING,
    CleaningPolicy,
    DuplicatePolicy,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    clean_records,
    is_internally_consistent,
)
from alphalab.data.corporate_actions import PriceBasis
from alphalab.data.csv_source import RawTable
from alphalab.data.exceptions import DataQualityError, DataValidationError
from alphalab.data.feed import Quote, Trade, TradeAggressor
from alphalab.data.ingestion import IngestionRequest, IngestionResult
from alphalab.data.schema import RecordType, TradeColumns, declare_trade_schema, detect_schema
from alphalab.data.source import SourceKind, raw_source_from_bytes
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency
from alphalab.data.validation import FindingKind, Severity, duplicate_key, validate_records
from alphalab.market.events import TickReceived
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.normalization import NormalizationPolicy, normalize_wire_trade
from alphalab.market.tick import Tick
from alphalab.persistence import deserialize, serialize
from alphalab.runtime import snapshot as pipeline_snapshot
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import backtest_config, context_factory, running_strategy_state

CODES = {"B": TradeAggressor.BUYER, "S": TradeAggressor.SELLER}
COLUMNS = TradeColumns(
    timestamp="ts", price="px", size="qty", trade_id="id", aggressor="side", aggressor_codes=CODES
)
POLICY = NormalizationPolicy(venue="XTEST", currency="USD")
KEEP = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_LAST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)


def _rows() -> list[dict[str, object]]:
    return [
        {"ts": "1700000000", "px": "100.25", "qty": "300", "id": "T1", "side": "B"},
        # Two different prints at one instant: both are trades.
        {"ts": "1700000001", "px": "100.30", "qty": "100", "id": "T2", "side": "S"},
        {"ts": "1700000001", "px": "100.30", "qty": "100", "id": "T3", "side": " B "},
        # The source did not flag this one.
        {"ts": "1700000002", "px": "100.20", "qty": "50", "id": "T4", "side": ""},
    ]


def _request(columns: TradeColumns | None = COLUMNS, **changes: Any) -> IngestionRequest:
    request = IngestionRequest(
        name="PRINTS",
        source=raw_source_from_bytes(SourceKind.IN_MEMORY, "prints", b"", 1.0, "text/csv", "utf-8"),
        frequency=TimeFrequency.TICK,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=REFUSE_EVERYTHING,
        price_basis=PriceBasis.RAW,
        timezone_name="UTC",
        symbol="XYZ",
        trade_columns=columns,
    )
    return replace(request, **changes)


def _ingest(rows: Sequence[Mapping[str, object]] | None = None, **changes: Any) -> IngestionResult:
    return ingest_rows(_rows() if rows is None else rows, _request(**changes))


def _prints(result: IngestionResult) -> list[Trade]:
    records = list(result.dataset.records)
    assert all(isinstance(record, Trade) for record in records)
    return [record for record in records if isinstance(record, Trade)]


class TestNothingUndeclaredIsATrade:
    def test_a_price_and_size_header_is_not_detected_as_anything(self) -> None:
        table = RawTable.from_rows(_rows())
        detection = detect_schema(table)

        assert detection.record_type is None
        with pytest.raises(DataValidationError, match="record type could not be determined"):
            detection.require()

    def test_declaring_trade_without_its_columns_is_refused(self) -> None:
        detection = detect_schema(RawTable.from_rows(_rows()), RecordType.TRADE)

        with pytest.raises(DataValidationError, match="declared TradeColumns"):
            detection.require()
        with pytest.raises(DataValidationError, match="declared TradeColumns"):
            _ingest(columns=None, declared_record_type=RecordType.TRADE)

    def test_trade_columns_for_a_table_declared_as_bars_are_refused(self) -> None:
        with pytest.raises(DataValidationError, match="one or the other"):
            _request(declared_record_type=RecordType.BAR)

    def test_a_declared_column_the_table_lacks_is_named(self) -> None:
        columns = replace(COLUMNS, size="volume")
        with pytest.raises(DataValidationError, match="size='volume'"):
            declare_trade_schema(RawTable.from_rows(_rows()), columns)

    @pytest.mark.parametrize(
        ("changes", "match"),
        [
            ({"price": " "}, "blank column"),
            ({"size": "px"}, "one column for two roles"),
            ({"aggressor_codes": {}}, "declared together"),
            ({"aggressor": None}, "declared together"),
            (
                {"aggressor_codes": {"B": TradeAggressor.BUYER, " B": TradeAggressor.SELLER}},
                "repeated",
            ),
            ({"aggressor_codes": {"B": "BUYER"}}, "not a TradeAggressor"),
        ],
    )
    def test_a_declaration_that_contradicts_itself_is_refused(
        self, changes: dict[str, Any], match: str
    ) -> None:
        with pytest.raises(DataValidationError, match=match):
            replace(COLUMNS, **changes)


class TestADeclaredTableOfPrints:
    def test_every_print_is_read_with_its_identifier_and_its_flag(self) -> None:
        result = _ingest()

        assert result.dataset.records == (
            Trade("XYZ", 1700000000.0, 100.25, 300.0, "T1", TradeAggressor.BUYER),
            Trade("XYZ", 1700000001.0, 100.30, 100.0, "T2", TradeAggressor.SELLER),
            Trade("XYZ", 1700000001.0, 100.30, 100.0, "T3", TradeAggressor.BUYER),
            Trade("XYZ", 1700000002.0, 100.20, 50.0, "T4", None),
        )
        assert result.quality.errors == ()
        assert result.transformations == ()

    def test_the_provenance_states_the_declaration(self) -> None:
        schema = _ingest().dataset.require_provenance().schema

        assert schema.data_type == "TRADE"
        assert schema.bindings == {
            "TIMESTAMP": "ts",
            "PRICE": "px",
            "SIZE": "qty",
            "TRADE_ID": "id",
            "AGGRESSOR": "side",
        }
        assert schema.aggressor_codes == {"B": "BUYER", "S": "SELLER"}

    def test_the_identity_is_reproducible_and_moves_with_the_declaration(self) -> None:
        first, second = _ingest(), _ingest()
        swapped = _ingest(
            columns=replace(
                COLUMNS,
                aggressor_codes={"B": TradeAggressor.SELLER, "S": TradeAggressor.BUYER},
            )
        )
        unflagged = _ingest(columns=replace(COLUMNS, aggressor=None, aggressor_codes={}))

        assert first.dataset_version == second.dataset_version
        assert first.dataset.records == second.dataset.records
        # Reading the flag the other way round is different data, and says so.
        assert swapped.dataset_version != first.dataset_version
        assert _prints(swapped)[0].aggressor is TradeAggressor.SELLER
        assert unflagged.dataset_version != first.dataset_version
        assert {record.aggressor for record in _prints(unflagged)} == {None}

    def test_a_code_the_declaration_does_not_name_rejects_its_row(self) -> None:
        rows: list[Mapping[str, object]] = [
            *_rows(),
            {"ts": "1700000003", "px": "100", "qty": "1", "id": "T5", "side": "X"},
        ]

        result = _ingest(rows)

        assert len(result.dataset.records) == 4
        (rejection,) = result.quality.rejected_rows
        assert [finding.kind for finding in rejection.findings] == [FindingKind.UNKNOWN_CODE]


class TestTwoPrintsAtOneInstant:
    def test_prints_sharing_an_instant_are_not_duplicates(self) -> None:
        findings = validate_records(_ingest().dataset.records, TimeFrequency.TICK)

        assert not [finding for finding in findings if finding.severity is Severity.ERROR]

    def test_identical_unidentified_prints_are_kept_and_reported(self) -> None:
        rows = [
            {"ts": "1700000000", "px": "10", "qty": "100"},
            {"ts": "1700000000", "px": "10", "qty": "100"},
        ]
        columns = TradeColumns(timestamp="ts", price="px", size="qty")

        result = _ingest(rows, columns=columns)

        assert len(result.dataset.records) == 2
        assert [finding.kind for finding in result.quality.warnings] == [
            FindingKind.INDISTINGUISHABLE_PRINTS
        ]
        assert duplicate_key(result.dataset.records[0]) is None

    def test_one_identifier_twice_is_a_duplicate_refused_or_resolved_by_policy(self) -> None:
        rows: list[Mapping[str, object]] = [
            *_rows(),
            {"ts": "1700000003", "px": "100.40", "qty": "300", "id": "T1", "side": "B"},
        ]

        with pytest.raises(DataQualityError, match="trade identifiers occur more than once"):
            _ingest(rows)
        kept = _ingest(rows, cleaning_policy=KEEP)

        assert [record.trade_id for record in _prints(kept)] == ["T2", "T3", "T4", "T1"]
        assert _prints(kept)[-1].price == 100.40
        assert kept.quality.duplicate_count == 1
        # The later copy stands where the identifier first occurred, which is out of
        # order -- so the sort the policy permits is recorded too.
        drop, sort = kept.transformations
        assert (drop.operation, sort.operation) == ("drop_duplicate", "sort_chronologically")
        assert "duplicate trade identifier" in drop.reason

    def test_a_print_of_nothing_is_invalid_and_dropped_under_drop(self) -> None:
        rows: list[Mapping[str, object]] = [
            *_rows(),
            {"ts": "1700000003", "px": "100", "qty": "0", "id": "T9", "side": "S"},
        ]

        with pytest.raises(DataQualityError, match="not internally consistent"):
            _ingest(rows)
        kept = _ingest(rows, cleaning_policy=KEEP)

        assert "T9" not in {record.trade_id for record in _prints(kept)}
        assert [record.operation for record in kept.transformations] == ["drop_invalid_record"]

    def test_a_quote_reported_invalid_is_dropped_as_invalid_too(self) -> None:
        """Found while adding prints: DROP kept the quote REFUSE refused (v3.12)."""

        bad = Quote("XYZ", 1.0, -1.0, 10.0, 5.0, 5.0)
        good = Quote("XYZ", 2.0, 9.0, 10.0, 5.0, 5.0)
        findings = validate_records((bad, good), None)

        assert not is_internally_consistent(bad)
        assert clean_records((bad, good), KEEP, findings).records == (good,)


class _PrintReader(BaseStrategy):
    """Records each print it is shown."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, TradeAggressor | None]] = []

    def on_tick(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.seen.append((event.tick.trade_id, event.tick.aggressor))
        return ()


class TestThePrintsReachTheExecutionPath:
    def test_normalization_carries_the_identifier_and_the_flag(self) -> None:
        ticks = normalize_records(_ingest().dataset.records, POLICY)

        assert all(isinstance(tick, Tick) for tick in ticks)
        assert [(tick.trade_id, tick.aggressor) for tick in ticks] == [  # type: ignore[union-attr]
            ("T1", TradeAggressor.BUYER),
            ("T2", TradeAggressor.SELLER),
            ("T3", TradeAggressor.BUYER),
            ("T4", None),
        ]

    def test_an_identifier_that_contradicts_the_print_is_refused(self) -> None:
        trade = Trade("XYZ", 1.0, 10.0, 1.0, "T1")

        assert normalize_wire_trade(trade, POLICY, "T1").trade_id == "T1"
        assert normalize_wire_trade(replace(trade, trade_id=""), POLICY, "X").trade_id == "X"
        with pytest.raises(MarketValidationError, match="one print has one identifier"):
            normalize_wire_trade(trade, POLICY, "T2")

    def test_a_backtest_over_prints_shows_each_one_to_the_strategy(self) -> None:
        strategy_id = str(uuid.uuid4())
        reader = _PrintReader()
        dataset = _ingest().dataset

        result = backtest(
            backtest_config(strategy_id),
            dataset,
            running_strategy_state(strategy_id, reader),
            context_factory,
            POLICY,
        )

        assert result.dataset_id == dataset.require_provenance().dataset_version
        assert reader.seen == [
            ("T1", TradeAggressor.BUYER),
            ("T2", TradeAggressor.SELLER),
            ("T3", TradeAggressor.BUYER),
            ("T4", None),
        ]
        assert len(to_market_dataset(dataset, POLICY).records) == 4

    def test_a_snapshot_keeps_every_print_flag(self) -> None:
        strategy_id = str(uuid.uuid4())
        dataset = _ingest().dataset
        result = backtest(
            backtest_config(strategy_id),
            dataset,
            running_strategy_state(strategy_id, _PrintReader()),
            context_factory,
            POLICY,
        )
        market = result.run.pipeline.market
        captured = deserialize(serialize(pipeline_snapshot.capture(result.run.pipeline)))

        restored = pipeline_snapshot.from_primitives(captured)

        assert restored.market.latest_ticks == dict(market.latest_ticks)
        flags = [
            record.event.tick.aggressor
            for record in restored.market.history
            if isinstance(record.event, TickReceived)
        ]
        assert flags == [TradeAggressor.BUYER, TradeAggressor.SELLER, TradeAggressor.BUYER, None]


def test_a_version_5_tick_is_read_as_unflagged() -> None:
    tick = {
        "asset_id": "a",
        "timestamp": 1.0,
        "price": "1",
        "quantity": "1",
        "trade_id": "",
        "venue": "V",
        "currency": "USD",
    }
    market = {"latest_ticks": {"a": tick}, "history": [{"fields": {"tick": dict(tick)}}]}

    upgraded = pipeline_snapshot._v5_ticks(market)

    assert upgraded["latest_ticks"]["a"]["aggressor"] is None
    assert upgraded["history"][0]["fields"]["tick"]["aggressor"] is None
    # Nothing that is not exactly a tick is touched.
    assert pipeline_snapshot._v5_ticks({"price": "1"}) == {"price": "1"}
