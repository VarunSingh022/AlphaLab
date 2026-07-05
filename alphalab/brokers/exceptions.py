"""Domain exceptions for the Broker Connector Framework."""

from alphalab.common.exceptions import AlphaLabError


class BrokerConnectorError(AlphaLabError):
    """Base exception for all Broker Connector errors."""


class BrokerValidationError(BrokerConnectorError):
    """Raised when broker configurations, orders, or executions fail validation."""


class InvalidBrokerStateError(BrokerConnectorError):
    """Raised when an invalid lifecycle transition is attempted."""
