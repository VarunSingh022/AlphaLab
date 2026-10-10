"""High-performance benchmarking suite for the functional Scheduler Engine."""

import time

from alphalab.scheduler import (
    SchedulerEngine,
    ScheduleType,
    Timer,
)


def run_benchmark() -> None:
    # Anchor the clock
    base_time = 1000000.0
    state = SchedulerEngine.initialize(base_time)

    N = 100_000
    print(f"Starting Scheduler Engine Benchmark: Processing {N} Timer Triggers...")

    # Pre-register 100,000 distinct timers
    for i in range(N):
        timer = Timer(
            timer_id=f"T-{i}",
            target_timestamp=base_time + float(i + 1),
            schedule_type=ScheduleType.ONE_SHOT,
        )
        state = SchedulerEngine.schedule_timer(state, timer, base_time)

    # Sequentially advance the clock and trigger them in batch
    start = time.perf_counter()

    # Jump to the end, triggering all 100,000 in one deterministic evaluation
    state, triggered = SchedulerEngine.advance_clock(state, base_time + float(N + 1))

    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"Timer Trigger Evaluation Time: {duration:.4f}s")
    print(f"Timers Triggered: {len(triggered)}")
    print(f"Throughput: {ops_sec:.2f} events/sec")


if __name__ == "__main__":
    run_benchmark()
