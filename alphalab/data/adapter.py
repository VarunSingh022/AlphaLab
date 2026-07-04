"""Adapter isolating provider logic to Canonical Dataset inputs."""

from alphalab.data.metadata import DatasetMetadata
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency


class DataAdapter:
    """Stateless generator for Data Engine Metadata."""

    @staticmethod
    def create_metadata(
        ds_id: str, src: str, asset: str, freq: str, start: float, end: float
    ) -> DatasetMetadata:
        """Helper standardizing string mappings into Enum types safely."""
        try:
            asset_class = DataAssetClass[asset.upper()]
        except KeyError:
            asset_class = DataAssetClass.EQUITY
            
        try:
            time_freq = TimeFrequency[freq.upper()]
        except KeyError:
            time_freq = TimeFrequency.DAILY
            
        return DatasetMetadata(ds_id, src, asset_class, time_freq, start, end)