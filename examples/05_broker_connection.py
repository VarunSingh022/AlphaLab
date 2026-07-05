"""
AlphaLab Examples
=================

Example 05 : Broker Integration

Difficulty : Intermediate

Estimated Time : 5 minutes

Prerequisites
-------------

✓ Example 04

Topics
------

• Broker Registration
• Authentication
• Connection Management
• Order Submission
• Portfolio Synchronization
• Immutable Integration State

Run

    python examples/05_broker_connection.py
"""

from typing import Any

from alphalab.integrations import (
    BrokerConfig,
    IntegrationEngine,
    broker_summary,
    connection_status,
    metrics_report,
)


class DemoBrokerProvider:
    """
    Minimal provider implementing IntegrationProviderProtocol.

    Real providers would wrap APIs such as:

    - Interactive Brokers
    - Alpaca
    - Binance
    - Zerodha Kite
    - TD Ameritrade
    """

    def authenticate(self, credentials: dict[str, str]) -> bool:
        return True

    def connect(self) -> bool:
        return True

    def disconnect(self) -> bool:
        return True

    def submit_order(self, order_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "FILLED",
            "order_id": order_payload["order_id"],
        }

    def cancel_order(self, order_id: str) -> bool:
        return True

    def sync_portfolio(self) -> dict[str, Any]:
        return {
            "cash": 1_000_000.0,
            "positions": [],
        }


def main() -> None:
    """Run a complete broker integration workflow."""

    # ------------------------------------------------------------
    # Step 1 : Initialize Integration Engine
    # ------------------------------------------------------------

    state = IntegrationEngine.initialize(
        engine_id="INTEGRATION-001",
    )

    # ------------------------------------------------------------
    # Step 2 : Register Broker
    # ------------------------------------------------------------

    config = BrokerConfig(
        broker_id="PAPER-BROKER",
        provider_name="Demo Provider",
        environment="paper",
        api_base_url="https://paper.example.com",
    )

    state = IntegrationEngine.register(
        state,
        config,
    )

    provider = DemoBrokerProvider()

    # ------------------------------------------------------------
    # Step 3 : Authenticate
    # ------------------------------------------------------------

    credentials = {
        "api_key": "demo-key",
        "secret": "demo-secret",
    }

    state = IntegrationEngine.authenticate(
        state,
        config.broker_id,
        provider,
        credentials,
        ts=1_720_000_000.0,
    )

    # ------------------------------------------------------------
    # Step 4 : Connect
    # ------------------------------------------------------------

    state = IntegrationEngine.connect(
        state,
        config.broker_id,
        provider,
        ts=1_720_000_001.0,
    )

    # ------------------------------------------------------------
    # Step 5 : Submit Order
    # ------------------------------------------------------------

    order = {
        "order_id": "ORDER-001",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 100,
        "price": 190.25,
    }

    state = IntegrationEngine.submit_order(
        state,
        config.broker_id,
        provider,
        order,
        ts=1_720_000_002.0,
    )

    # ------------------------------------------------------------
    # Step 6 : Synchronize Portfolio
    # ------------------------------------------------------------

    state = IntegrationEngine.sync_portfolio(
        state,
        config.broker_id,
        provider,
        ts=1_720_000_003.0,
    )

    # ------------------------------------------------------------
    # Step 7 : Inspect State
    # ------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Example 05")
    print("Broker Integration")
    print("=" * 60)
    print()

    print(f"Registered Brokers : {len(broker_summary(state))}")

    connection = connection_status(
        state,
        config.broker_id,
    )

    print(f"Connection State   : {connection}")

    metrics = metrics_report(state)

    print()
    print("Metrics")
    print(f"Orders Submitted   : {metrics.orders_submitted}")
    print(f"API Errors         : {metrics.api_errors}")
    print(f"Reconnects         : {metrics.total_reconnects}")

    print()
    print(f"Integration Events : {len(state.events)}")


if __name__ == "__main__":
    main()