"""Deterministic loading mechanisms avoiding magic filesystem scans."""

from typing import Any, cast

from alphalab.plugins.exceptions import PluginValidationError
from alphalab.plugins.protocol import PluginProtocol


class PluginLoader:
    """Pure stateless loader explicitly instantiating registered classes."""

    @staticmethod
    def load_from_class(plugin_class: type[Any]) -> PluginProtocol:
        """Instantiates a class and validates it matches the PluginProtocol."""
        try:
            instance = plugin_class()
        except Exception as e:
            raise PluginValidationError(f"Failed to instantiate plugin class: {e}") from e

        if not hasattr(instance, "metadata") or not hasattr(instance, "validate"):
            raise PluginValidationError("Class does not implement PluginProtocol.")

        if not instance.validate():
            raise PluginValidationError("Plugin failed internal self-validation.")

        return cast(PluginProtocol, instance)
