"""Builds ML-ready datasets directly from Feature Store.

This is the bridge for the roadmap's "Feature pipelines" requirement: rather than
inventing a separate feature representation for ML, `build_dataset_from_feature_store`
reads registered feature values straight out of `alphalab.feature_store`, the same
store `alphalab.factor_library` and `alphalab.alt_data` write into.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.feature_store.state import FeatureStoreState
from alphalab.feature_store.views import latest_value
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.linalg import Matrix

FeatureSpec = tuple[str, int]
"""A (feature_id, version) pair identifying a registered Feature Store feature."""


@dataclass(frozen=True, slots=True)
class Dataset:
    """An immutable, ML-ready design matrix and target vector.

    Attributes:
        feature_names: Column names of x, in order.
        x: The design matrix, one row per asset.
        y: The target vector, one value per asset, same row order as x.
        asset_ids: The asset each row corresponds to, same order as x and y --
            kept for traceability from a prediction back to the asset it concerns.
    """

    feature_names: tuple[str, ...]
    x: Matrix
    y: tuple[float, ...]
    asset_ids: tuple[str, ...]


def _numeric_value(value: float | Decimal | bool | str, context: str) -> float:
    if isinstance(value, str):
        raise MLInputError(f"{context} has a string value, which cannot be used as an ML feature.")
    return float(value)


def build_dataset_from_feature_store(
    state: FeatureStoreState,
    feature_specs: tuple[FeatureSpec, ...],
    target_spec: FeatureSpec,
    asset_ids: tuple[str, ...],
) -> Dataset:
    """Builds a Dataset by reading feature and target values for each asset.

    An asset is only included if every requested feature and the target both have
    a value present for it -- assets with any missing value are silently dropped,
    not imputed, since silent imputation could hide a real data availability
    problem.

    Args:
        state: The Feature Store state to read from.
        feature_specs: (feature_id, version) pairs to use as design matrix columns,
            in column order.
        target_spec: (feature_id, version) pair to use as the target.
        asset_ids: Candidate assets to build rows for.

    Raises:
        MLInputError: If feature_specs is empty, or no asset has complete data for
            every requested feature and the target.
    """
    if not feature_specs:
        raise MLInputError("feature_specs cannot be empty.")

    feature_names = tuple(f"{feature_id}:{version}" for feature_id, version in feature_specs)

    rows: list[tuple[float, ...]] = []
    targets: list[float] = []
    included_assets: list[str] = []

    for asset_id in asset_ids:
        row_values: list[float] = []
        complete = True

        for feature_id, version in feature_specs:
            found = latest_value(state, feature_id, version, asset_id)
            if found is None:
                complete = False
                break
            row_values.append(_numeric_value(found.value, f"Feature '{feature_id}:{version}'"))

        if not complete:
            continue

        target_feature_id, target_version = target_spec
        target_found = latest_value(state, target_feature_id, target_version, asset_id)
        if target_found is None:
            continue

        rows.append(tuple(row_values))
        targets.append(
            _numeric_value(target_found.value, f"Target '{target_feature_id}:{target_version}'")
        )
        included_assets.append(asset_id)

    if not rows:
        raise MLInputError(
            "No asset has complete data for every requested feature and the target; "
            "nothing to build a Dataset from."
        )

    return Dataset(
        feature_names=feature_names,
        x=tuple(rows),
        y=tuple(targets),
        asset_ids=tuple(included_assets),
    )
