"""A live run keeps what it asked the venue and what its algorithms sent -- across a restart.

Three v3.11 changes to :mod:`alphalab.runtime.live`, each proved through the
live session itself:

* **BRK-003.** The request ledger (every cancel and amendment issued) is held on
  :class:`~alphalab.runtime.live.LiveRunState` and persisted with the live
  envelope at schema 2, so a restarted run still recognises a retried request.
  The execution-algorithm bindings are held there too and are rebuilt exactly
  from the mirror on restore -- one home for one fact.
* **A parent worked in children is at the venue.** Until v3.11 the bindings were
  the caller's, :attr:`~alphalab.runtime.live.LiveRunState.unrouted_orders`
  could not see them, and the next :meth:`~alphalab.runtime.live.LiveSession.advance`
  would have sent the whole parent as well.
* **EXE-009.** :meth:`~alphalab.runtime.live.LiveSession.settle` and
  :meth:`~alphalab.runtime.live.LiveSession.advance` take the FX table, so a
  multi-currency live run settles a fill in a foreign currency.

The venue is a deterministic stand-in at the external boundary that answers
nothing synchronously -- a real venue confirms a cancel or an amendment later,
with an event -- and records every request that reached it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation.sizing import FixedQuantitySizing
from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.broker.events import BrokerEvent, OrderSubmitted
from alphalab.broker.lifecycle import VenueEvent, apply_venue_event
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.requests import (
    CancelRequest,
    ModifyRequest,
    RequestLedger,
    RequestOutcome,
)
from alphalab.broker.state import BrokerState
from alphalab.broker.validation import validate_order_submission
from alphalab.core.enums import AssetType, OrderType
from alphalab.core.lifecycle import ExecutionEventKind as E
from alphalab.execution.algorithms import ChildOrder
from alphalab.execution.costs import FREE
from alphalab.execution.policy import ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.market.record import MarketRecord
from alphalab.oms.order import Order as OMSOrder
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.account import Account
from alphalab.portfolio.fx import NO_RATES, FxRate, FxRates, MissingRateError
from alphalab.runtime.broker_routing import (
    ChildOrderBindings,
    RoutingConfig,
    RoutingRefusal,
    child_broker_order_id,
)
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionRouting
from alphalab.runtime.live import LiveRunState, LiveSession
from alphalab.runtime.live_snapshot import capture, from_primitives, restore
from alphalab.runtime.run import ExecutionMode, RunConfig
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    quote,
    registry_of,
    running_strategy_state,
    sized_quote,
)

_STRATEGY = "LIVE-REQ"
_ASSET = "5d41402a-bc4b-4a76-b971-9d911017c592"
_ROUTING = RoutingConfig(venue="VENUE-X", currency="USD")


@dataclass
class _Venue(PaperBroker):
    """Takes a submission as ``SUBMITTED`` and answers every request later.

    ``sent`` is the venue's own record of what reached it -- the thing a
    duplicate request would add to.
    """

    sent: list[tuple[str, str]] = field(default_factory=list)

    def __init__(self) -> None:
        super().__init__(FREE)
        self.sent = []

    def submit_order(
        self, state: BrokerState, order: BrokerOrder, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        validate_order_submission(state, order)
        self.sent.append(("submit", order.broker_order_id))
        submitted = replace(order, status=BrokerOrderStatus.SUBMITTED, updated_at=timestamp)
        event = OrderSubmitted(
            f"EV-{len(self.sent)}", timestamp, order.broker_order_id, order.oms_order_id
        )
        return (
            replace(
                state,
                orders=state.orders.set(order.broker_order_id, submitted),
                events=state.events.append(event),
            ),
            (event,),
        )

    def cancel_order(
        self, state: BrokerState, broker_order_id: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        self.sent.append(("cancel", broker_order_id))
        return state, ()

    def replace_order(
        self,
        state: BrokerState,
        broker_order_id: str,
        new_quantity: Decimal,
        new_price: Decimal,
        timestamp: float,
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        self.sent.append(("replace", broker_order_id))
        return state, ()


def _config() -> RunConfig:
    return RunConfig(
        pipeline=replace(pipeline_config(_STRATEGY), routing=ExecutionRouting.EXTERNAL),
        mode=ExecutionMode.LIVE,
        seed=4242,
        start_timestamp=1.0,
        compile_analytics=False,
    )


def _strategy() -> ScriptedStrategy:
    return ScriptedStrategy(_STRATEGY, _ASSET, {2.0: Decimal("10")})


def _objects() -> RunObjects:
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=FixedQuantitySizing(),
            simulator=ExecutionSimulator(),
            strategies={_STRATEGY: _strategy()},
        ),
        fill_policy=ImmediateFill(),
    )


def _record(at: float) -> MarketRecord:
    return MarketRecord("DS-LIVE", at, sized_quote(_ASSET, at, Decimal("100"), Decimal("1000")))


def _started(venue: _Venue) -> LiveRunState:
    state = LiveSession.initialize(
        _config(),
        running_strategy_state(_STRATEGY, _strategy()),
        BrokerEngine.initialize("VENUE-X", Decimal("1000000"), "USD"),
        _ROUTING,
    )
    state, _ = LiveSession.connect(state, venue, 1.5)
    return state


def _round_trip(state: LiveRunState) -> LiveRunState:
    """Through JSON text and back, as a restarted process reads it."""

    return restore(from_primitives(deserialize(serialize(capture(state)))), _objects())


def _with_an_order_at_the_venue(venue: _Venue) -> tuple[LiveRunState, str]:
    state, step = LiveSession.advance(_started(venue), _record(2.0), context_factory, venue)
    (routed,) = step.routed
    assert routed.broker_order_id is not None
    return state, routed.broker_order_id


# --------------------------------------------------------------------------- #
# BRK-003: the request ledger
# --------------------------------------------------------------------------- #


def test_an_amendment_reaches_the_venue_once_however_it_is_spelled_on_retry() -> None:
    venue = _Venue()
    state, handle = _with_an_order_at_the_venue(venue)

    amend = ModifyRequest(handle, 1, Decimal("12"), Decimal("101"), 3.0)
    state, decided, _ = LiveSession.amend(state, venue, amend)
    assert decided.outcome is RequestOutcome.NEW
    # The same request, retried after a timeout with the amounts spelled
    # differently. Until v3.11 (DET-006) its identity changed with the spelling
    # and the ledger refused it as a reused revision.
    retry = ModifyRequest(handle, 1, Decimal("12.0"), Decimal("101.00"), 4.0)
    state, again, _ = LiveSession.amend(state, venue, retry)

    assert again.outcome is RequestOutcome.DUPLICATE
    assert venue.sent.count(("replace", handle)) == 1


def test_a_restarted_run_still_recognises_a_retried_request() -> None:
    venue = _Venue()
    state, handle = _with_an_order_at_the_venue(venue)
    amend = ModifyRequest(handle, 1, Decimal("12"), Decimal("101"), 3.0)
    state, _, _ = LiveSession.amend(state, venue, amend)

    restored = _round_trip(state)
    assert restored.requests == state.requests
    restored, decided, _ = LiveSession.amend(restored, venue, replace(amend, requested_at=9.0))

    assert decided.outcome is RequestOutcome.DUPLICATE
    assert venue.sent.count(("replace", handle)) == 1


def test_forgetting_the_ledger_is_what_re_sends_the_request() -> None:
    """The counter-example: a restart that kept everything but the ledger."""

    venue = _Venue()
    state, handle = _with_an_order_at_the_venue(venue)
    amend = ModifyRequest(handle, 1, Decimal("12"), Decimal("101"), 3.0)
    state, _, _ = LiveSession.amend(state, venue, amend)

    amnesiac = replace(_round_trip(state), requests=RequestLedger())
    _, decided, _ = LiveSession.amend(amnesiac, venue, amend)

    assert decided.outcome is RequestOutcome.NEW
    assert venue.sent.count(("replace", handle)) == 2


def test_a_cancel_is_sent_once_and_its_attempt_numbers_survive_a_restart() -> None:
    venue = _Venue()
    state, handle = _with_an_order_at_the_venue(venue)

    state, first, _ = LiveSession.cancel(state, venue, CancelRequest(handle, 1, 5.0))
    assert first.send and state.broker.orders[handle].status is BrokerOrderStatus.PENDING_CANCEL
    # The venue refuses it; the order is working again.
    state = replace(
        state,
        broker=apply_venue_event(
            state.broker, VenueEvent(E.ORDER_CANCEL_REJECTED, 5.5, handle, reason="too late")
        )[0],
    )

    restored = _round_trip(state)
    _, stale, _ = LiveSession.cancel(restored, venue, CancelRequest(handle, 1, 6.0))
    assert stale.outcome is RequestOutcome.DUPLICATE, "attempt 1 was already issued"
    restored, second, _ = LiveSession.cancel(restored, venue, CancelRequest(handle, 2, 6.0))

    assert second.outcome is RequestOutcome.NEW
    assert venue.sent.count(("cancel", handle)) == 2
    assert restored.requests.latest_cancel[handle][0] == 2


def test_the_envelope_carries_the_requests_in_issue_order() -> None:
    venue = _Venue()
    state, handle = _with_an_order_at_the_venue(venue)
    state, _, _ = LiveSession.amend(
        state, venue, ModifyRequest(handle, 1, Decimal("12"), Decimal("101"), 3.0)
    )
    state, _, _ = LiveSession.cancel(state, venue, CancelRequest(handle, 1, 4.0))

    payload = deserialize(serialize(capture(state)))

    assert payload["schema_version"] == 2
    assert [record["kind"] for record in payload["requests"]] == ["modify", "cancel"]
    assert payload["requests"][0]["request"]["quantity"] == "12"


def _payload_with_requests(requests: list[dict[str, Any]]) -> dict[str, Any]:
    venue = _Venue()
    state, _ = _with_an_order_at_the_venue(venue)
    payload = deserialize(serialize(capture(state)))
    assert isinstance(payload, dict)
    payload["requests"] = requests
    return payload


def test_a_request_issued_twice_is_refused_on_decode() -> None:
    request = {"broker_order_id": "B-1", "attempt": 1, "requested_at": 1.0}
    payload = _payload_with_requests(
        [{"kind": "cancel", "request": request}, {"kind": "cancel", "request": request}]
    )

    with pytest.raises(StateDecodeError, match="twice"):
        from_primitives(payload)


@pytest.mark.parametrize(
    "record",
    [
        {"kind": "amend", "request": {"broker_order_id": "B-1", "attempt": 1}},
        {"kind": "cancel", "request": {"broker_order_id": "B-1", "attempt": 0, "requested_at": 1}},
        {
            "kind": "cancel",
            "request": {"broker_order_id": "B-1", "attempt": True, "requested_at": 1},
        },
        {
            "kind": "modify",
            "request": {
                "broker_order_id": "B-1",
                "revision": 1,
                "quantity": "ten",
                "price": "1",
                "requested_at": 1.0,
            },
        },
        {"kind": "cancel", "request": {"broker_order_id": "B-1", "requested_at": 1.0}},
    ],
)
def test_a_malformed_request_is_refused_on_decode(record: dict[str, Any]) -> None:
    with pytest.raises(StateDecodeError):
        from_primitives(_payload_with_requests([record]))


# --------------------------------------------------------------------------- #
# Algorithm children
# --------------------------------------------------------------------------- #


def _child(parent: OMSOrder, sequence: int, quantity: str) -> ChildOrder:
    parent_id = str(parent.order_id.value)
    return ChildOrder(
        child_id=f"{parent_id}/{sequence}",
        parent_order_id=parent_id,
        sequence=sequence,
        asset_id=parent.asset_id,
        side=parent.side,
        quantity=Decimal(quantity),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        released_at=2.0 + sequence,
        expires_at=None,
        strategy_id=_STRATEGY,
        contributions=(),
        algorithm_id="TEST-ALGO",
    )


def _held_parent(venue: _Venue) -> tuple[LiveRunState, OMSOrder]:
    """A run whose one working order is held for an algorithm before anything is sent.

    ``advance(route=False)`` stops the step before routing, and ``hold`` marks
    the order an algorithm's (ledger LIV-001). Until v3.11 this had to be
    composed by hand around :meth:`RunEngine.advance`, because the live step
    sent every new order whole at its end.
    """

    state, step = LiveSession.advance(
        _started(venue), _record(2.0), context_factory, venue, route=False
    )
    assert step.routed == ()
    (parent,) = state.run.working_orders
    return LiveSession.hold(state, [str(parent.order_id.value)]), parent


def _parent_worked_in_children(venue: _Venue) -> tuple[LiveRunState, OMSOrder]:
    """A held parent with its first child at the venue."""

    state, parent = _held_parent(venue)
    state, first = LiveSession.route_child(
        state, venue, parent, _child(parent, 1, "4"), 3.0, capability=None
    )
    assert first.decision.routed, first.decision.reason
    return state, parent


def test_a_held_parent_is_never_sent_whole_even_before_its_first_child() -> None:
    venue = _Venue()
    state, parent = _held_parent(venue)

    assert (state.unrouted_orders, state.held_orders) == ((), (parent,))
    state, routed, _ = LiveSession.route_working_orders(state, venue, 2.5)
    assert routed == ()
    # The next step creates nothing new and sends nothing: the hold outlives
    # the step that made it.
    state, step = LiveSession.advance(state, _record(3.0), context_factory, venue)
    assert step.routed == ()
    assert venue.sent == []


def test_without_a_hold_the_next_step_sends_the_parent_whole() -> None:
    """The defect the hold exists for: a pause alone is not enough."""

    venue = _Venue()
    state, _ = LiveSession.advance(
        _started(venue), _record(2.0), context_factory, venue, route=False
    )
    _, step = LiveSession.advance(state, _record(3.0), context_factory, venue)

    assert [attempt.routed for attempt in step.routed] == [True]


def test_a_hold_is_refused_for_an_order_it_cannot_hold() -> None:
    venue = _Venue()
    with pytest.raises(RuntimeValidationError, match="not a working order"):
        LiveSession.hold(_started(venue), ["no-such-order"])

    state, step = LiveSession.advance(_started(venue), _record(2.0), context_factory, venue)
    (routed,) = step.routed
    with pytest.raises(RuntimeValidationError, match="already at the venue whole"):
        LiveSession.hold(state, [routed.oms_order_id])


def test_a_hold_survives_a_restart() -> None:
    venue = _Venue()
    state, parent = _held_parent(venue)

    restored = _round_trip(state)

    assert restored.held == state.held == frozenset({str(parent.order_id.value)})
    _, step = LiveSession.advance(restored, _record(3.0), context_factory, venue)
    assert step.routed == () and venue.sent == []


def test_a_held_order_listed_twice_is_refused_on_decode() -> None:
    venue = _Venue()
    state, parent = _held_parent(venue)
    payload = deserialize(serialize(capture(state)))
    payload["held"] = [str(parent.order_id.value)] * 2

    with pytest.raises(StateDecodeError, match="twice"):
        from_primitives(payload)


def test_a_parent_worked_in_children_is_not_routed_whole() -> None:
    venue = _Venue()
    state, parent = _parent_worked_in_children(venue)

    assert state.unrouted_orders == ()
    assert state.orders_at_venue == (parent,)
    state, routed, _ = LiveSession.route_working_orders(state, venue, 3.5)
    assert routed == ()
    assert [kind for kind, _ in venue.sent] == ["submit"], "only the child reached the venue"


def test_a_restored_run_rebuilds_its_children_exactly_and_keeps_working_them() -> None:
    venue = _Venue()
    state, parent = _parent_worked_in_children(venue)
    state, second = LiveSession.route_child(
        state, venue, parent, _child(parent, 2, "3"), 4.0, capability=None
    )
    assert second.decision.routed

    restored = _round_trip(state)

    assert restored.children == state.children
    assert restored.children.children_of(str(parent.order_id.value)) == (
        child_broker_order_id(parent, 1),
        child_broker_order_id(parent, 2),
    )
    assert restored.unrouted_orders == ()
    (resumed_parent,) = restored.run.working_orders
    # A child already sent is not sent again after the restart.
    _, again = LiveSession.route_child(
        restored, venue, resumed_parent, _child(resumed_parent, 2, "1"), 5.0, capability=None
    )
    assert again.decision.refusal is RoutingRefusal.DUPLICATE_SUBMISSION
    # The restored bindings still count what is working: 7 of 10 are at the
    # venue, so a child for 4 more is refused and one for 3 is sent.
    _, over = LiveSession.route_child(
        restored, venue, resumed_parent, _child(resumed_parent, 3, "4"), 5.0, capability=None
    )
    assert over.decision.refusal is RoutingRefusal.INVALID_CHILD
    restored, fits = LiveSession.route_child(
        restored, venue, resumed_parent, _child(resumed_parent, 3, "3"), 5.0, capability=None
    )
    assert fits.decision.routed
    assert [kind for kind, _ in venue.sent] == ["submit", "submit", "submit"]


def test_the_bindings_are_not_carried_twice() -> None:
    """One home for one fact: the mirror holds the children, the envelope does not."""

    venue = _Venue()
    state, _ = _parent_worked_in_children(venue)
    payload = deserialize(serialize(capture(state)))

    assert isinstance(payload, dict)
    assert "children" not in payload
    assert ChildOrderBindings.from_mirror(state.broker) == state.children


# --------------------------------------------------------------------------- #
# EXE-009: a foreign-currency fill settles with the run's rates
# --------------------------------------------------------------------------- #

_SAP = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR", sector="Technology")
_RATES = FxRates.of(
    [
        FxRate("EUR", "USD", Decimal("1.10"), 1.0, "ECB"),
        FxRate("USD", "EUR", Decimal("0.909090"), 1.0, "ECB"),
    ]
)


def _euro_run(venue: PaperBroker) -> LiveRunState:
    base = pipeline_config(_STRATEGY)
    config = RunConfig(
        pipeline=replace(
            base,
            account=Account("acct-live-eur", "USD", "live, two currencies", 1.0),
            currency="USD",
            also_settles=frozenset({"EUR"}),
            budget=base.budget.in_currency("USD"),
            instruments=registry_of(_SAP),
            routing=ExecutionRouting.EXTERNAL,
        ),
        mode=ExecutionMode.LIVE,
        seed=909,
        start_timestamp=1.0,
        compile_analytics=False,
    )
    state = LiveSession.initialize(
        config,
        running_strategy_state(
            _STRATEGY, ScriptedStrategy(_STRATEGY, _SAP.asset_id, {2.0: Decimal("10")})
        ),
        BrokerEngine.initialize("XETR-SIM", Decimal("1000000"), "EUR"),
        RoutingConfig(venue="XETR-SIM", currency="EUR"),
    )
    pipeline, _ = ExecutionPipeline.convert_cash(
        state.run.pipeline, Decimal("11000"), "USD", "EUR", _RATES, 1.0
    )
    state = replace(state, run=replace(state.run, pipeline=pipeline))
    state, _ = LiveSession.connect(state, venue, 1.5)
    return state


def test_a_foreign_currency_venue_fill_settles_with_the_rates() -> None:
    venue = PaperBroker(FREE)  # fills on submission, so the fill comes straight back
    state = _euro_run(venue)
    record = MarketRecord("DS-EUR", 2.0, quote(_SAP.asset_id, 2.0, Decimal("100")))
    state, step = LiveSession.advance(state, record, context_factory, venue, rates=_RATES)
    assert [attempt.routed for attempt in step.routed] == [True]
    fills = tuple(state.broker.executions.values())

    settled_state, settled = LiveSession.settle(state, fills, _RATES)

    assert [outcome.booked for outcome in settled] == [True]
    position = settled_state.run.pipeline.portfolio.positions[_SAP.asset_id]
    assert (position.quantity, position.currency) == (Decimal("10.000000"), "EUR")
    assert settled_state.run.pipeline.portfolio.cash.balance("EUR") < Decimal("10000")


def test_without_the_rates_the_same_fill_cannot_settle() -> None:
    """The v3.10 behaviour: ``settle`` had no way to take them."""

    venue = PaperBroker(FREE)
    state = _euro_run(venue)
    record = MarketRecord("DS-EUR", 2.0, quote(_SAP.asset_id, 2.0, Decimal("100")))
    state, _ = LiveSession.advance(state, record, context_factory, venue, rates=_RATES)

    with pytest.raises(MissingRateError, match="EUR/USD"):
        LiveSession.settle(state, tuple(state.broker.executions.values()), NO_RATES)


def test_advance_settles_arriving_foreign_fills_with_its_rates() -> None:
    venue = PaperBroker(FREE)
    state = _euro_run(venue)
    first = MarketRecord("DS-EUR", 2.0, quote(_SAP.asset_id, 2.0, Decimal("100")))
    state, _ = LiveSession.advance(state, first, context_factory, venue, rates=_RATES)
    arrived = tuple(state.broker.executions.values())

    later = MarketRecord("DS-EUR", 3.0, quote(_SAP.asset_id, 3.0, Decimal("101")))
    state, step = LiveSession.advance(
        state, later, context_factory, venue, executions=arrived, rates=_RATES
    )

    assert [outcome.booked for outcome in step.settled] == [True]
    assert state.run.pipeline.portfolio.positions[_SAP.asset_id].quantity == Decimal("10.000000")
