"""Several canonical datasets read by one run, under one derived identity (v4.1.1).

A dataset is one series kind from one source, so a strategy trading an equity
and a future, or reading two providers, reads several. ``combine_datasets``
keeps each one's provenance and derives the run's identity from them -- from
their versions and content hashes, in the order given, because that order
breaks ties between records sharing an instant. Nothing is aligned, filled or
carried forward, and two answers to one question are refused.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.api import (
    CombinedDataset,
    combine_datasets,
    combined_market_dataset,
    ingest_rows,
    to_market_dataset,
)
from alphalab.backtesting.dataset import MarketRecord
from alphalab.data import (
    REFUSE_EVERYTHING,
    DataAssetClass,
    Dataset,
    DataValidationError,
    IngestionRequest,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)
from alphalab.data.time import BarStamp
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.normalization import NormalizationPolicy

DAY = 86_400.0
FIRST = 1_704_153_600.0
NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END, venue="XNYS", currency="USD", timeframe=TimeFrame.D1
)


def _rows(symbol: str, closes: Sequence[str], offset: float = 0.0) -> list[dict[str, object]]:
    return [
        {
            "timestamp": str(FIRST + index * DAY + offset),
            "symbol": symbol,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": "1000",
        }
        for index, close in enumerate(closes)
    ]


def _payload(rows: Sequence[Mapping[str, object]]) -> bytes:
    return "\n".join(",".join(str(value) for value in row.values()) for row in rows).encode()


def _dataset(
    name: str,
    rows: Sequence[Mapping[str, object]],
    asset_class: DataAssetClass = DataAssetClass.EQUITY,
) -> Dataset:
    request = IngestionRequest(
        name=name,
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, f"memory://{name}", _payload(rows), FIRST, "text/csv"
        ),
        frequency=TimeFrequency.DAILY,
        bar_stamp=BarStamp.INTERVAL_END,
        asset_class=asset_class,
        cleaning_policy=REFUSE_EVERYTHING,
        price_basis=PriceBasis.RAW,
    )
    return ingest_rows(rows, request).dataset


EQUITY = _dataset("EQ", _rows("AAA", ["10", "11", "12"]))
FUTURE = _dataset("FUT", _rows("FFF", ["500", "501", "502"], offset=3600.0), DataAssetClass.FUTURE)
SAME_INSTANTS = _dataset("EQ2", _rows("BBB", ["20", "21", "22"]))


def _close(record: MarketRecord) -> Decimal:
    bar = record.payload
    assert isinstance(bar, Bar)
    return bar.close


def _versions(*datasets: Dataset) -> tuple[str, ...]:
    return tuple(dataset.require_provenance().dataset_version for dataset in datasets)


def test_the_identity_is_derived_from_the_parts_in_order() -> None:
    combined = combine_datasets([(EQUITY, NORMALIZATION), (FUTURE, NORMALIZATION)])
    provenance = combined.require_provenance()
    versions = _versions(EQUITY, FUTURE)
    hashes = tuple(dataset.require_provenance().content_hash for dataset in (EQUITY, FUTURE))

    assert provenance.dataset_version == (
        "combined-" + hashlib.sha256("\n".join(versions).encode()).hexdigest()
    )
    assert provenance.content_hash == hashlib.sha256("\n".join(hashes).encode()).hexdigest()
    assert provenance.parts == tuple(zip(versions, hashes, strict=True))
    assert provenance.source.byte_count == sum(
        dataset.require_provenance().source.byte_count for dataset in (EQUITY, FUTURE)
    )
    assert combined.dataset_version == provenance.dataset_version


def test_another_order_is_another_identity_and_another_tie_break() -> None:
    forward = combine_datasets([(EQUITY, NORMALIZATION), (SAME_INSTANTS, NORMALIZATION)])
    backward = combine_datasets([(SAME_INSTANTS, NORMALIZATION), (EQUITY, NORMALIZATION)])

    assert forward.dataset_version != backward.dataset_version
    first = _close(combined_market_dataset(forward).records[0])
    second = _close(combined_market_dataset(backward).records[0])
    assert (first, second) == (Decimal("10"), Decimal("20"))


def test_the_stream_is_every_record_in_time_order_with_nothing_aligned() -> None:
    combined = combine_datasets([(EQUITY, NORMALIZATION), (FUTURE, NORMALIZATION)])
    market = combined_market_dataset(combined)

    assert market.dataset_id == combined.dataset_version
    stamps = [record.timestamp for record in market.records]
    assert stamps == sorted(stamps)
    # Each series keeps its own instants: the future an hour after the equity.
    assert stamps[:2] == [FIRST, FIRST + 3600.0]
    assert len(market) == len(to_market_dataset(EQUITY, NORMALIZATION)) + len(
        to_market_dataset(FUTURE, NORMALIZATION)
    )


def test_combining_is_deterministic() -> None:
    parts = [(EQUITY, NORMALIZATION), (FUTURE, NORMALIZATION)]
    assert combined_market_dataset(combine_datasets(parts)) == combined_market_dataset(
        combine_datasets(parts)
    )


def test_one_instruments_price_from_two_parts_at_one_instant_is_refused() -> None:
    overlap = _dataset("EQ-COPY", _rows("AAA", ["10"]))
    combined = combine_datasets([(EQUITY, NORMALIZATION), (overlap, NORMALIZATION)])

    with pytest.raises(DataValidationError, match="choose one source"):
        combined_market_dataset(combined)


def test_one_instrument_from_two_parts_at_different_instants_is_allowed() -> None:
    later = _dataset("EQ-LATER", _rows("AAA", ["13"], offset=3 * DAY))
    market = combined_market_dataset(
        combine_datasets([(EQUITY, NORMALIZATION), (later, NORMALIZATION)])
    )
    assert [_close(record) for record in market.records] == [
        Decimal(close) for close in ("10", "11", "12", "13")
    ]


def test_fewer_than_two_parts_or_a_part_twice_is_refused() -> None:
    with pytest.raises(DataValidationError, match="two or more"):
        combine_datasets([(EQUITY, NORMALIZATION)])
    with pytest.raises(DataValidationError, match="given twice"):
        combine_datasets([(EQUITY, NORMALIZATION), (EQUITY, NORMALIZATION)])


def test_a_part_without_provenance_is_refused() -> None:
    bare = replace(EQUITY, provenance=None)

    with pytest.raises(DataValidationError):
        combine_datasets([(EQUITY, NORMALIZATION), (bare, NORMALIZATION)])
    with pytest.raises(DataValidationError):
        CombinedDataset(((EQUITY, NORMALIZATION), (bare, NORMALIZATION))).require_provenance()
