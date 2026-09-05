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


class StateDecodeError(PersistenceError):
    """Raised when a snapshot payload cannot be read back into domain types.

    Separate from :class:`SerializationError`, which is raised on the way *out*
    when a value has no deterministic JSON form. This one is raised on the way
    *in*: the payload is valid JSON and does not describe the state it claims to.
    It names the field that failed, because "a snapshot did not load" is not
    actionable and "positions[AAPL].quantity is not a decimal" is.
    """
