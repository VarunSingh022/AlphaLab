"""Stateless registry manipulations for dataset catalogs."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.data.catalog import CatalogRecord
from alphalab.data.dataset import Dataset
from alphalab.data.events import DatasetCataloged
from alphalab.data.state import UniversalDataState


class DatasetRegistry:
    @staticmethod
    def _create_id() -> str: return str(new_id())

    @staticmethod
    def catalog(state: UniversalDataState, dataset: Dataset, ts: float) -> UniversalDataState:
        meta = dataset.metadata
        
        record = CatalogRecord(meta, dataset.quality, len(dataset.records))
        new_catalog = dict(state.catalog)
        new_catalog[meta.dataset_id] = record
        
        evt = DatasetCataloged(
            DatasetRegistry._create_id(), ts, meta.dataset_id, meta.asset_class.name)
        
        return replace(
            state, catalog=new_catalog, events=(*state.events, evt)
        )