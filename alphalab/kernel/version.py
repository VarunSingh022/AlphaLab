"""Version management module for state tracking and time-travel rollback capabilities."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Version:
    """Immutable version representation."""

    number: int
    timestamp: float


class VersionManager:
    """Manages state versions, allowing linear progression and rollback."""

    def __init__(self, initial_version: int = 0, initial_timestamp: float = 0.0) -> None:
        self._history: list[Version] = [
            Version(number=initial_version, timestamp=initial_timestamp)
        ]

    def increment(self, timestamp: float) -> Version:
        """Increments the current version monotonically."""
        current_ver = self.current()
        new_version = Version(number=current_ver.number + 1, timestamp=timestamp)
        self._history.append(new_version)
        return new_version

    def current(self) -> Version:
        """Returns the active version."""
        return self._history[-1]

    def rollback(self, steps: int = 1) -> Version:
        """Rolls back the version history by a given number of steps."""
        if steps < 0:
            raise ValueError("Rollback steps cannot be negative.")
        if steps >= len(self._history):
            raise ValueError("Cannot rollback beyond initial version history.")
        for _ in range(steps):
            self._history.pop()
        return self.current()

    def get_history(self) -> tuple[Version, ...]:
        """Returns the full version history as an immutable tuple."""
        return tuple(self._history)
