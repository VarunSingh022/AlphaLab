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
what they should hash to, and how big they are. AlphaLab never reads, writes or
hashes those bytes -- there is no object store here and this release does not
pretend otherwise. The reference is what makes a registry snapshot useful
anyway, because :meth:`ModelVersion.__serializable__` projects a version to its
metadata and references rather than stringifying the model object.

Which stage moves are legal is declared in
:mod:`alphalab.model_registry.stages`, not left implicit. The registry is
mechanism: it refuses incoherent moves (a demotion out of ``PRODUCTION``, an
archived version resurrected into production without being the one to roll back
to) and records what happened. Requiring *evidence* before a promotion is
policy, and lives in :mod:`alphalab.lifecycle`.
"""

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
    previous_production_version,
    validate_transition,
)

__all__ = [
    "LEGAL_TRANSITIONS",
    "ArtifactRef",
    "DeploymentMetadata",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelRegistryInputError",
    "ModelStage",
    "ModelVersion",
    "ParamValue",
    "PromotionRecord",
    "deployed_versions",
    "deployment_metadata",
    "get_model",
    "get_version",
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
    "versions_in_stage",
]
