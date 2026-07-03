"""Domain exceptions for the Broker Connector Framework."""


class BrokerConnectorError(Exception):
    """Base exception for all Broker Connector errors."""


class BrokerValidationError(BrokerConnectorError):
    """Raised when broker configurations, orders, or executions fail validation."""


class InvalidBrokerStateError(BrokerConnectorError):
    """Raised when an invalid lifecycle transition is attempted."""
