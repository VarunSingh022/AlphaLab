"""Benchmark the persistence boundary that production actually writes through.

Until v2.17 this measured ``MemoryStorage.append_event`` from the nine-module
store deprecated in v2.13 and removed in v2.17 (ADR-0034). A benchmark of a
surface with no production importer reports a throughput nobody can act on, so
it now measures the two things a run really pays for when it checkpoints:

1. **The codec spine.** ``serialize`` over a captured state, which every
   snapshot module in the repository goes through.
2. **The run-state store.** ``put`` / ``get`` over
   :class:`~alphalab.persistence.run_store.RunStateStore`, which envelopes the
   payload, digests it, and verifies that digest on the way back out. Both
   backends are measured, because the file backend's ``fsync`` is the cost a
   durable checkpoint actually has and the memory backend is what isolates the
   envelope work from the disk.

The store's own contract is that a full cycle inside ``id_scope`` draws **zero**
run identifiers (ADR-0029 decision 7), so the loop below runs inside one: what
is measured is the same code path a mid-run checkpoint takes.
"""

import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from alphalab.common.ids import id_scope
from alphalab.persistence import (
    FileRunStateStore,
    MemoryRunStateStore,
    RunStateStore,
    deserialize,
    serialize,
)

SEED = 20260914


@dataclass(frozen=True)
class BenchEvent:
    trade_id: str
    price: Decimal


def _measure(store: RunStateStore, label: str, payloads: tuple[str, ...]) -> None:
    with id_scope(SEED):
        start = time.perf_counter()
        for sequence, payload in enumerate(payloads):
            ref = store.put("BENCH-RUN", sequence, payload)
            store.get(ref)
        duration = time.perf_counter() - start

    count = len(payloads)
    print(f"  {label}: {duration:.4f}s, {count / duration:.2f} checkpoints/sec")


def run_benchmark() -> None:
    n_serialize = 100_000
    n_store = 5_000

    print(f"Starting Persistence Benchmark: serializing {n_serialize} events...")

    events = tuple(
        BenchEvent(trade_id=f"TRD-{i}", price=Decimal("150.00")) for i in range(n_serialize)
    )

    start = time.perf_counter()
    encoded = tuple(serialize(event) for event in events)
    duration = time.perf_counter() - start
    print(f"  serialize: {duration:.4f}s, {n_serialize / duration:.2f} events/sec")

    start = time.perf_counter()
    for payload in encoded:
        deserialize(payload)
    duration = time.perf_counter() - start
    print(f"  deserialize: {duration:.4f}s, {n_serialize / duration:.2f} events/sec")

    print(f"Run-state store: {n_store} put/get cycles per backend...")
    payloads = encoded[:n_store]

    _measure(MemoryRunStateStore(), "MemoryRunStateStore", payloads)

    with tempfile.TemporaryDirectory() as root:
        _measure(FileRunStateStore(Path(root)), "FileRunStateStore ", payloads)

    print(f"Total bytes serialized: {sum(len(p) for p in encoded)}")


if __name__ == "__main__":
    run_benchmark()
