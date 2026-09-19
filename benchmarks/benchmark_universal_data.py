"""High-performance benchmarking suite for the Universal Data Engine."""

import time

from alphalab.data import (
    CleaningPolicy,
    DataAdapter,
    DuplicatePolicy,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    UniversalDataEngine,
    dataset_lineage,
)

#: Stated rather than defaulted: v3.1 removed the implicit cleaning policy,
#: because what may be altered is the data owner's decision.
POLICY = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)


def run_benchmark() -> None:
    N = 100_000
    print(f"Starting Universal Data Engine Benchmark: Parsing and Aggregating {N} rows...")

    state = UniversalDataEngine.initialize("DATA-BENCH")
    meta = DataAdapter.create_metadata("BENCH-1", "CSV", "EQUITY", "TICK", 0.0, float(N))

    # Pre-generate 100,000 raw, untyped, aliased dictionary rows
    raw_rows = tuple(
        {
            "Date": float(i * 10),  # 10 second intervals
            "O": 100.0,
            "High": 105.0,
            "Low": 95.0,
            "Close": 102.0,
            "Vol": 50.0,
        }
        for i in range(N)
    )

    start = time.perf_counter()

    # 1. Parse into Canonical structures
    dataset = UniversalDataEngine.load(meta, raw_rows)

    # 2. Ingest
    state = UniversalDataEngine.ingest(state, dataset, 1000.0)

    # 3. Clean. Derives a new version; the raw one stays in state.
    state = UniversalDataEngine.clean(state, "BENCH-1", POLICY, 1001.0)

    # 4. Convert (Resample 10s ticks to 60s bars -> ~16,666 bars)
    state = UniversalDataEngine.convert(state, "BENCH-1", 60.0, 1002.0)

    resampled = next(name for name in state.datasets if name != "BENCH-1")

    # 5. Quality, measured on the resampled version.
    state = UniversalDataEngine.quality(state, resampled, 1003.0)

    duration = time.perf_counter() - start
    ops_sec = N / duration

    print(f"Total Raw Rows Ingested: {N}")
    print(f"Total Canonical Bars Generated: {len(state.datasets[resampled].records)}")
    lineage = " <- ".join(dataset_lineage(state, resampled))
    print(f"Versions Held: {len(state.datasets)} ({lineage})")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} raw rows processed/sec")


if __name__ == "__main__":
    run_benchmark()
