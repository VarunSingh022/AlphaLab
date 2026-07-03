"""Base implementations and standard wrappers for Plugins."""

from typing import Any

from alphalab.plugins.metadata import PluginMetadata


class BasePlugin:
    """Abstract base definition assisting in standard plugin creation."""

    def initialize(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def validate(self) -> bool:
        return True

    def metadata(self) -> PluginMetadata:
        raise NotImplementedError("Plugins must provide metadata.")

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Placeholder for domain-specific implementation logic."""
        raise NotImplementedError("Plugins must implement core execution logic.")
