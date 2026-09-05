"""The Model Registry data model and core registration / read operations.

Every type the registry works with lives here so the promotion, rollback, and
deployment modules can depend on this one module without cycles -- the same
single-module data-model layout ``alphalab.experiment_tracking.tracker`` uses.

Containers and indexes
----------------------
Every write returns a new immutable registry. At v2.3 each one copied the whole
name mapping and rebuilt the whole version tuple, so ``N`` registrations cost
``O(N^2)`` -- registering 8000 versions of one model took 13x as long as
registering 2000. Versions are now a
:class:`~alphalab.common.persistent_map.PersistentMap` keyed by version number
and the promotion log is an :class:`~alphalab.common.append_log.AppendOnlyLog`,
both of which share structure instead of copying.

``production`` and ``production_line`` are indexes over the same facts, kept so
that the write path never scans: finding the incumbent production version, and
finding the version to roll back to, are both O(1). They are maintained by this
package's functions, in the same way :class:`~alphalab.oms.book.OrderBook`
maintains its asset and strategy indexes -- build a registry through
:func:`register_model` and :func:`~alphalab.model_registry.promotion.promote`
rather than by hand.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Any

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.common.types import ParamValue
from alphalab.model_registry.exceptions import ModelRegistryInputError

__all__ = [
    "ArtifactRef",
    "DeploymentMetadata",
    "ModelRegistry",
    "ModelStage",
    "ModelVersion",
    "ParamValue",
    "PromotionRecord",
    "get_model",
    "get_version",
    "latest_version",
    "list_versions",
    "model_names",
    "register_model",
    "replace_version",
]


class ModelStage(Enum):
    """Lifecycle stage of a registered version.

    A version is always registered at ``NONE`` and moves forward through
    ``STAGING`` / ``PRODUCTION`` and eventually to ``ARCHIVED``. ``NONE`` is an
    initial-only state -- :func:`alphalab.model_registry.promotion.promote`
    refuses to move a version back to it. The legal moves between the others are
    declared in :mod:`alphalab.model_registry.stages`.

    This is AlphaLab's one stage vocabulary for a *registered, promotable
    artifact*. :mod:`alphalab.lifecycle` stages strategy versions with the same
    enum rather than defining a parallel one; the name is historical, from when
    trained models were the only thing staged. It is deliberately unrelated to
    :class:`alphalab.strategy.state.LifecycleState`, which tracks a strategy
    *instance running inside a session* -- a different axis entirely.
    """

    NONE = auto()
    STAGING = auto()
    PRODUCTION = auto()
    ARCHIVED = auto()


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A reference to the bytes a model version was trained into.

    AlphaLab stores no artifact bytes and implements no object store. A registry
    entry says *where* an artifact is and *what it should hash to*; fetching it
    is the caller's job, and so is putting it there. Recording the reference is
    still worth doing: it is what lets a deployment name an exact artifact, and
    what lets a later reader detect that the file behind a version changed.

    Attributes:
        uri: Location of the artifact, opaque to AlphaLab -- a path, an
            ``s3://`` URL, a content-addressed id.
        media_type: How the bytes are encoded, e.g. ``"application/json"``.
        checksum: Digest of the artifact's bytes, if the producer computed one.
            AlphaLab never computes it, because it never reads the bytes.
        size_bytes: Artifact size, if known.
    """

    uri: str
    media_type: str = ""
    checksum: str = ""
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if not self.uri.strip():
            raise ModelRegistryInputError("ArtifactRef.uri cannot be empty.")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ModelRegistryInputError(
                f"ArtifactRef.size_bytes cannot be negative, got {self.size_bytes}."
            )


@dataclass(frozen=True, slots=True)
class DeploymentMetadata:
    """A note on a version recording where it was deployed.

    This is a *note*, not the deployment record. What is actually live in an
    environment is owned by :mod:`alphalab.deployment_manager`'s append-only
    ledger; setting this by hand claims nothing and verifies nothing.
    :mod:`alphalab.lifecycle` derives it from the real deployment record so the
    two cannot disagree.

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
            :func:`get_model` to recover it with a checked type. It is held in
            memory only: see ``artifact`` for what a persisted registry records
            instead.
        stage: Current lifecycle stage.
        metrics: Evaluation metrics captured at registration time.
        parameters: Hyperparameters / configuration the model was trained with.
        tags: Free-form labels for filtering and grouping.
        run_id: The ``alphalab.experiment_tracking`` run that produced this
            model, if any. A link, not a copy -- run history stays in that
            package.
        created_at: Unix timestamp the version was registered.
        deployment: A note recording where the version was deployed. ``None``
            until something records one.
        artifact: Reference to the artifact this version was trained into, if
            the producer supplied one.
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
    artifact: ArtifactRef | None = None

    def __serializable__(self) -> dict[str, Any]:
        """Serialize as metadata plus references, never as the model object.

        ``model`` is an arbitrary object -- a fitted estimator, a network, a
        policy -- and there is no deterministic JSON form for "arbitrary
        object". Stringifying it would produce a payload that reads back as
        prose, which is the failure v2.1 removed from the append-only logs. The
        projection therefore records the model's *type* and its ``artifact``
        reference, which is what a reader can act on, and drops the object
        itself. A registry snapshot is metadata and references by construction.
        """

        return {
            "name": self.name,
            "version": self.version,
            "model_type": f"{type(self.model).__module__}.{type(self.model).__qualname__}",
            "stage": self.stage,
            "metrics": self.metrics,
            "parameters": self.parameters,
            "tags": self.tags,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "deployment": self.deployment,
            "artifact": self.artifact,
        }


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
        versions: Model name -> that model's versions, keyed by version number
            and iterating in ascending version order.
        promotions: Every stage transition ever applied, in the order it
            happened.
        production: Model name -> the version currently in ``PRODUCTION``. A
            name with no production version has no entry.
        production_line: Model name -> every version that has held
            ``PRODUCTION``, in the order it did. Rollback reads its
            second-to-last entry; the transition rules read its last, to tell a
            restore of a retired version from a resurrection of one that was
            never live.
    """

    versions: PersistentMap[str, PersistentMap[int, ModelVersion]] = field(
        default_factory=PersistentMap
    )
    promotions: AppendOnlyLog[PromotionRecord] = field(default_factory=AppendOnlyLog)
    production: PersistentMap[str, int] = field(default_factory=PersistentMap)
    production_line: PersistentMap[str, AppendOnlyLog[int]] = field(default_factory=PersistentMap)

    def __post_init__(self) -> None:
        # Only the container's own type is checked, never its contents: this
        # runs on every ``replace`` and therefore on every write, so inspecting
        # each entry would make a run of N writes cost O(N^2) -- which is the
        # cost this release removed. A registry built by this package's
        # functions is already in the right shape and takes the O(1) path; a
        # hand-built mapping is converted whole, once.
        if not isinstance(self.versions, PersistentMap):
            object.__setattr__(self, "versions", _as_lines(self.versions))
        if not isinstance(self.promotions, AppendOnlyLog):
            object.__setattr__(self, "promotions", AppendOnlyLog(self.promotions))


def _as_lines(
    versions: Mapping[str, Any],
) -> PersistentMap[str, PersistentMap[int, ModelVersion]]:
    """Accept a hand-built ``name -> versions`` mapping in any ordered shape."""

    def line(value: Any) -> PersistentMap[int, ModelVersion]:
        if isinstance(value, PersistentMap):
            return value
        entries: Iterable[ModelVersion] = value.values() if isinstance(value, Mapping) else value
        return PersistentMap((version.version, version) for version in entries)

    return PersistentMap((name, line(value)) for name, value in versions.items())


def register_model(
    registry: ModelRegistry,
    name: str,
    model: object,
    timestamp: float,
    metrics: Mapping[str, float] | None = None,
    parameters: Mapping[str, ParamValue] | None = None,
    tags: Mapping[str, str] | None = None,
    run_id: str | None = None,
    artifact: ArtifactRef | None = None,
) -> tuple[ModelRegistry, ModelVersion]:
    """Registers ``model`` as the next version of ``name``, at stage ``NONE``.

    The first registration of a name is version 1; each subsequent one
    increments.

    Raises:
        ModelRegistryInputError: If ``name`` is empty or blank.
    """
    if not name.strip():
        raise ModelRegistryInputError("name cannot be empty.")

    existing: PersistentMap[int, ModelVersion] = registry.versions.get(name, PersistentMap())
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
        artifact=artifact,
    )
    updated = replace(
        registry, versions=registry.versions.set(name, existing.set(version.version, version))
    )
    return updated, version


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
    return tuple(versions.values())


def get_version(registry: ModelRegistry, name: str, version: int) -> ModelVersion:
    """Returns a specific version of ``name``.

    Raises:
        ModelRegistryInputError: If ``name`` or that ``version`` is unknown.
    """
    versions = registry.versions.get(name)
    if not versions:
        raise ModelRegistryInputError(f"No model registered under name '{name}'.")
    found = versions.get(version)
    if found is None:
        raise ModelRegistryInputError(f"Model '{name}' has no version {version}.")
    return found


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
    line = registry.versions[updated.name]
    return replace(
        registry, versions=registry.versions.set(updated.name, line.set(updated.version, updated))
    )
