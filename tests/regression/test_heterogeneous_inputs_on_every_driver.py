"""External information reaches a strategy the same way on every driver (v4.1.1).

Until v4.1.1 only :meth:`BacktestEngine.run` delivered a point-in-time schedule
(ledger OFE-009). :meth:`ReplayBacktest.run` took none, so the replay of a
strategy acting on news or a macro release was a different run from its
backtest; ``alphalab.api.backtest`` and ``alphalab.api.replay`` took none, so an
application could not hand information to a run through the facade; and
:class:`~alphalab.runtime.live.LiveSession` had no call that delivered one and
routed what it produced. These tests hold each driver to the backtest's rule:
an observation is delivered at its knowledge instant, after every record of
that instant and before its slice closes; one known after the data is not
delivered; nothing is delivered twice.

The workload is :mod:`tests.integration.replay_workload`'s -- two instruments
quoted at every instant, partial fills, a strategy with declared memory -- with
a strategy that also trades on what it is told, so a delivery moved, repeated or
lost moves an order.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.alt_data import ExternalObservation, build_observation_set, delivery_schedule
from alphalab.alt_data.streaming import DeliverySchedule
from alphalab.backtesting import replay_snapshot
from alphalab.backtesting.engine import BacktestEngine, finalize
from alphalab.backtesting.replay import PausedReplay, ReplayBacktest
from alphalab.backtesting.schedule import RunSchedule, ScheduleCursor, ScheduleValidationError
from alphalab.backtesting.state import BacktestResult, ReplayResult
from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.common.ids import id_scope
from alphalab.common.point_in_time import VisibilityRule
from alphalab.execution.costs import FREE
from alphalab.oms.snapshot import capture as capture_oms
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.broker_routing import RoutingConfig
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.live import LiveSession
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, ObservationReceived
from tests.integration import replay_workload as workload
from tests.integration.harness import context_factory, running_strategy_state
from tests.unit.alt_data.pit_harness import SOURCE, observation

PUBLICATION = VisibilityRule.PUBLICATION
SUBJECTS = {"FIRST": workload.FIRST, "SECOND": workload.SECOND}

#: ``(subject, value, known_at)``. Records are quoted at 10.0 to 21.0, two an
#: instant: one is known before the first record, one at a record's own instant,
#: two at one instant between records (ordered by identity), one inside the data
#: and one after it -- which is never delivered.
READINGS = (
    ("FIRST", "1", 9.5),
    ("SECOND", "-1", 12.0),
    ("FIRST", "-1", 14.5),
    ("SECOND", "1", 14.5),
    ("FIRST", "1", 17.0),
    ("SECOND", "-1", 30.0),
)


class InformedStrategy(workload.RememberingStrategy):
    """The workload's strategy, which also takes a position on every reading it is told.

    The readings it has seen are part of its declared state, so a reading
    delivered twice, or lost across a pause, changes both its memory and an order;
    and each order is sized by the slices it has seen, so a reading delivered on
    the wrong side of its instant's slice changes an order too.
    """

    def __init__(self) -> None:
        super().__init__()
        self._readings: list[str] = []

    def on_observation(
        self, context: StrategyContext, event: ObservationReceived
    ) -> Iterable[Intent]:
        record = event.record
        assert isinstance(record, ExternalObservation)
        self._readings.append(f"{event.timestamp}:{event.delivery_id}")
        # Sized by the slices seen as well: a reading delivered after its
        # instant's slice instead of before it is sized differently.
        size = Decimal(1 + (len(self._readings) + len(self._slices)) % 3)
        return (
            Intent(
                strategy_id=workload.STRATEGY_ID,
                instrument=SUBJECTS[event.subject],
                target=size if record.value > 0 else -size,
                timestamp=event.timestamp,
            ),
        )

    def capture_state(self) -> dict[str, Any]:
        return {**super().capture_state(), "readings": list(self._readings)}

    def restore_state(self, payload: Any, version: int) -> None:
        super().restore_state(payload, version)
        self._readings = [str(reading) for reading in payload["readings"]]


def schedule(readings: Iterable[tuple[str, str, float]] = READINGS) -> DeliverySchedule[Any]:
    obs_set = build_observation_set(
        "readings",
        [observation(subject, "signal", value, at - 0.25, at) for subject, value, at in readings],
        SOURCE,
    )
    return delivery_schedule(obs_set, PUBLICATION)


def strategies(instance: InformedStrategy | None = None) -> Any:
    return workload.strategy_state(InformedStrategy() if instance is None else instance)


def objects(instance: InformedStrategy | None = None) -> RunObjects:
    built = workload.config()
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=built.pipeline.sizing_model,
            simulator=built.pipeline.simulator,
            strategies={workload.STRATEGY_ID: InformedStrategy() if instance is None else instance},
            instruments=built.pipeline.instruments,
        ),
        fill_policy=built.fill_policy,
    )


def replayed(observations: Iterable[Any] = ()) -> ReplayResult:
    result = ReplayBacktest.run(
        workload.config(),
        workload.dataset(),
        strategies(),
        context_factory,
        observations=observations,
    )
    assert isinstance(result, ReplayResult)
    return result


def backtested(observations: Iterable[Any] = ()) -> BacktestResult:
    return BacktestEngine.run(
        workload.config(), workload.dataset(), strategies(), context_factory, observations
    )


def economics(result: BacktestResult) -> str:
    """What a run did, without the mode it ran in -- which is all a replay and a backtest
    of the same records may differ in."""

    portfolio = result.state.portfolio
    return serialize(
        {
            "orders": capture_oms(result.state.oms),
            "fills": result.fills,
            "trades": result.trades,
            "positions": {
                str(asset): (position.quantity, position.cost_basis)
                for asset, position in sorted(portfolio.positions.items(), key=lambda kv: kv[0])
            },
            "cash": result.valuation.cash,
            "equity_curve": result.equity_curve,
            "strategy_events": result.state.strategy.events,
            "observations_delivered": result.run.observations_delivered,
            "last_observation": result.run.last_observation,
            "processed": result.records_processed,
        }
    )


def _readings_seen(result: BacktestResult) -> list[str]:
    instance = result.state.strategy.strategies[workload.STRATEGY_ID].instance
    assert isinstance(instance, InformedStrategy)
    return list(instance._readings)


# --------------------------------------------------------------------------- #
# Replay = backtest, with information in the run
# --------------------------------------------------------------------------- #


def test_a_replay_delivers_observations_exactly_as_the_backtest_does() -> None:
    backtest = backtested(schedule())
    replay = replayed(schedule())

    assert economics(replay.backtest) == economics(backtest)
    # Five delivered; the one known at 30.0 is after the last record (21.0).
    assert replay.backtest.run.observations_delivered == 5
    assert [entry.split(":")[0] for entry in _readings_seen(replay.backtest)] == [
        "9.5",
        "12.0",
        "14.5",
        "14.5",
        "17.0",
    ]


def test_the_information_moved_the_run() -> None:
    """Without it the run is another run -- so the parity above is not vacuous."""

    assert economics(replayed(schedule()).backtest) != economics(replayed().backtest)


def test_a_replay_given_no_observations_is_the_replay_it_was() -> None:
    unchanged = ReplayBacktest.run(
        workload.config(), workload.dataset(), strategies(), context_factory
    )
    assert isinstance(unchanged, ReplayResult)
    assert workload.outcome(replayed()) == workload.outcome(unchanged)
    assert replayed().backtest.run.observations_delivered == 0


def test_an_order_decided_on_information_never_fills_before_it_was_known() -> None:
    result = replayed(schedule())
    known = {
        str(order.order_id.value): order.created_at
        for order in result.backtest.state.oms.orders.orders()
    }
    for fill in result.backtest.fills:
        assert fill.filled_at >= known[fill.order_id]
    decided = [at for at in known.values() if at in {9.5, 12.0, 14.5, 17.0}]
    assert decided, "orders were placed at information instants"


# --------------------------------------------------------------------------- #
# Pause and resume, in process and through a payload
# --------------------------------------------------------------------------- #


def _paused(boundary: int, readings: Iterable[tuple[str, str, float]] = READINGS) -> PausedReplay:
    paused = ReplayBacktest.run(
        workload.config(),
        workload.dataset(),
        strategies(),
        context_factory,
        pause_when=workload.pause_at(boundary),
        observations=schedule(readings),
    )
    assert isinstance(paused, PausedReplay)
    return paused


def _through_a_payload(paused: PausedReplay, inputs: RunSchedule) -> PausedReplay:
    payload = serialize(replay_snapshot.capture(paused))
    return replay_snapshot.restore(
        replay_snapshot.from_primitives(deserialize(payload)),
        objects(),
        workload.dataset(),
        inputs,
    )


@pytest.mark.parametrize("boundary", range(workload.TOTAL))
def test_a_replay_paused_anywhere_resumes_into_the_uninterrupted_replay(boundary: int) -> None:
    reference = workload.outcome(replayed(schedule()))
    paused = _paused(boundary)
    taken = paused.records_replayed
    after = None if taken == 0 else workload.dataset().records[taken - 1].timestamp
    pending = ScheduleCursor.start(paused.schedule, after).pending
    assert len(pending) + paused.run.observations_delivered == len(schedule())

    resumed = ReplayBacktest.resume(paused, context_factory)
    assert isinstance(resumed, ReplayResult)
    assert workload.outcome(resumed) == reference


@pytest.mark.parametrize("boundary", range(workload.TOTAL))
def test_a_replay_with_information_outstanding_resumes_from_its_payload(boundary: int) -> None:
    """FUT-003: the payload records the schedule's digest, and loses nothing."""

    reference = workload.outcome(replayed(schedule()))
    paused = _paused(boundary)
    restored = _through_a_payload(paused, RunSchedule.of(observations=schedule()))

    resumed = ReplayBacktest.resume(restored, context_factory)
    assert isinstance(resumed, ReplayResult)
    assert workload.outcome(resumed) == reference


def test_a_pause_at_every_boundary_in_one_chain_changes_nothing() -> None:
    reference = workload.outcome(replayed(schedule()))
    inputs = RunSchedule.of(observations=schedule())
    state: ReplayResult | PausedReplay = _paused(0)
    while isinstance(state, PausedReplay):
        # Each pause written down and read back with fresh objects.
        restored = _through_a_payload(state, inputs)
        state = ReplayBacktest.resume(
            restored, context_factory, pause_when=workload.pause_at(state.records_replayed + 1)
        )
    assert workload.outcome(state) == reference


def test_a_payload_resumed_with_another_schedule_or_none_is_refused() -> None:
    paused = _paused(6)
    payload = deserialize(serialize(replay_snapshot.capture(paused)))
    snapshot = replay_snapshot.from_primitives(payload)
    assert snapshot.schedule_digest == RunSchedule.of(observations=schedule()).digest

    with pytest.raises(StateDecodeError, match="none was supplied"):
        replay_snapshot.restore(snapshot, objects(), workload.dataset())
    other = RunSchedule.of(observations=schedule(READINGS[:-1]))
    with pytest.raises(StateDecodeError, match="was supplied; resuming would apply other"):
        replay_snapshot.restore(snapshot, objects(), workload.dataset(), other)

    plain = ReplayBacktest.run(
        workload.config(),
        workload.dataset(),
        strategies(),
        context_factory,
        pause_when=workload.pause_at(6),
    )
    assert isinstance(plain, PausedReplay)
    bare = replay_snapshot.from_primitives(deserialize(serialize(replay_snapshot.capture(plain))))
    assert bare.schedule_digest is None
    with pytest.raises(StateDecodeError, match="paused with no schedule"):
        replay_snapshot.restore(bare, objects(), workload.dataset(), other)


def test_a_version_1_payload_reads_as_a_replay_given_no_schedule() -> None:
    plain = ReplayBacktest.run(
        workload.config(),
        workload.dataset(),
        strategies(),
        context_factory,
        pause_when=workload.pause_at(6),
    )
    assert isinstance(plain, PausedReplay)
    payload = deserialize(serialize(replay_snapshot.capture(plain)))
    assert payload["schema_version"] == 2
    payload["schema_version"] = 1
    del payload["schedule_digest"]

    assert replay_snapshot.from_primitives(payload).schedule_digest is None


def test_deliveries_known_only_after_the_data_round_trip_and_are_never_made() -> None:
    inside = tuple(reading for reading in READINGS if reading[2] < 15.0)
    beyond = (*inside, ("SECOND", "-1", 30.0), ("FIRST", "1", 99.0))
    reference = replayed(schedule(beyond))
    restored = _through_a_payload(
        _paused(14, beyond), RunSchedule.of(observations=schedule(beyond))
    )
    resumed = ReplayBacktest.resume(restored, context_factory)
    assert isinstance(resumed, ReplayResult)
    assert workload.outcome(resumed) == workload.outcome(reference)
    assert resumed.backtest.run.observations_delivered == len(inside)


# --------------------------------------------------------------------------- #
# A backtest stopped and continued by hand, with the public cursor
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("stop_after", range(1, workload.TOTAL))
def test_a_backtest_continued_from_a_snapshot_delivers_the_rest_exactly_once(
    stop_after: int,
) -> None:
    """The loop :meth:`BacktestEngine.run` takes, written out around a payload.

    ``ScheduleCursor.start`` with the timestamp of the record the run stopped
    after hands the continuation exactly the entries it has not applied.
    """

    config = workload.config()
    records = workload.dataset().records
    inputs = RunSchedule.of(observations=schedule())
    reference = backtested(schedule())
    assert config.seed is not None

    # Built outside the run's scope, as BacktestEngine.run's caller builds it:
    # starting a strategy records lifecycle events, which draw identifiers.
    initial = strategies()
    with id_scope(config.seed):
        state = replace(BacktestEngine.initialize(config, initial), source_id=workload.DATASET_ID)
        cursor = ScheduleCursor.start(inputs)
        for record in records[:stop_after]:
            cursor, state = cursor.before(state, record.timestamp, context_factory)
            state = RunEngine.close_slice(
                state, context_factory, cursor.rates, before=record.timestamp
            )
            state, _ = RunEngine.advance(state, record, context_factory, rates=cursor.rates)
    payload = serialize(capture(state))

    restored = restore(from_primitives(deserialize(payload)), objects())
    cursor = ScheduleCursor.start(inputs, records[stop_after - 1].timestamp)
    with BacktestEngine.resume(restored):
        for record in records[stop_after:]:
            cursor, restored = cursor.before(restored, record.timestamp, context_factory)
            restored = RunEngine.close_slice(
                restored, context_factory, cursor.rates, before=record.timestamp
            )
            restored, _ = RunEngine.advance(restored, record, context_factory, rates=cursor.rates)
        cursor, restored = cursor.finish(restored, context_factory)
        finished = finalize(RunEngine.close_slice(restored, context_factory, cursor.rates))

    assert economics(finished) == economics(reference)
    assert _readings_seen(finished) == _readings_seen(reference)


def test_the_merge_is_the_one_the_backtest_has_always_used() -> None:
    """Any order in, one order out; and the rule for observations is unchanged."""

    first = backtested(schedule())
    second = backtested(list(reversed(schedule().deliveries)))
    assert economics(first) == economics(second)
    inputs = RunSchedule.of(observations=schedule())
    assert [entry.at for entry in inputs.entries] == [9.5, 12.0, 14.5, 14.5, 17.0, 30.0]
    # After the records at 14.0: everything ordered before them was applied.
    assert [entry.at for entry in ScheduleCursor.start(inputs, 14.0).pending] == [
        14.5,
        14.5,
        17.0,
        30.0,
    ]
    # After the records at 12.0: the observation known at 12.0 waits for the
    # instant's records and is delivered when the next instant arrives.
    assert ScheduleCursor.start(inputs, 12.0).pending[0].at == 12.0


def test_one_observation_given_twice_is_refused() -> None:
    (first, *_) = schedule().deliveries
    with pytest.raises(ScheduleValidationError, match="observation"):
        RunSchedule.of(observations=(first, first))
    with pytest.raises(ScheduleValidationError, match="given twice"):
        RunSchedule.of(observations=(first,)).with_observations((first,))


# --------------------------------------------------------------------------- #
# Live: delivered when it arrived, and routed
# --------------------------------------------------------------------------- #


def test_a_live_session_delivers_an_observation_when_it_arrived_and_routes_it() -> None:
    config = workload.config()
    live = RunConfig(
        pipeline=replace(config.pipeline, routing=ExecutionRouting.EXTERNAL),
        mode=ExecutionMode.LIVE,
        seed=7,
        start_timestamp=1.0,
        compile_analytics=False,
    )
    venue = PaperBroker(FREE)
    strategy = InformedStrategy()
    state = LiveSession.initialize(
        live,
        running_strategy_state(workload.STRATEGY_ID, strategy, frozenset({"*", "slices"})),
        BrokerEngine.initialize("VENUE-X", Decimal("1000000"), "USD"),
        RoutingConfig(venue="VENUE-X", currency="USD"),
    )
    state, _ = LiveSession.connect(state, venue, 1.5)
    for record in workload.dataset().records[:2]:
        state, _ = LiveSession.advance(state, record, context_factory, venue)

    # Known at 9.5, but the host received it at 10.5: it is delivered at 10.5.
    delivery = schedule().deliveries[0]
    state, routed, _ = LiveSession.deliver_observation(
        state, delivery, venue, context_factory, now=10.5
    )

    assert strategy._readings == [f"10.5:{delivery.delivery_id}"]
    assert state.run.last_observation == delivery.order_key
    ((attempt,),) = (routed,)
    assert attempt.routed

    # Delivered once: the same delivery again does nothing and sends nothing.
    again, routed_again, _ = LiveSession.deliver_observation(
        state, delivery, venue, context_factory, now=10.6
    )
    assert routed_again == () and again.run == state.run

    # route=False leaves the order working for the caller to route.
    second = schedule().deliveries[1]
    held, nothing, _ = LiveSession.deliver_observation(
        state, second, venue, context_factory, now=12.5, route=False
    )
    assert nothing == ()
    assert held.unrouted_orders
