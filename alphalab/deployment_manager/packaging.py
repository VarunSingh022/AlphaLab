"""Assembling a versioned, checksummed release package.

A release package is a manifest -- component references (strategy ids, model
registry ``name@version`` strings, dataset ids) and a flat config mapping --
plus a deterministic SHA-256 checksum over that manifest. Nothing is copied or
archived to disk; the package records *what* a release consists of so a
deployment can be verified and compared, not the artifact bytes themselves.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from alphalab.deployment_manager.exceptions import DeploymentManagerInputError


def compute_checksum(components: Mapping[str, str], config: Mapping[str, str]) -> str:
    """Returns a deterministic SHA-256 hex digest over a manifest.

    Keys are sorted before hashing, so the digest depends only on the
    key/value pairs, never on mapping order.
    """
    canonical = "\n".join(
        [
            "components",
            *(f"{key}={components[key]}" for key in sorted(components)),
            "config",
            *(f"{key}={config[key]}" for key in sorted(config)),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReleasePackage:
    """One immutable, versioned release manifest.

    Attributes:
        name: The release line this belongs to, e.g. ``"eu-equity-stack"``.
        version: 1-based version number, assigned in registration order.
        components: Component name -> reference string. References are opaque
            here; ``"momentum@3"`` for a model registry version, a strategy id,
            a dataset id.
        config: Flat deployment configuration for this release.
        checksum: SHA-256 hex digest over ``components`` + ``config``.
        created_at: Unix timestamp the package was built.
    """

    name: str
    version: int
    components: Mapping[str, str]
    config: Mapping[str, str]
    checksum: str
    created_at: float = 0.0


def build_release(
    name: str,
    version: int,
    components: Mapping[str, str],
    config: Mapping[str, str],
    timestamp: float,
) -> ReleasePackage:
    """Builds a :class:`ReleasePackage` with its checksum computed.

    Raises:
        DeploymentManagerInputError: If ``name`` is blank, ``version`` is not
            positive, or ``components`` is empty.
    """
    if not name.strip():
        raise DeploymentManagerInputError("name cannot be empty.")
    if version <= 0:
        raise DeploymentManagerInputError(f"version must be positive, got {version}.")
    if not components:
        raise DeploymentManagerInputError("components cannot be empty.")

    frozen_components = dict(components)
    frozen_config = dict(config)
    return ReleasePackage(
        name=name,
        version=version,
        components=frozen_components,
        config=frozen_config,
        checksum=compute_checksum(frozen_components, frozen_config),
        created_at=timestamp,
    )


def verify_checksum(package: ReleasePackage) -> bool:
    """Returns whether a package's stored checksum matches its current manifest."""
    return package.checksum == compute_checksum(package.components, package.config)
