"""Real execution: the transport, the adapter, and the venue round trip.

Every test here drives :class:`~alphalab.broker.transport.HttpVenueTransport`
over a real TCP socket into a real HTTP server. Nothing stubs the transport,
nothing returns a canned ``accepted``, and the only thing that is not a venue is
the venue's identity. See ``tests/integration/venue_server.py``.

The suite is in four parts:

1. **Transport** -- signing, refusals versus absences, retries, redaction.
2. **Adapter lifecycle** -- submit, acknowledge, reject, cancel, replace, poll.
3. **Recovery** -- lost responses, duplicate fills, reconnect, reconciliation.
4. **Canonical propagation** -- a venue fill reaching the portfolio through the
   same path a simulated fill takes.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.broker.account import BrokerAccount
from alphalab.broker.events import (
    BrokerConnected,
    BrokerDisconnected,
    ExecutionReceived,
    OrderAccepted,
    OrderRejected,
    OrderSubmitted,
)
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.reconciliation import ExecutionOutcome, ExternalOrderMap
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.broker.transport import (
    HttpVenueTransport,
    VenueCredentials,
    VenueProtocolError,
    VenueResponse,
    VenueTransportError,
)
from alphalab.broker.venue import RestVenueBroker, VenueConfig
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.oms.order import Order as OMSOrder
from alphalab.runtime.broker_routing import (
    RoutingRefusal,
    apply_broker_execution,
    broker_order_id_for,
    route_order,
)
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)
from tests.integration.venue_server import VenueScript, fill, record_fill, run_venue

_KEY = "TESTKEY-0001"
_SECRET = "test-signing-secret-not-a-real-credential"
_SYMBOL = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _credentials() -> VenueCredentials:
    return VenueCredentials(_KEY, _SECRET)


def _broker(base_url: str, **config: object) -> RestVenueBroker:
    return RestVenueBroker(
        HttpVenueTransport(base_url, _credentials(), timeout_seconds=5.0),
        VenueConfig(**config),  # type: ignore[arg-type]
    )


def _state(status: ConnectionStatus = ConnectionStatus.CONNECTED) -> BrokerState:
    return BrokerState(
        broker_name="VENUE",
        connection_status=status,
        account=BrokerAccount(
            account_id="",
            cash=Decimal("0"),
            equity=Decimal("0"),
            buying_power=Decimal("0"),
            margin=Decimal("0"),
            available_funds=Decimal("0"),
            currency="USD",
        ),
    )


def _order(client_order_id: str = "ALB-1", quantity: str = "10") -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=client_order_id,
        oms_order_id="OMS-1",
        symbol=_SYMBOL,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal(quantity),
        price=Decimal("100"),
        filled_quantity=Decimal("0"),
        average_fill_price=Decimal("0"),
        status=OrderStatus.NEW,
        created_at=1.0,
        updated_at=1.0,
    )


# ---------------------------------------------------------------------------
# 1. Transport
# ---------------------------------------------------------------------------


def test_the_transport_signs_requests_the_venue_can_verify() -> None:
    """The signature is real HMAC over the real request, checked by the server."""

    with run_venue(_credentials()) as (url, book, _):
        response = HttpVenueTransport(url, _credentials()).request("GET", "/v1/account")

    assert response.status == 200
    assert response.ok
    assert book.requests == [("GET", "/v1/account")]
    assert response.json()["currency"] == "USD"


def test_a_wrong_secret_is_refused_by_the_venue() -> None:
    """Authentication is genuine: the server recomputes and compares."""

    with run_venue(_credentials()) as (url, book, _):
        wrong = VenueCredentials(_KEY, "a-different-secret")
        response = HttpVenueTransport(url, wrong).request("GET", "/v1/account")

    assert response.status == 401
    assert not response.ok
    assert book.requests == [], "an unsigned request never reaches the book"


def test_a_signature_is_bound_to_the_body_it_covers() -> None:
    """Changing the body invalidates the signature, so a request cannot be edited."""

    credentials = _credentials()
    signed = credentials.sign(1_700_000_000_000, "POST", "/v1/orders", b'{"quantity":"10"}')
    tampered = credentials.sign(1_700_000_000_000, "POST", "/v1/orders", b'{"quantity":"99"}')
    assert signed != tampered


def test_a_signature_is_bound_to_the_path_including_the_query() -> None:
    """A captured signature cannot be replayed against different parameters."""

    credentials = _credentials()
    one = credentials.sign(1_700_000_000_000, "GET", "/v1/executions?since=a", b"")
    two = credentials.sign(1_700_000_000_000, "GET", "/v1/executions?since=b", b"")
    assert one != two


def test_a_stale_signature_is_refused() -> None:
    """The timestamp window is what stops a captured request being replayed later."""

    # The server's clock is far ahead of the client's, so every signature the
    # client produces is outside the window.
    with run_venue(_credentials(), now_ms=lambda: 9_000_000_000_000) as (url, book, _):
        response = HttpVenueTransport(url, _credentials()).request("GET", "/v1/account")

    assert response.status == 401
    assert book.requests == []


def test_an_unreachable_venue_raises_rather_than_returning_a_status() -> None:
    """No answer is an exception; a refusal is a status. They are different facts."""

    transport = HttpVenueTransport("http://127.0.0.1:1", _credentials(), timeout_seconds=1.0)
    with pytest.raises(VenueTransportError) as caught:
        transport.request("GET", "/v1/account")

    assert "did not answer" in str(caught.value)


def test_a_transport_error_never_renders_the_secret() -> None:
    """The credential has no path into a traceback."""

    transport = HttpVenueTransport("http://127.0.0.1:1", _credentials(), timeout_seconds=1.0)
    with pytest.raises(VenueTransportError) as caught:
        transport.request("GET", "/v1/account")

    message = str(caught.value)
    assert _SECRET not in message
    assert "TEST..." in message, "the key is shown truncated, the secret not at all"


def test_credentials_do_not_render_their_secret() -> None:
    """`repr`, `str` and equality all exclude it."""

    credentials = _credentials()
    assert _SECRET not in repr(credentials)
    assert _SECRET not in str(credentials)
    assert credentials == VenueCredentials(_KEY, "a-completely-different-secret"), (
        "the secret is excluded from equality, so it cannot leak through a comparison"
    )


def test_an_empty_credential_is_refused_at_construction() -> None:
    with pytest.raises(Exception, match="api_secret cannot be empty"):
        VenueCredentials(_KEY, "  ")


def test_a_retryable_status_is_retried_and_then_succeeds() -> None:
    """Two 503s then an answer: three requests go down the wire, one result comes back."""

    script = VenueScript(refuse_next=[503, 503])
    with run_venue(_credentials(), script=script) as (url, book, _):
        broker = _broker(url, max_attempts=3)
        state, events = broker.connect(_state(ConnectionStatus.DISCONNECTED), 1.0)

    assert state.connection_status is ConnectionStatus.CONNECTED
    assert isinstance(events[0], BrokerConnected)
    assert book.requests == [("GET", "/v1/account")], "only the successful attempt was applied"


def test_a_refusal_that_is_not_retryable_is_never_repeated() -> None:
    """A 422 is a decision about the request. Repeating it would be refused identically."""

    script = VenueScript(refuse_next=[422, 422, 422])
    with run_venue(_credentials(), script=script) as (url, _, remaining):
        broker = _broker(url, max_attempts=3)
        broker.connect(_state(ConnectionStatus.DISCONNECTED), 1.0)

    assert remaining.refuse_next == [422, 422], "exactly one attempt was made"


def test_retries_are_bounded_and_the_failure_surfaces() -> None:
    """When the attempts run out the error propagates; nothing is assumed."""

    with run_venue(_credentials(), script=VenueScript(drop_next=5)) as (url, _, _script):
        broker = _broker(url, max_attempts=3)
        state, events = broker.connect(_state(ConnectionStatus.DISCONNECTED), 1.0)

    assert state.connection_status is ConnectionStatus.RECONNECTING
    assert isinstance(events[0], BrokerDisconnected)


def test_max_attempts_below_one_is_refused() -> None:
    with pytest.raises(Exception, match="max_attempts must be at least 1"):
        VenueConfig(max_attempts=0)


# ---------------------------------------------------------------------------
# 2. Adapter lifecycle
# ---------------------------------------------------------------------------


def test_connect_proves_the_credentials_rather_than_asserting_connectivity() -> None:
    with run_venue(_credentials()) as (url, book, _):
        state, _events = _broker(url).connect(_state(ConnectionStatus.DISCONNECTED), 1.0)

    assert state.connection_status is ConnectionStatus.CONNECTED
    assert book.requests == [("GET", "/v1/account")], "the claim was tested, not asserted"
    assert state.account.cash == Decimal("100000"), "the account came back from the venue"


def test_a_venue_that_refuses_the_connection_leaves_it_failed_not_connected() -> None:
    """A failed handshake must close the pre-trade gate, not open it."""

    with run_venue(_credentials(), script=VenueScript(refuse_next=[403])) as (url, _, _s):
        state, _events = _broker(url, max_attempts=1).connect(
            _state(ConnectionStatus.DISCONNECTED), 1.0
        )

    assert state.connection_status is ConnectionStatus.FAILED
    assert not state.connection_status.can_trade


def test_submitting_an_order_reaches_the_venue_and_is_acknowledged() -> None:
    with run_venue(_credentials()) as (url, book, _):
        state, events = _broker(url).submit_order(_state(), _order(), 2.0)

    assert ("POST", "/v1/orders") in book.requests
    assert "ALB-1" in book.orders, "the venue holds the order"
    assert isinstance(events[0], OrderSubmitted)
    assert isinstance(events[1], OrderAccepted)
    assert state.orders["ALB-1"].status is OrderStatus.ACCEPTED


def test_the_venue_rejecting_an_order_is_applied_as_a_rejection_not_raised() -> None:
    """A refusal is a lifecycle fact: the order dies and the OMS can retire it."""

    script = VenueScript(reject_submissions={"ALB-1": "insufficient buying power"})
    with run_venue(_credentials(), script=script) as (url, _, _s):
        state, events = _broker(url).submit_order(_state(), _order(), 2.0)

    assert state.orders["ALB-1"].status is OrderStatus.REJECTED
    rejection = next(e for e in events if isinstance(e, OrderRejected))
    assert "insufficient buying power" in rejection.reason


def test_a_submission_that_never_answers_raises_rather_than_inventing_an_outcome() -> None:
    with (
        run_venue(_credentials(), script=VenueScript(drop_next=5)) as (url, _, _s),
        pytest.raises(VenueTransportError),
    ):
        _broker(url, max_attempts=2).submit_order(_state(), _order(), 2.0)


def test_resubmitting_the_same_client_order_id_never_creates_a_second_order() -> None:
    """The idempotency guarantee, proven at the venue rather than in local memory."""

    with run_venue(_credentials()) as (url, book, _):
        broker = _broker(url)
        first, _ = broker.submit_order(_state(), _order(), 2.0)
        # A retry after a lost response: the same derived id, sent again.
        second, events = broker.submit_order(_state(), _order(), 3.0)

    assert len(book.orders) == 1, "one order at the venue after two submissions"
    assert [method for method, _ in book.requests].count("POST") == 2, "both really went out"
    assert second.orders["ALB-1"].status is OrderStatus.ACCEPTED
    assert isinstance(events[1], OrderAccepted)
    assert first.orders["ALB-1"].broker_order_id == second.orders["ALB-1"].broker_order_id


def test_a_partial_fill_is_reported_and_leaves_the_order_working() -> None:
    script = VenueScript(fills={"ALB-1": [fill("X1", "4", "100.5")]})
    with run_venue(_credentials(), script=script) as (url, _, _s):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(quantity="10"), 2.0)
        state, applied, events = broker.poll_executions(state, 3.0)

    assert len(applied) == 1
    assert applied[0].fill_quantity == Decimal("4")
    assert state.orders["ALB-1"].status is OrderStatus.PARTIALLY_FILLED
    assert state.orders["ALB-1"].remaining_quantity == Decimal("6")
    assert isinstance(events[0], ExecutionReceived)


def test_fills_that_complete_the_quantity_terminate_the_order() -> None:
    script = VenueScript(fills={"ALB-1": [fill("X1", "4", "100"), fill("X2", "6", "101")]})
    with run_venue(_credentials(), script=script) as (url, _, _s):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(quantity="10"), 2.0)
        state, applied, _events = broker.poll_executions(state, 3.0)

    assert len(applied) == 2
    order = state.orders["ALB-1"]
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == Decimal("10")
    assert order.average_fill_price == Decimal("100.6"), "volume weighted, not last"


def test_polling_with_a_cursor_returns_only_what_is_new() -> None:
    """Incremental reception: a second poll does not re-deliver the first fill."""

    script = VenueScript(fills={"ALB-1": [fill("X1", "4", "100")]})
    with run_venue(_credentials(), script=script) as (url, book, _):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(), 2.0)
        state, first, _ = broker.poll_executions(state, 3.0)

        record_fill(book, "ALB-1", fill("X2", "6", "101", timestamp=4.0))
        state, second, _ = broker.poll_executions(state, 5.0, since="X1")

    assert [execution.execution_id for execution in first] == ["X1"]
    assert [execution.execution_id for execution in second] == ["X2"]
    assert state.orders["ALB-1"].filled_quantity == Decimal("10")


def test_cancelling_an_order_reaches_the_venue() -> None:
    with run_venue(_credentials()) as (url, book, _):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(), 2.0)
        state, events = broker.cancel_order(state, "ALB-1", 3.0)

    assert ("DELETE", "/v1/orders/ALB-1") in book.requests
    assert book.orders["ALB-1"]["status"] == "cancelled"
    assert state.orders["ALB-1"].status is OrderStatus.CANCELLED
    assert len(events) == 1


def test_a_cancel_the_venue_refuses_leaves_the_order_exactly_as_it_was() -> None:
    """An order that filled in flight cannot be cancelled, and must not look cancelled."""

    script = VenueScript(fills={"ALB-1": [fill("X1", "10", "100")]})
    with run_venue(_credentials(), script=script) as (url, book, _):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(), 2.0)
        state, _applied, _ = broker.poll_executions(state, 3.0)
        book.orders["ALB-1"]["status"] = "filled"
        after, events = broker.cancel_order(state, "ALB-1", 4.0)

    assert events == ()
    assert after.orders["ALB-1"].status is OrderStatus.FILLED, "not locally cancelled"


def test_cancelling_an_order_this_process_never_sent_is_refused() -> None:
    with (
        run_venue(_credentials()) as (url, book, _),
        pytest.raises(Exception, match="not known to this broker state"),
    ):
        _broker(url).cancel_order(_state(), "SOMEONE-ELSES", 2.0)

    assert book.requests == [], "nothing was sent about someone else's order"


def test_replacing_an_order_reads_the_amended_quantity_back_from_the_venue() -> None:
    with run_venue(_credentials()) as (url, book, _):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(quantity="10"), 2.0)
        state, _ = broker.replace_order(state, "ALB-1", Decimal("7"), Decimal("99"), 3.0)

    assert book.orders["ALB-1"]["quantity"] == "7"
    assert state.orders["ALB-1"].quantity == Decimal("7"), "read back, not assumed"


def test_an_unreadable_response_raises_rather_than_being_partly_used() -> None:
    assert VenueResponse(200, b"not json").ok
    with pytest.raises(VenueProtocolError, match="not readable JSON"):
        VenueResponse(200, b"not json").json()


def test_an_unknown_order_status_from_the_venue_is_refused() -> None:
    """An order in a state AlphaLab cannot name must not be treated as working."""

    with run_venue(_credentials()) as (url, book, _):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(), 2.0)
        book.orders["ALB-1"]["status"] = "quantum_superposition"
        with pytest.raises(VenueProtocolError, match="not a status AlphaLab knows"):
            broker.fetch_order(state, "ALB-1")


# ---------------------------------------------------------------------------
# 3. Recovery and reconciliation
# ---------------------------------------------------------------------------


def test_a_duplicate_fill_is_applied_exactly_once() -> None:
    """A venue redelivering after a reconnect must not double-count."""

    execution = BrokerExecution(
        execution_id="X1",
        broker_order_id="ALB-1",
        symbol=_SYMBOL,
        fill_quantity=Decimal("4"),
        fill_price=Decimal("100"),
        commission=Decimal("0"),
        timestamp=3.0,
    )
    with run_venue(_credentials()) as (url, _, _s):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(), 2.0)
        state, first = broker.apply_execution(state, execution, 3.0)
        state, second = broker.apply_execution(state, execution, 4.0)

    assert len(first) == 1, "the first delivery was applied"
    assert second == (), "the redelivery produced nothing"
    assert state.orders["ALB-1"].filled_quantity == Decimal("4"), "counted once"


def test_repolling_the_whole_fill_history_applies_nothing_twice() -> None:
    """What a recovering process does: ask for everything, apply only what is new."""

    script = VenueScript(fills={"ALB-1": [fill("X1", "4", "100"), fill("X2", "3", "101")]})
    with run_venue(_credentials(), script=script) as (url, _, _s):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(), 2.0)
        state, first, _ = broker.poll_executions(state, 3.0)
        state, again, _ = broker.poll_executions(state, 4.0)

    assert len(first) == 2
    assert again == (), "a full re-poll after recovery applies nothing"
    assert state.orders["ALB-1"].filled_quantity == Decimal("7")


def test_a_fill_for_an_order_this_process_never_sent_is_never_applied() -> None:
    stranger = BrokerExecution(
        execution_id="X9",
        broker_order_id="NOT-OURS",
        symbol=_SYMBOL,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        commission=Decimal("0"),
        timestamp=3.0,
    )
    with run_venue(_credentials()) as (url, _, _s):
        broker = _broker(url)
        state, events = broker.apply_execution(_state(), stranger, 3.0)
        decision = broker.reconcilable_executions(state, stranger)

    assert events == ()
    assert decision.outcome is ExecutionOutcome.UNKNOWN_ORDER


def test_an_overfill_is_refused_not_truncated() -> None:
    over = BrokerExecution(
        execution_id="X1",
        broker_order_id="ALB-1",
        symbol=_SYMBOL,
        fill_quantity=Decimal("25"),
        fill_price=Decimal("100"),
        commission=Decimal("0"),
        timestamp=3.0,
    )
    with run_venue(_credentials()) as (url, _, _s):
        broker = _broker(url)
        state, _ = broker.submit_order(_state(), _order(quantity="10"), 2.0)
        state, events = broker.apply_execution(state, over, 3.0)

    assert events == ()
    assert state.orders["ALB-1"].filled_quantity == Decimal("0")


def test_a_lost_response_is_recovered_by_reading_the_order_back() -> None:
    """The recovery primitive: the venue is asked what it actually did."""

    with run_venue(_credentials()) as (url, book, _script):
        broker = _broker(url, max_attempts=1)
        # The order reaches the venue, and the answer is lost on the way back.
        state, _ = broker.submit_order(_state(), _order(), 2.0)
        record_fill(book, "ALB-1", fill("X1", "10", "100", timestamp=3.0))
        recovered = broker.fetch_order(state, "ALB-1")

    assert recovered.status is OrderStatus.FILLED
    assert recovered.filled_quantity == Decimal("10")


def test_a_heartbeat_that_fails_closes_the_pre_trade_gate() -> None:
    """Liveness is checked, not asserted: a dead venue stops taking orders."""

    with run_venue(_credentials(), script=VenueScript(drop_next=9)) as (url, _, _s):
        broker = _broker(url, max_attempts=1)
        state, events = broker.heartbeat(_state(), 5.0)

    assert state.connection_status is ConnectionStatus.RECONNECTING
    assert not state.connection_status.can_trade
    assert isinstance(events[0], BrokerDisconnected)


def test_a_heartbeat_that_answers_records_when_it_did() -> None:
    with run_venue(_credentials()) as (url, _, _s):
        state, events = _broker(url).heartbeat(_state(), 5.0)

    assert state.last_heartbeat == 5.0
    assert len(events) == 1


def test_reconnecting_after_a_failure_restores_the_gate() -> None:
    """Two drops, then the venue is back: the full disconnect/reconnect cycle."""

    with run_venue(_credentials(), script=VenueScript(drop_next=2)) as (url, _, script):
        broker = _broker(url, max_attempts=2)
        down, _ = broker.connect(_state(ConnectionStatus.DISCONNECTED), 1.0)
        assert down.connection_status is ConnectionStatus.RECONNECTING
        assert script.drop_next == 0

        up, events = broker.connect(down, 2.0)

    assert up.connection_status is ConnectionStatus.CONNECTED
    assert isinstance(events[0], BrokerConnected)


def test_routing_refuses_to_send_on_a_connection_that_is_not_connected() -> None:
    """The pre-trade gate, over the real adapter rather than the paper one."""

    with run_venue(_credentials(), script=VenueScript(drop_next=9)) as (url, book, _):
        broker = _broker(url, max_attempts=1)
        down, _ = broker.connect(_state(ConnectionStatus.DISCONNECTED), 1.0)
        assert down.connection_status is ConnectionStatus.RECONNECTING

        before = len(book.requests)
        result = route_order(down, broker, _oms_order(), 2.0)

    assert not result.decision.routed
    assert result.decision.refusal is RoutingRefusal.DISCONNECTED
    assert len(book.requests) == before, "nothing was sent"


def test_the_venues_positions_can_be_read_for_reconciliation() -> None:
    with run_venue(_credentials()) as (url, book, _):
        book.positions.append(
            {
                "symbol": _SYMBOL,
                "quantity": "10",
                "average_price": "100",
                "market_value": "1010",
                "unrealized_pnl": "10",
                "realized_pnl": "0",
                "market_price": "101",
                "asset_class": "equity",
            }
        )
        state = _broker(url).fetch_positions(_state())

    assert state.positions[_SYMBOL].quantity == Decimal("10")
    assert state.positions[_SYMBOL].market_price == Decimal("101")


# ---------------------------------------------------------------------------
# 4. Canonical propagation
# ---------------------------------------------------------------------------


def _oms_order() -> OMSOrder:
    """One accepted OMS order, built through the real execution path."""

    strategy_id = "LIVE-STRAT"
    config: RunConfig = replace(
        backtest_config(strategy_id),
        mode=ExecutionMode.LIVE,
    )
    assert config.pipeline.routing is ExecutionRouting.EXTERNAL

    state = RunEngine.initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, _SYMBOL, {2.0: Decimal("10")})
        ),
    )
    dataset = dataset_of_quotes(_SYMBOL, [Decimal("100"), Decimal("101")])
    for record in dataset.records:
        state, _ = RunEngine.advance(state, record, context_factory)

    working = state.working_orders
    assert working, "a live run leaves the order working for routing"
    return working[0]


def test_an_order_from_the_real_path_routes_to_the_real_venue() -> None:
    """End to end: strategy -> pipeline -> OMS -> routing -> socket -> venue."""

    order = _oms_order()
    with run_venue(_credentials()) as (url, book, _):
        broker = _broker(url)
        state, _ = broker.connect(_state(ConnectionStatus.DISCONNECTED), 1.0)
        result = route_order(state, broker, order, 3.0, ExternalOrderMap())

    assert result.decision.routed
    client_id = broker_order_id_for(order)
    assert client_id in book.orders, "the venue holds the order the OMS produced"
    assert book.orders[client_id]["status"] == "accepted"


def test_a_venue_fill_reaches_the_portfolio_through_the_canonical_path() -> None:
    """The return leg: a real venue's fill becomes cash and a position."""

    strategy_id = "LIVE-STRAT"
    config: RunConfig = replace(backtest_config(strategy_id), mode=ExecutionMode.LIVE)
    state = RunEngine.initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, _SYMBOL, {2.0: Decimal("10")})
        ),
    )
    for record in dataset_of_quotes(_SYMBOL, [Decimal("100"), Decimal("101")]).records:
        state, _ = RunEngine.advance(state, record, context_factory)

    order = state.working_orders[0]
    cash_before = state.pipeline.portfolio.cash.balances["USD"]

    with run_venue(_credentials()) as (url, book, _):
        broker = _broker(url)
        broker_state, _ = broker.connect(_state(ConnectionStatus.DISCONNECTED), 1.0)
        routed = route_order(broker_state, broker, order, 3.0, ExternalOrderMap())
        assert routed.decision.routed

        client_id = broker_order_id_for(order)
        record_fill(book, client_id, fill("X1", str(order.remaining_quantity), "101"))
        broker_state, applied, _ = broker.poll_executions(routed.broker_state, 4.0)

    assert len(applied) == 1
    pipeline, fills, trades = apply_broker_execution(state.pipeline, order, applied[0])

    assert len(fills) == 1, "a canonical core.Fill, not a venue-specific type"
    assert len(trades) == 1
    assert pipeline.portfolio.positions[_SYMBOL].quantity == Decimal("10")
    assert pipeline.portfolio.cash.balances["USD"] < cash_before, "the fill cost cash"
    filled = pipeline.oms.orders.find(order.order_id)
    assert filled.status is OrderStatus.FILLED, "the OMS order reached its terminal state"
