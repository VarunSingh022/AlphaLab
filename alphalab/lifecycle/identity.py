"""The references the lifecycle passes between packages.

A model version and a strategy version are both identified by a name and a
1-based version number. Structurally they are the same pair, which is exactly
why they need distinct types: a function that takes ``(str, int)`` cannot say
which of the two it wants, and the release manifest that carries both would
otherwise hold two indistinguishable strings.

Rendering is ``"name@version"``, the form
:mod:`alphalab.deployment_manager` already documented for the component
references in a :class:`~alphalab.deployment_manager.packaging.ReleasePackage`
(``"momentum@3"``). It round-trips, and a name containing ``"@"`` is refused at
construction rather than producing a reference that parses back to something
else.

An experiment run is referenced by its ``run_id`` string, with no wrapper type:
:attr:`~alphalab.model_registry.registry.ModelVersion.run_id` is already a bare
run id, and a second spelling of the same reference would be the duplication
this package exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass

from alphalab.lifecycle.exceptions import LifecycleInputError

__all__ = [
    "COMPONENT_EVIDENCE",
    "COMPONENT_MODEL",
    "COMPONENT_RUN",
    "COMPONENT_STRATEGY",
    "DeploymentRef",
    "ModelRef",
    "StrategyVersionRef",
    "parse_ref",
]

#: Manifest keys a lifecycle-built release package uses for its components.
COMPONENT_STRATEGY = "strategy"
COMPONENT_MODEL = "model"
COMPONENT_RUN = "experiment_run"
COMPONENT_EVIDENCE = "evidence"

_SEPARATOR = "@"


def _validate(name: str, version: int, kind: str) -> None:
    if not name.strip():
        raise LifecycleInputError(f"{kind} name cannot be empty.")
    if _SEPARATOR in name:
        raise LifecycleInputError(
            f"{kind} name {name!r} cannot contain {_SEPARATOR!r}; it is the separator "
            "a reference renders with, and a name containing it would parse back to a "
            "different reference."
        )
    if version < 1:
        raise LifecycleInputError(f"{kind} version must be 1 or greater, got {version}.")


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A reference to one version of one registered model."""

    name: str
    version: int

    def __post_init__(self) -> None:
        _validate(self.name, self.version, "Model")

    def __str__(self) -> str:
        return f"{self.name}{_SEPARATOR}{self.version}"


@dataclass(frozen=True, slots=True)
class StrategyVersionRef:
    """A reference to one version of one strategy line.

    Distinct from the strategy *line* (``name`` alone), from the
    :class:`ModelRef` a version may carry, and from the :class:`DeploymentRef`
    naming a place it runs. Conflating any two of those is what makes "which
    strategy is live?" unanswerable.
    """

    name: str
    version: int

    def __post_init__(self) -> None:
        _validate(self.name, self.version, "Strategy")

    def __str__(self) -> str:
        return f"{self.name}{_SEPARATOR}{self.version}"


@dataclass(frozen=True, slots=True)
class DeploymentRef:
    """Where a release is running, and which release it is.

    A deployment's identity is the environment plus the release version active
    in it. It is not the strategy version: one strategy version deployed to two
    environments is two deployments, and that distinction is what rollback acts
    on.
    """

    environment: str
    release_name: str
    release_version: int

    def __post_init__(self) -> None:
        if not self.environment.strip():
            raise LifecycleInputError("Deployment environment cannot be empty.")
        _validate(self.release_name, self.release_version, "Release")

    def __str__(self) -> str:
        return f"{self.environment}:{self.release_name}{_SEPARATOR}{self.release_version}"


def parse_ref(reference: str) -> tuple[str, int]:
    """Split a rendered ``"name@version"`` reference back into its parts.

    Raises:
        LifecycleInputError: If the reference has no separator, or its version
            is not a positive integer.
    """
    name, separator, version = reference.rpartition(_SEPARATOR)
    if not separator:
        raise LifecycleInputError(
            f"{reference!r} is not a reference; expected 'name{_SEPARATOR}version'."
        )
    try:
        parsed = int(version)
    except ValueError:
        raise LifecycleInputError(f"{reference!r} has a non-numeric version {version!r}.") from None
    if parsed < 1:
        raise LifecycleInputError(f"{reference!r} has a version below 1.")
    return name, parsed
