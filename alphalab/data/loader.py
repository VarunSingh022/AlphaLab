"""Orchestrates translating parsed sequences into Dataset blocks."""

from alphalab.data.dataset import Dataset
from alphalab.data.feed import Bar
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.quality import evaluate_bar_quality
from alphalab.data.schema import DatasetSchema


def create_dataset(metadata: DatasetMetadata, bars: tuple[Bar, ...]) -> Dataset:
    schema = DatasetSchema(("timestamp", "open", "high", "low", "close", "volume"), "BAR")
    quality = evaluate_bar_quality(metadata.dataset_id, bars)
    return Dataset(metadata, schema, quality, bars)
