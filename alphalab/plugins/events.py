"""Immutable domain events describing the Plugin lifecycle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PluginSystemEvent:
    """Base class for all Plugin SDK system events."""

    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class PluginRegistered(PluginSystemEvent):
    """Emitted when a plugin is successfully added to the registry."""

    plugin_id: str
    plugin_type: str


@dataclass(frozen=True, slots=True)
class PluginLoaded(PluginSystemEvent):
    """Emitted when a plugin's code is successfully instantiated and verified."""

    plugin_id: str


@dataclass(frozen=True, slots=True)
class PluginEnabled(PluginSystemEvent):
    """Emitted when a plugin is activated for use."""

    plugin_id: str


@dataclass(frozen=True, slots=True)
class PluginDisabled(PluginSystemEvent):
    """Emitted when a plugin is deactivated."""

    plugin_id: str


@dataclass(frozen=True, slots=True)
class PluginRemoved(PluginSystemEvent):
    """Emitted when a plugin is completely removed from the registry."""

    plugin_id: str
