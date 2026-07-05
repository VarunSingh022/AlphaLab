"""
AlphaLab Examples
=================

Example 04 : Market Data

Difficulty : Beginner

Estimated Time : 5 minutes

Topics
------

• Market Data Engine
• Dataset Registration
• Subscription Management
• Market Data Views
• Immutable State

Run

    python examples/04_market_data.py
"""

from pathlib import Path

from alphalab.marketdata import (
    Dataset,
    DataSubscription,
    MarketDataEngine,
    active_subscriptions,
    available_datasets,
)

DATA_DIR = Path(__file__).parent / "data"


def main() -> None:
    """Demonstrate the AlphaLab Market Data Engine."""

    # ------------------------------------------------------------------
    # Step 1 : Initialize Engine
    # ------------------------------------------------------------------

    state = MarketDataEngine.initialize(
        engine_id="MARKETDATA-001",
    )

    # ------------------------------------------------------------------
    # Step 2 : Register Dataset
    # ------------------------------------------------------------------

    dataset = Dataset(
        dataset_id="DATASET-001",
        name="Sample OHLCV",
        description="Synthetic OHLCV dataset for AlphaLab examples.",
        source=str(DATA_DIR / "sample_ohlcv.csv"),
        format="csv",
    )

    state = MarketDataEngine.register_dataset(
        state,
        dataset,
        ts=1_720_000_000.0,
    )

    # ------------------------------------------------------------------
    # Step 3 : Subscribe
    # ------------------------------------------------------------------

    subscription = DataSubscription(
        subscription_id="SUB-001",
        dataset_id=dataset.dataset_id,
        consumer_id="EXAMPLE-04",
    )

    state = MarketDataEngine.subscribe(
        state,
        subscription,
        ts=1_720_000_001.0,
    )

    # ------------------------------------------------------------------
    # Step 4 : Inspect State
    # ------------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Example 04")
    print("Market Data")
    print("=" * 60)
    print()

    print(f"Registered Datasets : {len(available_datasets(state))}")
    print(f"Active Subscriptions : {len(active_subscriptions(state))}")
    print()

    print("Datasets")

    for ds in available_datasets(state):
        print(f"  ID          : {ds.dataset_id}")
        print(f"  Name        : {ds.name}")
        print(f"  Source      : {ds.source}")
        print(f"  Format      : {ds.format}")
        print()

    print("Subscriptions")

    for sub in active_subscriptions(state):
        print(f"  Subscription : {sub.subscription_id}")
        print(f"  Dataset      : {sub.dataset_id}")
        print(f"  Consumer     : {sub.consumer_id}")
        print()

    print(f"Events Generated : {len(state.events)}")


if __name__ == "__main__":
    main()