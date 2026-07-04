"""Adapter translating AlphaLab domain objects to Provider Payloads."""

from typing import Any


class IntegrationAdapter:
    """Stateless translator mapping generic framework outputs to remote integrations."""

    @staticmethod
    def to_broker_payload(alpha_order: dict[str, Any]) -> dict[str, Any]:
        """Translates internal AlphaLab orders to normalized generic broker payloads."""
        return {
            "order_id": alpha_order["order_id"],
            "symbol": alpha_order["symbol"],
            "side": alpha_order["side"].upper(),
            "type": alpha_order["order_type"].upper(),
            "quantity": float(alpha_order["quantity"]),
            "price": float(alpha_order.get("price", 0.0)),
        }
