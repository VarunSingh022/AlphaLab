"""AlphaLab Deployment Manager.

Packaging, release management, production deployment, and rollbacks for
deployable strategy/model stacks.

Where ``alphalab.model_registry`` versions individual trained models and
``alphalab.production`` operates a *running* cluster (heartbeats, checkpoints,
recovery), the Deployment Manager sits between them: it bundles a set of
component references and config into a versioned, checksummed
``ReleasePackage``, and maintains an append-only ledger of which release is
active in which environment.

- packaging: ``build_release`` assembles a manifest and computes a
  deterministic SHA-256 ``checksum`` over it; ``verify_checksum`` detects
  manifest drift. No artifact bytes are archived -- the package records what a
  release consists of.
- releases: ``register_release`` auto-increments a version per release name;
  ``get_release`` / ``latest_release`` / ``list_releases`` read them back.
- deployment: ``deploy`` makes a release the single active one in an
  environment (refusing an unverified checksum or a no-op re-deploy) and
  appends a ``DeploymentRecord``; ``active_release`` reads the current pointer.
- rollback: ``rollback`` re-deploys whatever was active in an environment
  immediately before the current release, flagged as a rollback in the ledger.

State is threaded functionally through immutable ``DeploymentManager`` values.
"""

from alphalab.deployment_manager.deployment import (
    active_release,
    deploy,
    deployed_environments,
)
from alphalab.deployment_manager.exceptions import (
    DeploymentManagerError,
    DeploymentManagerInputError,
)
from alphalab.deployment_manager.packaging import (
    ReleasePackage,
    build_release,
    compute_checksum,
    verify_checksum,
)
from alphalab.deployment_manager.releases import (
    DeploymentManager,
    DeploymentRecord,
    get_release,
    latest_release,
    list_releases,
    register_release,
    release_names,
)
from alphalab.deployment_manager.rollback import (
    deployment_history,
    previous_release,
    rollback,
)

__all__ = [
    "DeploymentManager",
    "DeploymentManagerError",
    "DeploymentManagerInputError",
    "DeploymentRecord",
    "ReleasePackage",
    "active_release",
    "build_release",
    "compute_checksum",
    "deploy",
    "deployed_environments",
    "deployment_history",
    "get_release",
    "latest_release",
    "list_releases",
    "previous_release",
    "register_release",
    "release_names",
    "rollback",
    "verify_checksum",
]
