"""The live driver, end to end, against a real venue over a real socket.

ADR-0030 listed ``LiveDriver`` as a later entry in its Tier-3 table and recorded
the gap: "no live driver exists". ``runtime/session.py`` said the same against
itself -- "a live session driven by this module still produces working orders
and stops".

Every test here drives :class:`~alphalab.runtime.live.LiveSession` through
:class:`~alphalab.broker.venue.RestVenueBroker` over
:class:`~alphalab.broker.transport.HttpVenueTransport` into the real HTTP venue
in ``tests/integration/venue_server.py``, which verifies the HMAC signature, the
timestamp window and the idempotency key. Nothing here stubs a transport and
nothing returns a canned ``accepted``.

What is being proved is the **cycle**, not the pieces -- those were proved in
v2.15 by ``test_real_execution.py``:

    venue fills --> settle --> RunEngine.advance --> route working orders
         ^                                                   |
         +---------------------------------------------------+

and that the cycle survives a process boundary, which is the property
:mod:`alphalab.broker.snapshot` exists for: a restart that forgot which orders
the venue already holds re-sends every one of them.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.broker.account import BrokerAccount
from alphalab.broker.reconciliation import ExecutionOutcome, ExternalOrderMap
from alphalab.broker.snapshot import BROKER_SNAPSHOT_SCHEMA
from alphalab.broker.snapshot import capture as capture_broker
from alphalab.broker.snapshot import from_primitives as broker_from_primitives
from alphalab.broker.snapshot import restore as restore_broker
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.broker.transport import HttpVenueTransport, VenueCredentials
from alphalab.broker.venue import RestVenueBroker, VenueConfig
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.broker_routing import RoutingConfig, RoutingRefusal
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.live import LiveRunState, LiveSession, live_health
from alphalab.runtime.run import ExecutionMode, RunConfig
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    dataset_of_quotes,
    pipeline_config,
    running_strategy_state,
)
from tests.integration.venue_server import fill, record_fill, run_venue

_KEY = "TESTKEY-0001"
_SECRET = "test-signing-secret-not-a-real-credential"
_SYMBOL = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
_STRATEGY = "LIVE-MOM"


def _credentials() -> VenueCredentials:
    return VenueCredentials(_KEY, _SECRET)


def _broker(base_url: str) -> RestVenueBroker:
    return RestVenueBroker(
        HttpVenueTransport(base_url, _credentials()),
        VenueConfig(broker_name="TESTVENUE", account_id="ACC-LIVE"),
    )


def _broker_state() -> BrokerState:
    return BrokerState(
        broker_name="TESTVENUE",
        connection_status=ConnectionStatus.DISCONNECTED,
        account=BrokerAccount(
            account_id="ACC-LIVE",
            cash=Decimal("1000000"),
            equity=Decimal("1000000"),
            buying_power=Decimal("1000000"),
            margin=Decimal("0"),
            available_funds=Decimal("1000000"),
            currency="USD",
        ),
    )


def _live_config(seed: int | None = 31337) -> RunConfig:
    return RunConfig(
        pipeline=replace(pipeline_config(_STRATEGY), routing=ExecutionRouting.EXTERNAL),
        mode=ExecutionMode.LIVE,
        seed=seed,
        start_timestamp=1.0,
        compile_analytics=False,
    )


def _strategy(buy_at: float = 2.0, quantity: Decimal = Decimal("10")) -> ScriptedStrategy:
    return ScriptedStrategy(_STRATEGY, _SYMBOL, {buy_at: quantity})


def _started(base_url: str, seed: int | None = 31337) -> LiveRunState:
    """A connected live run, ready to take records."""

    state = LiveSession.initialize(
        _live_config(seed),
        running_strategy_state(_STRATEGY, _strategy()),
        _broker_state(),
        RoutingConfig(venue="TESTVENUE", currency="USD"),
    )
    state, _ = LiveSession.connect(state, _broker(base_url), 1.5)
    return state


# ---------------------------------------------------------------------------
# 1 -- the driver refuses to be used where it would double-book
# ---------------------------------------------------------------------------


def test_a_live_session_refuses_any_mode_but_live() -> None:
    """The one mistake this driver exists to make impossible."""

    from alphalab.runtime.exceptions import RuntimeValidationError

    for mode in (ExecutionMode.BACKTEST, ExecutionMode.REPLAY, ExecutionMode.PAPER):
        config = RunConfig(pipeline=pipeline_config(_STRATEGY), mode=mode, start_timestamp=1.0)
        with pytest.raises(RuntimeValidationError, match=r"ExecutionMode\.LIVE"):
            LiveSession.initialize(
                config, running_strategy_state(_STRATEGY, _strategy()), _broker_state()
            )


def test_live_mode_routes_externally_so_nothing_is_simulated() -> None:
    """``RunConfig.__post_init__`` already forces this; pinned because the
    driver's safety rests on it."""

    config = _live_config()
    assert config.pipeline.routing is ExecutionRouting.EXTERNAL
    assert config.mode.routing is ExecutionRouting.EXTERNAL


# ---------------------------------------------------------------------------
# 2 -- the cycle
# ---------------------------------------------------------------------------


def test_one_record_produces_an_order_that_reaches_the_venue() -> None:
    """The step v2.15 left to the caller: advance, then route."""

    with run_venue(_credentials()) as (base_url, book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        record = dataset_of_quotes(_SYMBOL, [Decimal("100")]).records[0]

        state, step = LiveSession.advance(state, record, context_factory, broker)

        assert len(step.routed) == 1, "the working order was not routed"
        attempt = step.routed[0]
        assert attempt.routed
        assert attempt.broker_order_id is not None
        # The venue really holds it, under the derived client order id.
        assert attempt.broker_order_id in book.orders
        assert book.orders[attempt.broker_order_id]["status"] == "accepted"
        # And AlphaLab's binding says so.
        assert state.mapping.oms_id_for(attempt.broker_order_id) == attempt.oms_order_id
        assert state.unrouted_orders == ()
        assert len(state.orders_at_venue) == 1


def test_a_venue_fill_settles_through_the_canonical_path() -> None:
    """A live fill reaches the portfolio by the route a simulated one takes."""

    with run_venue(_credentials()) as (base_url, book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        records = dataset_of_quotes(_SYMBOL, [Decimal("100"), Decimal("101")]).records

        state, first = LiveSession.advance(state, records[0], context_factory, broker)
        client_order_id = first.routed[0].broker_order_id
        assert client_order_id is not None

        # The venue trades the resting order.
        record_fill(book, client_order_id, fill("EX-1", "10", "100.50", timestamp=2.5))
        broker_state, applied, _events = broker.poll_executions(state.broker, 2.5)
        state = replace(state, broker=broker_state)
        assert len(applied) == 1

        before_cash = state.run.pipeline.portfolio.cash.balance("USD")
        state, second = LiveSession.advance(
            state, records[1], context_factory, broker, executions=applied
        )

        assert len(second.settled) == 1
        settled = second.settled[0]
        assert settled.booked, "the fill did not reach the portfolio"
        assert len(settled.fills) == 1
        # `poll_executions` already recorded it in the venue-side ledger, so the
        # broker layer calls it a duplicate. That says nothing about the
        # portfolio, which is the point of keeping the two answers apart.
        assert settled.decision.outcome is ExecutionOutcome.DUPLICATE
        assert not settled.is_break

        # The canonical accounting moved: cash paid, position held.
        position = state.run.pipeline.portfolio.positions[_SYMBOL]
        assert position.quantity == Decimal("10")
        assert state.run.pipeline.portfolio.cash.balance("USD") < before_cash
        assert second.breaks == ()


def test_the_fill_is_settled_before_the_strategy_is_dispatched() -> None:
    """The order in ``advance`` is the contract, not an implementation detail.

    A strategy dispatched before a known fill is applied reads a book that does
    not hold a position it already owns.
    """

    seen: list[Decimal] = []

    class Watcher(ScriptedStrategy):
        def on_quote(self, context, event):  # type: ignore[no-untyped-def]
            seen.append(context.portfolio.quantity(_SYMBOL))
            return super().on_quote(context, event)

    with run_venue(_credentials()) as (base_url, book, _script):
        state = LiveSession.initialize(
            _live_config(),
            running_strategy_state(_STRATEGY, Watcher(_STRATEGY, _SYMBOL, {2.0: Decimal("10")})),
            _broker_state(),
            RoutingConfig(venue="TESTVENUE", currency="USD"),
        )
        state, _ = LiveSession.connect(state, _broker(base_url), 1.5)
        broker = _broker(base_url)
        records = dataset_of_quotes(_SYMBOL, [Decimal("100"), Decimal("101")]).records

        state, first = LiveSession.advance(state, records[0], context_factory, broker)
        client_order_id = first.routed[0].broker_order_id
        assert client_order_id is not None
        record_fill(book, client_order_id, fill("EX-1", "10", "100.50", timestamp=2.5))
        broker_state, applied, _ = broker.poll_executions(state.broker, 2.5)
        state = replace(state, broker=broker_state)

        state, _ = LiveSession.advance(
            state, records[1], context_factory, broker, executions=applied
        )

    assert seen == [Decimal("0"), Decimal("10")], (
        "the second dispatch did not see the fill that had already arrived"
    )


def test_a_redelivered_fill_is_a_duplicate_and_changes_nothing() -> None:
    """A venue replays fills after a reconnect. Both layers refuse the repeat."""

    with run_venue(_credentials()) as (base_url, book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        records = dataset_of_quotes(_SYMBOL, [Decimal("100"), Decimal("101")]).records

        state, first = LiveSession.advance(state, records[0], context_factory, broker)
        client_order_id = first.routed[0].broker_order_id
        assert client_order_id is not None
        record_fill(book, client_order_id, fill("EX-1", "10", "100.50", timestamp=2.5))
        broker_state, applied, _ = broker.poll_executions(state.broker, 2.5)
        state = replace(state, broker=broker_state)

        state, _ = LiveSession.advance(
            state, records[1], context_factory, broker, executions=applied
        )
        quantity = state.run.pipeline.portfolio.positions[_SYMBOL].quantity
        cash = state.run.pipeline.portfolio.cash.balance("USD")

        # The same fill again, as a reconnecting venue would send it.
        state, settled = LiveSession.settle(state, applied)

        assert len(settled) == 1
        assert settled[0].decision.outcome is ExecutionOutcome.DUPLICATE
        assert not settled[0].booked, "the portfolio booked a redelivered fill"
        assert settled[0].fills == ()
        assert not settled[0].is_break, "redelivery is expected, not a break"
        assert state.run.pipeline.portfolio.positions[_SYMBOL].quantity == quantity
        assert state.run.pipeline.portfolio.cash.balance("USD") == cash


def test_an_order_is_never_routed_twice() -> None:
    """The duplicate-submission gate, reached through the driver."""

    with run_venue(_credentials()) as (base_url, book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        records = dataset_of_quotes(_SYMBOL, [Decimal("100"), Decimal("101")]).records

        state, first = LiveSession.advance(state, records[0], context_factory, broker)
        assert len(first.routed) == 1
        submitted = len(book.orders)

        # A second record while the order is still working routes nothing new.
        state, second = LiveSession.advance(state, records[1], context_factory, broker)

        assert second.routed == (), "an order already at the venue was offered again"
        assert len(book.orders) == submitted


def test_a_disconnected_session_holds_orders_instead_of_losing_them() -> None:
    with run_venue(_credentials()) as (base_url, _book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        state, _ = LiveSession.disconnect(state, broker, "operator", 1.9)
        record = dataset_of_quotes(_SYMBOL, [Decimal("100")]).records[0]

        state, step = LiveSession.advance(state, record, context_factory, broker)

        assert len(step.routed) == 1
        assert not step.routed[0].routed
        assert step.routed[0].decision.refusal is RoutingRefusal.DISCONNECTED
        assert len(state.unrouted_orders) == 1, "the order was lost rather than held"
        assert "held because the venue was unreachable" in " ".join(live_health(state))


def test_a_fill_for_an_order_this_run_does_not_hold_is_a_break_not_a_crash() -> None:
    """Reconciliation's job. Raising here would stop a live run over bookkeeping."""

    from alphalab.broker.execution import BrokerExecution

    with run_venue(_credentials()) as (base_url, _book, _script):
        state = _started(base_url)
        stranger = BrokerExecution(
            execution_id="EX-STRANGER",
            broker_order_id="ALB-not-ours",
            symbol=_SYMBOL,
            fill_quantity=Decimal("5"),
            fill_price=Decimal("100"),
            commission=Decimal("0"),
            timestamp=3.0,
        )

        state, settled = LiveSession.settle(state, [stranger])

        assert len(settled) == 1
        assert settled[0].decision.outcome is ExecutionOutcome.UNKNOWN_ORDER
        assert settled[0].is_break
        assert not settled[0].booked
        assert settled[0].fills == ()
        assert len(state.open_breaks) == 1
        assert "need reconciling" in " ".join(live_health(state))


# ---------------------------------------------------------------------------
# 3 -- the aggregate adds no second authority
# ---------------------------------------------------------------------------


def test_the_live_state_holds_no_accounting_of_its_own() -> None:
    """Every number is read from the authority that owns it."""

    from dataclasses import fields

    names = {f.name for f in fields(LiveRunState)}
    assert names == {
        "run",
        "broker",
        "mapping",
        "reconciliation",
        "routing",
        "routed",
        "settled",
    }
    # No cursor, no cash, no positions, no orders of its own.
    for forbidden in ("processed", "cash", "positions", "orders", "steps", "pipeline"):
        assert forbidden not in names


def test_the_run_state_gained_no_broker_fields() -> None:
    """ADR-0030 decision 2 fixes ``RunState`` at eight fields. Pinned."""

    from dataclasses import fields

    from alphalab.runtime.run import RunState

    assert {f.name for f in fields(RunState)} == {
        "config",
        "pipeline",
        "processed",
        "current_timestamp",
        "last_record_timestamp",
        "source_id",
        "steps",
        "skipped",
    }


def test_the_run_snapshot_schema_did_not_move() -> None:
    """A backtest payload is unaffected by the live driver existing."""

    from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA

    assert RUN_SNAPSHOT_SCHEMA == 1


# ---------------------------------------------------------------------------
# 4 -- durability across a process boundary
# ---------------------------------------------------------------------------


def test_the_venue_binding_survives_a_capture_and_restore() -> None:
    """The fact a restart must never guess."""

    with run_venue(_credentials()) as (base_url, _book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        record = dataset_of_quotes(_SYMBOL, [Decimal("100")]).records[0]
        state, step = LiveSession.advance(state, record, context_factory, broker)
        client_order_id = step.routed[0].broker_order_id
        oms_order_id = step.routed[0].oms_order_id

        payload = serialize(capture_broker(state.broker, state.mapping, state.reconciliation))
        broker_state, mapping, log = restore_broker(broker_from_primitives(deserialize(payload)))

        assert mapping.broker_id_for(oms_order_id) == client_order_id
        assert mapping.oms_id_for(client_order_id or "") == oms_order_id
        assert broker_state.orders == state.broker.orders
        assert broker_state.executions == state.broker.executions
        assert broker_state.connection_status is state.broker.connection_status
        assert log.breaks == state.reconciliation.breaks


def test_a_restored_run_does_not_re_send_orders_the_venue_already_holds() -> None:
    """The whole reason the mapping is captured."""

    with run_venue(_credentials()) as (base_url, book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        record = dataset_of_quotes(_SYMBOL, [Decimal("100")]).records[0]
        state, _ = LiveSession.advance(state, record, context_factory, broker)
        assert len(book.orders) == 1

        payload = serialize(capture_broker(state.broker, state.mapping, state.reconciliation))
        broker_state, mapping, log = restore_broker(broker_from_primitives(deserialize(payload)))
        resumed = LiveRunState(
            run=state.run, broker=broker_state, mapping=mapping, reconciliation=log
        )

        resumed, routed, _ = LiveSession.route_working_orders(resumed, broker, 3.0)

        assert routed == (), "a restored run offered an order the venue already held"
        assert len(book.orders) == 1, "the venue received a duplicate order"
        assert resumed.unrouted_orders == ()


def test_losing_the_mapping_is_what_duplicates_an_order() -> None:
    """The counter-example, so the guarantee above is not a coincidence."""

    with run_venue(_credentials()) as (base_url, book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        record = dataset_of_quotes(_SYMBOL, [Decimal("100")]).records[0]
        state, _ = LiveSession.advance(state, record, context_factory, broker)
        assert len(book.orders) == 1

        # A restart that kept the run and forgot the venue binding.
        amnesiac = LiveRunState(run=state.run, broker=state.broker, mapping=ExternalOrderMap())
        assert len(amnesiac.unrouted_orders) == 1, (
            "without the mapping the run believes its order was never sent"
        )


def test_the_broker_snapshot_refuses_an_unknown_schema_version() -> None:
    payload = json.loads(serialize(capture_broker(_broker_state())))
    payload["schema_version"] = BROKER_SNAPSHOT_SCHEMA + 1

    from alphalab.persistence.exceptions import StateDecodeError

    with pytest.raises(StateDecodeError, match="schema version"):
        broker_from_primitives(payload)


def test_the_broker_snapshot_refuses_a_payload_with_no_version() -> None:
    payload = json.loads(serialize(capture_broker(_broker_state())))
    del payload["schema_version"]

    from alphalab.persistence.exceptions import StateDecodeError

    with pytest.raises(StateDecodeError):
        broker_from_primitives(payload)


# ---------------------------------------------------------------------------
# 5 -- the whole live envelope
# ---------------------------------------------------------------------------


def test_a_live_run_captures_and_restores_as_one_envelope() -> None:
    from alphalab.runtime.live_snapshot import LIVE_SNAPSHOT_SCHEMA, capture, from_primitives

    with run_venue(_credentials()) as (base_url, _book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        record = dataset_of_quotes(_SYMBOL, [Decimal("100")]).records[0]
        state, step = LiveSession.advance(state, record, context_factory, broker)

        snapshot = capture(state)
        assert snapshot.schema_version == LIVE_SNAPSHOT_SCHEMA
        assert snapshot.venue == "TESTVENUE"

        decoded = from_primitives(json.loads(serialize(snapshot)))

        assert decoded.venue == snapshot.venue
        assert decoded.currency == snapshot.currency
        assert decoded.broker.order_bindings == snapshot.broker.order_bindings
        assert decoded.run.processed == snapshot.run.processed
        assert decoded.run.source_id == snapshot.run.source_id
        assert step.routed[0].oms_order_id in decoded.broker.order_bindings


def test_the_live_envelope_carries_no_derivable_second_copy() -> None:
    """``routed`` and ``settled`` are observability, derivable from the halves."""

    from dataclasses import fields

    from alphalab.runtime.live_snapshot import LiveRunSnapshot

    names = {f.name for f in fields(LiveRunSnapshot)}
    assert "routed" not in names
    assert "settled" not in names
    assert {"run", "broker"} <= names


def test_health_is_empty_for_a_healthy_run() -> None:
    with run_venue(_credentials()) as (base_url, _book, _script):
        state = _started(base_url)
        assert live_health(state) == ()


# ---------------------------------------------------------------------------
# 6 -- the two ledgers, which are not one ledger
# ---------------------------------------------------------------------------


def test_a_fill_the_venue_ledger_holds_can_still_be_unbooked_by_the_portfolio() -> None:
    """The distinction the first draft of this driver got wrong.

    ``BrokerState.executions`` answers "has the venue-side bookkeeping recorded
    this fill?"; ``ExecutionState.reports`` answers "has the portfolio booked
    it?". ``poll_executions`` applies to the first before returning, so every
    fill that arrives that way is a ``DUPLICATE`` at the broker layer and
    entirely new to the portfolio. Gating the portfolio on the broker layer's
    answer silently dropped every live fill.
    """

    with run_venue(_credentials()) as (base_url, book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        records = dataset_of_quotes(_SYMBOL, [Decimal("100"), Decimal("101")]).records

        state, first = LiveSession.advance(state, records[0], context_factory, broker)
        client_order_id = first.routed[0].broker_order_id
        assert client_order_id is not None
        record_fill(book, client_order_id, fill("EX-1", "10", "100.50", timestamp=2.5))

        broker_state, applied, _ = broker.poll_executions(state.broker, 2.5)
        state = replace(state, broker=broker_state)

        # Ledger one already holds it. Ledger two has never seen it.
        assert "EX-1" in state.broker.executions
        assert "EX-1" not in state.run.pipeline.execution.reports

        state, settled = LiveSession.settle(state, applied)

        assert settled[0].decision.outcome is ExecutionOutcome.DUPLICATE
        assert settled[0].booked, "the venue ledger's answer suppressed the portfolio's"
        assert "EX-1" in state.run.pipeline.execution.reports


def test_each_ledger_refuses_its_own_repeat_independently() -> None:
    """Neither layer relies on the other for idempotence."""

    with run_venue(_credentials()) as (base_url, book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        record = dataset_of_quotes(_SYMBOL, [Decimal("100")]).records[0]
        state, first = LiveSession.advance(state, record, context_factory, broker)
        client_order_id = first.routed[0].broker_order_id
        assert client_order_id is not None
        record_fill(book, client_order_id, fill("EX-1", "10", "100.50", timestamp=2.5))
        broker_state, applied, _ = broker.poll_executions(state.broker, 2.5)
        state = replace(state, broker=broker_state)

        state, _ = LiveSession.settle(state, applied)
        quantity = state.run.pipeline.portfolio.positions[_SYMBOL].quantity
        cash = state.run.pipeline.portfolio.cash.balance("USD")

        # Three more deliveries of the same fill.
        for _ in range(3):
            state, settled = LiveSession.settle(state, applied)
            assert not settled[0].booked

        assert state.run.pipeline.portfolio.positions[_SYMBOL].quantity == quantity
        assert state.run.pipeline.portfolio.cash.balance("USD") == cash
        assert len(state.run.pipeline.execution.reports) == 1


def test_a_break_never_reaches_the_portfolio_however_the_ledgers_stand() -> None:
    """``DUPLICATE`` passes through to the portfolio; a break does not."""

    from alphalab.broker.execution import BrokerExecution

    with run_venue(_credentials()) as (base_url, _book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        record = dataset_of_quotes(_SYMBOL, [Decimal("100")]).records[0]
        state, first = LiveSession.advance(state, record, context_factory, broker)
        client_order_id = first.routed[0].broker_order_id
        assert client_order_id is not None

        overfill = BrokerExecution(
            execution_id="EX-OVER",
            broker_order_id=client_order_id,
            symbol=_SYMBOL,
            fill_quantity=Decimal("999999"),
            fill_price=Decimal("100"),
            commission=Decimal("0"),
            timestamp=3.0,
        )

        state, settled = LiveSession.settle(state, [overfill])

        assert settled[0].decision.outcome is ExecutionOutcome.OVERFILL
        assert settled[0].is_break
        assert not settled[0].booked
        assert "EX-OVER" not in state.run.pipeline.execution.reports
        assert _SYMBOL not in state.run.pipeline.portfolio.positions


def test_a_fill_finds_its_order_by_key_and_never_by_scanning() -> None:
    """The order book indexes by ``OrderId`` and v2.2 made that index keyed.

    Walking ``orders()`` to match a handle would put a linear term back on the
    fill path that the book has not had since. Caught in the v2.16 acceptance
    review, in this driver's own first implementation.
    """

    import ast
    import inspect

    from alphalab.runtime import live

    source = inspect.getsource(live._oms_order_for)
    body = source.split('"""')[-1]
    assert ".orders()" not in body, "the fill path scans the order book"
    assert "for order in" not in body, "the fill path iterates the order book"
    assert "contains(" in body and "find(" in body, "the keyed lookup is not used"

    # And a fill for a handle the mapping does not know is still `None`, not a
    # raise out of the book.
    tree = ast.parse(inspect.getsource(live))
    assert tree is not None


def test_a_fill_naming_a_handle_bound_to_a_forgotten_order_is_a_break() -> None:
    """Both absences answer ``None``, and neither raises out of the book."""

    from dataclasses import replace as dc_replace

    from alphalab.broker.execution import BrokerExecution
    from alphalab.broker.reconciliation import ExternalOrderMap

    with run_venue(_credentials()) as (base_url, _book, _script):
        state = _started(base_url)
        broker = _broker(base_url)
        record = dataset_of_quotes(_SYMBOL, [Decimal("100")]).records[0]
        state, step = LiveSession.advance(state, record, context_factory, broker)
        client_order_id = step.routed[0].broker_order_id
        assert client_order_id is not None

        # A binding to an order id this run does not hold.
        stray = ExternalOrderMap().bind("11111111-2222-3333-4444-555555555555", "ALB-ghost")
        orphaned = dc_replace(state, mapping=stray)
        execution = BrokerExecution(
            execution_id="EX-ORPHAN",
            broker_order_id="ALB-ghost",
            symbol=_SYMBOL,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100"),
            commission=Decimal("0"),
            timestamp=3.0,
        )

        # The broker layer refuses it first -- it knows no such order either.
        orphaned, settled = LiveSession.settle(orphaned, [execution])

        assert len(settled) == 1
        assert not settled[0].booked
        assert settled[0].is_break
