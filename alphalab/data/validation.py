"""Strict validation preventing corrupt configurations."""

from alphalab.data.dataset import Dataset
from alphalab.data.exceptions import DataValidationError
from alphalab.data.state import UniversalDataState


def validate_dataset_ingestion(state: UniversalDataState, dataset: Dataset) -> None:
    if not dataset.metadata.dataset_id.strip():
        raise DataValidationError("Dataset ID cannot be empty.")
    if dataset.metadata.dataset_id in state.datasets:
        raise DataValidationError(f"Dataset {dataset.metadata.dataset_id} already exists.")