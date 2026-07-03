"""Immutable interface protocol for valid Plugins."""

from typing import Protocol

from alphalab.plugins.metadata import PluginMetadata


class PluginProtocol(Protocol):
    """Pure interface contract that every AlphaLab plugin must fulfill."""

    def initialize(self) -> None:
        """Called exactly once when the plugin is prepared for action."""
        ...

    def shutdown(self) -> None:
        """Called exactly once to gracefully release resources."""
        ...

    def validate(self) -> bool:
        """Returns True if the plugin's internal configuration is valid."""
        ...

    def metadata(self) -> PluginMetadata:
        """Returns the immutable metadata signature of the plugin."""
        ...
