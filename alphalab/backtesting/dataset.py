"""The dataset a backtest -- or a replay of it -- consumes.

A :class:`MarketDataset` is an ordered, validated sequence of
:class:`~alphalab.market.record.MarketRecord` s. A record pairs one canonical
market input (a :class:`~alphalab.market.quote.Quote`,
:class:`~alphalab.market.bar.Bar` or :class:`~alphalab.market.tick.Tick`) with
the identity and timestamp that :mod:`alphalab.replay` requires of anything it
sequences.

That pairing is deliberate and is what makes backtest/replay parity structural
rather than aspirational: one dataset type satisfies both
:class:`~alphalab.replay.loader.HistoricalEventProtocol` and the backtest loop,
so the two paths cannot drift apart in what they read or the order they read it
in. They differ only in what drives the cursor.

``MarketInput`` and ``MarketRecord`` are defined in
:mod:`alphalab.market.record` as of v2.3 and re-exported here unchanged, so a
live feed adapter can produce records without importing this package.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from alphalab.backtesting.exceptions import DatasetValidationError
from alphalab.market.record import MarketInput, MarketRecord, records_from_inputs

__all__ = ["MarketDataset", "MarketInput", "MarketRecord", "validate_dataset"]


@dataclass(frozen=True, slots=True)
class MarketDataset:
    """An ordered, validated sequence of market records.

    Attributes:
        dataset_id: Identifier used for the replay session and record ids.
        records: Chronologically ordered records with unique ids.
    """

    dataset_id: str
    records: tuple[MarketRecord, ...]

    def __post_init__(self) -> None:
        validate_dataset(self)

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[MarketRecord]:
        return iter(self.records)

    @property
    def start_time(self) -> float:
        """Timestamp of the first record."""

        return self.records[0].timestamp

    @property
    def end_time(self) -> float:
        """Timestamp of the last record."""

        return self.records[-1].timestamp

    @classmethod
    def of(cls, dataset_id: str, inputs: Sequence[MarketInput]) -> MarketDataset:
        """Build a dataset from market inputs, assigning deterministic ids.

        Ids are ``"<dataset_id>-<index>"`` with a fixed-width index, so the same
        inputs always produce the same record identities -- which a replay of
        the run depends on.
        """

        return cls(dataset_id, records_from_inputs(dataset_id, inputs))


def validate_dataset(dataset: MarketDataset) -> None:
    """Ensure a dataset is non-empty, ordered, and unambiguously identified."""

    if not dataset.dataset_id:
        raise DatasetValidationError("Dataset must have a dataset_id.")
    if not dataset.records:
        raise DatasetValidationError("Empty datasets cannot be backtested or replayed.")

    seen: set[str] = set()
    previous = float("-inf")
    for record in dataset.records:
        if record.timestamp < previous:
            raise DatasetValidationError(
                f"Records are not chronologically ordered: {record.event_id} at "
                f"{record.timestamp} follows an event at {previous}."
            )
        if record.event_id in seen:
            raise DatasetValidationError(f"Duplicate record id: {record.event_id}")
        if record.timestamp != record.payload.timestamp:
            raise DatasetValidationError(
                f"Record {record.event_id} timestamp {record.timestamp} disagrees "
                f"with its market input at {record.payload.timestamp}."
            )
        seen.add(record.event_id)
        previous = record.timestamp
