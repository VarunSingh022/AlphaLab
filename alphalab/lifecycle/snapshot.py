"""Complete, restorable snapshots of a :class:`~alphalab.lifecycle.state.LifecycleState`.

v2.4 gave the lifecycle an audit trail: every promotion records what justified
it, every deployment records what it replaced, and the whole state serializes
deterministically. What it could not do was read any of that back. A promotion
that cannot be reloaded six months later is a log line, not an audit trail.

What restore guarantees
-----------------------
``restore(capture(state), models=...) == state``, under the contract ADR-0014
states for every AlphaLab state: the restored value *compares equal*, and does
not reproduce internal container lineage.

The supplied models
-------------------
``ModelVersion.model`` is an arbitrary Python object -- a fitted estimator, a
network, a policy -- and :meth:`ModelVersion.__serializable__` deliberately drops
it, recording ``model_type`` and the :class:`~alphalab.model_registry.registry.ArtifactRef`
instead. There is no deterministic JSON form for "arbitrary object", and
inventing one is how a payload comes to read back as prose.

So a lifecycle snapshot is metadata and references, and :func:`restore` takes the
objects back from the caller, keyed by rendered model reference::

    restore(snapshot, models={"momentum@1": trained_model})

A reference the caller does not supply is an error, never a silently substituted
``None``: a registry holding nothing where a model belongs would answer "which
model is in production?" with something that looks like an answer.
``model_type`` is recorded so a caller can check it got back what it stored, and
:func:`restore` refuses an object whose type does not match what was captured.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.constants import DEFAULT_SCHEMA_VERSION
from alphalab.common.persistent_map import PersistentMap
from alphalab.deployment_manager.packaging import ReleasePackage
from alphalab.deployment_manager.releases import DeploymentManager, DeploymentRecord
from alphalab.experiment_tracking.tracker import ExperimentRun, ExperimentTracker, RunStatus
from alphalab.lifecycle.evidence import ValidationEvidence, ValidationMethod
from alphalab.lifecycle.identity import ModelRef
from alphalab.lifecycle.state import LifecycleState
from alphalab.lifecycle.strategy_version import (
    StrategyPromotionRecord,
    StrategyVersion,
    StrategyVersionRegistry,
)
from alphalab.model_registry.registry import (
    ArtifactRef,
    DeploymentMetadata,
    ModelRegistry,
    ModelStage,
    ModelVersion,
    PromotionRecord,
)
from alphalab.persistence.decode import (
    as_bool,
    as_float,
    as_int,
    as_mapping,
    as_named_enum,
    as_optional_str,
    as_sequence,
    as_str,
    as_str_mapping,
    require,
    require_schema_version,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.studio.strategy import StrategyDefinition

__all__ = [
    "LIFECYCLE_SNAPSHOT_SCHEMA",
    "LifecycleSnapshot",
    "ModelVersionRecord",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. See ADR-0014.
LIFECYCLE_SNAPSHOT_SCHEMA = DEFAULT_SCHEMA_VERSION

_SUBSYSTEM = "lifecycle"


@dataclass(frozen=True, slots=True)
class ModelVersionRecord:
    """A model version as it survives serialization: everything but the object.

    ``model_type`` is the fully qualified type of the object that was registered,
    so a caller supplying models back to :func:`restore` can be told when it
    handed over the wrong one.
    """

    name: str
    version: int
    model_type: str
    stage: ModelStage
    metrics: Mapping[str, float]
    parameters: Mapping[str, Any]
    tags: Mapping[str, str]
    run_id: str | None
    created_at: float
    deployment: DeploymentMetadata | None
    artifact: ArtifactRef | None

    @property
    def ref(self) -> str:
        """The rendered reference a caller keys a supplied model by."""

        return f"{self.name}@{self.version}"


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot:
    """Complete, JSON-serializable projection of a :class:`LifecycleState`."""

    runs: tuple[ExperimentRun, ...]
    model_versions: tuple[ModelVersionRecord, ...]
    model_promotions: tuple[PromotionRecord, ...]
    production: Mapping[str, int]
    production_line: Mapping[str, tuple[int, ...]]
    strategy_versions: tuple[StrategyVersion, ...]
    strategy_promotions: tuple[StrategyPromotionRecord, ...]
    release_packages: tuple[ReleasePackage, ...]
    deployments: tuple[DeploymentRecord, ...]
    evidence: tuple[ValidationEvidence, ...]
    releases: Mapping[str, int]
    schema_version: int = LIFECYCLE_SNAPSHOT_SCHEMA


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def _model_type(model: object) -> str:
    return f"{type(model).__module__}.{type(model).__qualname__}"


def capture(state: LifecycleState) -> LifecycleSnapshot:
    """Project ``state`` into its complete serializable snapshot.

    Every registry keyed by name flattens to an array: the key is already a field
    of each record (a run's ``run_id``, a version's ``name``/``version``, a
    release's ``name``), so storing it twice would let a payload disagree with
    itself. The two indexes that are *not* derivable that way -- the production
    pointer and the production tenure line -- are kept as mappings.
    """

    return LifecycleSnapshot(
        runs=tuple(state.experiments.runs.values()),
        model_versions=tuple(
            ModelVersionRecord(
                name=version.name,
                version=version.version,
                model_type=_model_type(version.model),
                stage=version.stage,
                metrics=dict(version.metrics),
                parameters=dict(version.parameters),
                tags=dict(version.tags),
                run_id=version.run_id,
                created_at=version.created_at,
                deployment=version.deployment,
                artifact=version.artifact,
            )
            for line in state.models.versions.values()
            for version in line.values()
        ),
        model_promotions=state.models.promotions.to_tuple(),
        production=dict(state.models.production),
        production_line={name: tuple(line) for name, line in state.models.production_line.items()},
        strategy_versions=tuple(
            version for line in state.strategies.versions.values() for version in line.values()
        ),
        strategy_promotions=state.strategies.promotions.to_tuple(),
        release_packages=tuple(
            package for line in state.deployments.releases.values() for package in line
        ),
        deployments=state.deployments.deployments.to_tuple(),
        evidence=tuple(state.evidence.values()),
        releases=dict(state.releases),
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def _require_model(models: Mapping[str, object], record: ModelVersionRecord) -> object:
    model = models.get(record.ref)
    if model is None:
        raise StateDecodeError(
            f"No model supplied for {record.ref!r}. A lifecycle snapshot records a model "
            f"version's metadata and its {record.model_type} type, never the object "
            "itself; pass it back through restore(snapshot, models={...})."
        )
    supplied = _model_type(model)
    if supplied != record.model_type:
        raise StateDecodeError(
            f"Model supplied for {record.ref!r} is a {supplied}, but the snapshot "
            f"recorded a {record.model_type}."
        )
    return model


def restore(snapshot: LifecycleSnapshot, models: Mapping[str, object]) -> LifecycleState:
    """Rebuild the state a snapshot was captured from.

    Args:
        snapshot: The captured projection.
        models: The registered model objects, keyed by rendered reference
            (``"momentum@1"``). Every model version in the snapshot must have an
            entry.

    Raises:
        StateDecodeError: If a model version has no supplied object, or the
            supplied object is not of the type that was captured.
    """

    versions: PersistentMap[str, PersistentMap[int, ModelVersion]] = PersistentMap()
    for record in snapshot.model_versions:
        model_line: PersistentMap[int, ModelVersion] = versions.get(record.name, PersistentMap())
        versions = versions.set(
            record.name,
            model_line.set(
                record.version,
                ModelVersion(
                    name=record.name,
                    version=record.version,
                    model=_require_model(models, record),
                    stage=record.stage,
                    metrics=dict(record.metrics),
                    parameters=dict(record.parameters),
                    tags=dict(record.tags),
                    run_id=record.run_id,
                    created_at=record.created_at,
                    deployment=record.deployment,
                    artifact=record.artifact,
                ),
            ),
        )

    strategies: PersistentMap[str, PersistentMap[int, StrategyVersion]] = PersistentMap()
    for version in snapshot.strategy_versions:
        strategy_line: PersistentMap[int, StrategyVersion] = strategies.get(
            version.name, PersistentMap()
        )
        strategies = strategies.set(version.name, strategy_line.set(version.version, version))

    releases: PersistentMap[str, AppendOnlyLog[ReleasePackage]] = PersistentMap()
    for package in snapshot.release_packages:
        releases = releases.set(
            package.name, releases.get(package.name, AppendOnlyLog()).append(package)
        )

    environments: PersistentMap[str, AppendOnlyLog[DeploymentRecord]] = PersistentMap()
    for record_ in snapshot.deployments:
        environments = environments.set(
            record_.environment,
            environments.get(record_.environment, AppendOnlyLog()).append(record_),
        )

    return LifecycleState(
        experiments=ExperimentTracker(
            runs=PersistentMap((run.run_id, run) for run in snapshot.runs)
        ),
        models=ModelRegistry(
            versions=versions,
            promotions=AppendOnlyLog(snapshot.model_promotions),
            production=PersistentMap(snapshot.production),
            production_line=PersistentMap(
                (name, AppendOnlyLog(line)) for name, line in snapshot.production_line.items()
            ),
        ),
        strategies=StrategyVersionRegistry(
            versions=strategies, promotions=AppendOnlyLog(snapshot.strategy_promotions)
        ),
        deployments=DeploymentManager(
            releases=releases,
            deployments=AppendOnlyLog(snapshot.deployments),
            environments=environments,
        ),
        evidence=PersistentMap((item.evidence_id, item) for item in snapshot.evidence),
        releases=PersistentMap(snapshot.releases),
    )


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _param(value: Any, field: str) -> Any:
    """Decode a ``ParamValue``: a string, int, float or bool, and nothing else."""

    if isinstance(value, str | bool | int | float):
        return value
    raise StateDecodeError(f"{field} is not a parameter value: {value!r}")


def _float_mapping(value: Any, field: str) -> dict[str, float]:
    payload = as_mapping(value, field)
    return {
        as_str(key, f"{field} key"): as_float(item, f"{field}[{key}]")
        for key, item in payload.items()
    }


def _param_mapping(value: Any, field: str) -> dict[str, Any]:
    payload = as_mapping(value, field)
    return {
        as_str(key, f"{field} key"): _param(item, f"{field}[{key}]")
        for key, item in payload.items()
    }


def _int_mapping(value: Any, field: str) -> dict[str, int]:
    payload = as_mapping(value, field)
    return {
        as_str(key, f"{field} key"): as_int(item, f"{field}[{key}]")
        for key, item in payload.items()
    }


def _run(value: Any, index: int) -> ExperimentRun:
    where = f"runs[{index}]"
    payload = as_mapping(value, where)
    metrics = as_mapping(require(payload, "metrics"), f"{where}.metrics")
    return ExperimentRun(
        run_id=as_str(require(payload, "run_id"), f"{where}.run_id"),
        name=as_str(require(payload, "name"), f"{where}.name"),
        status=as_named_enum(RunStatus, require(payload, "status"), f"{where}.status"),
        parameters=_param_mapping(require(payload, "parameters"), f"{where}.parameters"),
        metrics=PersistentMap(
            (
                as_str(name, f"{where}.metrics key"),
                AppendOnlyLog(
                    as_float(item, f"{where}.metrics[{name}]")
                    for item in as_sequence(history, f"{where}.metrics[{name}]")
                ),
            )
            for name, history in metrics.items()
        ),
        parent_run_id=as_optional_str(require(payload, "parent_run_id"), f"{where}.parent_run_id"),
        tags=as_str_mapping(require(payload, "tags"), f"{where}.tags"),
        created_at=as_float(require(payload, "created_at"), f"{where}.created_at"),
        completed_at=(
            None
            if require(payload, "completed_at") is None
            else as_float(payload["completed_at"], f"{where}.completed_at")
        ),
    )


def _artifact(value: Any, where: str) -> ArtifactRef | None:
    if value is None:
        return None
    payload = as_mapping(value, where)
    size = require(payload, "size_bytes")
    return ArtifactRef(
        uri=as_str(require(payload, "uri"), f"{where}.uri"),
        media_type=as_str(require(payload, "media_type"), f"{where}.media_type"),
        checksum=as_str(require(payload, "checksum"), f"{where}.checksum"),
        size_bytes=None if size is None else as_int(size, f"{where}.size_bytes"),
    )


def _deployment_note(value: Any, where: str) -> DeploymentMetadata | None:
    if value is None:
        return None
    payload = as_mapping(value, where)
    return DeploymentMetadata(
        environment=as_str(require(payload, "environment"), f"{where}.environment"),
        deployed_at=as_float(require(payload, "deployed_at"), f"{where}.deployed_at"),
        deployed_by=as_str(require(payload, "deployed_by"), f"{where}.deployed_by"),
        notes=as_str(require(payload, "notes"), f"{where}.notes"),
    )


def _model_version(value: Any, index: int) -> ModelVersionRecord:
    where = f"model_versions[{index}]"
    payload = as_mapping(value, where)
    return ModelVersionRecord(
        name=as_str(require(payload, "name"), f"{where}.name"),
        version=as_int(require(payload, "version"), f"{where}.version"),
        model_type=as_str(require(payload, "model_type"), f"{where}.model_type"),
        stage=as_named_enum(ModelStage, require(payload, "stage"), f"{where}.stage"),
        metrics=_float_mapping(require(payload, "metrics"), f"{where}.metrics"),
        parameters=_param_mapping(require(payload, "parameters"), f"{where}.parameters"),
        tags=as_str_mapping(require(payload, "tags"), f"{where}.tags"),
        run_id=as_optional_str(require(payload, "run_id"), f"{where}.run_id"),
        created_at=as_float(require(payload, "created_at"), f"{where}.created_at"),
        deployment=_deployment_note(require(payload, "deployment"), f"{where}.deployment"),
        artifact=_artifact(require(payload, "artifact"), f"{where}.artifact"),
    )


def _model_promotion(value: Any, index: int) -> PromotionRecord:
    where = f"model_promotions[{index}]"
    payload = as_mapping(value, where)
    return PromotionRecord(
        name=as_str(require(payload, "name"), f"{where}.name"),
        version=as_int(require(payload, "version"), f"{where}.version"),
        from_stage=as_named_enum(ModelStage, require(payload, "from_stage"), f"{where}.from_stage"),
        to_stage=as_named_enum(ModelStage, require(payload, "to_stage"), f"{where}.to_stage"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
    )


def _definition(value: Any, where: str) -> StrategyDefinition:
    payload = as_mapping(value, where)
    return StrategyDefinition(
        strategy_id=as_str(require(payload, "strategy_id"), f"{where}.strategy_id"),
        name=as_str(require(payload, "name"), f"{where}.name"),
        version=as_str(require(payload, "version"), f"{where}.version"),
        author=as_str(require(payload, "author"), f"{where}.author"),
        description=as_str(require(payload, "description"), f"{where}.description"),
        parameters=_float_mapping(require(payload, "parameters"), f"{where}.parameters"),
        metadata=as_str_mapping(require(payload, "metadata"), f"{where}.metadata"),
    )


def _model_ref(value: Any, where: str) -> ModelRef | None:
    if value is None:
        return None
    payload = as_mapping(value, where)
    return ModelRef(
        name=as_str(require(payload, "name"), f"{where}.name"),
        version=as_int(require(payload, "version"), f"{where}.version"),
    )


def _strategy_version(value: Any, index: int) -> StrategyVersion:
    where = f"strategy_versions[{index}]"
    payload = as_mapping(value, where)
    return StrategyVersion(
        name=as_str(require(payload, "name"), f"{where}.name"),
        version=as_int(require(payload, "version"), f"{where}.version"),
        definition=_definition(require(payload, "definition"), f"{where}.definition"),
        stage=as_named_enum(ModelStage, require(payload, "stage"), f"{where}.stage"),
        model=_model_ref(require(payload, "model"), f"{where}.model"),
        run_id=as_optional_str(require(payload, "run_id"), f"{where}.run_id"),
        evidence_id=as_optional_str(require(payload, "evidence_id"), f"{where}.evidence_id"),
        policy_id=as_optional_str(require(payload, "policy_id"), f"{where}.policy_id"),
        created_at=as_float(require(payload, "created_at"), f"{where}.created_at"),
        tags=as_str_mapping(require(payload, "tags"), f"{where}.tags"),
    )


def _strategy_promotion(value: Any, index: int) -> StrategyPromotionRecord:
    where = f"strategy_promotions[{index}]"
    payload = as_mapping(value, where)
    return StrategyPromotionRecord(
        name=as_str(require(payload, "name"), f"{where}.name"),
        version=as_int(require(payload, "version"), f"{where}.version"),
        from_stage=as_named_enum(ModelStage, require(payload, "from_stage"), f"{where}.from_stage"),
        to_stage=as_named_enum(ModelStage, require(payload, "to_stage"), f"{where}.to_stage"),
        reason=as_str(require(payload, "reason"), f"{where}.reason"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
    )


def _release(value: Any, index: int) -> ReleasePackage:
    where = f"release_packages[{index}]"
    payload = as_mapping(value, where)
    return ReleasePackage(
        name=as_str(require(payload, "name"), f"{where}.name"),
        version=as_int(require(payload, "version"), f"{where}.version"),
        components=as_str_mapping(require(payload, "components"), f"{where}.components"),
        config=as_str_mapping(require(payload, "config"), f"{where}.config"),
        checksum=as_str(require(payload, "checksum"), f"{where}.checksum"),
        created_at=as_float(require(payload, "created_at"), f"{where}.created_at"),
    )


def _deployment(value: Any, index: int) -> DeploymentRecord:
    where = f"deployments[{index}]"
    payload = as_mapping(value, where)
    replaced = require(payload, "replaced_version")
    return DeploymentRecord(
        environment=as_str(require(payload, "environment"), f"{where}.environment"),
        release_name=as_str(require(payload, "release_name"), f"{where}.release_name"),
        version=as_int(require(payload, "version"), f"{where}.version"),
        replaced_version=(
            None if replaced is None else as_int(replaced, f"{where}.replaced_version")
        ),
        is_rollback=as_bool(require(payload, "is_rollback"), f"{where}.is_rollback"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
    )


def _evidence(value: Any, index: int) -> ValidationEvidence:
    where = f"evidence[{index}]"
    payload = as_mapping(value, where)
    seed = require(payload, "seed")
    return ValidationEvidence(
        evidence_id=as_str(require(payload, "evidence_id"), f"{where}.evidence_id"),
        method=as_named_enum(ValidationMethod, require(payload, "method"), f"{where}.method"),
        subject=as_str(require(payload, "subject"), f"{where}.subject"),
        dataset_id=as_str(require(payload, "dataset_id"), f"{where}.dataset_id"),
        metrics=_float_mapping(require(payload, "metrics"), f"{where}.metrics"),
        seed=None if seed is None else as_int(seed, f"{where}.seed"),
        produced_at=as_float(require(payload, "produced_at"), f"{where}.produced_at"),
        source_id=as_str(require(payload, "source_id"), f"{where}.source_id"),
    )


def from_primitives(payload: Mapping[str, Any]) -> LifecycleSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`LifecycleSnapshot`.

    Raises:
        StateDecodeError: If the payload is not an object, declares a schema
            version this build does not read, is missing a field, or holds a
            value of the wrong type. The message names the field.
    """

    payload = as_mapping(payload, "lifecycle snapshot")
    require_schema_version(payload, LIFECYCLE_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    def indexed(key: str) -> Any:
        return enumerate(as_sequence(require(payload, key), key))

    line_payload = as_mapping(require(payload, "production_line"), "production_line")
    return LifecycleSnapshot(
        runs=tuple(_run(item, i) for i, item in indexed("runs")),
        model_versions=tuple(_model_version(item, i) for i, item in indexed("model_versions")),
        model_promotions=tuple(
            _model_promotion(item, i) for i, item in indexed("model_promotions")
        ),
        production=_int_mapping(require(payload, "production"), "production"),
        production_line={
            as_str(name, "production_line key"): tuple(
                as_int(v, f"production_line[{name}]")
                for v in as_sequence(line, f"production_line[{name}]")
            )
            for name, line in line_payload.items()
        },
        strategy_versions=tuple(
            _strategy_version(item, i) for i, item in indexed("strategy_versions")
        ),
        strategy_promotions=tuple(
            _strategy_promotion(item, i) for i, item in indexed("strategy_promotions")
        ),
        release_packages=tuple(_release(item, i) for i, item in indexed("release_packages")),
        deployments=tuple(_deployment(item, i) for i, item in indexed("deployments")),
        evidence=tuple(_evidence(item, i) for i, item in indexed("evidence")),
        releases=_int_mapping(require(payload, "releases"), "releases"),
        schema_version=LIFECYCLE_SNAPSHOT_SCHEMA,
    )
