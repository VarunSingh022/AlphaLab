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
  of about 9 percent more, so series and trace together cost 10.9 to 12.8
  percent on the reference machine (CPython 3.12, Apple arm64). They cost 12.6
  to 14.4 when PC-07 was proposed, 12.1 to 13.3 after two output-identical
  changes to the trace's writer, and 10.9 to 12.8 after a third (v4.3 M6: one
  lookup by a value's exact type, one template per record), made when a hosted
  runner read 15.0 at one density (CPython 3.12, Ubuntu). Narrowing what
  the trace covers would not bring it to 10 percent: with a trade on every
  eighth record it costs 12.2 percent without the order events and 11.4 without
  the equity points, most of it the work it does every step.

The owner approved plan change PC-07 (2026-11-15). The series is held to the
plan's 10 percent, ``MAX_SERIES_OVERHEAD``, and series and trace together to
``MAX_TRACED_OVERHEAD``, 15 percent: a ceiling, not a target. A combined result
above it fails this benchmark. That is a failed performance gate, to be taken
to the owner for a new decision, never met by raising the limit or by narrowing
what the trace covers.

``digest_run``'s share is printed beside each figure, not gated. Every ceiling is
judged through ``_stable_timing``: CPU time, collector paused, workloads
interleaved, fastest of several.
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

from _stable_timing import SAMPLES, fastest

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
        ]
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
