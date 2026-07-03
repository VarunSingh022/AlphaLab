"""Adapters mapping pure PluginProtocols to subsystem-specific interfaces."""

from alphalab.plugins.exceptions import PluginValidationError
from alphalab.plugins.metadata import PluginType
from alphalab.plugins.protocol import PluginProtocol


class PluginAdapter:
    """Stateless translator ensuring plugins match target subsystem criteria."""

    @staticmethod
    def assert_strategy_plugin(plugin: PluginProtocol) -> PluginProtocol:
        """Verifies a plugin is intended for the Strategy Engine."""
        if plugin.metadata().plugin_type != PluginType.STRATEGY:
            raise PluginValidationError(f"Plugin {plugin.metadata().name} is not a Strategy.")
        return plugin

    @staticmethod
    def assert_risk_plugin(plugin: PluginProtocol) -> PluginProtocol:
        """Verifies a plugin is intended for the Risk Engine."""
        if plugin.metadata().plugin_type != PluginType.RISK:
            raise PluginValidationError(f"Plugin {plugin.metadata().name} is not a Risk plugin.")
        return plugin

    @staticmethod
    def assert_feed_plugin(plugin: PluginProtocol) -> PluginProtocol:
        """Verifies a plugin is intended for the Feed Engine."""
        if plugin.metadata().plugin_type != PluginType.FEED:
            raise PluginValidationError(f"Plugin {plugin.metadata().name} is not a Feed plugin.")
        return plugin
