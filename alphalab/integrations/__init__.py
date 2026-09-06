"""AlphaLab Broker Integration Framework."""

from alphalab.integrations.adapter import IntegrationAdapter
from alphalab.integrations.auth import AuthCredentials, AuthState, AuthStatus
from alphalab.integrations.broker import BrokerHealth
from alphalab.integrations.config import BrokerConfig
from alphalab.integrations.connection import ConnectionState, ConnectionStatus
from alphalab.integrations.engine import IntegrationEngine
from alphalab.integrations.events import (
    AuthenticationFailed,
    AuthenticationSucceeded,
    BrokerConnected,
    BrokerDisconnected,
    ConnectionRecovered,
    IntegrationEvent,
    OrderAccepted,
    OrderCancelled,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PortfolioSynchronized,
)
from alphalab.integrations.exceptions import (
    AuthenticationError,
    ConnectionManagerError,
    IntegrationError,
    IntegrationValidationError,
)
from alphalab.integrations.manager import IntegrationManager
from alphalab.integrations.protocol import IntegrationProviderProtocol
from alphalab.integrations.registry import BrokerRegistry
from alphalab.integrations.state import IntegrationMetrics, IntegrationState
from alphalab.integrations.validation import validate_connection_attempt, validate_registration
from alphalab.integrations.views import (
    authentication_status,
    broker_health,
    broker_summary,
    connection_status,
    metrics_report,
)

__all__ = [
    "AuthCredentials",
    "AuthState",
    "AuthStatus",
    "AuthenticationError",
    "AuthenticationFailed",
    "AuthenticationSucceeded",
    "BrokerConfig",
    "BrokerConnected",
    "BrokerDisconnected",
    "BrokerHealth",
    "BrokerRegistry",
    "ConnectionManagerError",
    "ConnectionRecovered",
    "ConnectionState",
    "ConnectionStatus",
    "IntegrationAdapter",
    "IntegrationEngine",
    "IntegrationError",
    "IntegrationEvent",
    "IntegrationManager",
    "IntegrationMetrics",
    "IntegrationProviderProtocol",
    "IntegrationState",
    "IntegrationValidationError",
    "OrderAccepted",
    "OrderCancelled",
    "OrderFilled",
    "OrderRejected",
    "OrderSubmitted",
    "PortfolioSynchronized",
    "authentication_status",
    "broker_health",
    "broker_summary",
    "connection_status",
    "metrics_report",
    "validate_connection_attempt",
    "validate_registration",
]


# Deprecated in v2.6, removed in v3.0.
#
# The adapters here return canned responses and none is wired to an endpoint;
# `alphalab.broker` is the canonical adapter boundary (ADR-0012) and
# `alphalab.brokers` the router over it. The warning is at import because
# nothing on the execution path imports this package, so it reaches exactly the
# callers who do -- and nobody else. It sits below the imports so that the
# module's own exports are unaffected by it. See ADR-0015 decision 9.
import warnings

warnings.warn(
    "alphalab.integrations is deprecated and will be removed in v3.0. "
    "Use alphalab.broker for the adapter contract and alphalab.brokers for "
    "routing.",
    DeprecationWarning,
    stacklevel=2,
)
