"""Domain exceptions for the Production Runtime Framework."""

class ProductionError(Exception):
    """Base exception for all Production Runtime errors."""

class ProductionValidationError(ProductionError):
    """Raised when runtime payloads, modules, or configurations fail validation."""

class InvalidRuntimeStateError(ProductionError):
    """Raised when an illegal lifecycle transition is attempted."""

class CheckpointError(ProductionError):
    """Raised when checkpoint creation or restoration fails."""

class RecoveryError(ProductionError):
    """Raised when the runtime cannot safely recover from a critical failure."""