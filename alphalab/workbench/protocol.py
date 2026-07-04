"""Immutable interface protocol for UI extensions."""

from typing import Protocol


class WorkbenchPluginProtocol(Protocol):
    """Pure functional interface for extending the GUI."""
    def get_plugin_name(self) -> str: ...
    def get_injected_panels(self) -> tuple[str, ...]: ...