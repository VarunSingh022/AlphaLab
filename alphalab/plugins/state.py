"""Global immutable state container for the Plugin SDK.

The indexes and the event log use the canonical containers from
:mod:`alphalab.common`, for the reason v2.1 and v2.2 introduced them. All are
immutable, define value equality, and iterate deterministically.

What was quadratic, and which half of it the containers fixed
-------------------------------------------------------------

ADR-0032 category C finding 1 named the container half -- ``state.events`` grown
with tuple splats and ``plugins`` with ``dict()`` copies -- and ADR-0034 converts
both. Profiling first found a larger term the finding did not name: at 4,000
registrations, **54%** of the cost was
:func:`~alphalab.plugins.validation.validate_registration` building
``{p.metadata().name for p in state.plugins.values()}`` to check name
uniqueness. That is a full scan *and* a ``metadata()`` call per registered
plugin, on every registration.

``registered_names`` is the index that answers it in O(1). Like
``alphalab.distributed``'s ``queued_ids`` and
``alphalab.feature_store``'s ``registered_feature_ids``, it is **derived** --
always exactly ``{p.metadata().name for p in plugins.values()}`` -- and
``tests/regression/test_standalone_state_scaling.py`` asserts that after every
registration and removal.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap, PersistentSet
from alphalab.plugins.events import PluginSystemEvent
from alphalab.plugins.protocol import PluginProtocol


@dataclass(frozen=True, slots=True)
class PluginStatistics:
    """Immutable tracking metrics for the Plugin Engine."""

    total_registered: int = 0
    total_unregistered: int = 0
    enabled_count: int = 0
    disabled_count: int = 0


@dataclass(frozen=True, slots=True)
class PluginState:
    """Deterministic snapshot of the Plugin SDK environment.

    Attributes:
        engine_id: Identifier for this plugin host.
        plugins: Registered plugins, keyed by ``plugin_id``.
        enabled_ids: Which of them are enabled.
        registered_names: Every registered plugin's declared name. A derived
            index; see the module docstring.
        statistics: Registration and enablement counters.
        events: Everything that has happened, in order.
        metadata: Host-specific attributes with no canonical field.
    """

    engine_id: str
    plugins: PersistentMap[str, PluginProtocol] = field(default_factory=PersistentMap)
    enabled_ids: PersistentSet[str] = field(default_factory=PersistentSet)
    registered_names: PersistentSet[str] = field(default_factory=PersistentSet)
    statistics: PluginStatistics = field(default_factory=PluginStatistics)
    events: AppendOnlyLog[PluginSystemEvent] = field(default_factory=AppendOnlyLog)
    metadata: Mapping[str, str] = field(default_factory=dict)
