"""
AlphaLab Examples
=================

Example 55 : Reproducible Adaptive Replay

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 13 (durable run state)
✓ Example 47 (reproducible research artifacts)
✓ Example 54 (an adaptive strategy)

Topics
------

• Checkpoints every ten observations, written as JSON with their identity
• A checkpoint restored in a second interpreter -- another hash seed, another
  working directory -- and continued to exactly the uninterrupted state
• A late observation refused rather than absorbed out of order
• A correction reprocessed from the last checkpoint before it
• Replays assessed: reproduced, other inputs, and a rule caught keeping a memory
• An adaptive backtest's manifest reproduced by a rerun, and a rerun whose
  strategy starts where the first one ended caught diverging

What this shows
---------------

Replaying an adaptive strategy is evidence only if the replay can be stopped,
moved to another process and resumed without changing what it learns, and if a
history that arrives out of order is corrected rather than merged. A checkpoint
here carries the state's identity and is refused if a single value in it is
edited; restoring one in a fresh interpreter and continuing reaches the very
state the uninterrupted replay reaches; and a correction to the past is applied
by replaying from the last checkpoint before it -- never by feeding an old
timestamp to a state that has moved on, which is refused.

Run

    python examples/55_reproducible_adaptive_replay.py
"""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from _adaptive_evidence import (
    CLASSES,
    CONFIGURATION,
    DEFINITION,
    ENGINE,
    RULE,
    SOURCES,
    STRATEGY_ID,
    STREAM,
    run_backtest,
    traded_rows,
)
from _point_in_time import banner, ingest_prices, label, section

from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    LifecycleState,
    assess_adaptive_replay,
    assess_reproducibility,
    code_identity_for,
    fingerprint_for_version,
    get_strategy_version,
    manifest_for_run,
    register_strategy,
    research_configuration_with_adaptive,
)
from alphalab.strategy import (
    AdaptationMode,
    AdaptiveConfiguration,
    AdaptiveObservation,
    AdaptiveOrderingError,
    AdaptiveState,
    StateValue,
    TrailingZScoreRule,
    apply_update,
    checkpoint,
    initial_state,
    observation_stream,
    replay_updates,
    restore,
)

#: How the parent tells a second interpreter which checkpoint to continue from.
CHILD_ENV = "ALPHALAB_EXAMPLE_55_CHECKPOINT"

#: The second interpreter's hash seed -- any value other than this one's.
HASH_SEED = "271828"

CHECKPOINT_EVERY = 10
LEARNING = AdaptationMode.LEARNING


def checkpoint_file(folder: Path, observations: int) -> Path:
    return folder / f"{CONFIGURATION.name}-{observations:04d}.json"


def replay_with_checkpoints(
    rows: list[tuple[float, dict[str, float]]], folder: Path
) -> AdaptiveState:
    """Replay in chunks of ten, writing a checkpoint after each chunk."""

    state = initial_state(CONFIGURATION, RULE)
    for begin in range(0, len(rows), CHECKPOINT_EVERY):
        chunk = observation_stream(
            STREAM, rows[begin : begin + CHECKPOINT_EVERY], first_sequence=begin
        )
        state = replay_updates(CONFIGURATION, RULE, chunk, LEARNING, initial=state).final
        checkpoint_file(folder, state.observations).write_text(
            json.dumps(checkpoint(state), sort_keys=True), encoding="utf-8"
        )
    return state


def continue_in_child(spec: Mapping[str, str]) -> None:
    """Run in the second interpreter: restore the checkpoint and replay the rest."""

    payload = json.loads(Path(spec["checkpoint"]).read_text(encoding="utf-8"))
    state = restore(payload, CONFIGURATION, RULE)
    rows = traded_rows(ingest_prices())
    rest = observation_stream(STREAM, rows[state.observations :], first_sequence=state.observations)
    continued = replay_updates(CONFIGURATION, RULE, rest, LEARNING, initial=state)
    print(json.dumps({"restored": state.state_id, "final": continued.final.state_id}))


class RuleWithAMemory:
    """The z-score rule, plus a count of its own decisions kept on the instance.

    Exactly what an adaptive rule must not do: the count is state no payload
    and no checkpoint holds, so the second replay through the same instance
    starts from where the first left it.
    """

    def __init__(self) -> None:
        self._rule = TrailingZScoreRule()
        self._decisions = 0

    @property
    def rule_id(self) -> str:
        return self._rule.rule_id

    @property
    def rule_version(self) -> int:
        return self._rule.rule_version

    def validate(self, configuration: AdaptiveConfiguration) -> None:
        self._rule.validate(configuration)

    def initial(self, configuration: AdaptiveConfiguration) -> Mapping[str, StateValue]:
        return self._rule.initial(configuration)

    def observe(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, StateValue]:
        return self._rule.observe(payload, observation, configuration)

    def adapt(
        self, payload: Mapping[str, StateValue], configuration: AdaptiveConfiguration
    ) -> Mapping[str, StateValue]:
        return self._rule.adapt(payload, configuration)

    def decide(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, float]:
        self._decisions += 1
        outputs = dict(self._rule.decide(payload, observation, configuration))
        if outputs:
            outputs["confidence"] = min(1.0, self._decisions / 100.0)
        return outputs


def main() -> None:
    banner(55, "Reproducible Adaptive Replay")
    prices = ingest_prices()
    rows = traded_rows(prices)
    stream = observation_stream(STREAM, rows, first_sequence=0)
    whole = replay_updates(CONFIGURATION, RULE, stream, LEARNING)

    with tempfile.TemporaryDirectory(prefix="alphalab-example-55-") as workspace:
        folder = Path(workspace)

        section("1. Checkpoints every ten observations")
        final = replay_with_checkpoints(rows, folder)
        written = sorted(path.name for path in folder.iterdir())
        print(f"{len(written)} checkpoints written: {written[0]} ... {written[-1]}")
        print(f"the checkpointed replay ends where one pass ends: {final == whole.final}")
        forty = json.loads(checkpoint_file(folder, 40).read_text(encoding="utf-8"))
        print(
            f"at 40: version {forty['version']}, last close {label(forty['last_timestamp'])}, "
            f"state {forty['state_id'][:16]}..."
        )

        section("2. Restored in a second interpreter, continued exactly")
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            env={
                **os.environ,
                CHILD_ENV: json.dumps({"checkpoint": str(checkpoint_file(folder, 40))}),
                "PYTHONHASHSEED": HASH_SEED,
            },
            cwd=folder,
            capture_output=True,
            text=True,
            check=True,
        )
        child = json.loads(completed.stdout)
        print(f"a fresh interpreter, PYTHONHASHSEED={HASH_SEED}, run from the checkpoint folder:")
        print(f"  restored {child['restored'][:16]}..., continued to {child['final'][:16]}...")
        print(f"  the uninterrupted replay's state: {child['final'] == whole.final.state_id}")

        section("3. A late observation is refused, not absorbed")
        position = 33
        timestamp, values = rows[position]
        corrected = round(values["price"] * 1.02, 3)
        late = AdaptiveObservation(
            timestamp, whole.final.observations, STREAM, {"price": corrected}
        )
        print(
            f"a correction to AAA's close of {label(timestamp)}: {values['price']} -> {corrected}"
        )
        try:
            apply_update(whole.final, late, CONFIGURATION, RULE, LEARNING)
        except AdaptiveOrderingError as refusal:
            print(f"refused: {str(refusal).rsplit('. ', 1)[-1]}")

        section("4. The correction reprocessed from the last checkpoint before it")
        history = [*rows[:position], (timestamp, {"price": corrected}), *rows[position + 1 :]]
        base = position - position % CHECKPOINT_EVERY
        resumed = restore(
            json.loads(checkpoint_file(folder, base).read_text(encoding="utf-8")),
            CONFIGURATION,
            RULE,
        )
        reprocessed = replay_updates(
            CONFIGURATION,
            RULE,
            observation_stream(STREAM, history[base:], first_sequence=base),
            LEARNING,
            initial=resumed,
        )
        corrected_replay = replay_updates(
            CONFIGURATION, RULE, observation_stream(STREAM, history, first_sequence=0), LEARNING
        )
        print(f"from checkpoint {base}: {reprocessed.observations} observations replayed")
        print(
            f"the same state as replaying the corrected history from the start: "
            f"{reprocessed.final == corrected_replay.final}"
        )
        changed = [
            index
            for index, (before, after) in enumerate(
                zip(whole.decisions, corrected_replay.decisions, strict=True)
            )
            if before.outputs != after.outputs
        ]
        print(f"decisions the correction changed: positions {changed[0]} to {changed[-1]}")

    section("5. Replays assessed")
    again = replay_updates(CONFIGURATION, RULE, stream, LEARNING)
    reproduced = assess_adaptive_replay(whole, again)
    print(f"the same replay again   : {reproduced.rerun.name}")
    other = assess_adaptive_replay(whole, corrected_replay)
    print(f"the corrected history   : {other.rerun.name} -- {other.detail[-1]}")
    remembering = RuleWithAMemory()
    first = replay_updates(CONFIGURATION, remembering, stream, LEARNING)
    second = replay_updates(CONFIGURATION, remembering, stream, LEARNING)
    caught = assess_adaptive_replay(first, second)
    print(f"a rule with a memory    : {caught.rerun.name} at observation {caught.first_divergence}")
    print(f"  {caught.detail[0]}")

    section("6. An adaptive backtest's manifest, and its reruns")
    lifecycle, reference = register_strategy(LifecycleState(), STRATEGY_ID, DEFINITION, 1.0)
    version = get_strategy_version(lifecycle.strategies, STRATEGY_ID, reference.version)
    fingerprint = fingerprint_for_version(
        version,
        code_identity_for(CLASSES.require(STRATEGY_ID), "alphalab-examples", "3.7.0", SOURCES),
        NO_DEPENDENCIES,
        research_configuration_with_adaptive(
            {"validation": "in-sample"}, [(CONFIGURATION, initial_state(CONFIGURATION, RULE))]
        ),
        ENGINE,
    )
    result, strategy = run_backtest(prices, None)
    manifest = manifest_for_run(result, prices, fingerprint, ENGINE)
    print(f"manifest {manifest.manifest_id[:16]}..., result {manifest.result_id[:16]}...")
    rerun, _ = run_backtest(prices, None)
    fresh = assess_reproducibility(manifest, manifest_for_run(rerun, prices, fingerprint, ENGINE))
    print(f"a rerun with a fresh strategy: {fresh.rerun.name}")

    stale, _ = run_backtest(prices, strategy.adaptive_state)
    outcome = assess_reproducibility(manifest, manifest_for_run(stale, prices, fingerprint, ENGINE))
    record = stale.state.strategy.strategies[STRATEGY_ID]
    print(f"a rerun whose strategy starts where the first run ended: {outcome.rerun.name}")
    print(f"  the strategy {record.status.name}: {str(record.last_error).split('. ', 1)[0]}")


if __name__ == "__main__":
    _spec = os.environ.get(CHILD_ENV)
    if _spec:
        continue_in_child(json.loads(_spec))
    else:
        main()
