"""Immutable interface protocol for supervised runtime subsystems."""

from collections.abc import Mapping
from typing import Protocol


class SubsystemProtocol(Protocol):
    """Pure functional interface for subsystems supervised by the Runtime."""
    
    def get_id(self) -> str:
        ...

    def get_status(self) -> str:
        ...

    def get_metrics(self) -> Mapping[str, float]:
        ...