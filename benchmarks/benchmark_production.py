"""High-performance benchmarking suite for the functional Production Engine."""

import time

from alphalab.production import LogLevel, ProductionAdapter, ProductionEngine


def run_benchmark() -> None:
    N = 50_000
    print(f"Starting Production Engine Benchmark: Processing {N} Scheduler Ticks...")

    state = ProductionEngine.initialize("PROD-BENCH")
    state = ProductionEngine.start(state, 1000.0)
    state = ProductionEngine.register_module(state, "CORE", 1000.0)
    state = ProductionEngine.start_module(state, "CORE", 1000.0)

    start = time.perf_counter()

    for i in range(N):
        ts = 1001.0 + (i * 0.1)
        # Simulate active heartbeat every 10 ticks
        if i % 10 == 0:
            state = ProductionEngine.heartbeat(state, "CORE", 1.5, ts)
            
        # Simulate logging every tick
        log = ProductionAdapter.to_log(ts, LogLevel.INFO, "CORE", f"Tick {i}")
        state = ProductionEngine.log(state, log)
        
        # Advance scheduler
        state = ProductionEngine.tick(state, ts)

    duration = time.perf_counter() - start
    ops_sec = N / duration
    
    print(f"Total Ticks Evaluated: {N}")
    print(f"Total Heartbeats Logged: {state.metrics.heartbeats_received}")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} engine ticks/sec")

if __name__ == "__main__":
    run_benchmark()