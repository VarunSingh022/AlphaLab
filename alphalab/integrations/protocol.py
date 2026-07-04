"""Immutable interface protocol for Broker Provider implementations."""

from typing import Any, Protocol


class IntegrationProviderProtocol(Protocol):
    """Pure functional interface defining the boundary for real-world broker SDKs."""

    def authenticate(self, credentials: dict[str, str]) -> bool: ...

    def connect(self) -> bool: ...

    def disconnect(self) -> bool: ...

    def submit_order(self, order_payload: dict[str, Any]) -> dict[str, Any]:
        """Submits an order and returns a normalized dictionary response."""
        ...

    def cancel_order(self, order_id: str) -> bool: ...

    def sync_portfolio(self) -> dict[str, Any]:
        """Returns normalized remote positions and balances."""
        ...
