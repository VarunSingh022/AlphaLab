"""
AlphaLab Examples
=================

Example 07 : Universal Data

Difficulty : Intermediate

Estimated Time : 8 minutes

Prerequisites
-------------

✓ Example 06

Topics
------

• Universal Data Engine
• Canonical Datasets
• Dataset Ingestion
• Data Quality
• Dataset Catalog
• Immutable dataset versions (v3.1)
• Explicit cleaning policy (v3.1)

Run

    python examples/07_universal_data.py
"""

from alphalab.data import (
    CleaningPolicy,
    DatasetMetadata,
    DuplicatePolicy,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    UniversalDataEngine,
    catalog_summary,
    dataset_lineage,
    dataset_summary,
    metadata_view,
    quality_report,
)
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency


def main() -> None:
    """Demonstrate the Universal Data Engine."""

    # ------------------------------------------------------------
    # Step 1 : Initialize Engine
    # ------------------------------------------------------------

    state = UniversalDataEngine.initialize(
        engine_id="DATA-001",
    )

    # ------------------------------------------------------------
    # Step 2 : Define Dataset Metadata
    # ------------------------------------------------------------

    metadata = DatasetMetadata(
        dataset_id="DATASET-001",
        source_name="examples/data/sample_ohlcv.csv",
        asset_class=DataAssetClass.EQUITY,
        frequency=TimeFrequency.DAILY,
        start_timestamp=1735689600.0,
        end_timestamp=1738195200.0,
    )

    # ------------------------------------------------------------
    # Step 3 : Example Vendor Records
    # ------------------------------------------------------------

    raw_rows = (
        {
            "symbol": "AAPL",
            "timestamp": 1735689600.0,
            "open": 188.20,
            "high": 189.40,
            "low": 187.90,
            "close": 189.05,
            "volume": 51234567,
        },
        {
            "symbol": "MSFT",
            "timestamp": 1735689600.0,
            "open": 421.10,
            "high": 425.50,
            "low": 420.80,
            "close": 424.70,
            "volume": 28943120,
        },
        {
            "symbol": "SPY",
            "timestamp": 1735689600.0,
            "open": 592.40,
            "high": 594.10,
            "low": 591.70,
            "close": 593.85,
            "volume": 68423119,
        },
    )

    # ------------------------------------------------------------
    # Step 4 : Load Canonical Dataset
    # ------------------------------------------------------------

    dataset = UniversalDataEngine.load(
        metadata,
        raw_rows,
    )

    # ------------------------------------------------------------
    # Step 5 : Ingest Dataset
    # ------------------------------------------------------------

    state = UniversalDataEngine.ingest(
        state,
        dataset,
        ts=1_720_000_000.0,
    )

    # ------------------------------------------------------------
    # Step 6 : Clean under an explicit policy
    #
    # v3.1 requires the policy: what may be altered is the data owner's
    # decision, not the engine's. Cleaning derives a NEW version and leaves
    # the ingested one exactly as it was.
    # ------------------------------------------------------------

    policy = CleaningPolicy(
        duplicates=DuplicatePolicy.KEEP_FIRST,
        ordering=OrderingPolicy.SORT,
        invalid_records=InvalidRecordPolicy.DROP,
        missing_values=MissingValuePolicy.DROP_ROW,
    )

    state = UniversalDataEngine.clean(
        state,
        metadata.dataset_id,
        policy,
        ts=1_720_000_001.0,
    )

    state = UniversalDataEngine.quality(
        state,
        metadata.dataset_id,
        ts=1_720_000_002.0,
    )

    # ------------------------------------------------------------
    # Step 7 : Catalog Dataset
    # ------------------------------------------------------------

    state = UniversalDataEngine.catalog(
        state,
        metadata.dataset_id,
        ts=1_720_000_003.0,
    )

    # ------------------------------------------------------------
    # Step 8 : Inspect State
    # ------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Example 07")
    print("Universal Data Engine")
    print("=" * 60)
    print()

    print(f"Dataset Versions : {len(dataset_summary(state))}")
    print(f"Catalog Entries : {len(catalog_summary(state))}")

    print()
    print("Versions held")
    for held in dataset_summary(state):
        name = held.metadata.dataset_id
        symbols = sorted({record.symbol for record in held.records})
        print(f"  {name[:48]:<48} {len(held.records)} records {symbols}")

    for held in dataset_summary(state):
        chain = dataset_lineage(state, held.metadata.dataset_id)
        if len(chain) > 1:
            print()
            print("Lineage")
            print(f"  derived : {chain[0][:60]}")
            print(f"  from    : {chain[1]}")

    meta = metadata_view(
        state,
        metadata.dataset_id,
    )

    if meta is not None:
        print()
        print("Dataset Metadata")
        print(f"  Source      : {meta.source_name}")
        print(f"  Asset Class : {meta.asset_class.name}")
        print(f"  Frequency   : {meta.frequency.name}")

    report = quality_report(
        state,
        metadata.dataset_id,
    )

    if report is not None:
        print()
        print("Quality Report")
        print(report)

    print()
    print(f"Events Generated : {len(state.events)}")


if __name__ == "__main__":
    main()
