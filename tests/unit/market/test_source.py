"""The market-data adapter boundary: identity, ordering, and re-iterability."""

from decimal import Decimal

import pytest

from alphalab.backtesting.dataset import MarketDataset
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.quote import Quote
from alphalab.market.record import MarketRecord
from alphalab.market.source import (
    MarketDataSource,
    OrderingGuarantee,
    SequenceSource,
    validate_ordering,
)


def _quote(timestamp: float, asset_id: str = "AAPL") -> Quote:
    return Quote(
        asset_id=asset_id,
        timestamp=timestamp,
        bid=Decimal("10.00"),
        ask=Decimal("10.10"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        venue="SIM",
        currency="USD",
    )


def test_sequence_source_satisfies_the_protocol() -> None:
    source = SequenceSource.of("S", [_quote(1.0)])
    assert isinstance(source, MarketDataSource)


def test_record_ids_are_deterministic_in_source_id_and_position() -> None:
    inputs = [_quote(float(t)) for t in (1, 2, 3)]
    assert [r.event_id for r in SequenceSource.of("S", inputs).records()] == ["S-0", "S-1", "S-2"]
    # Same inputs, same ids -- which is what makes two runs comparable.
    assert list(SequenceSource.of("S", inputs).records()) == list(
        SequenceSource.of("S", inputs).records()
    )


def test_record_id_width_is_fixed_so_ids_sort_lexicographically() -> None:
    inputs = [_quote(float(t)) for t in range(12)]
    ids = [r.event_id for r in SequenceSource.of("S", inputs).records()]
    assert ids[0] == "S-00"
    assert ids[-1] == "S-11"
    assert ids == sorted(ids)


def test_a_source_and_a_dataset_built_from_the_same_inputs_agree_on_identity() -> None:
    """Historical and source-driven runs must not disagree about record ids."""
    inputs = [_quote(float(t)) for t in (1, 2, 3)]
    dataset = MarketDataset.of("RUN", inputs)
    source = SequenceSource.of("RUN", inputs)

    assert list(source.records()) == list(dataset.records)


def test_a_source_can_be_iterated_more_than_once() -> None:
    """Comparing two runs means replaying one source twice."""
    source = SequenceSource.of("S", [_quote(1.0), _quote(2.0)])
    assert list(source.records()) == list(source.records())
    assert len(source) == 2


def test_from_records_preserves_supplied_identities() -> None:
    records = (MarketRecord("EXT-1", 1.0, _quote(1.0)), MarketRecord("EXT-2", 2.0, _quote(2.0)))
    source = SequenceSource.from_records("S", records)
    assert [r.event_id for r in source.records()] == ["EXT-1", "EXT-2"]


def test_sources_declare_their_ordering_rather_than_assuming_one() -> None:
    stored = SequenceSource.of("S", [_quote(1.0)])
    venue = SequenceSource.from_records("V", (), OrderingGuarantee.UNORDERED)

    assert stored.ordering is OrderingGuarantee.CHRONOLOGICAL
    assert venue.ordering is OrderingGuarantee.UNORDERED


def test_validate_ordering_accepts_chronological_records_with_unique_ids() -> None:
    validate_ordering(
        list(SequenceSource.of("S", [_quote(1.0), _quote(1.0), _quote(2.0)]).records())
    )


def test_validate_ordering_rejects_a_backward_timestamp() -> None:
    records = (MarketRecord("a", 2.0, _quote(2.0)), MarketRecord("b", 1.0, _quote(1.0)))
    with pytest.raises(MarketValidationError, match="not chronologically ordered"):
        validate_ordering(records)


def test_validate_ordering_rejects_a_duplicate_record_id() -> None:
    records = (MarketRecord("a", 1.0, _quote(1.0)), MarketRecord("a", 2.0, _quote(2.0)))
    with pytest.raises(MarketValidationError, match="Duplicate record id"):
        validate_ordering(records)


def test_record_exposes_the_asset_its_payload_refers_to() -> None:
    source = SequenceSource.of("S", [_quote(1.0, "MSFT")])
    assert next(iter(source.records())).asset_id == "MSFT"
