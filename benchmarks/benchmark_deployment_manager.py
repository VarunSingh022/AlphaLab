"""High-performance benchmark suite for the Deployment Manager."""

import time

from alphalab.deployment_manager import (
    DeploymentManager,
    active_release,
    deploy,
    register_release,
    rollback,
)

COMPONENTS = {"strategy": "ma_crossover-004", "model": "momentum@1", "dataset": "eod-2020"}
CONFIG = {"max_gross": "1.0", "venue": "paper", "region": "eu"}


def run_benchmark() -> None:
    manager = DeploymentManager()

    N_RELEASES = 10_000
    print(f"Starting Deployment Manager Benchmark: registering {N_RELEASES} releases...")
    start = time.perf_counter()
    for i in range(N_RELEASES):
        manager, _ = register_release(
            manager, "stack", COMPONENTS, {**CONFIG, "build": str(i)}, timestamp=float(i)
        )
    duration = time.perf_counter() - start
    print(
        f"  register_release (checksum each): {duration:.4f}s, {N_RELEASES / duration:.2f} ops/sec"
    )

    N_DEPLOY = 5_000
    start = time.perf_counter()
    for i in range(1, N_DEPLOY + 1):
        manager = deploy(manager, "stack", i, "production", timestamp=float(i))
    duration = time.perf_counter() - start
    print(f"  deploy -> production: {duration:.4f}s, {N_DEPLOY / duration:.2f} ops/sec")

    N_ROLLBACK = 2_000
    start = time.perf_counter()
    for i in range(N_ROLLBACK):
        manager = rollback(manager, "production", timestamp=float(i))
        manager = deploy(manager, "stack", N_DEPLOY, "production", timestamp=float(i) + 0.5)
    duration = time.perf_counter() - start
    print(f"  rollback + re-deploy (chain): {duration:.4f}s, {N_ROLLBACK / duration:.2f} ops/sec")

    N_QUERY = 50_000
    start = time.perf_counter()
    for _ in range(N_QUERY):
        active_release(manager, "production")
    duration = time.perf_counter() - start
    print(f"  active_release: {duration:.4f}s, {N_QUERY / duration:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
