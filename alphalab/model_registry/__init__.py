"""AlphaLab Model Registry.

Versioned registration, stage promotion, rollback, and deployment metadata for
trained models.

``alphalab.experiment_tracking`` already versions experiment *runs* -- their
metric histories and the lineage of one run being a re-run of another. This
package versions the trained model *artifacts* those runs produce, and governs
each one's lifecycle: registered at ``NONE``, promoted through ``STAGING`` and
``PRODUCTION``, finally ``ARCHIVED``. ``ModelVersion.run_id`` links a version
back to the experiment run that produced it -- a reference, not a copy; run
history stays in ``experiment_tracking``.

The registry is artifact-type-agnostic. It stores the model object itself
(``alphalab.ml`` / ``alphalab.deep_learning`` /
``alphalab.reinforcement_learning`` model dataclasses, or anything else)
alongside its metadata and stage; :func:`get_model` recovers it with a checked
type. Nothing here serializes models to disk -- state is threaded functionally
through immutable ``ModelRegistry`` values, exactly as the rest of the recent
engines do.

A version may also carry an :class:`ArtifactRef`: where the trained bytes live,
what they should hash to, and how big they are. Through v2.14 nothing here read
or wrote those bytes; :class:`ArtifactStore` -- :class:`FileArtifactStore` and
the explicitly named :class:`MemoryArtifactStore` -- is the object store that
was missing, and it is content-addressed, so an artifact's identity *is* its
SHA-256 and verification is checked rather than trusted. The reference makes a
registry snapshot useful either way, because
:meth:`ModelVersion.__serializable__` projects a version to its metadata and
references rather than stringifying the model object. See ADR-0031.

Which stage moves are legal is declared in
:mod:`alphalab.model_registry.stages`, not left implicit. The registry is
mechanism: it refuses incoherent moves (a demotion out of ``PRODUCTION``, an
archived version resurrected into production without being the one to roll back
to) and records what happened. Requiring *evidence* before a promotion is
policy, and lives in :mod:`alphalab.lifecycle`.
"""

from alphalab.model_registry.artifact_store import (
    ARTIFACT_URI_SCHEME,
    DEFAULT_MEDIA_TYPE,
    ArtifactStore,
    FileArtifactStore,
    MemoryArtifactStore,
    artifact_uri,
    compute_digest,
    digest_of,
    verify_artifact,
)
from alphalab.model_registry.deployment import (
    deployed_versions,
    deployment_metadata,
    set_deployment_metadata,
)
from alphalab.model_registry.exceptions import ModelRegistryError, ModelRegistryInputError
from alphalab.model_registry.promotion import (
    production_version,
    promote,
    staging_version,
    versions_in_stage,
)
from alphalab.model_registry.registry import (
    ArtifactRef,
    DeploymentMetadata,
    ModelRegistry,
    ModelStage,
    ModelVersion,
    ParamValue,
    PromotionRecord,
    get_model,
    get_version,
    latest_version,
    list_versions,
    model_names,
    register_model,
)
from alphalab.model_registry.rollback import promotion_history, rollback
from alphalab.model_registry.stages import (
    LEGAL_TRANSITIONS,
    illegal_stage_move,
    previous_production_version,
    validate_transition,
)

__all__ = [
    "ARTIFACT_URI_SCHEME",
    "DEFAULT_MEDIA_TYPE",
    "LEGAL_TRANSITIONS",
    "ArtifactRef",
    "ArtifactStore",
    "DeploymentMetadata",
    "FileArtifactStore",
    "MemoryArtifactStore",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelRegistryInputError",
    "ModelStage",
    "ModelVersion",
    "ParamValue",
    "PromotionRecord",
    "artifact_uri",
    "compute_digest",
    "deployed_versions",
    "deployment_metadata",
    "digest_of",
    "get_model",
    "get_version",
    "illegal_stage_move",
    "latest_version",
    "list_versions",
    "model_names",
    "previous_production_version",
    "production_version",
    "promote",
    "promotion_history",
    "register_model",
    "rollback",
    "set_deployment_metadata",
    "staging_version",
    "validate_transition",
    "verify_artifact",
    "versions_in_stage",
]
