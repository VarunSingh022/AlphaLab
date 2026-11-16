"""What a captured run costs over the run it captures (v4.3, ADR-0055 section 4, PC-07).

The plan budgets a capture at 10 percent of the run's time, where identifying
the run by ``digest_run`` costs 62 to 69 percent of it at the plan's scale
probe. Measured here at four trade densities -- a trade on every record, every
second, every fourth, every eighth -- on the backtesting benchmark's engine
workload, whose strategy does no work of its own, so the run is the engine's
cost alone and every ratio is as large as it gets:

* **the series** costs 1 to 4 percent of the run, inside the plan's budget,
  and is judged against it;
* **the trace** adds a fixed cost per step -- the canonical serialization of
  what the step added, an equity point at the least, and one SHA-256 link --
  of about 5 to 6 percent more, so series and trace together cost 7.6 to 8.9
  percent on the reference machine (CPython 3.12, Apple arm64).

How it got there, every change output-identical (the pinned identities hold):

* 12.6 to 14.4 percent when PC-07 was proposed.
* 12.1 to 13.3 after two changes to the trace's writer.
* 10.9 to 12.8 after a third (v4.3 M6): one lookup by a value's exact type, one
  template per record. It was made when a hosted runner read 15.0 percent at
  one density (CPython 3.12, Ubuntu). The owner approved it on 2026-11-16.
  Hosted runners then read it at 11.9 to 16.8 percent on CPython 3.12, over the
  ceiling at a trade on every eighth record, 10.8 to 13.1 on 3.13 and 10.7 to
  14.7 on 3.14.
* 7.6 to 8.9 after a fourth (v4.3 M6): the chain computed 64 steps at a time,
  not between two of the run's steps, where it ran on code and data the step
  had pushed out of the processor's caches. Hosted Linux runners then read 7.6
  to 8.0 percent on CPython 3.12, 8.2 to 10.5 on 3.13 and 7.0 to 9.5 on 3.14.
  Hosted macOS runners read 3.6 to 25.3, figures that cannot all be true (see
  below), as failed gates.

The figures before the fourth change bear on what narrowing would buy: with a
trade on every eighth record the trace cost 12.2 percent without the order
events and 11.4 without the equity points. Narrowing would not have brought
it to 10 percent, because most of its cost is the work it does every step.

The owner approved plan change PC-07 (2026-11-15). The series is held to the
plan's 10 percent, ``MAX_SERIES_OVERHEAD``, and series and trace together to
``MAX_TRACED_OVERHEAD``, 15 percent: a ceiling, not a target. A combined result
above it fails this benchmark. That is a failed performance gate, to be taken
to the owner for a new decision, never met by raising the limit or by narrowing
what the trace covers.

``digest_run``'s share is printed beside each figure, not gated. Every ceiling is
judged through ``_stable_timing``: CPU time, collector paused, workloads
interleaved, fastest of several.

Nine samples, not the harness's three (v4.3 M6, TD-35). The capture's cost is
about a tenth of the run, judged against a ceiling at a seventh, so a minimum
inflated by a few percent decides the verdict. On hosted macOS runners the
fastest of three read figures that cannot all be true: the series alone above
series and trace, which include it, and a capture cheaper than no capture. A
slow phase of the runner had inflated every sample of one workload and none of
another. Nine interleaved samples give each workload more chances at a quiet
moment. The limits, the workload and what the trace covers are unchanged.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from uuid import uuid4

from _stable_timing import fastest

# The engine workload is the backtesting benchmark's, shared rather than copied.
from benchmark_backtesting import _config, _context_factory, _dataset

from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
    run_captured,
)
from alphalab.backtesting.engine import BacktestEngine
from alphalab.lifecycle.reproducibility import digest_run
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

#: The plan's budget, which the series alone is judged against.
MAX_SERIES_OVERHEAD = 0.10
#: PC-07, approved by the owner: series and trace together, a ceiling.
MAX_TRACED_OVERHEAD = 0.15
RECORDS = 4_000
DENSITIES = (1, 2, 4, 8)
#: Samples of each workload, the fastest kept (TD-35).
SAMPLES = 9

EXTRACTOR = ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.REFUSE)
SERIES = CaptureSpec(EXTRACTOR, trace=False)
TRACED = CaptureSpec(EXTRACTOR, trace=True)


class _Periodic(BaseStrategy):
    """Flips between long and short one share on every ``every``-th record."""

    def __init__(self, strategy_id: str, asset_id: str, every: int) -> None:
        self._strategy_id, self._asset_id, self._every = strategy_id, asset_id, every

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        index = int(event.quote.timestamp)
        if index % self._every:
            return ()
        target = Decimal(1) if (index // self._every) % 2 == 0 else Decimal(-1)
        return (Intent(self._strategy_id, self._asset_id, target, timestamp=event.quote.timestamp),)


def _running(strategy_id: str, asset_id: str, every: int) -> RuntimeState:
    state = register_strategy(
        create_runtime(), strategy_id, _Periodic(strategy_id, asset_id, every)
    )
    entry = state.strategies[strategy_id]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"quotes"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(state, strategies={strategy_id: entry})


def _measure(config: Any, dataset: Any, runtime: RuntimeState) -> list[float]:
    """The run, its series capture, its traced capture and ``digest_run``, judged together."""

    plain = BacktestEngine.run(config, dataset, runtime, _context_factory)
    return fastest(
        [
            lambda: BacktestEngine.run(config, dataset, runtime, _context_factory),
            lambda: run_captured(config, dataset, runtime, _context_factory, capture=SERIES),
            lambda: run_captured(config, dataset, runtime, _context_factory, capture=TRACED),
            lambda: digest_run(plain),
        ],
        SAMPLES,
    )


def run_benchmark() -> int:
    print(f"Starting Capture Benchmark ({RECORDS} records at four trade densities)...")
    failures = 0
    for every in DENSITIES:
        strategy_id, asset_id = str(uuid4()), str(uuid4())
        config, dataset = _config(strategy_id), _dataset(asset_id, RECORDS)
        runtime = _running(strategy_id, asset_id, every)
        plain = BacktestEngine.run(config, dataset, runtime, _context_factory)
        captured = run_captured(config, dataset, runtime, _context_factory, capture=TRACED)
        if captured.result != plain:
            raise SystemExit("A captured run's result differs from the run it captures.")

        run_time, series_time, traced_time, digest_time = _measure(config, dataset, runtime)
        series = series_time / max(run_time, 1e-9) - 1.0
        traced = traced_time / max(run_time, 1e-9) - 1.0
        per_step = (traced_time - run_time) / (RECORDS + 2) * 1e6
        verdict = "ok"
        if series > MAX_SERIES_OVERHEAD:
            verdict = "OVER THE SERIES BUDGET"
        if traced > MAX_TRACED_OVERHEAD:
            verdict = "OVER PC-07's CEILING: a failed performance gate, for the owner"
        print(
            f"  a trade every {every}: run {run_time:.3f}s, digest_run "
            f"{digest_time / run_time:.0%} of it; series {series:+.1%} (budget "
            f"{MAX_SERIES_OVERHEAD:.0%}), series and trace "
            f"{traced:+.1%} (PC-07: {MAX_TRACED_OVERHEAD:.0%}), {per_step:.1f} us a step "
            f"{verdict}"
        )
        failures += verdict != "ok"
    print(f"  judged: CPU time, collector paused, fastest of {SAMPLES}")
    print(f"  the trace absorbed, at a trade every {DENSITIES[-1]}: {captured.record.traced}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
