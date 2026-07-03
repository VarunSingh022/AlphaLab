"""Immutable definitions for plugin types and metadata."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto


class PluginType(Enum):
    """Explicit classifications for platform extensions."""

    STRATEGY = auto()
    INDICATOR = auto()
    RISK = auto()
    BROKER = auto()
    FEED = auto()
    ANALYTICS = auto()
    REPORTING = auto()
    OPTIMIZER = auto()
    SCHEDULER = auto()
    RUNTIME = auto()


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """Immutable record containing plugin identity and capabilities."""

    plugin_id: str
    name: str
    version: str
    author: str
    description: str
    plugin_type: PluginType
    api_version: str
    enabled: bool = False
    metadata: Mapping[str, str] = field(default_factory=dict)
