"""Top-level Engine Facade for the Plugin SDK."""

from alphalab.plugins.state import PluginState


class PluginEngine:
    """Facade for setting up the immutable Plugin environment."""

    @staticmethod
    def initialize(engine_id: str) -> PluginState:
        """Constructs an empty base state for the plugin layer."""
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return PluginState(engine_id=engine_id)
