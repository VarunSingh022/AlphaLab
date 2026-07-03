"""Global immutable state container for the Plugin SDK."""

from collections.abc import Mapping
from dataclasses import dataclass, field

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
    """Deterministic snapshot of the Plugin SDK environment."""

    engine_id: str
    plugins: Mapping[str, PluginProtocol] = field(default_factory=dict)
    enabled_ids: frozenset[str] = field(default_factory=frozenset)
    statistics: PluginStatistics = field(default_factory=PluginStatistics)
    events: tuple[PluginSystemEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)
