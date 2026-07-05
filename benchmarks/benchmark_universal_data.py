"""High-performance benchmarking suite for the Universal Data Engine."""

import time

from alphalab.data import DataAdapter, UniversalDataEngine


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

    # 3. Clean
    state = UniversalDataEngine.clean(state, "BENCH-1", 1001.0)

    # 4. Convert (Resample 10s ticks to 60s bars -> ~16,666 bars)
    state = UniversalDataEngine.convert(state, "BENCH-1", 60.0, 1002.0)

    # 5. Quality
    state = UniversalDataEngine.quality(state, "BENCH-1", 1003.0)

    duration = time.perf_counter() - start
    ops_sec = N / duration

    print(f"Total Raw Rows Ingested: {N}")
    print(f"Total Canonical Bars Generated: {len(state.datasets['BENCH-1'].records)}")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} raw rows processed/sec")


if __name__ == "__main__":
    run_benchmark()
