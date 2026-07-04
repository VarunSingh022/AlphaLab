"""Alpaca Broker Client Stub."""

import uuid
from typing import Any


class AlpacaClient:
    """Isolated deterministic wrapper around Alpaca logic."""

    def authenticate(self, credentials: dict[str, str]) -> bool:
        return True

    def connect(self) -> bool:
        return True

    def disconnect(self) -> bool:
        return True

    def submit_order(self, order_payload: dict[str, Any]) -> dict[str, Any]:
        if order_payload.get("quantity", 0) <= 0:
            return {"status": "REJECTED", "reason": "qty <= 0"}
        return {"status": "ACCEPTED", "remote_id": f"ALP-{uuid.uuid4()}"}

    def cancel_order(self, order_id: str) -> bool:
        return True

    def modify_order(self, order_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        return {"status": "MODIFIED", "remote_id": order_id}

    def positions(self) -> dict[str, Any]:
        return {"positions": []}

    def orders(self) -> dict[str, Any]:
        return {"orders": []}

    def account(self) -> dict[str, Any]:
        return {"balances": {"USD": 100000.0}}

    def health(self) -> dict[str, Any]:
        return {"status": "healthy", "latency_ms": 2.5}

    def sync_portfolio(self) -> dict[str, Any]:
        return {"drift_detected": False}