"""Global immutable state container for the Feature Store.

The keyed indexes and the append-only histories use the canonical containers
from :mod:`alphalab.common`, for the reason v2.1 and v2.2 introduced them.

Until v2.17 every mutator rebuilt a whole ``dict`` and a whole ``tuple`` per
transition, so ``N`` transitions copied ``O(N^2)`` entries. ADR-0032 classified
this as category C finding 1 and ADR-0034 takes the fix. No new mechanism is
introduced and no semantics change: :class:`~alphalab.common.append_log.AppendOnlyLog`,
:class:`~alphalab.common.persistent_map.PersistentMap` and
:class:`~alphalab.common.persistent_map.PersistentSet` are the containers the
execution path has used since v2.1 and v2.2. All three are immutable, all three
define value equality, and a ``PersistentMap`` iterates in first-insertion order
of the keys still present -- which is what a ``dict`` does.

``registered_feature_ids`` and the larger term
----------------------------------------------

Converting the containers *alone* made registration **four times slower**, and
measuring rather than assuming is what caught it.
:func:`~alphalab.feature_store.checks.check_dependencies_registered` built
``{meta.feature_id for meta in state.features.values()}`` on every registration:
a full scan of the registry per call, quadratic, and the dominant term by far.
A ``PersistentMap`` walks version chains, so it iterates more slowly than a
``dict`` -- which turned a term the conversion was never going to fix into a
worse one.

``registered_feature_ids`` is the index that answers the question the check
actually asks -- *is any version of this feature registered?* -- in O(1). Like
``alphalab.distributed``'s ``queued_ids`` and the OMS order book's asset and
strategy indexes, it is **derived**: it is always exactly
``{metadata.feature_id for metadata in features.values()}``, and
``tests/regression/test_standalone_state_scaling.py`` asserts that after every
registration so the two cannot drift.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap, PersistentSet
from alphalab.feature_store.cache import FeatureCache
from alphalab.feature_store.decision import FeatureWriteDecision
from alphalab.feature_store.events import FeatureStoreEvent
from alphalab.feature_store.metadata import FeatureMetadata
from alphalab.feature_store.value import FeatureValue


@dataclass(frozen=True, slots=True)
class FeatureStoreStatistics:
    """Immutable tracking metrics for the Feature Store."""

    total_registered: int = 0
    total_deprecated: int = 0
    total_values_written: int = 0
    total_values_rejected: int = 0


@dataclass(frozen=True, slots=True)
class FeatureStoreState:
    """Deterministic snapshot of registered features, values, and cache contents.

    Attributes:
        engine_id: Identifier for this Feature Store instance.
        features: Registered feature definitions, keyed by "feature_id:version".
        registered_feature_ids: Every ``feature_id`` with at least one registered
            version. A derived index; see the module docstring.
        deprecated_keys: "feature_id:version" keys marked deprecated. Deprecated
            features remain in `features` and queryable, they are simply excluded
            from `views.active_features`.
        values: Most recently written value per "feature_id:version:asset_id" key.
        cache: Read-through cache of the same values, tracked separately so cache
            hit/miss statistics reflect read access patterns independent of writes.
        history: Every write decision ever produced, in order.
        events: Every domain event ever emitted, in order.
        statistics: Aggregate counters for registrations, deprecations, and writes.
        metadata: Free-form key/value labels for this Feature Store instance.
    """

    engine_id: str
    features: PersistentMap[str, FeatureMetadata] = field(default_factory=PersistentMap)
    registered_feature_ids: PersistentSet[str] = field(default_factory=PersistentSet)
    deprecated_keys: PersistentSet[str] = field(default_factory=PersistentSet)
    values: PersistentMap[str, FeatureValue] = field(default_factory=PersistentMap)
    cache: FeatureCache = field(default_factory=FeatureCache)
    history: AppendOnlyLog[FeatureWriteDecision] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[FeatureStoreEvent] = field(default_factory=AppendOnlyLog)
    statistics: FeatureStoreStatistics = field(default_factory=FeatureStoreStatistics)
    metadata: Mapping[str, str] = field(default_factory=dict)
