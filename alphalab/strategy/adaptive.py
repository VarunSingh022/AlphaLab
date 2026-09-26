"""Adaptive strategies: state that learns, and a history that replays exactly.

A strategy that updates itself with new data -- an exponentially weighted mean,
a rolling z-score, a recursively estimated hedge ratio -- is the easiest kind of
strategy to make irreproducible. Its behaviour at any moment depends on every
observation it has seen, in the order it saw them, and if that history lives in
a mutable attribute nobody can say, after the fact, why it did what it did.

This module keeps seven things apart, because each answers a different question:

======================== ===================================================
Identity                 the strategy's :class:`~alphalab.lifecycle.fingerprint.StrategyFingerprint`
                         -- unchanged by learning
Configuration            :class:`AdaptiveConfiguration` -- the rule, its
                         parameters, its cadence; immutable, with a derived
                         identity
Observation              :class:`AdaptiveObservation` -- one ordered input,
                         with a derived identity
Adaptive state           :class:`AdaptiveState` -- what has been learned, as
                         an immutable value
Update event             :class:`AdaptiveTransition` -- which observation
                         moved which state to which
Resulting version        :attr:`AdaptiveState.version`, :attr:`AdaptiveState.state_id`
Decision                 :class:`AdaptiveDecision` -- what the state said,
                         and from which state
======================== ===================================================

and :func:`apply_update` is the one function that moves a state forward. It is pure:
the same state, observation, configuration and rule produce the same next state
and decision in any process, which is what makes a live strategy and its
research replay the same computation.

Explicit semantics
------------------

**Initial state** -- :func:`initial_state`, derived from the configuration and
the rule, or a supplied state (a checkpoint) whose identity is recorded.
**Cadence** -- every observation is *observed* (folded into what the rule has
seen); :class:`UpdateCadence` decides when the rule *adapts* (changes what it
has learned): every observation, every ``n``, or once a minimum of event time
has passed -- event time from the observations, never a wall clock.
**Ordering** -- observations are consumed in strictly increasing
``(timestamp, sequence)`` order; one at or before the last is refused, never
reordered. **Timing** -- :class:`DecisionTiming` states whether a decision reads
the state before or after the observation updates it; ``BEFORE_UPDATE`` is the
prequential order in which a model is never evaluated on what it just learned.
**Control** -- :class:`AdaptationMode` ``FROZEN`` consumes observations and
decides without learning: how a state trained in research is evaluated out of
sample, or deployed without further adaptation. **Warmup** -- decisions are
withheld, not zeroed, until the configured number of observations. **Replay**
-- :func:`replay_updates` folds :func:`apply_update` over a stream. **Checkpoint and
restart** -- :func:`checkpoint` renders a state as JSON-safe primitives with its
identity; :func:`restore` rebuilds it and refuses one whose identity does not
recompute. **Reset** -- a new :func:`initial_state`. **Reprocessing** -- a late
observation is included by replaying from a checkpoint before its position.

Lineage, and why the final state commits to the whole history
--------------------------------------------------------------

:attr:`AdaptiveState.lineage` is a hash chain: each step hashes the previous
lineage, the observation's identity, what the step did and the resulting
payload. A state's :attr:`~AdaptiveState.state_id` includes its lineage, so two
states can share a payload and never an identity unless they were reached by
the same observations in the same order. That is also how adaptive state
reaches a reproducibility manifest: a strategy built on
:class:`~alphalab.strategy.adaptive_strategy.AdaptiveStrategy` hands its state
to the run snapshot through
:class:`~alphalab.strategy.protocol.StrategyStateProtocol`, so a run's record --
and :func:`~alphalab.lifecycle.reproducibility.digest_run` -- commits to every
update it made.

Nothing here reads a clock, draws a random number, reads the environment or
calls ``hash()``. A rule is caller code and could; :func:`replay_updates` run twice is
the evidence that it did not, and
:func:`~alphalab.lifecycle.reproducibility.assess_adaptive_replay` compares two.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from itertools import pairwise
from types import MappingProxyType
from typing import Any, Final, Protocol

from alphalab.strategy.exceptions import AdaptiveOrderingError, AdaptiveStateError

__all__ = [
    "ADAPTIVE_CONFIGURATION_SCHEME",
    "ADAPTIVE_OBSERVATION_SCHEME",
    "ADAPTIVE_REPLAY_SCHEME",
    "ADAPTIVE_STATE_SCHEME",
    "AdaptationMode",
    "AdaptiveConfiguration",
    "AdaptiveDecision",
    "AdaptiveObservation",
    "AdaptiveReplay",
    "AdaptiveRule",
    "AdaptiveState",
    "AdaptiveStep",
    "AdaptiveTransition",
    "DecisionTiming",
    "StateValue",
    "TransitionKind",
    "UpdateCadence",
    "apply_update",
    "canonical_configuration_key",
    "checkpoint",
    "initial_state",
    "observation_stream",
    "replay_updates",
    "restore",
]

#: Scheme tags, and the first line of each canonical key. Frozen for the life
#: of the scheme; changing one re-identifies every value derived under it.
ADAPTIVE_CONFIGURATION_SCHEME: Final = "alphalab.adaptive_configuration.v1"
ADAPTIVE_OBSERVATION_SCHEME: Final = "alphalab.adaptive_observation.v1"
ADAPTIVE_STATE_SCHEME: Final = "alphalab.adaptive_state.v1"
ADAPTIVE_REPLAY_SCHEME: Final = "alphalab.adaptive_replay.v1"

#: One value an adaptive state may hold. Deliberately narrow: each type renders
#: into an identity with ``repr`` and survives JSON exactly, which a ``Decimal``
#: (read back as ``str``) or a nested mapping would not.
type StateValue = float | int | str | bool | tuple[float, ...]


def _sha256(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _is_name(value: str) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value[0].isalpha()
        and all(character.isalnum() or character in "_." for character in value)
    )


def _require_number(value: float, what: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AdaptiveStateError(f"{what} is {value!r}; it must be a number.")
    if not math.isfinite(value):
        raise AdaptiveStateError(
            f"{what} is {value!r}. A non-finite value is not equal to itself, so no identity "
            "built on it could ever verify."
        )


class UpdateCadence(Enum):
    """When the rule *adapts* -- changes what it has learned.

    Every observation is observed regardless; the cadence gates adaptation,
    so a rule re-estimated weekly still sees every day.
    """

    #: Adapt after every observation.
    EVERY_OBSERVATION = auto()

    #: Adapt after every ``cadence_every``-th observation.
    EVERY_N_OBSERVATIONS = auto()

    #: Adapt once at least ``cadence_seconds`` of event time have passed since
    #: the last adaptation -- measured between observation timestamps, never
    #: against a clock.
    MINIMUM_INTERVAL = auto()


class DecisionTiming(Enum):
    """Which state a decision reads."""

    #: The state before the observation updates it: decide, then learn. The
    #: prequential order, in which a rule is never evaluated on what it has just
    #: been told.
    BEFORE_UPDATE = auto()

    #: The state after the observation updates it: learn, then decide.
    AFTER_UPDATE = auto()


class AdaptationMode(Enum):
    """Whether observations change the state."""

    #: Observe and adapt under the cadence.
    LEARNING = auto()

    #: Consume observations and decide; learn nothing. The payload and version
    #: do not move, and the position does, so ordering is still enforced.
    FROZEN = auto()


class TransitionKind(Enum):
    """What one observation did to the state."""

    #: Observed, and the rule adapted.
    ADAPTED = auto()

    #: Observed; adaptation held by the cadence.
    OBSERVED = auto()

    #: Consumed in ``FROZEN`` mode: nothing learned.
    FROZEN = auto()


@dataclass(frozen=True, slots=True)
class AdaptiveConfiguration:
    """Everything that decides how an adaptive component learns and decides.

    Attributes:
        name: What the component is called. Part of its identity.
        rule_id: Which rule, as the rule reports it.
        rule_version: The rule's own version of its semantics.
        inputs: The observation values the rule reads, in the order it reads
            them.
        parameters: The rule's numeric parameters. Copied and made read-only.
        cadence: When the rule adapts.
        cadence_every: ``n`` for ``EVERY_N_OBSERVATIONS``; ``None`` otherwise.
        cadence_seconds: The interval for ``MINIMUM_INTERVAL``; ``None``
            otherwise.
        decision_timing: Which state decisions read.
        warmup: Observations whose decisions are withheld. No default: how much
            history a rule needs before it is trusted is the researcher's call.

    Raises:
        AdaptiveStateError: If a field is malformed, or the cadence fields do
            not match the cadence -- a value the cadence does not read would
            still change the identity.
    """

    name: str
    rule_id: str
    rule_version: int
    inputs: tuple[str, ...]
    parameters: Mapping[str, float]
    cadence: UpdateCadence
    cadence_every: int | None
    cadence_seconds: float | None
    decision_timing: DecisionTiming
    warmup: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        if not self.name.strip() or "@" in self.name or "\n" in self.name:
            raise AdaptiveStateError(
                f"An adaptive component is named on one line, without '@': {self.name!r}."
            )
        if not _is_name(self.rule_id):
            raise AdaptiveStateError(f"rule_id {self.rule_id!r} is not an identifier.")
        if isinstance(self.rule_version, bool) or not isinstance(self.rule_version, int):
            raise AdaptiveStateError(f"rule_version {self.rule_version!r} is not an integer.")
        if self.rule_version < 1:
            raise AdaptiveStateError(f"rule_version {self.rule_version} must be at least 1.")
        if not self.inputs:
            raise AdaptiveStateError("An adaptive rule reads at least one input.")
        if len(set(self.inputs)) != len(self.inputs):
            raise AdaptiveStateError(f"The inputs repeat: {list(self.inputs)}.")
        for name in self.inputs:
            if not _is_name(name):
                raise AdaptiveStateError(f"Input {name!r} is not an identifier.")
        for key, value in self.parameters.items():
            if not _is_name(key):
                raise AdaptiveStateError(f"Parameter {key!r} is not an identifier.")
            _require_number(value, f"Parameter {key!r}")
        if isinstance(self.warmup, bool) or not isinstance(self.warmup, int) or self.warmup < 0:
            raise AdaptiveStateError(f"warmup {self.warmup!r} is not a count of observations.")
        self._check_cadence()

    def _check_cadence(self) -> None:
        every, seconds = self.cadence_every, self.cadence_seconds
        if self.cadence is UpdateCadence.EVERY_N_OBSERVATIONS:
            if isinstance(every, bool) or not isinstance(every, int) or every < 1:
                raise AdaptiveStateError(
                    f"EVERY_N_OBSERVATIONS needs cadence_every of at least 1, got {every!r}."
                )
            if seconds is not None:
                raise AdaptiveStateError("EVERY_N_OBSERVATIONS reads no cadence_seconds.")
        elif self.cadence is UpdateCadence.MINIMUM_INTERVAL:
            if seconds is None:
                raise AdaptiveStateError("MINIMUM_INTERVAL needs cadence_seconds.")
            _require_number(seconds, "cadence_seconds")
            if seconds <= 0.0:
                raise AdaptiveStateError(f"cadence_seconds {seconds!r} must be positive.")
            if every is not None:
                raise AdaptiveStateError("MINIMUM_INTERVAL reads no cadence_every.")
        elif every is not None or seconds is not None:
            raise AdaptiveStateError(
                "EVERY_OBSERVATION reads neither cadence_every nor cadence_seconds."
            )

    @property
    def configuration_id(self) -> str:
        """``"<name>@<sha256>"`` over :func:`canonical_configuration_key`."""

        return f"{self.name}@{_sha256([canonical_configuration_key(self)])}"


def canonical_configuration_key(configuration: AdaptiveConfiguration) -> str:
    """Render the canonical key an adaptive configuration's identity is derived from.

    Inputs keep their order -- a regression reads its target and its regressor
    in a stated order -- and parameters are sorted. Every value is rendered with
    ``repr``, so ``10`` and ``10.0`` are two configurations, as v3.6 renders
    them.
    """

    parameters = configuration.parameters
    return "\n".join(
        [
            ADAPTIVE_CONFIGURATION_SCHEME,
            f"name={configuration.name!r}",
            f"rule={configuration.rule_id!r}",
            f"rule_version={configuration.rule_version!r}",
            f"inputs={configuration.inputs!r}",
            f"cadence={configuration.cadence.name}",
            f"cadence_every={configuration.cadence_every!r}",
            f"cadence_seconds={configuration.cadence_seconds!r}",
            f"decision_timing={configuration.decision_timing.name}",
            f"warmup={configuration.warmup!r}",
            "parameters",
            *(f"{key!r}={parameters[key]!r}" for key in sorted(parameters)),
        ]
    )


@dataclass(frozen=True, slots=True)
class AdaptiveObservation:
    """One ordered input to an adaptive component.

    Attributes:
        timestamp: When it became known -- a bar's close, an observation's
            knowledge instant. Event time, never a clock reading.
        sequence: The tie-break between observations at one timestamp, and a
            position in the stream. :func:`observation_stream` and
            :class:`~alphalab.strategy.adaptive_strategy.AdaptiveStrategy` both
            number observations by their position, so a live strategy and its
            replay produce the same identities.
        stream: Which stream the observation came from -- a dataset version and
            a symbol, an observation set. Part of its identity.
        values: Name to value. Copied and made read-only.
        observation_id: The derived identity, computed on construction.

    Raises:
        AdaptiveStateError: If the timestamp or a value is not finite, the
            sequence is negative, the stream is blank, or a name is not an
            identifier.
    """

    timestamp: float
    sequence: int
    stream: str
    values: Mapping[str, float]
    observation_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        _require_number(self.timestamp, "An observation's timestamp")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise AdaptiveStateError(f"sequence {self.sequence!r} is not an integer.")
        if self.sequence < 0:
            raise AdaptiveStateError(f"sequence {self.sequence} is negative.")
        if not self.stream.strip() or "\n" in self.stream:
            raise AdaptiveStateError(f"An observation's stream is one line: {self.stream!r}.")
        for name, value in self.values.items():
            if not _is_name(name):
                raise AdaptiveStateError(f"Observation value {name!r} is not an identifier.")
            _require_number(value, f"Observation value {name!r}")
        object.__setattr__(
            self,
            "observation_id",
            _sha256(
                [
                    ADAPTIVE_OBSERVATION_SCHEME,
                    f"timestamp={self.timestamp!r}",
                    f"sequence={self.sequence!r}",
                    f"stream={self.stream!r}",
                    *(f"{name!r}={self.values[name]!r}" for name in sorted(self.values)),
                ]
            ),
        )


def observation_stream(
    stream: str,
    rows: Iterable[tuple[float, Mapping[str, float]]],
    *,
    first_sequence: int,
) -> tuple[AdaptiveObservation, ...]:
    """Number ``rows`` into observations, in the order given.

    ``first_sequence`` is where numbering starts: ``0`` for a fresh state, or a
    restored state's :attr:`~AdaptiveState.observations` to continue from it.
    """

    return tuple(
        AdaptiveObservation(timestamp, first_sequence + offset, stream, values)
        for offset, (timestamp, values) in enumerate(rows)
    )


def _require_payload(payload: Mapping[str, StateValue], what: str) -> dict[str, StateValue]:
    """Check a payload is made of values that render and serialize exactly."""

    if not isinstance(payload, Mapping):
        raise AdaptiveStateError(f"{what} returned {type(payload).__name__}, not a mapping.")
    checked: dict[str, StateValue] = {}
    for key, value in payload.items():
        if not _is_name(key):
            raise AdaptiveStateError(f"{what} holds a key {key!r} that is not an identifier.")
        if isinstance(value, bool | str):
            checked[key] = value
        elif isinstance(value, int | float):
            _require_number(value, f"{what} value {key!r}")
            checked[key] = value
        elif isinstance(value, tuple):
            for element in value:
                if not isinstance(element, float) or not math.isfinite(element):
                    raise AdaptiveStateError(
                        f"{what} value {key!r} holds {element!r}; a sequence in adaptive state "
                        "holds finite floats only, so it reads back exactly as it was written."
                    )
            checked[key] = value
        else:
            raise AdaptiveStateError(
                f"{what} value {key!r} is {type(value).__name__}. Adaptive state holds floats, "
                "integers, strings, booleans and tuples of floats -- values whose rendering and "
                "JSON form are both exact."
            )
    return checked


def _payload_lines(payload: Mapping[str, StateValue]) -> list[str]:
    return [f"{key!r}={payload[key]!r}" for key in sorted(payload)]


@dataclass(frozen=True, slots=True)
class AdaptiveState:
    """What an adaptive component has learned, as an immutable value.

    Attributes:
        configuration_id: The configuration this state belongs to.
        version: How many adaptations produced it; ``0`` for an initial state.
        observations: How many observations it has consumed.
        payload: What the rule has learned. Read-only.
        last_timestamp: The last observation's timestamp, or ``None``.
        last_sequence: Its sequence, or ``None``.
        last_adapted_at: The timestamp of the last adaptation, or ``None``.
        lineage: The hash chain over every step that produced this state.
        state_id: The derived identity of this exact state -- including how it
            was reached. Computed once, on construction, and never supplied.
    """

    configuration_id: str
    version: int
    observations: int
    payload: Mapping[str, StateValue]
    last_timestamp: float | None
    last_sequence: int | None
    last_adapted_at: float | None
    lineage: str
    state_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payload",
            MappingProxyType(_require_payload(self.payload, "An adaptive state's payload")),
        )
        for name in ("version", "observations"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AdaptiveStateError(f"An adaptive state's {name} is {value!r}.")
        if self.version > self.observations:
            raise AdaptiveStateError(
                f"A state cannot have adapted {self.version} times on {self.observations} "
                "observations."
            )
        positioned = (self.last_timestamp is not None, self.last_sequence is not None)
        if positioned[0] != positioned[1] or positioned[0] != (self.observations > 0):
            raise AdaptiveStateError(
                "A state has consumed observations exactly when it records the last one's "
                f"timestamp and sequence; got observations={self.observations}, "
                f"last_timestamp={self.last_timestamp!r}, last_sequence={self.last_sequence!r}."
            )
        if (self.last_adapted_at is None) != (self.version == 0):
            raise AdaptiveStateError(
                f"A state records when it last adapted exactly when it has adapted; got "
                f"version={self.version} and last_adapted_at={self.last_adapted_at!r}."
            )
        if not self.lineage.strip() or not self.configuration_id.strip():
            raise AdaptiveStateError("An adaptive state names its configuration and lineage.")
        object.__setattr__(
            self,
            "state_id",
            _sha256(
                [
                    ADAPTIVE_STATE_SCHEME,
                    f"configuration={self.configuration_id!r}",
                    f"version={self.version!r}",
                    f"observations={self.observations!r}",
                    f"last_timestamp={self.last_timestamp!r}",
                    f"last_sequence={self.last_sequence!r}",
                    f"last_adapted_at={self.last_adapted_at!r}",
                    f"lineage={self.lineage!r}",
                    "payload",
                    *_payload_lines(self.payload),
                ]
            ),
        )


class AdaptiveRule(Protocol):
    """A deterministic learning rule, in four pure functions.

    ``observe`` folds one observation into what the rule has seen; ``adapt``
    turns what it has seen into what it has learned, when the cadence says so;
    ``decide`` reads a state and an observation and returns named numbers. None
    may read a clock, draw a random number, read the environment or keep state
    of its own: everything it knows is in the payload it is handed.
    """

    @property
    def rule_id(self) -> str: ...

    @property
    def rule_version(self) -> int: ...

    def validate(self, configuration: AdaptiveConfiguration) -> None: ...

    def initial(self, configuration: AdaptiveConfiguration) -> Mapping[str, StateValue]: ...

    def observe(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, StateValue]: ...

    def adapt(
        self, payload: Mapping[str, StateValue], configuration: AdaptiveConfiguration
    ) -> Mapping[str, StateValue]: ...

    def decide(
        self,
        payload: Mapping[str, StateValue],
        observation: AdaptiveObservation,
        configuration: AdaptiveConfiguration,
    ) -> Mapping[str, float]: ...


def _require_rule(rule: AdaptiveRule, configuration: AdaptiveConfiguration) -> None:
    if rule.rule_id != configuration.rule_id or rule.rule_version != configuration.rule_version:
        raise AdaptiveStateError(
            f"The configuration names rule {configuration.rule_id!r} version "
            f"{configuration.rule_version} and was given {rule.rule_id!r} version "
            f"{rule.rule_version}. A rule other than the configured one would learn something "
            "the configuration's identity does not name."
        )


def _genesis(configuration_id: str, payload: Mapping[str, StateValue]) -> str:
    return _sha256(
        [f"{ADAPTIVE_STATE_SCHEME}.genesis", repr(configuration_id), *_payload_lines(payload)]
    )


def initial_state(configuration: AdaptiveConfiguration, rule: AdaptiveRule) -> AdaptiveState:
    """The state a component starts from, derived from its configuration and rule.

    Raises:
        AdaptiveStateError: If the rule is not the configured one, refuses the
            configuration, or returns a payload the state cannot hold.
    """

    _require_rule(rule, configuration)
    rule.validate(configuration)
    payload = _require_payload(rule.initial(configuration), f"{rule.rule_id}.initial")
    identity = configuration.configuration_id
    return AdaptiveState(
        configuration_id=identity,
        version=0,
        observations=0,
        payload=payload,
        last_timestamp=None,
        last_sequence=None,
        last_adapted_at=None,
        lineage=_genesis(identity, payload),
    )


@dataclass(frozen=True, slots=True)
class AdaptiveDecision:
    """What a state decided on one observation.

    Attributes:
        observation_id: The observation decided on.
        state_id: The state the decision read -- before or after the update,
            as the configuration's timing states.
        outputs: Name to number. Empty when withheld, or when the rule could
            not yet decide; never a stand-in zero.
        withheld: Whether the decision fell inside the warmup.
    """

    observation_id: str
    state_id: str
    outputs: Mapping[str, float]
    withheld: bool


@dataclass(frozen=True, slots=True)
class AdaptiveTransition:
    """One update event: which observation moved which state to which.

    Attributes:
        observation_id: The observation consumed.
        kind: What it did.
        before: The state it was applied to.
        after: The state it produced.
    """

    observation_id: str
    kind: TransitionKind
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class AdaptiveStep:
    """The result of advancing a state by one observation."""

    state: AdaptiveState
    transition: AdaptiveTransition
    decision: AdaptiveDecision


def _adapts(configuration: AdaptiveConfiguration, state: AdaptiveState, at: float) -> bool:
    if configuration.cadence is UpdateCadence.EVERY_OBSERVATION:
        return True
    if configuration.cadence is UpdateCadence.EVERY_N_OBSERVATIONS:
        every = configuration.cadence_every
        return every is not None and (state.observations + 1) % every == 0
    seconds = configuration.cadence_seconds
    return seconds is not None and (
        state.last_adapted_at is None or at - state.last_adapted_at >= seconds
    )


def _require_outputs(outputs: Mapping[str, float], rule_id: str) -> dict[str, float]:
    checked: dict[str, float] = {}
    for key, value in outputs.items():
        if not _is_name(key):
            raise AdaptiveStateError(f"{rule_id}.decide returned a key {key!r}.")
        _require_number(value, f"{rule_id}.decide output {key!r}")
        checked[key] = float(value)
    return checked


def apply_update(
    state: AdaptiveState,
    observation: AdaptiveObservation,
    configuration: AdaptiveConfiguration,
    rule: AdaptiveRule,
    mode: AdaptationMode,
) -> AdaptiveStep:
    """Move ``state`` forward by one observation. Pure and deterministic.

    Raises:
        AdaptiveStateError: If the state belongs to another configuration, the
            rule is not the configured one, the observation lacks an input the
            rule reads, or the rule returns something the state cannot hold.
        AdaptiveOrderingError: If the observation is not strictly after the
            last one the state consumed.
    """

    if state.configuration_id != configuration.configuration_id:
        raise AdaptiveStateError(
            f"The state belongs to {state.configuration_id!r} and the configuration is "
            f"{configuration.configuration_id!r}."
        )
    _require_rule(rule, configuration)
    missing = [name for name in configuration.inputs if name not in observation.values]
    if missing:
        raise AdaptiveStateError(
            f"Observation {observation.sequence} at {observation.timestamp!r} lacks {missing}, "
            "which the rule reads. A missing input is not zero, and is not filled."
        )
    last = (
        None
        if state.last_timestamp is None or state.last_sequence is None
        else (state.last_timestamp, state.last_sequence)
    )
    if last is not None and (observation.timestamp, observation.sequence) <= last:
        raise AdaptiveOrderingError(
            f"Observation ({observation.timestamp!r}, {observation.sequence}) is not after "
            f"the state's last ({state.last_timestamp!r}, {state.last_sequence}). Include a "
            "late observation by replaying from a checkpoint before its position."
        )

    withheld = state.observations + 1 <= configuration.warmup
    outputs: dict[str, float] = {}
    decided_from = state.state_id
    if not withheld and configuration.decision_timing is DecisionTiming.BEFORE_UPDATE:
        outputs = _require_outputs(
            rule.decide(state.payload, observation, configuration), rule.rule_id
        )

    version = state.version
    adapted_at = state.last_adapted_at
    if mode is AdaptationMode.FROZEN:
        kind = TransitionKind.FROZEN
        payload: Mapping[str, StateValue] = state.payload
    else:
        payload = _require_payload(
            rule.observe(state.payload, observation, configuration), f"{rule.rule_id}.observe"
        )
        if _adapts(configuration, state, observation.timestamp):
            payload = _require_payload(rule.adapt(payload, configuration), f"{rule.rule_id}.adapt")
            kind = TransitionKind.ADAPTED
            version += 1
            adapted_at = observation.timestamp
        else:
            kind = TransitionKind.OBSERVED

    after = AdaptiveState(
        configuration_id=state.configuration_id,
        version=version,
        observations=state.observations + 1,
        payload=payload,
        last_timestamp=observation.timestamp,
        last_sequence=observation.sequence,
        last_adapted_at=adapted_at,
        lineage=_sha256(
            [
                f"{ADAPTIVE_STATE_SCHEME}.step",
                repr(state.lineage),
                repr(observation.observation_id),
                kind.name,
                *_payload_lines(payload),
            ]
        ),
    )
    if not withheld and configuration.decision_timing is DecisionTiming.AFTER_UPDATE:
        outputs = _require_outputs(
            rule.decide(after.payload, observation, configuration), rule.rule_id
        )
        decided_from = after.state_id
    return AdaptiveStep(
        state=after,
        transition=AdaptiveTransition(
            observation.observation_id, kind, state.state_id, after.state_id
        ),
        decision=AdaptiveDecision(
            observation_id=observation.observation_id,
            state_id=decided_from,
            outputs=MappingProxyType(outputs),
            withheld=withheld,
        ),
    )


@dataclass(frozen=True, slots=True)
class AdaptiveReplay:
    """A component folded over a stream: every update, every decision, the result.

    Attributes:
        configuration: The configuration replayed.
        mode: Learning or frozen.
        initial: The state the replay started from.
        stream_id: SHA-256 over every observation's identity, in order.
        observations: How many were consumed.
        transitions: One update event per observation.
        decisions: One decision per observation.
        final: The state after the last.
    """

    configuration: AdaptiveConfiguration
    mode: AdaptationMode
    initial: AdaptiveState
    stream_id: str
    observations: int
    transitions: tuple[AdaptiveTransition, ...]
    decisions: tuple[AdaptiveDecision, ...]
    final: AdaptiveState

    @property
    def replay_id(self) -> str:
        """SHA-256 over the inputs, every decision and the final state."""

        return _sha256(
            [
                ADAPTIVE_REPLAY_SCHEME,
                f"configuration={self.configuration.configuration_id!r}",
                f"mode={self.mode.name}",
                f"initial={self.initial.state_id!r}",
                f"stream={self.stream_id!r}",
                f"observations={self.observations!r}",
                f"final={self.final.state_id!r}",
                "decisions",
                *(
                    f"{decision.observation_id!r}:{decision.state_id!r}:{decision.withheld!r}:"
                    + ",".join(
                        f"{key!r}={decision.outputs[key]!r}" for key in sorted(decision.outputs)
                    )
                    for decision in self.decisions
                ),
            ]
        )

    def verify(self) -> bool:
        """Whether the replay's record holds together without re-running the rule.

        The transitions must chain -- the first from :attr:`initial`, each from
        where the last ended, the last into :attr:`final` -- every decision must
        belong to its transition's observation, the counts must agree, and the
        stream identity must recompute from the observations the transitions
        name. A record edited anywhere fails at least one of these.
        """

        if not (len(self.transitions) == len(self.decisions) == self.observations):
            return False
        if not self.transitions:
            return self.initial == self.final
        if self.transitions[0].before != self.initial.state_id:
            return False
        if self.transitions[-1].after != self.final.state_id:
            return False
        if any(earlier.after != later.before for earlier, later in pairwise(self.transitions)):
            return False
        if any(
            decision.observation_id != transition.observation_id
            for decision, transition in zip(self.decisions, self.transitions, strict=True)
        ):
            return False
        expected = _stream_id(transition.observation_id for transition in self.transitions)
        return (
            expected == self.stream_id
            and self.final.observations == self.initial.observations + self.observations
        )


def _stream_id(observation_ids: Iterable[str]) -> str:
    return _sha256([f"{ADAPTIVE_REPLAY_SCHEME}.stream", *observation_ids])


def replay_updates(
    configuration: AdaptiveConfiguration,
    rule: AdaptiveRule,
    observations: Iterable[AdaptiveObservation],
    mode: AdaptationMode,
    initial: AdaptiveState | None = None,
) -> AdaptiveReplay:
    """Fold :func:`apply_update` over ``observations``, recording every step.

    Args:
        configuration: What learns, and how.
        rule: The configured rule.
        observations: The stream, in order.
        mode: Learning or frozen.
        initial: A state to start from -- a checkpoint -- or ``None`` for
            :func:`initial_state`.

    Raises:
        AdaptiveStateError: If there are no observations -- a replay of nothing
            reproduces nothing -- or for every reason :func:`apply_update` refuses.
        AdaptiveOrderingError: If the stream is out of order.
    """

    start = initial_state(configuration, rule) if initial is None else initial
    state = start
    transitions: list[AdaptiveTransition] = []
    decisions: list[AdaptiveDecision] = []
    for observation in observations:
        step = apply_update(state, observation, configuration, rule, mode)
        state = step.state
        transitions.append(step.transition)
        decisions.append(step.decision)
    if not transitions:
        raise AdaptiveStateError("A replay over no observations reproduces nothing.")
    return AdaptiveReplay(
        configuration=configuration,
        mode=mode,
        initial=start,
        stream_id=_stream_id(transition.observation_id for transition in transitions),
        observations=len(transitions),
        transitions=tuple(transitions),
        decisions=tuple(decisions),
        final=state,
    )


#: The keys a checkpoint carries, exactly.
_CHECKPOINT_KEYS: Final = frozenset(
    {
        "scheme",
        "configuration_id",
        "version",
        "observations",
        "payload",
        "last_timestamp",
        "last_sequence",
        "last_adapted_at",
        "lineage",
        "state_id",
    }
)


def checkpoint(state: AdaptiveState) -> dict[str, Any]:
    """A state as JSON-safe primitives, with its identity.

    What :class:`~alphalab.strategy.adaptive_strategy.AdaptiveStrategy` hands
    the run snapshot, and what a caller writes to storage to restart from. A
    tuple is written as a list; :func:`restore` reads it back as a tuple.
    """

    return {
        "scheme": ADAPTIVE_STATE_SCHEME,
        "configuration_id": state.configuration_id,
        "version": state.version,
        "observations": state.observations,
        "payload": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in sorted(state.payload.items())
        },
        "last_timestamp": state.last_timestamp,
        "last_sequence": state.last_sequence,
        "last_adapted_at": state.last_adapted_at,
        "lineage": state.lineage,
        "state_id": state.state_id,
    }


def _optional_number(value: Any, what: str) -> float | None:
    if value is None:
        return None
    _require_number(value, what)
    return float(value)


def _restore_value(key: str, value: Any) -> StateValue:
    if isinstance(value, list):
        if not all(isinstance(element, float) for element in value):
            raise AdaptiveStateError(
                f"Checkpoint payload {key!r} holds a list that is not all floats."
            )
        return tuple(value)
    if isinstance(value, bool | str | int | float):
        return value
    raise AdaptiveStateError(f"Checkpoint payload {key!r} holds {type(value).__name__}.")


def restore(
    payload: Mapping[str, Any], configuration: AdaptiveConfiguration, rule: AdaptiveRule
) -> AdaptiveState:
    """Rebuild a state from :func:`checkpoint`'s primitives, or refuse.

    The recorded identity is recomputed from the restored fields and compared,
    so a checkpoint edited anywhere -- a learned value, a counter, the lineage --
    is refused rather than resumed from.

    Raises:
        AdaptiveStateError: If the checkpoint's keys, scheme or types are not
            what :func:`checkpoint` writes; if it belongs to another
            configuration; if its identity does not recompute; or if the rule is
            not the configured one or refuses the configuration.
    """

    _require_rule(rule, configuration)
    rule.validate(configuration)
    if set(payload) != _CHECKPOINT_KEYS:
        raise AdaptiveStateError(
            f"A checkpoint carries exactly {sorted(_CHECKPOINT_KEYS)}; got {sorted(payload)}."
        )
    if payload["scheme"] != ADAPTIVE_STATE_SCHEME:
        raise AdaptiveStateError(
            f"The checkpoint was written under {payload['scheme']!r}, not "
            f"{ADAPTIVE_STATE_SCHEME!r}."
        )
    if payload["configuration_id"] != configuration.configuration_id:
        raise AdaptiveStateError(
            f"The checkpoint belongs to {payload['configuration_id']!r}, not to "
            f"{configuration.configuration_id!r}."
        )
    learned = payload["payload"]
    if not isinstance(learned, Mapping):
        raise AdaptiveStateError("The checkpoint's payload is not a mapping.")
    for name in ("version", "observations"):
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AdaptiveStateError(f"The checkpoint's {name} is {value!r}.")
    sequence = payload["last_sequence"]
    if sequence is not None and (isinstance(sequence, bool) or not isinstance(sequence, int)):
        raise AdaptiveStateError(f"The checkpoint's last_sequence is {sequence!r}.")
    lineage = payload["lineage"]
    if not isinstance(lineage, str):
        raise AdaptiveStateError("The checkpoint's lineage is not a string.")
    state = AdaptiveState(
        configuration_id=configuration.configuration_id,
        version=payload["version"],
        observations=payload["observations"],
        payload={str(key): _restore_value(str(key), value) for key, value in learned.items()},
        last_timestamp=_optional_number(payload["last_timestamp"], "last_timestamp"),
        last_sequence=sequence,
        last_adapted_at=_optional_number(payload["last_adapted_at"], "last_adapted_at"),
        lineage=lineage,
    )
    if state.state_id != payload["state_id"]:
        raise AdaptiveStateError(
            f"The checkpoint records state {payload['state_id']!r} and its contents derive "
            f"{state.state_id!r}. It was altered after it was written; resuming from it would "
            "continue a history that never happened."
        )
    return state
