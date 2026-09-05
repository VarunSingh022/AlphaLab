"""The strategy version: the thing that gets promoted, deployed and rolled back.

Before v2.4 a strategy had a name, an instance and a runtime status, and
:class:`~alphalab.studio.strategy.StrategyDefinition` recorded its parameters
with a free-form ``version`` string. Nothing was a *version* in the sense the
lifecycle needs: an immutable, numbered record you can point a deployment at and
compare against the one before it.

Four identities are kept apart here, because collapsing any two of them makes
"what is running?" unanswerable:

``name``
    The strategy line. Every version of one strategy shares it.
``version``
    This particular immutable record of that line, numbered in registration
    order. Two versions differ in their definition, their model, or both.
``model``
    The :class:`~alphalab.lifecycle.identity.ModelRef` this version runs, if it
    runs one. A model version can back several strategy versions and has its own
    stage in :mod:`alphalab.model_registry`.
``deployment``
    Where a version runs. Owned by :mod:`alphalab.deployment_manager`, not
    recorded here -- one version deployed to two environments is two
    deployments, and a field on the version could not say that.

The definition itself is a ``StrategyDefinition``, the canonical Studio record,
carried rather than re-modelled: :mod:`alphalab.research_assistant` already
lifts a searched candidate into one, so a candidate reaches a strategy version
without a second parameter format in between.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.identity import ModelRef, StrategyVersionRef
from alphalab.model_registry.registry import ModelStage
from alphalab.studio.strategy import StrategyDefinition

__all__ = [
    "StrategyPromotionRecord",
    "StrategyVersion",
    "StrategyVersionRegistry",
    "get_strategy_version",
    "latest_strategy_version",
    "list_strategy_versions",
    "register_strategy_version",
    "replace_strategy_version",
    "strategy_names",
]


@dataclass(frozen=True, slots=True)
class StrategyVersion:
    """One immutable, numbered version of a strategy line.

    Attributes:
        name: The strategy line this is a version of.
        version: 1-based version number, assigned in registration order.
        definition: The canonical ``StrategyDefinition`` -- parameters, author,
            description -- this version is. Carried, not copied into a parallel
            shape.
        stage: Where this version is in the lifecycle. The same
            :class:`~alphalab.model_registry.registry.ModelStage` a model
            version uses, because it is the same question about a different
            artifact; see that enum's docstring for why there is one vocabulary
            and not two.
        model: The model version this strategy runs, if any.
        run_id: The ``alphalab.experiment_tracking`` run that produced it, if
            any. A bare run id, spelled exactly as ``ModelVersion.run_id`` is.
        evidence_id: The evidence that supported its promotion to ``STAGING``.
            ``None`` until it is promoted; the evidence itself lives in
            :attr:`~alphalab.lifecycle.state.LifecycleState.evidence`.
        policy_id: The policy that evidence was judged against, so a promoted
            version records not just that it passed but what it passed.
        created_at: Unix timestamp the version was registered.
        tags: Free-form labels for filtering and grouping.
    """

    name: str
    version: int
    definition: StrategyDefinition
    stage: ModelStage
    model: ModelRef | None = None
    run_id: str | None = None
    evidence_id: str | None = None
    policy_id: str | None = None
    created_at: float = 0.0
    tags: Mapping[str, str] = field(default_factory=dict)

    @property
    def ref(self) -> StrategyVersionRef:
        """This version's reference."""

        return StrategyVersionRef(self.name, self.version)


@dataclass(frozen=True, slots=True)
class StrategyPromotionRecord:
    """An append-only audit entry for one strategy version's stage change.

    Attributes:
        name: The strategy line.
        version: The version whose stage changed.
        from_stage: Stage before the transition.
        to_stage: Stage after the transition.
        reason: Why it moved -- the evidence that justified a promotion, the
            environment a deployment targeted, the rollback that restored it.
        timestamp: Unix timestamp the transition happened.
    """

    name: str
    version: int
    from_stage: ModelStage
    to_stage: ModelStage
    reason: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class StrategyVersionRegistry:
    """Immutable registry of every strategy version and every stage change.

    Attributes:
        versions: Strategy name -> that line's versions, keyed by version number
            and iterating in ascending version order.
        promotions: Every stage change ever applied, in the order it happened.

    There is deliberately no production index here, unlike
    :class:`~alphalab.model_registry.registry.ModelRegistry`. What is live is
    the deployment ledger's answer, and a second copy of it on this side would
    be a second thing to keep true.
    """

    versions: PersistentMap[str, PersistentMap[int, StrategyVersion]] = field(
        default_factory=PersistentMap
    )
    promotions: AppendOnlyLog[StrategyPromotionRecord] = field(default_factory=AppendOnlyLog)

    def __post_init__(self) -> None:
        # The container's own type only; see ModelRegistry.__post_init__ for why
        # inspecting the entries on every write would be quadratic.
        if not isinstance(self.versions, PersistentMap):
            object.__setattr__(self, "versions", _as_lines(self.versions))
        if not isinstance(self.promotions, AppendOnlyLog):
            object.__setattr__(self, "promotions", AppendOnlyLog(self.promotions))


def _as_lines(
    versions: Mapping[str, Any],
) -> PersistentMap[str, PersistentMap[int, StrategyVersion]]:
    """Accept a hand-built ``name -> versions`` mapping in any ordered shape."""

    def line(value: Any) -> PersistentMap[int, StrategyVersion]:
        if isinstance(value, PersistentMap):
            return value
        entries = value.values() if isinstance(value, Mapping) else value
        return PersistentMap((version.version, version) for version in entries)

    return PersistentMap((name, line(value)) for name, value in versions.items())


def register_strategy_version(
    registry: StrategyVersionRegistry,
    name: str,
    definition: StrategyDefinition,
    timestamp: float,
    model: ModelRef | None = None,
    run_id: str | None = None,
    tags: Mapping[str, str] | None = None,
) -> tuple[StrategyVersionRegistry, StrategyVersion]:
    """Registers ``definition`` as the next version of strategy line ``name``.

    The first registration of a name is version 1; each subsequent one
    increments. A new version always starts at ``NONE``: registering something
    is not a claim that it works.

    Raises:
        LifecycleInputError: If ``name`` is empty or contains ``"@"``, the
            character a reference renders with.
    """
    if not name.strip():
        raise LifecycleInputError("Strategy name cannot be empty.")
    if "@" in name:
        raise LifecycleInputError(
            f"Strategy name {name!r} cannot contain '@'; it is the separator a "
            "version reference renders with."
        )

    existing: PersistentMap[int, StrategyVersion] = registry.versions.get(name, PersistentMap())
    version = StrategyVersion(
        name=name,
        version=len(existing) + 1,
        definition=definition,
        stage=ModelStage.NONE,
        model=model,
        run_id=run_id,
        created_at=timestamp,
        tags=dict(tags) if tags else {},
    )
    updated = replace(
        registry, versions=registry.versions.set(name, existing.set(version.version, version))
    )
    return updated, version


def strategy_names(registry: StrategyVersionRegistry) -> tuple[str, ...]:
    """Returns every registered strategy name, in registration order."""
    return tuple(registry.versions)


def list_strategy_versions(
    registry: StrategyVersionRegistry, name: str
) -> tuple[StrategyVersion, ...]:
    """Returns every version of ``name`` in ascending version order.

    Raises:
        LifecycleInputError: If ``name`` has no registered versions.
    """
    versions = registry.versions.get(name)
    if not versions:
        raise LifecycleInputError(f"No strategy registered under name '{name}'.")
    return tuple(versions.values())


def get_strategy_version(
    registry: StrategyVersionRegistry, name: str, version: int
) -> StrategyVersion:
    """Returns a specific version of ``name``.

    Raises:
        LifecycleInputError: If ``name`` or that ``version`` is unknown.
    """
    versions = registry.versions.get(name)
    if not versions:
        raise LifecycleInputError(f"No strategy registered under name '{name}'.")
    found = versions.get(version)
    if found is None:
        raise LifecycleInputError(f"Strategy '{name}' has no version {version}.")
    return found


def latest_strategy_version(registry: StrategyVersionRegistry, name: str) -> StrategyVersion:
    """Returns the highest-numbered version of ``name``.

    Raises:
        LifecycleInputError: If ``name`` has no registered versions.
    """
    return list_strategy_versions(registry, name)[-1]


def replace_strategy_version(
    registry: StrategyVersionRegistry, updated: StrategyVersion
) -> StrategyVersionRegistry:
    """Returns a registry with ``updated`` swapped in for its current entry.

    ``updated`` must already exist (same name and version); callers guarantee
    this by deriving it from :func:`get_strategy_version`.
    """
    line = registry.versions[updated.name]
    return replace(
        registry, versions=registry.versions.set(updated.name, line.set(updated.version, updated))
    )
