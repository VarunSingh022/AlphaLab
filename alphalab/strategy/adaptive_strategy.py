"""An adaptive strategy on the execution path, with its state where the run can see it.

:class:`AdaptiveStrategy` is the bridge from :mod:`alphalab.strategy.adaptive`
to a run. A subclass says two things -- which market events become
observations, and what a decision means as intents -- and the base class does
the rest through :func:`~alphalab.strategy.adaptive.apply_update`, the same function
:func:`~alphalab.strategy.adaptive.replay_updates` folds in research. A backtest, a
replay, a paper run and a live run therefore advance the state exactly as a
research replay over the same observations does, and a test in
``tests/unit/strategy/test_adaptive_strategy.py`` holds the two to identical
final states.

Its state is not hidden
-----------------------

The strategy satisfies
:class:`~alphalab.strategy.protocol.StrategyStateProtocol`: ``capture_state``
returns :func:`~alphalab.strategy.adaptive.checkpoint` and ``restore_state``
rebuilds through :func:`~alphalab.strategy.adaptive.restore`, which refuses a
checkpoint whose identity does not recompute. The run snapshot therefore records
the adaptive state -- lineage and all -- and a stopped run resumes with exactly
what it had learned. Because the state's identity commits to every update,
:func:`~alphalab.lifecycle.reproducibility.digest_run` over a run of this
strategy commits to the whole learning history, and a rerun that learned
differently diverges.

What it is not
--------------

Not a runtime and not a scheduler: it reacts to the market events the dispatcher
already routes -- bars, ticks, quotes and trades -- and emits intents, like any
strategy. Observation sequence numbers are the count of observations the state
has consumed, so they continue across a restore and match
:func:`~alphalab.strategy.adaptive.observation_stream` numbering from the same
position.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, Final

from alphalab.strategy.adaptive import (
    AdaptationMode,
    AdaptiveConfiguration,
    AdaptiveDecision,
    AdaptiveObservation,
    AdaptiveRule,
    AdaptiveState,
    apply_update,
    checkpoint,
    initial_state,
    restore,
)
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.exceptions import AdaptiveStateError
from alphalab.strategy.protocol import BaseStrategy

__all__ = ["ADAPTIVE_STRATEGY_STATE_VERSION", "AdaptiveStrategy"]

#: The version of the state shape :class:`AdaptiveStrategy` captures. The
#: strategy's own integer under ADR-0025 decision 7, carried beside the payload.
ADAPTIVE_STRATEGY_STATE_VERSION: Final = 1


class AdaptiveStrategy(BaseStrategy, ABC):
    """A strategy whose quantitative behaviour is an adaptive rule's decisions.

    Args:
        strategy_id: The identity it runs under.
        configuration: What learns, and how.
        rule: The configured rule.
        mode: Learning or frozen.
        initial: The state to start from -- a research-trained checkpoint -- or
            ``None`` for the configuration's initial state.

    Raises:
        AdaptiveStateError: If ``initial`` belongs to another configuration, or
            the rule is not the configured one.
    """

    def __init__(
        self,
        strategy_id: str,
        configuration: AdaptiveConfiguration,
        rule: AdaptiveRule,
        mode: AdaptationMode,
        initial: AdaptiveState | None,
    ) -> None:
        self._strategy_id = strategy_id
        self._configuration = configuration
        self._rule = rule
        self._mode = mode
        start = initial_state(configuration, rule) if initial is None else initial
        if start.configuration_id != configuration.configuration_id:
            raise AdaptiveStateError(
                f"The initial state belongs to {start.configuration_id!r}, not to "
                f"{configuration.configuration_id!r}."
            )
        self._state = start

    @property
    def strategy_id(self) -> str:
        """The identity this strategy runs under."""

        return self._strategy_id

    @property
    def configuration(self) -> AdaptiveConfiguration:
        """What learns, and how."""

        return self._configuration

    @property
    def mode(self) -> AdaptationMode:
        """Learning or frozen."""

        return self._mode

    @property
    def adaptive_state(self) -> AdaptiveState:
        """The current state -- an immutable value, replaced on every observation."""

        return self._state

    @abstractmethod
    def observation_for(self, event: Any, sequence: int) -> AdaptiveObservation | None:
        """The observation a market event carries, or ``None`` to ignore the event.

        ``sequence`` is the number to give it: the count of observations the
        state has consumed, so it continues across a restore.
        """

    @abstractmethod
    def intents_for(
        self, decision: AdaptiveDecision, event: Any, context: StrategyContext
    ) -> Iterable[Intent]:
        """What a decision means as intents. An empty or withheld decision may mean none."""

    def _react(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        observation = self.observation_for(event, self._state.observations)
        if observation is None:
            return ()
        step = apply_update(self._state, observation, self._configuration, self._rule, self._mode)
        self._state = step.state
        return tuple(self.intents_for(step.decision, event, context))

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._react(context, event)

    def on_tick(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._react(context, event)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._react(context, event)

    def on_trade(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._react(context, event)

    def strategy_state_version(self) -> int:
        """:data:`ADAPTIVE_STRATEGY_STATE_VERSION`."""

        return ADAPTIVE_STRATEGY_STATE_VERSION

    def capture_state(self) -> dict[str, Any]:
        """The adaptive state as a checkpoint. A pure read."""

        return checkpoint(self._state)

    def restore_state(self, payload: Any, version: int) -> None:
        """Rebuild the adaptive state from a checkpoint, or refuse.

        Raises:
            AdaptiveStateError: If ``version`` is not one this class wrote, or
                the checkpoint does not restore -- another configuration, or an
                identity that does not recompute.
        """

        if version != ADAPTIVE_STRATEGY_STATE_VERSION:
            raise AdaptiveStateError(
                f"Adaptive strategy state version {version} was not written by this class, "
                f"which writes version {ADAPTIVE_STRATEGY_STATE_VERSION}."
            )
        if not isinstance(payload, dict):
            raise AdaptiveStateError("An adaptive strategy's captured state is a mapping.")
        self._state = restore(payload, self._configuration, self._rule)
