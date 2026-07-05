"""Domain exceptions for the Persistence Layer."""

from alphalab.common.exceptions import AlphaLabError


class PersistenceError(AlphaLabError):
    """Base exception for all Persistence Engine errors."""


class PersistenceValidationError(PersistenceError):
    """Raised when persistence data or operations fail structural validation."""


class SerializationError(PersistenceError):
    """Raised when an object cannot be deterministically serialized or deserialized."""


class StorageError(PersistenceError):
    """Raised when a storage constraint (like append-only ordering) is violated."""
