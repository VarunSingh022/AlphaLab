"""S-13's experiment: an event-driven strategy over alternative data with vintages (ADR-0073).

A researcher's strategy trades surprises -- observations of an event, each
delivered at the instant it was published and never before -- in their
direction, for a window of instants after each. Its event-window variants are
the experiment's candidates, validated walk-forward over backtests in a ledger.

One surprise is revised late: the event at 24 is published at 24.5 and its
second vintage at 47.5, long after the strategy traded the first. The workload
is built twice, the revision flipping the surprise's sign or confirming it, so
a test can hold every decision taken before 47.5 to be the same in both.

Every figure is synthetic: the prices are made to drift each surprise's way
for three instants after it is published. Nothing here says that surprises
predict returns; the scenario proves that the components compose, and that
the ledger keeps their time semantics.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from alphalab.alt_data import ExternalObservation, ObservationSet, build_observation_set
from alphalab.alt_data import delivery_schedule as _delivery_schedule
from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
)
from alphalab.backtesting.schedule import RunSchedule
from alphalab.common.point_in_time import VisibilityRule
from alphalab.lifecycle.designs import WalkForward
from alphalab.lifecycle.experiments import (
    ExperimentSpec,
    RunnerRegistry,
    TrialInputs,
    TrialLimits,
    TrialSpec,
    dataset_identity,
)
from alphalab.research.selection import SelectionMeasure
from alphalab.research.walk_forward import WindowMode
from alphalab.research.walk_forward_optimization import ParameterSpace, Refit
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.retention import RetentionPolicy
from alphalab.strategy import BaseStrategy, ObservationReceived
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration.harness import (
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)
from tests.unit.alt_data.pit_harness import SOURCE, observation

STRATEGY_ID = "event-window"
ASSET_ID = "3f2b8a61-5c4d-4e7f-9a1b-2c3d4e5f6a7b"
SIZE = Decimal(10)

#: Each surprise: the instant it describes and its first value. Each is
#: published half a second later.
EVENTS = (
    (4.0, "2.0"),
    (9.0, "-1.0"),
    (14.0, "0.8"),
    (19.0, "-2.5"),
    (24.0, "1.2"),
    (29.0, "-0.6"),
    (34.0, "3.0"),
    (39.0, "-1.8"),
    (44.0, "0.7"),
    (49.0, "2.2"),
    (54.0, "-1.1"),
)
#: The late revision: the surprise at 24 gets a second vintage at 47.5.
REVISED_EVENT = 24.0
REVISED_AT = 47.5
#: The two second vintages: one flips the surprise's sign, one confirms it.
FLIPPED = "-3.0"
CONFIRMED = "1.4"


def surprises(revision: str) -> ObservationSet[ExternalObservation]:
    """Every surprise's first vintage, and the late second vintage of the one at 24."""

    records = [
        observation("ACME", "surprise", value, at, at + 0.5, category="event")
        for at, value in EVENTS
    ]
    records.append(
        observation(
            "ACME", "surprise", revision, REVISED_EVENT, REVISED_AT, revision=1, category="event"
        )
    )
    return build_observation_set("surprises", records, SOURCE)


def deliveries(revision: str) -> tuple[ObservationDelivery[ExternalObservation], ...]:
    """Each vintage at the instant it was published."""

    return tuple(_delivery_schedule(surprises(revision), VisibilityRule.PUBLICATION))


def _mids() -> list[Decimal]:
    """Sixty mids, at 2 to 61: each surprise moves the price its way for three instants."""

    mids: list[Decimal] = []
    price = Decimal(100)
    for t in range(2, 62):
        drift = sum(
            (Decimal(value) * Decimal("0.4") for at, value in EVENTS if at < t <= at + 3),
            Decimal(0),
        )
        price += drift + (Decimal("0.15") if t % 2 == 0 else Decimal("-0.15"))
        mids.append(price)
    return mids


DATASET = dataset_of_quotes(ASSET_ID, _mids(), dataset_id="S13")


@dataclass(frozen=True)
class EventPlan:
    """An event-window variant: how long a surprise is held, and how large one must be."""

    window: int
    threshold: float


CANDIDATES = [
    {"window": window, "threshold": threshold}
    for window in (1, 2, 3, 5)
    for threshold in (0.5, 1.5)
]


@dataclass(frozen=True)
class Decision:
    """One trade and what it was decided on: the evidence of its timing.

    Attributes:
        at: The instant it was decided.
        record_id: The observation it acted on, or ``""`` for a window's end.
        delta: The signed quantity traded.
    """

    at: float
    record_id: str
    delta: Decimal


class EventWindow(BaseStrategy):
    """Trades each surprise its way for ``window`` instants after it is known.

    * **A surprise.** A first vintage at least ``threshold`` in size, or a
      later vintage that flips the sign of the one it revises.
    * **A position.** ``SIZE`` the surprise's way, held for ``window`` quotes.
    * **History.** A vintage delivered before the strategy has seen a quote --
      published before its run began -- is known and never traded.

    It keeps every vintage it has been told, and its decisions.
    """

    def __init__(self, window: int, threshold: Decimal) -> None:
        self._window = window
        self._threshold = threshold
        self._known: dict[float, Decimal] = {}
        self._position = Decimal(0)
        self._remaining = 0
        self._trading = False
        self.decisions: list[Decision] = []

    @property
    def known(self) -> Mapping[float, Decimal]:
        """Each event's instant and the latest value it has been told, history included."""

        return dict(self._known)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self._trading = True
        at = event.quote.timestamp
        if self._remaining == 0:
            return ()
        self._remaining -= 1
        if self._remaining > 0 or self._position == 0:
            return ()
        return self._trade(at, "", -self._position)

    def on_observation(
        self, context: StrategyContext, event: ObservationReceived
    ) -> Iterable[Intent]:
        record = event.record
        if not isinstance(record, ExternalObservation) or record.metric != "surprise":
            return ()
        event_at = record.stamp.observed_at
        previous = self._known.get(event_at)
        self._known[event_at] = record.value
        if not self._trading:
            return ()
        if previous is None:
            surprise = record.value if abs(record.value) >= self._threshold else Decimal(0)
        else:
            surprise = record.value if (record.value > 0) != (previous > 0) else Decimal(0)
        if surprise == 0:
            return ()
        target = SIZE if surprise > 0 else -SIZE
        self._remaining = self._window
        return self._trade(event.timestamp, record.record_id, target - self._position)

    def _trade(self, at: float, record_id: str, delta: Decimal) -> Iterable[Intent]:
        if delta == 0:
            return ()
        self._position += delta
        self.decisions.append(Decision(at, record_id, delta))
        return (Intent(strategy_id=STRATEGY_ID, instrument=ASSET_ID, target=delta, timestamp=at),)


def runner(revision: str, built: dict[str, EventWindow] | None = None) -> Any:
    """S-13's runner over the surprises with ``revision`` as the late vintage.

    ``built`` collects each trial's strategy by its ``trial_id``, so a test can
    read its decisions after a sequential run.
    """

    schedule = RunSchedule.of(observations=deliveries(revision))

    def build(trial: TrialSpec) -> TrialInputs:
        plan: EventPlan = trial.configuration
        strategy = EventWindow(plan.window, Decimal(str(plan.threshold)))
        if built is not None:
            built[trial.trial_id] = strategy
        return TrialInputs(
            config=backtest_config(STRATEGY_ID),
            dataset=DATASET,
            strategy_state=running_strategy_state(STRATEGY_ID, strategy),
            context_factory=context_factory,
            schedule=schedule,
            observations=(),
        )

    return build


def registry(revision: str, built: dict[str, EventWindow] | None = None) -> RunnerRegistry:
    """A registry holding S-13's runner as ``"event-window"``."""

    return RunnerRegistry().register(STRATEGY_ID, runner(revision, built))


def spec(revision: str) -> ExperimentSpec:
    """S-13's experiment: every event-window variant, walk-forward, three folds of 20/10/10."""

    config = backtest_config(STRATEGY_ID)
    return ExperimentSpec(
        name="s13-event-window",
        hypothesis="a surprise moves the price its way for a few instants after it is published",
        strategy="event-window@" + "e1" * 32,
        configuration_type=EventPlan,
        space=ParameterSpace.of(CANDIDATES),
        dataset=dataset_identity(DATASET),
        schedule=RunSchedule.of(observations=deliveries(revision)).binding,
        instruments=None,
        execution=execution_assumptions(config.pipeline, config.fill_policy),
        capture=CaptureSpec(
            ReturnExtractor(
                EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.TRUNCATE
            ),
            trace=True,
        ),
        retention=RetentionPolicy(market_history=24, steps=10, audit_events=64, results=64),
        validation=WalkForward(
            train=20,
            validation=10,
            test=10,
            mode=WindowMode.ROLLING,
            step=None,
            warm_up=5,
            refit=Refit.TRAIN,
            measure=SelectionMeasure.MEAN,
            purge=None,
            lockbox=None,
        ),
        seed=20261116,
        parent_id=None,
        limits=TrialLimits(None),
    )
