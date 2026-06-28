"""Benchmark the AlphaLab event pipeline."""

from time import perf_counter

from alphalab.core.events import DomainEvent, EventPipeline, EventPriority


def benchmark_event_pipeline(event_count: int = 100_000) -> None:
    """Benchmark enqueue and dispatch throughput.

    Args:
        event_count: Number of events to enqueue and dispatch.
    """

    events = [DomainEvent() for _ in range(event_count)]
    pipeline = EventPipeline()
    handled = 0

    def handle_event(event: DomainEvent) -> None:
        nonlocal handled
        handled += 1

    pipeline.subscribe(DomainEvent, handle_event)

    enqueue_start = perf_counter()
    pipeline.publish_many(events, EventPriority.LOGGING)
    enqueue_seconds = perf_counter() - enqueue_start

    dispatch_start = perf_counter()
    dispatched = pipeline.run()
    dispatch_seconds = perf_counter() - dispatch_start

    total_seconds = enqueue_seconds + dispatch_seconds
    throughput = event_count / total_seconds

    print(f"events: {event_count}")
    print(f"enqueue_seconds: {enqueue_seconds:.6f}")
    print(f"dispatch_seconds: {dispatch_seconds:.6f}")
    print(f"total_seconds: {total_seconds:.6f}")
    print(f"throughput_events_per_second: {throughput:.2f}")
    print(f"handled: {handled}")
    print(f"dispatched: {dispatched}")


if __name__ == "__main__":
    benchmark_event_pipeline()
