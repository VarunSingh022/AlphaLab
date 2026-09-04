"""High-performance benchmark suite for the Model Registry."""

import time

from alphalab.model_registry import (
    ModelRegistry,
    ModelStage,
    production_version,
    promote,
    register_model,
    rollback,
)


def run_benchmark() -> None:
    registry = ModelRegistry()

    N_REGISTER = 20_000
    print(f"Starting Model Registry Benchmark: registering {N_REGISTER} versions of one model...")
    start = time.perf_counter()
    for i in range(N_REGISTER):
        registry, _ = register_model(
            registry,
            "alpha",
            object(),
            timestamp=float(i),
            metrics={"score": float(i)},
            parameters={"lr": 0.1},
        )
    duration = time.perf_counter() - start
    print(
        f"  register_model (growing history): {duration:.4f}s, {N_REGISTER / duration:.2f} ops/sec"
    )

    N_PROMOTE = 2_000
    start = time.perf_counter()
    for i in range(1, N_PROMOTE + 1):
        registry = promote(registry, "alpha", i, ModelStage.PRODUCTION, timestamp=float(i))
    duration = time.perf_counter() - start
    print(
        f"  promote -> PRODUCTION (auto-archive incumbent): {duration:.4f}s, "
        f"{N_PROMOTE / duration:.2f} ops/sec"
    )

    N_ROLLBACK = 1_000
    start = time.perf_counter()
    for i in range(N_ROLLBACK):
        registry = rollback(registry, "alpha", timestamp=float(i))
        registry = promote(
            registry, "alpha", N_PROMOTE, ModelStage.PRODUCTION, timestamp=float(i) + 0.5
        )
    duration = time.perf_counter() - start
    print(f"  rollback + re-promote (chain): {duration:.4f}s, {N_ROLLBACK / duration:.2f} ops/sec")

    N_QUERY = 50_000
    start = time.perf_counter()
    for _ in range(N_QUERY):
        production_version(registry, "alpha")
    duration = time.perf_counter() - start
    print(
        f"  production_version ({N_REGISTER} versions): {duration:.4f}s, "
        f"{N_QUERY / duration:.2f} ops/sec"
    )


if __name__ == "__main__":
    run_benchmark()
