"""
AlphaLab Examples
=================

Example 54 : An Adaptive Strategy

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 11 (the unified backtest)
✓ Example 46 (strategy fingerprints)

Topics
------

• Configuration, observation, learned state, update and decision -- kept apart
• A learning rule of four pure functions, and a strategy of two methods on it
• The same learning in a backtest and in a research replay, state for state
• Cadence: observe every bar, adapt every fifth, lose nothing in between
• Controlled adaptation: train, freeze, decide on the frozen state
• The learned state in the run snapshot, restored exactly, an edit refused
• A fingerprint that names the learning configuration and its starting state

What this shows
---------------

A strategy that updates itself is the easiest kind to make irreproducible: its
behaviour depends on everything it has seen, in order, and a mutable attribute
cannot say afterwards why it did what it did. Here the learned state is an
immutable value with a hash-chained lineage, one pure function moves it
forward, and the strategy hands it to the run snapshot. So the backtest ends in
exactly the state a research replay over the same bars reaches, a restored
strategy continues from exactly what it had learned, and a strategy that starts
from a trained state has a different fingerprint from one that starts empty.

The strategy is in ``_adaptive_evidence.py``, shared with example 55: it says
which bar becomes an observation and what a decision means as an intent, and
:class:`~alphalab.strategy.AdaptiveStrategy` does the rest.

Run

    python examples/54_adaptive_strategy.py
"""

import json

from _adaptive_evidence import (
    CLASSES,
    CONFIGURATION,
    DEFINITION,
    ENGINE,
    RULE,
    SOURCES,
    STRATEGY_ID,
    STREAM,
    Reversion,
    configuration_for,
    run_backtest,
    traded_rows,
)
from _point_in_time import banner, ingest_prices, label, section

from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    LifecycleState,
    code_identity_for,
    fingerprint_for_version,
    get_strategy_version,
    register_strategy,
    research_configuration_with_adaptive,
)
from alphalab.runtime.run_snapshot import capture
from alphalab.runtime.snapshot import StrategyStateRecord
from alphalab.strategy import (
    AdaptationMode,
    AdaptiveStateError,
    TransitionKind,
    UpdateCadence,
    initial_state,
    observation_stream,
    replay_updates,
)


def main() -> None:
    banner(54, "An Adaptive Strategy")
    prices = ingest_prices()

    section("1. What learns, and how -- an identity of its own")
    print(f"configuration {CONFIGURATION.configuration_id}")
    print(
        f"rule {CONFIGURATION.rule_id} v{CONFIGURATION.rule_version}, adapts "
        f"{CONFIGURATION.cadence.name}, decides {CONFIGURATION.decision_timing.name}, "
        f"warmup {CONFIGURATION.warmup}"
    )
    start = initial_state(CONFIGURATION, RULE)
    print(f"initial state {start.state_id[:16]}... at version {start.version}")

    section("2. A backtest through the execution path")
    result, strategy = run_backtest(prices, None)
    learned = strategy.adaptive_state
    orders = len(result.state.oms.orders.orders())
    print(f"{result.records_processed} bars, {orders} orders, {len(result.state.fills)} fills")
    print(
        f"learned: version {learned.version} after {learned.observations} observations, "
        f"the last at {label(learned.last_timestamp or 0.0)}"
    )
    print(f"window {learned.payload['values']}")

    section("3. The same learning in a research replay")
    rows = traded_rows(prices)
    research = replay_updates(
        CONFIGURATION,
        RULE,
        observation_stream(STREAM, rows, first_sequence=0),
        AdaptationMode.LEARNING,
    )
    signals = sum(1 for decision in research.decisions if decision.outputs.get("signal"))
    withheld = sum(1 for decision in research.decisions if decision.withheld)
    print(f"the replay ends in the backtest's state: {research.final == learned}")
    print(f"it signalled {signals} times; the backtest placed {orders} orders")
    print(f"decisions withheld in the warmup: {withheld}; the record verifies: {research.verify()}")

    section("4. Cadence: observe every bar, adapt every fifth")
    weekly = configuration_for(DEFINITION.parameters, UpdateCadence.EVERY_N_OBSERVATIONS, 5)
    weekly_stream = observation_stream(STREAM, rows, first_sequence=0)
    midweek = replay_updates(weekly, RULE, weekly_stream[:8], AdaptationMode.LEARNING).final
    window, pending = midweek.payload["values"], midweek.payload["pending"]
    assert isinstance(window, tuple) and isinstance(pending, tuple)
    print(
        f"after 8 bars: {len(window)} values learned, "
        f"{len(pending)} waiting for the next adaptation"
    )
    slow = replay_updates(weekly, RULE, weekly_stream, AdaptationMode.LEARNING)
    kinds = [transition.kind for transition in slow.transitions]
    print(
        f"over all {slow.observations}: {kinds.count(TransitionKind.ADAPTED)} adaptations, "
        f"{kinds.count(TransitionKind.OBSERVED)} observations held for the next one"
    )
    print(
        f"a configuration of its own: {weekly.configuration_id != CONFIGURATION.configuration_id}"
    )

    section("5. Controlled adaptation: train, then freeze")
    half = len(rows) // 2
    trained = replay_updates(
        CONFIGURATION,
        RULE,
        observation_stream(STREAM, rows[:half], first_sequence=0),
        AdaptationMode.LEARNING,
    ).final
    frozen = replay_updates(
        CONFIGURATION,
        RULE,
        observation_stream(STREAM, rows[half:], first_sequence=half),
        AdaptationMode.FROZEN,
        initial=trained,
    )
    decided = sum(1 for decision in frozen.decisions if decision.outputs)
    print(f"trained on {trained.observations} bars: version {trained.version}")
    print(
        f"frozen for {frozen.observations} more: learned window unchanged "
        f"{frozen.final.payload == trained.payload}, version {frozen.final.version}, "
        f"{decided} decisions made"
    )

    section("6. The learned state in the run snapshot, restored exactly")
    snapshot = capture(result.run)
    (record,) = [entry for entry in snapshot.pipeline.strategy if entry.strategy_id == STRATEGY_ID]
    declared = record.state
    assert isinstance(declared, StrategyStateRecord)
    recorded = declared.payload["state_id"]
    matches = recorded == learned.state_id
    print(f"the snapshot records state {recorded[:16]}... -- the strategy's: {matches}")
    fresh = Reversion(STRATEGY_ID, CONFIGURATION, RULE, AdaptationMode.LEARNING, None)
    fresh.restore_state(declared.payload, declared.version)
    print(f"a fresh strategy restored from it holds that state: {fresh.adaptive_state == learned}")
    edited = json.loads(json.dumps(declared.payload))
    edited["payload"]["values"][0] += 1.0
    try:
        fresh.restore_state(edited, declared.version)
    except AdaptiveStateError as refusal:
        print(f"a checkpoint edited by one value is refused: {str(refusal).rsplit('. ', 1)[-1]}")

    section("7. A fingerprint that names what learns and where it starts")
    lifecycle, reference = register_strategy(LifecycleState(), STRATEGY_ID, DEFINITION, 1.0)
    version = get_strategy_version(lifecycle.strategies, STRATEGY_ID, reference.version)
    code = code_identity_for(CLASSES.require(STRATEGY_ID), "alphalab-examples", "3.7.0", SOURCES)
    fingerprints = {
        name: fingerprint_for_version(
            version,
            code,
            NO_DEPENDENCIES,
            research_configuration_with_adaptive(
                {"validation": "in-sample"}, [(CONFIGURATION, initial)]
            ),
            ENGINE,
        )
        for name, initial in (("empty", start), ("trained", trained))
    }
    print(f"entry point {code.entry_point}")
    for key, value in sorted(fingerprints["empty"].research.settings.items()):
        print(f"  {key} = {value}")
    differs = fingerprints["empty"].fingerprint != fingerprints["trained"].fingerprint
    print(f"starting from the trained state is a different strategy: {differs}")


if __name__ == "__main__":
    main()
