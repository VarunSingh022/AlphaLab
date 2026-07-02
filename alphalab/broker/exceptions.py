"""Domain exceptions for the Broker Abstraction Layer."""


class BrokerError(Exception):
    """Base exception for all Broker Abstraction errors."""
    pass


class BrokerValidationError(BrokerError):
    """Raised when an order or execution fails structural or logical validation."""
    pass


class InvalidBrokerStateError(BrokerError):
    """Raised when an invalid state transition is attempted on a broker order."""
    pass