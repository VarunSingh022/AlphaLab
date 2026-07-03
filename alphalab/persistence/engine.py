"""High-level facade orchestrating storage execution."""

from alphalab.persistence.state import PersistenceState


class PersistenceEngine:
    """Facade orchestrating safe interaction with configured storage protocols."""

    @staticmethod
    def initialize(engine_id: str) -> PersistenceState:
        """Constructs an empty base state for the persistence layer."""
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")

        return PersistenceState(engine_id=engine_id)
