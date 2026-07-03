"""High-performance benchmarking suite for the functional Plugin Engine."""

import time

from alphalab.plugins import (
    BasePlugin,
    PluginEngine,
    PluginManager,
    PluginMetadata,
    PluginType,
    lookup,
)


class DummyBenchmarkPlugin(BasePlugin):
    """Dynamic mock plugin created strictly for benchmarking volume."""

    def __init__(self, pid: str, name: str) -> None:
        self._meta = PluginMetadata(
            plugin_id=pid,
            name=name,
            version="1.0",
            author="Bench",
            description="Speed test",
            plugin_type=PluginType.STRATEGY,
            api_version="1.0.0",
        )

    def metadata(self) -> PluginMetadata:
        return self._meta


def run_benchmark() -> None:
    state = PluginEngine.initialize("PLUG-BENCH")

    N = 10_000
    print(f"Starting Plugin SDK Benchmark: Registering and Querying {N} Plugins...")

    plugins = tuple(DummyBenchmarkPlugin(f"P-{i}", f"BenchName-{i}") for i in range(N))

    # 1. Registration Benchmark
    start_reg = time.perf_counter()
    for i, plugin in enumerate(plugins):
        state = PluginManager.register_plugin(state, plugin, float(1000 + i))
    duration_reg = time.perf_counter() - start_reg

    reg_sec = N / duration_reg

    # 2. Lookup Benchmark
    start_look = time.perf_counter()
    for i in range(N):
        _ = lookup(state, f"P-{i}")
    duration_look = time.perf_counter() - start_look

    look_sec = N / duration_look

    print(f"Total Registrations: {state.statistics.total_registered}")
    print(f"Registration Time: {duration_reg:.4f}s")
    print(f"Registration Throughput: {reg_sec:.2f} ops/sec")

    print(f"Lookup Time: {duration_look:.4f}s")
    print(f"Lookup Throughput: {look_sec:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
