"""
AlphaLab Examples
=================

Example 04 : Market Data

Difficulty : Beginner

Estimated Time : 5 minutes

Topics
------

• Provider Registration
• Immutable State
• Market Data Views

Run

    python examples/04_market_data.py
"""

from alphalab.marketdata import (
    MarketDataEngine,
    ProviderConfig,
    provider_summary,
)


def main() -> None:
    """Demonstrate the AlphaLab Market Data Engine."""

    # ------------------------------------------------------------
    # Step 1 : Initialize Engine
    # ------------------------------------------------------------

    state = MarketDataEngine.initialize(
        engine_id="MARKETDATA-001",
    )

    # ------------------------------------------------------------
    # Step 2 : Register Provider
    # ------------------------------------------------------------

    provider = ProviderConfig(
        provider_id="SIMULATED",
        name="Example Provider",
        api_key="DEMO_API_KEY",
        api_secret="DEMO_SECRET",
        base_url="https://example.invalid",
        timeout=5.0,
    )

    state = MarketDataEngine.register(
        state,
        provider,
        ts=1_720_000_000.0,
    )

    # ------------------------------------------------------------
    # Step 3 : Inspect State
    # ------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Example 04")
    print("Market Data")
    print("=" * 60)
    print()

    print(f"Registered Providers : {len(provider_summary(state))}")
    print(f"Connections          : {len(state.connections)}")
    print(f"Subscriptions        : {len(state.subscriptions)}")
    print(f"Cached Quotes        : {len(state.quotes)}")
    print(f"Cached Trades        : {len(state.trades)}")
    print(f"Cached Bars          : {len(state.bars)}")
    print()

    for provider in provider_summary(state):
        print(f"Provider ID : {provider.provider_id}")
        print(f"Name        : {provider.name}")
        print(f"Base URL    : {provider.base_url}")
        print(f"Timeout     : {provider.timeout:.1f} sec")
        print()

    print(f"Events Generated : {len(state.events)}")


if __name__ == "__main__":
    main()