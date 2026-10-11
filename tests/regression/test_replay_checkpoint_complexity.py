"""A replay checkpoint costs time and space linear in what the run recorded (v4.1, LIM-006).

A paused replay is captured whole -- the run's snapshot, the cursor and its
lifecycle log -- and restored by decoding all of it and rebuilding two
identifier streams by replaying their draws. Each part is linear in the run's
recorded history; a part that copied what it had already copied, once per
record, would make a long replay's checkpoint quadratic, as the cursor's own log
once made replaying it (``test_replay_cursor_complexity.py``). Measured for v4.1
(``docs/audit/scripts/stress_v4_1.py``): about 3.2 KB and 0.12-0.16 ms a record
at 2,000 to 32,000 records with every history kept, flat; with a retention
policy bounding every derived history, the cursor's log included, the payload
stops growing with the records and grows only with the orders (PRF-011).

The size checks are exact; the timing check reads time the one stabilized way
every guard here does (``tests/regression/_timing.py``) and bounds a 4x input at
8x -- linear is about 4x, quadratic about 16x.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from functools import cache

from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay import PausedReplay, ReplayBacktest
from alphalab.backtesting.replay_snapshot import capture, from_primitives, restore
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.retention import RetentionPolicy, slack
from alphalab.runtime.run import RunConfig
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)
from tests.regression._timing import CLOCK, timings

STRATEGY = "4a1d2f4e-0c3b-4d6a-9f21-7e5b3c2a1d10"
ASSET = "9b8c7d6e-5f4a-4b3c-8d2e-1f0a9b8c7d60"
SMALL, LARGE = 500, 2_000


def _plan(records: int) -> dict[float, Decimal]:
    return {
        2.0 + index: Decimal("5") if index % 10 == 0 else Decimal("-5")
        for index in range(0, records, 5)
    }


def _config(bounded: bool) -> RunConfig:
    config = backtest_config(STRATEGY, seed=11)
    if not bounded:
        return config
    policy = RetentionPolicy(market_history=50, steps=50, audit_events=50, results=50)
    return dataclasses.replace(
        config, pipeline=dataclasses.replace(config.pipeline, retention=policy)
    )


def _dataset(records: int) -> MarketDataset:
    return dataset_of_quotes(ASSET, [Decimal(100) + Decimal(index % 7) for index in range(records)])


@cache
def _paused(records: int, bounded: bool = False) -> PausedReplay:
    """A replay of ``records`` paused before its last record."""

    paused = ReplayBacktest.run(
        _config(bounded),
        _dataset(records),
        running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, _plan(records))),
        context_factory,
        pause_when=lambda progress: progress.records_replayed == records - 1,
    )
    assert isinstance(paused, PausedReplay)
    return paused


def _objects(records: int, bounded: bool = False) -> RunObjects:
    config = _config(bounded)
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY: ScriptedStrategy(STRATEGY, ASSET, _plan(records))},
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )


def _payload(records: int, bounded: bool = False) -> str:
    return serialize(capture(_paused(records, bounded)))


def test_a_checkpoint_holds_the_same_bytes_a_record_at_every_length() -> None:
    small, large = len(_payload(SMALL)) / SMALL, len(_payload(LARGE)) / LARGE

    assert 0.9 < large / small < 1.1, (small, large)


def test_a_bounded_run_s_checkpoint_stops_growing_with_its_records() -> None:
    """Retention bounds every derived history, the cursor's log included; orders remain."""

    small, large = _paused(SMALL, bounded=True), _paused(LARGE, bounded=True)

    for paused in (small, large):
        # Kept: the bound, at most an eighth more before the next trim, and the pause.
        assert 50 <= len(paused.cursor.system_events) <= 50 + slack(50) + 1
    assert large.cursor.system_events.dropped > small.cursor.system_events.dropped
    orders = len(large.run.pipeline.oms.orders.orders()) / len(
        small.run.pipeline.oms.orders.orders()
    )
    growth = len(_payload(LARGE, bounded=True)) / len(_payload(SMALL, bounded=True))
    assert growth < orders, f"the payload grew {growth:.2f}x for {orders:.2f}x the orders"


def _round_trip(records: int) -> float:
    paused, objects, dataset = _paused(records), _objects(records), _dataset(records)
    start = CLOCK()
    restored = restore(from_primitives(deserialize(serialize(capture(paused)))), objects, dataset)
    ReplayBacktest.resume(restored, context_factory)
    return CLOCK() - start


def test_capturing_restoring_and_resuming_stay_linear_in_the_records_replayed() -> None:
    """Quadratic over a 4x workload is ~16x; linear is ~4x."""

    small, large = timings(_round_trip, SMALL, LARGE)

    assert large < small * 8.0, (
        f"Checkpointing and resuming a replay of {LARGE} records took {large:.3f}s against "
        f"{small:.3f}s for {SMALL}; some part of it is re-copied per record."
    )
