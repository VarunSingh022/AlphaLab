"""The Model Registry data model and core registration / read operations.

Every type the registry works with lives here so the promotion, rollback, and
deployment modules can depend on this one module without cycles -- the same
single-module data-model layout ``alphalab.experiment_tracking.tracker`` uses.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum, auto

from alphalab.common.registry import with_mapping_item
from alphalab.model_registry.exceptions import ModelRegistryInputError

ParamValue = str | int | float | bool
"""Mixed-type hyperparameter value.

Intentionally identical to ``alphalab.experiment_tracking.tracker.ParamValue``.
The two are kept separate for now to avoid coupling the registry to the
experiment-tracking package; consolidating both onto a shared alias is a
candidate for the v2 refactor.
"""


class ModelStage(Enum):
    """Lifecycle stage of a registered model version.

    A version is always registered at ``NONE`` and moves forward through
    ``STAGING`` / ``PRODUCTION`` and eventually to ``ARCHIVED``. ``NONE`` is an
    initial-only state -- :func:`alphalab.model_registry.promotion.promote`
    refuses to move a version back to it.
    """

    NONE = auto()
    STAGING = auto()
    PRODUCTION = auto()
    ARCHIVED = auto()


@dataclass(frozen=True, slots=True)
class DeploymentMetadata:
    """Where and how a model version is deployed.

    Attributes:
        environment: Deployment target name, e.g. ``"paper"`` or ``"live-eu"``.
        deployed_at: Unix timestamp the deployment was recorded.
        deployed_by: Identifier of whoever recorded the deployment.
        notes: Free-form description.
    """

    environment: str
    deployed_at: float
    deployed_by: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ModelVersion:
    """One registered version of a named model.

    Attributes:
        name: The logical model name this is a version of.
        version: 1-based version number, assigned in registration order.
        model: The trained model object itself. The registry is
            artifact-type-agnostic -- this is any of ``alphalab.ml`` /
            ``alphalab.deep_learning`` / ``alphalab.reinforcement_learning``'s
            frozen model dataclasses, or any other object. Use
            :func:`get_model` to recover it with a checked type.
        stage: Current lifecycle stage.
        metrics: Evaluation metrics captured at registration time.
        parameters: Hyperparameters / configuration the model was trained with.
        tags: Free-form labels for filtering and grouping.
        run_id: The ``alphalab.experiment_tracking`` run that produced this
            model, if any. A link, not a copy -- run history stays in that
            package.
        created_at: Unix timestamp the version was registered.
        deployment: Deployment metadata, set once the version reaches
            ``STAGING`` or ``PRODUCTION``. ``None`` until then.
    """

    name: str
    version: int
    model: object
    stage: ModelStage
    metrics: Mapping[str, float] = field(default_factory=dict)
    parameters: Mapping[str, ParamValue] = field(default_factory=dict)
    tags: Mapping[str, str] = field(default_factory=dict)
    run_id: str | None = None
    created_at: float = 0.0
    deployment: DeploymentMetadata | None = None


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    """An append-only audit entry for a single stage transition.

    Attributes:
        name: The model name.
        version: The version whose stage changed.
        from_stage: Stage before the transition.
        to_stage: Stage after the transition.
        timestamp: Unix timestamp the transition happened.
    """

    name: str
    version: int
    from_stage: ModelStage
    to_stage: ModelStage
    timestamp: float


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    """Immutable registry of every model version and every stage transition.

    Attributes:
        versions: Model name -> that model's versions, in ascending version
            order.
        promotions: Every stage transition ever applied, in the order it
            happened. Rollback reads this to find the previous production
            version.
    """

    versions: Mapping[str, tuple[ModelVersion, ...]] = field(default_factory=dict)
    promotions: tuple[PromotionRecord, ...] = ()


def register_model(
    registry: ModelRegistry,
    name: str,
    model: object,
    timestamp: float,
    metrics: Mapping[str, float] | None = None,
    parameters: Mapping[str, ParamValue] | None = None,
    tags: Mapping[str, str] | None = None,
    run_id: str | None = None,
) -> tuple[ModelRegistry, ModelVersion]:
    """Registers ``model`` as the next version of ``name``, at stage ``NONE``.

    The first registration of a name is version 1; each subsequent one
    increments.

    Raises:
        ModelRegistryInputError: If ``name`` is empty or blank.
    """
    if not name.strip():
        raise ModelRegistryInputError("name cannot be empty.")

    existing = registry.versions.get(name, ())
    version = ModelVersion(
        name=name,
        version=len(existing) + 1,
        model=model,
        stage=ModelStage.NONE,
        metrics=dict(metrics) if metrics else {},
        parameters=dict(parameters) if parameters else {},
        tags=dict(tags) if tags else {},
        run_id=run_id,
        created_at=timestamp,
    )
    new_versions = with_mapping_item(registry.versions, name, (*existing, version))
    return replace(registry, versions=new_versions), version


def model_names(registry: ModelRegistry) -> tuple[str, ...]:
    """Returns every registered model name, in registration order."""
    return tuple(registry.versions)


def list_versions(registry: ModelRegistry, name: str) -> tuple[ModelVersion, ...]:
    """Returns every version of ``name`` in ascending version order.

    Raises:
        ModelRegistryInputError: If ``name`` has no registered versions.
    """
    versions = registry.versions.get(name)
    if not versions:
        raise ModelRegistryInputError(f"No model registered under name '{name}'.")
    return versions


def get_version(registry: ModelRegistry, name: str, version: int) -> ModelVersion:
    """Returns a specific version of ``name``.

    Raises:
        ModelRegistryInputError: If ``name`` or that ``version`` is unknown.
    """
    for candidate in list_versions(registry, name):
        if candidate.version == version:
            return candidate
    raise ModelRegistryInputError(f"Model '{name}' has no version {version}.")


def latest_version(registry: ModelRegistry, name: str) -> ModelVersion:
    """Returns the highest-numbered version of ``name``.

    Raises:
        ModelRegistryInputError: If ``name`` has no registered versions.
    """
    return list_versions(registry, name)[-1]


def get_model[ModelT](
    registry: ModelRegistry, name: str, version: int, expected_type: type[ModelT]
) -> ModelT:
    """Returns a version's stored model object, checked against ``expected_type``.

    Raises:
        ModelRegistryInputError: If the version is unknown, or its stored model
            is not an instance of ``expected_type``.
    """
    model = get_version(registry, name, version).model
    if not isinstance(model, expected_type):
        raise ModelRegistryInputError(
            f"Model '{name}' version {version} is a {type(model).__name__}, "
            f"not a {expected_type.__name__}."
        )
    return model


def replace_version(registry: ModelRegistry, updated: ModelVersion) -> ModelRegistry:
    """Returns a registry with ``updated`` swapped in for its current entry.

    Internal helper shared by the promotion, rollback, and deployment modules.
    ``updated`` must already exist (same name and version); callers guarantee
    this by deriving ``updated`` from :func:`get_version`.
    """
    current = registry.versions[updated.name]
    new_tuple = tuple(
        updated if existing.version == updated.version else existing for existing in current
    )
    new_versions = with_mapping_item(registry.versions, updated.name, new_tuple)
    return replace(registry, versions=new_versions)
