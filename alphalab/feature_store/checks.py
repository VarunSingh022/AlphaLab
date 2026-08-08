"""Pure stateless feature validation checks returning violations if breached."""

from decimal import Decimal

from alphalab.feature_store.metadata import FeatureMetadata, FeatureValueType
from alphalab.feature_store.state import FeatureStoreState
from alphalab.feature_store.value import FeatureValue
from alphalab.feature_store.violations import FeatureViolation

_EXPECTED_PYTHON_TYPES: dict[FeatureValueType, tuple[type, ...]] = {
    FeatureValueType.FLOAT: (float,),
    FeatureValueType.DECIMAL: (Decimal,),
    FeatureValueType.INTEGER: (int,),
    FeatureValueType.BOOLEAN: (bool,),
    FeatureValueType.STRING: (str,),
}


def check_feature_registered(
    value: FeatureValue, state: FeatureStoreState
) -> FeatureViolation | None:
    """Verifies the value's (feature_id, version) pair is registered."""
    key = f"{value.feature_id}:{value.version}"
    if key not in state.features:
        return FeatureViolation(
            rule="FeatureRegistered",
            description="Value references a feature_id/version that is not registered.",
            severity="HIGH",
            feature_id=value.feature_id,
            current_value=key,
            allowed_value="a registered feature_id:version",
        )
    return None


def check_value_type(value: FeatureValue, metadata: FeatureMetadata) -> FeatureViolation | None:
    """Verifies the runtime type of a value matches its feature's declared type.

    Booleans are checked before the general int/float branch is consulted since in
    Python `bool` is a subclass of `int`, which would otherwise let a boolean satisfy
    an INTEGER-typed feature silently.
    """
    expected_types = _EXPECTED_PYTHON_TYPES[metadata.value_type]

    is_bool_mismatch = (
        isinstance(value.value, bool) and metadata.value_type != FeatureValueType.BOOLEAN
    )
    type_matches = isinstance(value.value, expected_types) and not is_bool_mismatch

    if not type_matches:
        return FeatureViolation(
            rule="ValueType",
            description="Value's runtime type does not match the feature's declared value_type.",
            severity="HIGH",
            feature_id=value.feature_id,
            current_value=type(value.value).__name__,
            allowed_value=metadata.value_type.name,
        )
    return None


def check_asset_scope(value: FeatureValue, metadata: FeatureMetadata) -> FeatureViolation | None:
    """Verifies asset_id presence matches the feature's asset_scoped declaration."""
    if metadata.asset_scoped and value.asset_id is None:
        return FeatureViolation(
            rule="AssetScope",
            description="Feature is asset-scoped but the value has no asset_id.",
            severity="HIGH",
            feature_id=value.feature_id,
            current_value="None",
            allowed_value="a non-empty asset_id",
        )
    if not metadata.asset_scoped and value.asset_id is not None:
        return FeatureViolation(
            rule="AssetScope",
            description="Feature is not asset-scoped but the value has an asset_id.",
            severity="MEDIUM",
            feature_id=value.feature_id,
            current_value=value.asset_id,
            allowed_value="None",
        )
    return None


def check_dependencies_registered(
    metadata: FeatureMetadata, state: FeatureStoreState
) -> FeatureViolation | None:
    """Verifies every declared upstream dependency has at least one registered version."""
    registered_ids = {meta.feature_id for meta in state.features.values()}
    missing = tuple(dep for dep in metadata.depends_on if dep not in registered_ids)

    if missing:
        return FeatureViolation(
            rule="DependenciesRegistered",
            description="One or more declared dependencies are not registered.",
            severity="HIGH",
            feature_id=metadata.feature_id,
            current_value=", ".join(missing),
            allowed_value="all depends_on entries registered",
        )
    return None
