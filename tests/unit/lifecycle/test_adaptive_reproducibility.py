"""v3.7 reaches the v3.6 contracts without widening them.

An adaptive component's configuration and starting state enter a fingerprint
through the research-settings section it already hashes; a replay of one is
assessed with the same four :class:`RerunOutcome` answers a run gets; and a
study's auxiliary inputs are listed as what a rerun must be given. No
fingerprint key, manifest key or evidence digest moved.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from alphalab.factor_library.definition import FeatureDefinition, FeatureField, FeatureKind
from alphalab.lifecycle import (
    ADAPTIVE_SETTING_PREFIX,
    ExternalInput,
    LifecycleInputError,
    RerunOutcome,
    assess_adaptive_replay,
    canonical_fingerprint_key,
    external_requirements,
    manifest_for_study,
    research_configuration,
    research_configuration_with_adaptive,
)
from alphalab.research import ResearchStudy, build_result
from alphalab.strategy import (
    AdaptationMode,
    AdaptiveConfiguration,
    AdaptiveObservation,
    DecisionTiming,
    ExponentialMeanRule,
    StateValue,
    UpdateCadence,
    initial_state,
    observation_stream,
    replay_updates,
)
from tests.unit.lifecycle.evidence_harness import ENGINE, SYMBOL, fingerprint, ingest

CONFIGURATION = AdaptiveConfiguration(
    name="ewma",
    rule_id="exponential_mean",
    rule_version=1,
    inputs=("price",),
    parameters={"smoothing": 0.5},
    cadence=UpdateCadence.EVERY_OBSERVATION,
    cadence_every=None,
    cadence_seconds=None,
    decision_timing=DecisionTiming.BEFORE_UPDATE,
    warmup=0,
)
RULE = ExponentialMeanRule()


def _stream(values: list[float]) -> tuple[AdaptiveObservation, ...]:
    rows = [(float(index), {"price": value}) for index, value in enumerate(values)]
    return observation_stream("s", rows, first_sequence=0)


# --------------------------------------------------------------------------- #
# Fingerprints
# --------------------------------------------------------------------------- #


def test_adaptive_settings_enter_the_fingerprint_through_research_settings() -> None:
    start = initial_state(CONFIGURATION, RULE)

    research = research_configuration_with_adaptive(
        {"validation": "walk-forward"}, [(CONFIGURATION, start)]
    )

    assert research.settings["validation"] == "walk-forward"
    assert research.settings[f"{ADAPTIVE_SETTING_PREFIX}ewma.configuration"] == (
        CONFIGURATION.configuration_id
    )
    assert research.settings[f"{ADAPTIVE_SETTING_PREFIX}ewma.initial_state"] == start.state_id


def test_a_changed_rule_setting_or_starting_state_is_a_different_strategy() -> None:
    start = initial_state(CONFIGURATION, RULE)
    trained = replay_updates(
        CONFIGURATION, RULE, _stream([1.0, 2.0]), AdaptationMode.LEARNING
    ).final
    slower = replace(CONFIGURATION, parameters={"smoothing": 0.1})

    baseline = fingerprint(
        research=research_configuration_with_adaptive({}, [(CONFIGURATION, start)])
    )
    retuned = fingerprint(
        research=research_configuration_with_adaptive({}, [(slower, initial_state(slower, RULE))])
    )
    pretrained = fingerprint(
        research=research_configuration_with_adaptive({}, [(CONFIGURATION, trained)])
    )

    assert len({baseline.fingerprint, retuned.fingerprint, pretrained.fingerprint}) == 3


def test_the_fingerprint_key_itself_did_not_move() -> None:
    """A non-adaptive strategy renders exactly as it did at v3.6."""

    plain = fingerprint(research=research_configuration({"validation": "x"}))
    key = canonical_fingerprint_key(
        plain.name,
        plain.strategy_id,
        plain.code,
        plain.dependencies,
        plain.parameters,
        plain.research,
        plain.engine,
    )

    assert "adaptive" not in key
    assert key.splitlines()[0] == "alphalab.strategy_fingerprint.v1"


def test_incoherent_adaptive_settings_are_refused() -> None:
    start = initial_state(CONFIGURATION, RULE)
    other = replace(CONFIGURATION, name="other")
    mine = {f"{ADAPTIVE_SETTING_PREFIX}ewma.configuration": "mine"}

    with pytest.raises(LifecycleInputError, match="names no adaptive component"):
        research_configuration_with_adaptive({}, [])
    with pytest.raises(LifecycleInputError, match="also supplied by the caller"):
        research_configuration_with_adaptive(mine, [(CONFIGURATION, start)])
    with pytest.raises(LifecycleInputError, match="named 'ewma'"):
        research_configuration_with_adaptive({}, [(CONFIGURATION, start), (CONFIGURATION, start)])
    with pytest.raises(LifecycleInputError, match="belongs to"):
        research_configuration_with_adaptive({}, [(other, start)])


# --------------------------------------------------------------------------- #
# Replay assessment
# --------------------------------------------------------------------------- #


class _Leaky:
    """A rule that keeps a counter of its own -- exactly what a rule must not do."""

    def __init__(self) -> None:
        self._inner = ExponentialMeanRule()
        self.calls = 0

    @property
    def rule_id(self) -> str:
        return self._inner.rule_id

    @property
    def rule_version(self) -> int:
        return self._inner.rule_version

    def validate(self, configuration: AdaptiveConfiguration) -> None:
        self._inner.validate(configuration)

    def initial(self, configuration: AdaptiveConfiguration) -> Mapping[str, StateValue]:
        return self._inner.initial(configuration)

    def observe(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, StateValue]:
        return self._inner.observe(payload, observation, configuration)

    def adapt(
        self, payload: Mapping[str, StateValue], configuration: AdaptiveConfiguration
    ) -> Mapping[str, StateValue]:
        self.calls += 1
        adapted = dict(self._inner.adapt(payload, configuration))
        mean = adapted["mean"]
        assert isinstance(mean, float)
        adapted["mean"] = mean + self.calls * 1e-9
        return adapted

    def decide(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, float]:
        return self._inner.decide(payload, observation, configuration)


def test_a_rerun_of_the_same_inputs_reproduces() -> None:
    first = replay_updates(CONFIGURATION, RULE, _stream([1.0, 3.0, 2.0]), AdaptationMode.LEARNING)
    again = replay_updates(CONFIGURATION, RULE, _stream([1.0, 3.0, 2.0]), AdaptationMode.LEARNING)

    assessment = assess_adaptive_replay(first, again)

    assert assessment.rerun is RerunOutcome.REPRODUCED
    assert assessment.record_verified
    assert assessment.first_divergence is None


def test_no_rerun_establishes_nothing() -> None:
    first = replay_updates(CONFIGURATION, RULE, _stream([1.0]), AdaptationMode.LEARNING)

    assert assess_adaptive_replay(first).rerun is RerunOutcome.NOT_ATTEMPTED


def test_a_rerun_of_other_inputs_establishes_nothing_either_way() -> None:
    first = replay_updates(CONFIGURATION, RULE, _stream([1.0, 3.0]), AdaptationMode.LEARNING)
    other_stream = replay_updates(CONFIGURATION, RULE, _stream([1.0, 4.0]), AdaptationMode.LEARNING)
    frozen = replay_updates(CONFIGURATION, RULE, _stream([1.0, 3.0]), AdaptationMode.FROZEN)

    streams = assess_adaptive_replay(first, other_stream)
    modes = assess_adaptive_replay(first, frozen)

    assert streams.rerun is RerunOutcome.INPUTS_DIFFER
    assert any(line.startswith("stream:") for line in streams.detail)
    assert modes.rerun is RerunOutcome.INPUTS_DIFFER
    assert any(line.startswith("mode:") for line in modes.detail)


def test_an_impure_rule_diverges_and_says_where() -> None:
    leaky = _Leaky()
    first = replay_updates(CONFIGURATION, leaky, _stream([1.0, 3.0, 2.0]), AdaptationMode.LEARNING)
    second = replay_updates(CONFIGURATION, leaky, _stream([1.0, 3.0, 2.0]), AdaptationMode.LEARNING)

    assessment = assess_adaptive_replay(first, second)

    assert assessment.rerun is RerunOutcome.DIVERGED
    assert assessment.first_divergence == 0


def test_an_altered_record_is_not_evidence() -> None:
    first = replay_updates(CONFIGURATION, RULE, _stream([1.0, 3.0]), AdaptationMode.LEARNING)
    altered = replace(first, transitions=first.transitions[::-1])

    assert not assess_adaptive_replay(altered).record_verified
    with pytest.raises(LifecycleInputError, match="does not hold together"):
        assess_adaptive_replay(altered, first)
    with pytest.raises(LifecycleInputError, match="does not hold together"):
        assess_adaptive_replay(first, altered)


# --------------------------------------------------------------------------- #
# A study's auxiliary inputs are part of what a rerun needs
# --------------------------------------------------------------------------- #


def _study_manifest(inputs: Mapping[str, str]) -> Any:
    dataset = ingest()
    study = ResearchStudy(
        study_name="v37-study",
        dataset_version=dataset.require_provenance().dataset_version,
        universe=(SYMBOL,),
        features=(FeatureDefinition("mom_2", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=2),),
        horizons=(1,),
        inputs=inputs,
    )
    result = build_result(study, {"mom_2.h1.ic_mean": 0.125}, 5.0)
    return manifest_for_study(result, dataset, ENGINE)


def test_a_studys_auxiliary_inputs_are_listed_as_external_requirements() -> None:
    manifest = _study_manifest({"events": "earnings@1", "fundamentals": "fund@2"})

    auxiliary = [
        requirement.detail
        for requirement in external_requirements(manifest)
        if requirement.input is ExternalInput.AUXILIARY_DATA
    ]

    assert auxiliary == [
        "events: earnings@1, supplied exactly as the study named it",
        "fundamentals: fund@2, supplied exactly as the study named it",
    ]


def test_a_study_without_inputs_lists_none_and_its_manifest_commits_to_them() -> None:
    plain = _study_manifest({})
    named = _study_manifest({"events": "earnings@1"})

    assert not any(
        requirement.input is ExternalInput.AUXILIARY_DATA
        for requirement in external_requirements(plain)
    )
    assert plain.configuration_id != named.configuration_id
    assert plain.manifest_id != named.manifest_id
