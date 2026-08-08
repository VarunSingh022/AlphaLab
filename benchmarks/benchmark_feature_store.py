"""High-performance benchmark suite for the functional Feature Store."""

import time

from alphalab.feature_store import (
    FeatureMetadata,
    FeatureRegistry,
    FeatureStoreEngine,
    FeatureType,
    FeatureValue,
    FeatureValueStore,
    FeatureValueType,
)


def run_benchmark() -> None:
    state = FeatureStoreEngine.initialize("BENCH-FS")

    metadata = FeatureMetadata(
        feature_id="momentum_20d",
        name="20-Day Momentum",
        version=1,
        feature_type=FeatureType.PRICE,
        value_type=FeatureValueType.FLOAT,
        owner="quant-research",
        description="20-day trailing price momentum.",
    )
    state = FeatureRegistry.register(state, metadata, 0.0)

    N_REGISTRATIONS = 1_000
    print(f"Starting Feature Registration Benchmark: {N_REGISTRATIONS} registrations...")

    reg_start = time.perf_counter()
    for i in range(N_REGISTRATIONS):
        versioned = FeatureMetadata(
            feature_id=f"bench_feature_{i}",
            name=f"Bench Feature {i}",
            version=1,
            feature_type=FeatureType.DERIVED,
            value_type=FeatureValueType.FLOAT,
            owner="quant-research",
            description="Synthetic benchmark feature.",
        )
        state = FeatureRegistry.register(state, versioned, float(i))
    reg_duration = time.perf_counter() - reg_start
    reg_ops_sec = N_REGISTRATIONS / reg_duration

    print(f"Registration Time: {reg_duration:.4f}s")
    print(f"Registration Throughput: {reg_ops_sec:.2f} registrations/sec")

    value = FeatureValue(
        feature_id="momentum_20d", version=1, asset_id="AAPL", value=0.045, timestamp=0.0
    )

    N_WRITES = 100_000
    print(f"\nStarting Feature Value Write Benchmark: {N_WRITES} writes...")

    write_start = time.perf_counter()
    for i in range(N_WRITES):
        state, _ = FeatureValueStore.write(state, value, float(i))
    write_duration = time.perf_counter() - write_start

    write_ops_sec = N_WRITES / write_duration
    print(f"Write Time: {write_duration:.4f}s")
    print(f"Write Throughput: {write_ops_sec:.2f} writes/sec")
    print(f"Total feature store events emitted: {len(state.events)}")
    print(f"Cache entries: {len(state.cache.entries)}")


if __name__ == "__main__":
    run_benchmark()
