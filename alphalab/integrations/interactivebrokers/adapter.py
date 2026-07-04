"""Interactive Brokers Provider Adapter."""

from typing import Any

from alphalab.integrations.interactivebrokers.client import IBKRClient
from alphalab.integrations.interactivebrokers.config import InteractiveBrokersConfig


class InteractiveBrokersAdapter:
    """Thin adapter mapping AlphaLab requests to the IBKR client."""

    __slots__ = ("_client", "_config")

    def __init__(self, config: InteractiveBrokersConfig) -> None:
        self._config = config
        self._client = IBKRClient()

    def connect(self) -> bool:
        return self._client.connect()

    def disconnect(self) -> bool:
        return self._client.disconnect()

    def authenticate(self, credentials: dict[str, str]) -> bool:
        return self._client.authenticate(credentials)

    def submit_order(self, order_payload: dict[str, Any]) -> dict[str, Any]:
        return self._client.submit_order(order_payload)

    def cancel_order(self, order_id: str) -> bool:
        return self._client.cancel_order(order_id)

    def modify_order(self, order_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        return self._client.modify_order(order_id, updates)

    def positions(self) -> dict[str, Any]:
        return self._client.positions()

    def orders(self) -> dict[str, Any]:
        return self._client.orders()

    def account(self) -> dict[str, Any]:
        return self._client.account()

    def health(self) -> dict[str, Any]:
        return self._client.health()

    def sync_portfolio(self) -> dict[str, Any]:
        return self._client.sync_portfolio()
