"""The adaptive engine: explicit update semantics, lineage, replay and restart.

Every semantic the v3.7 brief names for adaptive strategies is asserted here on
the real engine and the three shipped rules: initial state, cadence, ordering,
state transition, persistence representation, replay, checkpointing,
reset/restart and deterministic reprocessing -- and the refusals that keep each
honest.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any

import pytest

from alphalab.common.statistics import mean, standard_deviation
from alphalab.persistence.serializer import serialize
from alphalab.strategy import (
    ADAPTIVE_CONFIGURATION_SCHEME,
    AdaptationMode,
    AdaptiveConfiguration,
    AdaptiveObservation,
    AdaptiveOrderingError,
    AdaptiveState,
    AdaptiveStateError,
    DecisionTiming,
    ExponentialMeanRule,
    RecursiveLeastSquaresRule,
    TrailingZScoreRule,
    TransitionKind,
    UpdateCadence,
    apply_update,
    canonical_configuration_key,
    checkpoint,
    initial_state,
    observation_stream,
    replay_updates,
    restore,
)

LEARNING = AdaptationMode.LEARNING
FROZEN = AdaptationMode.FROZEN


def _ewma(**changes: object) -> AdaptiveConfiguration:
    arguments: dict[str, object] = {
        "name": "ewma",
        "rule_id": "exponential_mean",
        "rule_version": 1,
        "inputs": ("price",),
        "parameters": {"smoothing": 0.5},
        "cadence": UpdateCadence.EVERY_OBSERVATION,
        "cadence_every": None,
        "cadence_seconds": None,
        "decision_timing": DecisionTiming.BEFORE_UPDATE,
        "warmup": 0,
    }
    arguments.update(changes)
    return AdaptiveConfiguration(**arguments)  # type: ignore[arg-type]


def _zscore(**changes: object) -> AdaptiveConfiguration:
    return _ewma(
        **{
            "name": "zscore",
            "rule_id": "trailing_zscore",
            "parameters": {"window": 3, "entry": 1.0},
            **changes,
        }
    )


def _stream(
    values: list[float], start: int = 0, stream: str = "prices@1:AAA"
) -> tuple[AdaptiveObservation, ...]:
    return observation_stream(
        stream,
        [(float(100 + start + index), {"price": value}) for index, value in enumerate(values)],
        first_sequence=start,
    )


EWMA = ExponentialMeanRule()
ZSCORE = TrailingZScoreRule()
RLS = RecursiveLeastSquaresRule()


# --------------------------------------------------------------------------- #
# Configuration and observation identity
# --------------------------------------------------------------------------- #


def test_the_configuration_identity_is_derived_and_parameter_order_free() -> None:
    first = _ewma(parameters={"smoothing": 0.5})

    assert first.configuration_id == _ewma(parameters={"smoothing": 0.5}).configuration_id
    assert first.configuration_id.startswith("ewma@")
    assert canonical_configuration_key(first).splitlines()[0] == ADAPTIVE_CONFIGURATION_SCHEME


@pytest.mark.parametrize(
    "changes",
    [
        {"parameters": {"smoothing": 0.25}},
        {"rule_version": 2},
        {"warmup": 3},
        {"decision_timing": DecisionTiming.AFTER_UPDATE},
        {"cadence": UpdateCadence.EVERY_N_OBSERVATIONS, "cadence_every": 2},
        {"name": "ewma_fast"},
        {"inputs": ("close",)},
    ],
)
def test_every_setting_that_changes_behaviour_changes_the_identity(
    changes: dict[str, object],
) -> None:
    assert _ewma(**changes).configuration_id != _ewma().configuration_id


def test_a_number_keeps_its_spelling_in_the_identity() -> None:
    assert (
        _zscore(parameters={"window": 3, "entry": 1.0}).configuration_id
        != _zscore(parameters={"window": 3.0, "entry": 1.0}).configuration_id
    )


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"name": "a@b"}, "without '@'"),
        ({"rule_id": "Exponential Mean"}, "not an identifier"),
        ({"rule_version": 0}, "at least 1"),
        ({"inputs": ()}, "at least one input"),
        ({"inputs": ("p", "p")}, "repeat"),
        ({"parameters": {"smoothing": math.nan}}, "non-finite"),
        ({"parameters": {"smoothing": True}}, "must be a number"),
        ({"warmup": -1}, "count of observations"),
        ({"cadence": UpdateCadence.EVERY_N_OBSERVATIONS}, "cadence_every"),
        (
            {
                "cadence": UpdateCadence.EVERY_N_OBSERVATIONS,
                "cadence_every": 2,
                "cadence_seconds": 1.0,
            },
            "reads no cadence_seconds",
        ),
        ({"cadence": UpdateCadence.MINIMUM_INTERVAL}, "needs cadence_seconds"),
        ({"cadence": UpdateCadence.MINIMUM_INTERVAL, "cadence_seconds": 0.0}, "positive"),
        ({"cadence_every": 5}, "reads neither"),
    ],
)
def test_an_incoherent_configuration_is_refused(changes: dict[str, object], match: str) -> None:
    with pytest.raises(AdaptiveStateError, match=match):
        _ewma(**changes)


def test_an_observation_identity_names_its_position_stream_and_values() -> None:
    first = AdaptiveObservation(100.0, 0, "s", {"price": 1.0})

    assert first.observation_id == AdaptiveObservation(100.0, 0, "s", {"price": 1.0}).observation_id
    for other in (
        AdaptiveObservation(100.0, 1, "s", {"price": 1.0}),
        AdaptiveObservation(101.0, 0, "s", {"price": 1.0}),
        AdaptiveObservation(100.0, 0, "t", {"price": 1.0}),
        AdaptiveObservation(100.0, 0, "s", {"price": 1.5}),
    ):
        assert other.observation_id != first.observation_id


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ((math.inf, 0, "s", {"p": 1.0}), "non-finite"),
        ((1.0, -1, "s", {"p": 1.0}), "negative"),
        ((1.0, 0, " ", {"p": 1.0}), "one line"),
        ((1.0, 0, "s", {"p": math.nan}), "non-finite"),
        ((1.0, 0, "s", {"bad key": 1.0}), "not an identifier"),
    ],
)
def test_a_malformed_observation_is_refused(arguments: tuple[Any, ...], match: str) -> None:
    with pytest.raises(AdaptiveStateError, match=match):
        AdaptiveObservation(*arguments)


# --------------------------------------------------------------------------- #
# The initial state, and a rule that is not the configured one
# --------------------------------------------------------------------------- #


def test_the_initial_state_is_derived_and_reproducible() -> None:
    state = initial_state(_ewma(), EWMA)

    assert state == initial_state(_ewma(), EWMA)
    assert state.state_id == initial_state(_ewma(), EWMA).state_id
    assert state.version == 0 and state.observations == 0
    assert state.last_timestamp is None


def test_a_rule_other_than_the_configured_one_is_refused() -> None:
    with pytest.raises(AdaptiveStateError, match="would learn something"):
        initial_state(_ewma(), ZSCORE)


@pytest.mark.parametrize(
    ("configuration", "rule", "match"),
    [
        (_ewma(parameters={}), EWMA, "exactly the parameters"),
        (_ewma(parameters={"smoothing": 0.5, "extra": 1.0}), EWMA, "exactly the parameters"),
        (_ewma(parameters={"smoothing": 1.5}), EWMA, r"\(0, 1\]"),
        (_ewma(inputs=("a", "b")), EWMA, "reads 1 input"),
        (_zscore(parameters={"window": 2.5, "entry": 1.0}), ZSCORE, "whole number"),
        (_zscore(parameters={"window": 3, "entry": 0.0}), ZSCORE, "positive"),
    ],
)
def test_a_rule_refuses_a_configuration_it_cannot_read(
    configuration: AdaptiveConfiguration, rule: Any, match: str
) -> None:
    with pytest.raises(AdaptiveStateError, match=match):
        initial_state(configuration, rule)


# --------------------------------------------------------------------------- #
# Advancing
# --------------------------------------------------------------------------- #


def test_the_exponential_mean_learns_as_its_textbook_form() -> None:
    result = replay_updates(_ewma(), EWMA, _stream([10.0, 20.0, 40.0]), LEARNING)

    # Seeded with 10, then 10 + 0.5(20 - 10) = 15, then 15 + 0.5(40 - 15) = 27.5.
    assert result.final.payload["mean"] == 27.5
    assert result.final.version == 3
    assert [d.outputs.get("mean") for d in result.decisions] == [None, 10.0, 15.0]


def test_a_decision_reads_the_state_its_timing_names() -> None:
    before = replay_updates(_ewma(), EWMA, _stream([10.0, 20.0]), LEARNING)
    after = replay_updates(
        _ewma(decision_timing=DecisionTiming.AFTER_UPDATE), EWMA, _stream([10.0, 20.0]), LEARNING
    )

    assert before.decisions[1].state_id == before.transitions[1].before
    assert before.decisions[1].outputs["mean"] == 10.0, "prequential: decided before learning"
    assert after.decisions[1].state_id == after.transitions[1].after
    assert after.decisions[1].outputs["mean"] == 15.0


def test_decisions_are_withheld_during_warmup_never_zeroed() -> None:
    result = replay_updates(_ewma(warmup=2), EWMA, _stream([10.0, 20.0, 40.0]), LEARNING)

    assert [d.withheld for d in result.decisions] == [True, True, False]
    assert result.decisions[0].outputs == {}
    assert result.decisions[2].outputs["mean"] == 15.0


def test_an_undefined_decision_is_empty_rather_than_zero() -> None:
    result = replay_updates(_zscore(), ZSCORE, _stream([1.0, 1.0, 1.0, 1.0]), LEARNING)

    assert all(decision.outputs == {} for decision in result.decisions), "a constant window"


def test_a_frozen_state_consumes_and_decides_and_learns_nothing() -> None:
    trained = replay_updates(_ewma(), EWMA, _stream([10.0, 20.0]), LEARNING).final

    frozen = replay_updates(_ewma(), EWMA, _stream([90.0, 99.0], start=2), FROZEN, initial=trained)

    assert frozen.final.payload == trained.payload
    assert frozen.final.version == trained.version
    assert frozen.final.observations == 4
    assert [t.kind for t in frozen.transitions] == [TransitionKind.FROZEN] * 2
    assert frozen.decisions[1].outputs["mean"] == 15.0


def test_every_n_cadence_observes_everything_and_adapts_on_its_multiples() -> None:
    configuration = _ewma(cadence=UpdateCadence.EVERY_N_OBSERVATIONS, cadence_every=2)

    result = replay_updates(configuration, EWMA, _stream([10.0, 20.0, 40.0, 60.0]), LEARNING)

    assert [t.kind for t in result.transitions] == [
        TransitionKind.OBSERVED,
        TransitionKind.ADAPTED,
        TransitionKind.OBSERVED,
        TransitionKind.ADAPTED,
    ]
    # Batch means 15 then 50: seeded with 15, then 15 + 0.5(50 - 15) = 32.5. No value lost.
    assert result.final.payload["mean"] == 32.5
    assert result.final.version == 2


def test_minimum_interval_cadence_reads_event_time_between_observations() -> None:
    configuration = _ewma(cadence=UpdateCadence.MINIMUM_INTERVAL, cadence_seconds=2.0)

    result = replay_updates(configuration, EWMA, _stream([10.0, 20.0, 30.0, 40.0, 50.0]), LEARNING)

    assert [t.kind.name for t in result.transitions] == [
        "ADAPTED",
        "OBSERVED",
        "ADAPTED",
        "OBSERVED",
        "ADAPTED",
    ]
    assert result.final.last_adapted_at == 104.0


def test_an_out_of_order_or_repeated_observation_is_refused() -> None:
    state = replay_updates(_ewma(), EWMA, _stream([10.0, 20.0]), LEARNING).final

    with pytest.raises(AdaptiveOrderingError, match="checkpoint"):
        apply_update(
            state, AdaptiveObservation(100.0, 5, "s", {"price": 1.0}), _ewma(), EWMA, LEARNING
        )
    with pytest.raises(AdaptiveOrderingError):
        apply_update(
            state, AdaptiveObservation(101.0, 1, "s", {"price": 1.0}), _ewma(), EWMA, LEARNING
        )


def test_same_timestamp_observations_are_ordered_by_sequence() -> None:
    state = initial_state(_ewma(), EWMA)
    first = apply_update(
        state, AdaptiveObservation(100.0, 0, "s", {"price": 1.0}), _ewma(), EWMA, LEARNING
    )

    second = apply_update(
        first.state, AdaptiveObservation(100.0, 1, "s", {"price": 3.0}), _ewma(), EWMA, LEARNING
    )

    assert second.state.payload["mean"] == 2.0


def test_a_missing_input_is_refused_and_never_filled() -> None:
    with pytest.raises(AdaptiveStateError, match="not filled"):
        apply_update(
            initial_state(_ewma(), EWMA),
            AdaptiveObservation(1.0, 0, "s", {"volume": 5.0}),
            _ewma(),
            EWMA,
            LEARNING,
        )


def test_a_state_from_another_configuration_is_refused() -> None:
    with pytest.raises(AdaptiveStateError, match="belongs to"):
        apply_update(
            initial_state(_ewma(), EWMA), _stream([1.0])[0], _ewma(warmup=1), EWMA, LEARNING
        )


def test_a_rule_returning_an_unserializable_payload_is_refused() -> None:
    class Broken(ExponentialMeanRule):
        def observe(self, payload: Any, observation: Any, configuration: Any) -> Any:
            return {**payload, "pending_sum": [1.0]}

    with pytest.raises(AdaptiveStateError, match="list"):
        apply_update(
            initial_state(_ewma(), Broken()), _stream([1.0])[0], _ewma(), Broken(), LEARNING
        )


def test_a_state_whose_counters_contradict_each_other_is_refused() -> None:
    state = initial_state(_ewma(), EWMA)

    with pytest.raises(AdaptiveStateError, match="adapted"):
        replace(state, version=1)
    with pytest.raises(AdaptiveStateError, match="exactly when it records"):
        replace(state, observations=3)


# --------------------------------------------------------------------------- #
# Lineage
# --------------------------------------------------------------------------- #


def test_the_same_payload_reached_two_ways_is_two_states() -> None:
    one = replay_updates(_zscore(), ZSCORE, _stream([1.0, 2.0, 3.0, 4.0]), LEARNING).final
    two = replay_updates(_zscore(), ZSCORE, _stream([9.0, 2.0, 3.0, 4.0]), LEARNING).final

    assert one.payload == two.payload, "both windows hold (2, 3, 4)"
    assert one.lineage != two.lineage
    assert one.state_id != two.state_id


# --------------------------------------------------------------------------- #
# Replay, checkpoint, restart, reprocessing
# --------------------------------------------------------------------------- #


def test_a_replay_is_deterministic_and_its_record_holds_together() -> None:
    values = [1.0, 4.0, 2.0, 8.0, 5.0, 7.0]

    first = replay_updates(_zscore(), ZSCORE, _stream(values), LEARNING)
    second = replay_updates(_zscore(), ZSCORE, _stream(values), LEARNING)

    assert first.replay_id == second.replay_id
    assert first.verify()
    assert not replace(first, transitions=first.transitions[::-1]).verify()
    assert not replace(first, observations=5).verify()
    assert not replace(first, stream_id="0" * 64).verify()


def test_a_replay_over_nothing_is_refused() -> None:
    with pytest.raises(AdaptiveStateError, match="reproduces nothing"):
        replay_updates(_ewma(), EWMA, (), LEARNING)


def test_a_checkpoint_survives_json_exactly() -> None:
    state = replay_updates(_zscore(), ZSCORE, _stream([1.0, 4.0, 2.0, 8.0]), LEARNING).final

    written = json.loads(serialize(checkpoint(state)))
    restored = restore(written, _zscore(), ZSCORE)

    assert restored == state
    assert restored.state_id == state.state_id
    assert isinstance(restored.payload["values"], tuple)


@pytest.mark.parametrize(
    ("edit", "match"),
    [
        (lambda c: {**c, "payload": {**c["payload"], "values": [1.0, 2.0, 99.0]}}, "altered"),
        (lambda c: {**c, "observations": c["observations"] + 1}, "altered"),
        (lambda c: {**c, "lineage": "0" * 64}, "altered"),
        (lambda c: {**c, "scheme": "other"}, "written under"),
        (lambda c: {**c, "configuration_id": "other@1"}, "belongs to"),
        (lambda c: {key: value for key, value in c.items() if key != "lineage"}, "exactly"),
        (lambda c: {**c, "payload": {**c["payload"], "values": [1, 2]}}, "not all floats"),
    ],
)
def test_an_altered_or_foreign_checkpoint_is_refused(edit: Any, match: str) -> None:
    state = replay_updates(_zscore(), ZSCORE, _stream([1.0, 4.0, 2.0]), LEARNING).final
    written = json.loads(serialize(checkpoint(state)))

    with pytest.raises(AdaptiveStateError, match=match):
        restore(edit(written), _zscore(), ZSCORE)


def test_restarting_from_a_checkpoint_reproduces_one_uninterrupted_replay() -> None:
    values = [1.0, 4.0, 2.0, 8.0, 5.0, 7.0, 3.0, 6.0]
    whole = replay_updates(_zscore(), ZSCORE, _stream(values), LEARNING)

    for split in range(1, len(values)):
        head = replay_updates(_zscore(), ZSCORE, _stream(values[:split]), LEARNING)
        resumed = restore(json.loads(serialize(checkpoint(head.final))), _zscore(), ZSCORE)
        tail = replay_updates(
            _zscore(), ZSCORE, _stream(values[split:], start=split), LEARNING, initial=resumed
        )
        assert tail.final == whole.final, split
        assert head.decisions + tail.decisions == whole.decisions, split


def test_a_late_observation_is_included_by_replaying_from_before_its_position() -> None:
    """Deterministic reprocessing: the late value refused live, folded in by a replay."""

    on_time = [1.0, 4.0, 2.0]
    checkpointed = replay_updates(_zscore(), ZSCORE, _stream(on_time), LEARNING).final
    late = AdaptiveObservation(101.5, 2, "prices@1:AAA", {"price": 9.0})

    with pytest.raises(AdaptiveOrderingError):
        apply_update(checkpointed, late, _zscore(), ZSCORE, LEARNING)

    head = replay_updates(_zscore(), ZSCORE, _stream(on_time[:2]), LEARNING).final
    corrected = replay_updates(
        _zscore(),
        ZSCORE,
        (late, AdaptiveObservation(102.0, 3, "prices@1:AAA", {"price": 2.0})),
        LEARNING,
        initial=head,
    )
    assert corrected.final.payload["values"] == (4.0, 9.0, 2.0)
    assert corrected.final.state_id != checkpointed.state_id


def test_a_reset_is_a_fresh_initial_state() -> None:
    trained = replay_updates(_ewma(), EWMA, _stream([10.0, 20.0]), LEARNING).final

    reset = initial_state(_ewma(), EWMA)

    assert reset.observations == 0 and reset.payload["count"] == 0
    assert reset.state_id != trained.state_id


# --------------------------------------------------------------------------- #
# The rules' arithmetic
# --------------------------------------------------------------------------- #


def test_the_trailing_zscore_uses_the_one_statistics_authority() -> None:
    result = replay_updates(_zscore(), ZSCORE, _stream([1.0, 4.0, 2.0, 8.0]), LEARNING)
    decision = result.decisions[3]

    window = (1.0, 4.0, 2.0)
    expected = (8.0 - mean(window)) / standard_deviation(window)
    assert decision.outputs["zscore"] == expected
    assert decision.outputs["signal"] == -1.0, "above the mean by more than the entry"


def test_recursive_least_squares_recovers_a_noiseless_line() -> None:
    configuration = _ewma(
        name="hedge",
        rule_id="recursive_least_squares",
        inputs=("target", "regressor"),
        parameters={"forgetting": 1.0, "prior_variance": 1e6},
    )
    rows = [
        (float(index), {"target": 2.0 + 3.0 * x, "regressor": x})
        for index, x in enumerate([0.5, 1.5, -1.0, 2.0, 0.25, 3.0, -2.0, 1.0])
    ]

    result = replay_updates(
        configuration, RLS, observation_stream("pairs", rows, first_sequence=0), LEARNING
    )

    assert result.final.payload["slope"] == pytest.approx(3.0, abs=1e-6)
    assert result.final.payload["intercept"] == pytest.approx(2.0, abs=1e-6)
    last = result.decisions[-1].outputs
    assert last["residual"] == pytest.approx(0.0, abs=1e-5)


def test_recursive_least_squares_holds_back_until_two_pairs() -> None:
    configuration = _ewma(
        name="hedge",
        rule_id="recursive_least_squares",
        inputs=("target", "regressor"),
        parameters={"forgetting": 0.99, "prior_variance": 10.0},
    )
    rows = [(1.0, {"target": 1.0, "regressor": 1.0}), (2.0, {"target": 2.0, "regressor": 2.0})]

    result = replay_updates(
        configuration, RLS, observation_stream("p", rows, first_sequence=0), LEARNING
    )

    assert [d.outputs for d in result.decisions] == [{}, {}]
    assert isinstance(result.final, AdaptiveState)
