"""Immutable interface protocols for distributed execution representations."""

from typing import Any, Protocol


class JobRunnerProtocol(Protocol):
    """Pure functional interface defining how a job payload is abstractly structured."""

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Executes the specific job payload and returns results."""
        ...
