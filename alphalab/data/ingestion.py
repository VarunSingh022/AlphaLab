"""Higher level combinators."""

from collections.abc import Mapping, Sequence
from typing import Any

from alphalab.data.dataset import Dataset
from alphalab.data.loader import create_dataset
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.parser import parse_raw_rows


def parse_and_load(metadata: DatasetMetadata, raw_rows: Sequence[Mapping[str, Any]]) -> Dataset:
    """Directly wraps an untyped dictionary array into a strictly typed Canonical Dataset."""
    bars = parse_raw_rows("UNKNOWN", raw_rows)
    # Re-apply dataset symbol definition uniformly
    bars = tuple(
        type(b)(metadata.dataset_id, b.timestamp, b.open, b.high, b.low, b.close, b.volume)
        for b in bars
    )
    return create_dataset(metadata, bars)
