"""Domain exceptions for the Universal Data Engine."""

class UniversalDataError(Exception):
    """Base exception for all Data Engine errors."""

class DataValidationError(UniversalDataError):
    """Raised when datasets fail structural validation."""

class DataQualityError(UniversalDataError):
    """Raised when data quality drops below acceptable thresholds."""

class InvalidDataStateError(UniversalDataError):
    """Raised when an illegal lifecycle transition is attempted."""