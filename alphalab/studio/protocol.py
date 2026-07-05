"""Immutable interface protocol for Studio abstractions."""

from typing import Protocol


class StudioComponentProtocol(Protocol):
    """Pure functional interface for objects trackable by Strategy Studio."""

    def get_component_id(self) -> str: ...
    def get_component_type(self) -> str: ...
