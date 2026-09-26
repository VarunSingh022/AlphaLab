"""High-performance benchmark suite for the v3.7 adaptive-strategy paths.

Five computational paths, each on a workload an adaptive research loop produces:

* **Replay** -- each of the three shipped rules folded over a long stream,
  adapting on every observation and on every twentieth, with every update and
  every decision recorded.
* **Single steps** -- :func:`~alphalab.strategy.apply_update` one observation
  at a time, as an adaptive strategy calls it once per market event.
* **The replay record** -- its identity derived and its chain verified without
  re-running the rule.
* **Checkpoints** -- a state written as JSON and restored, with its identity
  recomputed and compared, as a run stopped and resumed does.
* **Assessment** -- two replays of the same stream compared.

Each path is measured at two sizes, so the printed ops/sec is readable *and*
the scaling is visible. ``tests/regression/test_v37_complexity.py`` asserts the
replay's growth ratio; this prints the absolute numbers. Streams are built
before their timers start, with no clock and no random number.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable

from alphalab.lifecycle import assess_adaptive_replay
from alphalab.strategy import (
    AdaptationMode,
    AdaptiveConfiguration,
    AdaptiveObservation,
    AdaptiveReplay,
    AdaptiveRule,
    AdaptiveState,
    DecisionTiming,
    ExponentialMeanRule,
    RecursiveLeastSquaresRule,
    TrailingZScoreRule,
    UpdateCadence,
    apply_update,
    checkpoint,
    observation_stream,
    replay_updates,
    restore,
)

LEARNING = AdaptationMode.LEARNING
Stream = tuple[AdaptiveObservation, ...]


def _timed(label: str, count: int, work: Callable[[], object]) -> None:
    start = time.perf_counter()
    work()
    duration = time.perf_counter() - start
    print(f"  {label:<60} {duration:.4f}s, {count / max(duration, 1e-9):>12,.0f} ops/sec")


def _configuration(
    rule: AdaptiveRule,
    inputs: tuple[str, ...],
    parameters: dict[str, float],
    every: int | None,
) -> AdaptiveConfiguration:
    return AdaptiveConfiguration(
        name=f"bench_{rule.rule_id}",
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        inputs=inputs,
        parameters=parameters,
        cadence=UpdateCadence.EVERY_OBSERVATION
        if every is None
        else UpdateCadence.EVERY_N_OBSERVATIONS,
        cadence_every=every,
        cadence_seconds=None,
        decision_timing=DecisionTiming.BEFORE_UPDATE,
        warmup=20,
    )


RULES: tuple[tuple[str, AdaptiveRule, tuple[str, ...], dict[str, float]], ...] = (
    ("exponential mean", ExponentialMeanRule(), ("price",), {"smoothing": 0.1}),
    ("z-score of 20", TrailingZScoreRule(), ("price",), {"window": 20.0, "entry": 1.5}),
    (
        "least squares",
        RecursiveLeastSquaresRule(),
        ("price", "market"),
        {"forgetting": 0.99, "prior_variance": 1000.0},
    ),
)


def _stream(count: int) -> Stream:
    """A price and a market level with a shared cycle and some idiosyncratic jitter."""

    rows = [
        (
            float(index) * 60.0,
            {
                "price": 100.0 + 5.0 * math.sin(index / 40.0) + float((index * 41) % 11 - 5) / 10,
                "market": 100.0 + 4.0 * math.sin(index / 40.0),
            },
        )
        for index in range(count)
    ]
    return observation_stream("bench", rows, first_sequence=0)


def _replay(
    configuration: AdaptiveConfiguration, rule: AdaptiveRule, stream: Stream
) -> Callable[[], AdaptiveReplay]:
    return lambda: replay_updates(configuration, rule, stream, LEARNING)


def _steps(
    configuration: AdaptiveConfiguration, rule: AdaptiveRule, start: AdaptiveState, stream: Stream
) -> Callable[[], object]:
    def work() -> object:
        state = start
        for observation in stream:
            state = apply_update(state, observation, configuration, rule, LEARNING).state
        return state

    return work


def _identity(replay: AdaptiveReplay) -> Callable[[], object]:
    return lambda: replay.replay_id


def _verify(replay: AdaptiveReplay) -> Callable[[], object]:
    return lambda: replay.verify()


def _round_trips(
    state: AdaptiveState, configuration: AdaptiveConfiguration, rule: AdaptiveRule, count: int
) -> Callable[[], object]:
    return lambda: [
        restore(json.loads(json.dumps(checkpoint(state))), configuration, rule)
        for _ in range(count)
    ]


def _assess(first: AdaptiveReplay, second: AdaptiveReplay) -> Callable[[], object]:
    return lambda: assess_adaptive_replay(first, second)


def benchmark_replays() -> None:
    print("\n[1] Replays: every observation observed, every update recorded")
    for count in (5_000, 20_000):
        stream = _stream(count)
        for name, rule, inputs, parameters in RULES:
            for every in (None, 20):
                configuration = _configuration(rule, inputs, parameters, every)
                cadence = "each" if every is None else f"every {every}th"
                _timed(
                    f"replay_updates: {name}, adapt {cadence} ({count:,})",
                    count,
                    _replay(configuration, rule, stream),
                )


def benchmark_steps() -> None:
    print("\n[2] Single steps, as a strategy takes them")
    _, rule, inputs, parameters = RULES[1]
    configuration = _configuration(rule, inputs, parameters, None)
    for count in (5_000, 20_000):
        stream = _stream(count)
        start = replay_updates(configuration, rule, stream[:1], LEARNING).initial
        _timed(
            f"apply_update: z-score of 20 ({count:,} observations)",
            count,
            _steps(configuration, rule, start, stream),
        )


def benchmark_records() -> None:
    print("\n[3] The replay record")
    _, rule, inputs, parameters = RULES[1]
    configuration = _configuration(rule, inputs, parameters, None)
    for count in (5_000, 20_000):
        replay = replay_updates(configuration, rule, _stream(count), LEARNING)
        _timed(f"replay_id over every decision ({count:,})", count, _identity(replay))
        _timed(f"verify, without re-running the rule ({count:,})", count, _verify(replay))


def benchmark_checkpoints() -> None:
    print("\n[4] Checkpoints")
    for name, rule, inputs, parameters in RULES:
        configuration = _configuration(rule, inputs, parameters, None)
        state = replay_updates(configuration, rule, _stream(500), LEARNING).final
        for count in (1_000, 4_000):
            _timed(
                f"checkpoint -> JSON -> restore, {name} (x{count:,})",
                count,
                _round_trips(state, configuration, rule, count),
            )


def benchmark_assessment() -> None:
    print("\n[5] Assessment")
    _, rule, inputs, parameters = RULES[1]
    configuration = _configuration(rule, inputs, parameters, None)
    for count in (5_000, 20_000):
        stream = _stream(count)
        first = replay_updates(configuration, rule, stream, LEARNING)
        second = replay_updates(configuration, rule, stream, LEARNING)
        _timed(f"assess_adaptive_replay, reproduced ({count:,})", count, _assess(first, second))


def run_benchmark() -> None:
    print("=" * 94)
    print("AlphaLab v3.7 -- adaptive strategies: replay, steps, records, checkpoints, assessment")
    print("=" * 94)
    benchmark_replays()
    benchmark_steps()
    benchmark_records()
    benchmark_checkpoints()
    benchmark_assessment()
    print()


if __name__ == "__main__":
    run_benchmark()
