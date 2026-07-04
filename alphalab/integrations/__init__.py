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