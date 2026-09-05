"""Unit tests for the market dataset both drivers read."""

from decimal import Decimal

import pytest

from alphalab.backtesting.dataset import MarketDataset, MarketRecord, validate_dataset
from alphalab.backtesting.exceptions import DatasetValidationError
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.quote import Quote
from alphalab.market.tick import Tick
from alphalab.replay.validation import validate_events


def _quote(timestamp: float, asset_id: str = "AAPL") -> Quote:
    return Quote(
        asset_id=asset_id,
        timestamp=timestamp,
        bid=Decimal("99.99"),
        ask=Decimal("100.01"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        venue="SIM",
        currency="USD",
    )


def _bar(timestamp: float) -> Bar:
    return Bar(
        asset_id="AAPL",
        timestamp=timestamp,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
        vwap=Decimal("100.2"),
        trade_count=10,
        timeframe=TimeFrame.M1,
    )


def _tick(timestamp: float) -> Tick:
    return Tick(
        asset_id="AAPL",
        timestamp=timestamp,
        price=Decimal("100.005"),
        quantity=Decimal("5"),
        trade_id="T1",
        venue="SIM",
        currency="USD",
    )


def test_of_assigns_deterministic_padded_ids() -> None:
    dataset = MarketDataset.of("DS", [_quote(1.0 + i) for i in range(12)])

    assert dataset.records[0].event_id == "DS-00"
    assert dataset.records[11].event_id == "DS-11"
    assert MarketDataset.of("DS", [_quote(1.0 + i) for i in range(12)]) == dataset


def test_a_dataset_carries_quotes_bars_and_ticks_together() -> None:
    dataset = MarketDataset.of("DS", [_quote(1.0), _bar(2.0), _tick(3.0)])

    assert [type(r.payload).__name__ for r in dataset.records] == ["Quote", "Bar", "Tick"]


def test_bounds_come_from_the_first_and_last_record() -> None:
    dataset = MarketDataset.of("DS", [_quote(5.0), _quote(9.0)])

    assert dataset.start_time == 5.0
    assert dataset.end_time == 9.0
    assert len(dataset) == 2


def test_record_exposes_the_underlying_asset() -> None:
    dataset = MarketDataset.of("DS", [_quote(1.0, asset_id="MSFT")])

    assert dataset.records[0].asset_id == "MSFT"


def test_an_empty_dataset_is_rejected() -> None:
    with pytest.raises(DatasetValidationError, match="Empty datasets"):
        MarketDataset("DS", ())


def test_an_unnamed_dataset_is_rejected() -> None:
    with pytest.raises(DatasetValidationError, match="dataset_id"):
        MarketDataset("", (MarketRecord("a", 1.0, _quote(1.0)),))


def test_out_of_order_records_are_rejected() -> None:
    records = (
        MarketRecord("a", 2.0, _quote(2.0)),
        MarketRecord("b", 1.0, _quote(1.0)),
    )

    with pytest.raises(DatasetValidationError, match="not chronologically ordered"):
        MarketDataset("DS", records)


def test_duplicate_record_ids_are_rejected() -> None:
    records = (
        MarketRecord("a", 1.0, _quote(1.0)),
        MarketRecord("a", 2.0, _quote(2.0)),
    )

    with pytest.raises(DatasetValidationError, match="Duplicate record id"):
        MarketDataset("DS", records)


def test_a_record_disagreeing_with_its_payload_timestamp_is_rejected() -> None:
    records = (MarketRecord("a", 1.0, _quote(9.0)),)

    with pytest.raises(DatasetValidationError, match="disagrees"):
        MarketDataset("DS", records)


def test_simultaneous_records_are_allowed() -> None:
    dataset = MarketDataset.of("DS", [_quote(1.0, "AAPL"), _quote(1.0, "MSFT")])

    validate_dataset(dataset)
    assert len(dataset) == 2


def test_a_dataset_satisfies_the_replay_historical_event_protocol() -> None:
    """The structural reason backtest and replay read the same thing."""

    dataset = MarketDataset.of("DS", [_quote(1.0), _quote(2.0)])

    validate_events(dataset.records)
