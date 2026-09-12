"""A broker adapter that reaches a real venue over HTTP.

:class:`~alphalab.broker.paper.PaperBroker` is the reference implementation of
:class:`~alphalab.broker.protocol.BrokerProtocol` and is a *simulation*: it
invents its own fills and is its own venue. :class:`RestVenueBroker` is the same
contract implemented against something outside the process. Both are
:class:`BrokerProtocol`, so :mod:`alphalab.runtime.broker_routing` routes to
either without knowing which -- which is the property the whole boundary exists
to have.

What is genuinely different about a real venue
----------------------------------------------
Three things, and they are the whole of this module:

**The venue decides, and says so later.** ``submit_order`` returns when the
venue has *acknowledged* the order, not when it has filled. Fills arrive
afterwards, and for a REST venue they arrive by being asked for --
:meth:`RestVenueBroker.poll_executions`. That method is not on
``BrokerProtocol`` and should not be: a socket-pushed venue receives the same
fills without polling, and both hand them to the same
:meth:`~RestVenueBroker.apply_execution`.

**An answer can fail to arrive.** A timed-out submission has an unknown outcome.
This is why the client order id is *derived* from the OMS order id
(:func:`~alphalab.runtime.broker_routing.broker_order_id_for`) and why every
request here addresses an order by it: a retry after a lost response reaches the
order the first attempt may have created, rather than creating a second one. The
venue deduplicates, not AlphaLab's memory of what it sent.

**A refusal is a fact, not a failure.** A venue answering ``422`` has decided
something about the order, and that decision becomes an
:class:`~alphalab.broker.events.OrderRejected` event and a ``REJECTED`` order.
Only the *absence* of an answer raises.

Retries, and the one rule about them
------------------------------------
:meth:`_send` retries a request that did not answer, and a ``5xx``/``429`` that
asked to be retried. It never retries anything else, because every other status
is the venue deciding about this exact request and repeating it would be refused
identically. Attempts are bounded by :attr:`VenueConfig.max_attempts`; when they
run out the transport error propagates, because an order whose fate is unknown
must be surfaced rather than assumed either way.

Identifiers
-----------
``execution_id`` is **the venue's own fill identifier**, taken verbatim. It is
the deduplication key in
:func:`~alphalab.broker.reconciliation.classify_execution`, so it has to be
stable across a redelivery after reconnect -- a minted one would be different
every time the same fill arrived and every redelivery would double-count.
``PaperBroker`` mints one because it *is* the venue; an adapter to someone
else's venue must not.

Event identifiers are drawn from :func:`~alphalab.common.ids.new_id` exactly as
``PaperBroker`` draws them. Two adapters at one contract behaving differently in
their event stream is the divergence this boundary exists to prevent.

Secrets
-------
Nothing in this module renders a credential. The transport holds them, redacts
them from its own errors, and this layer never touches
:attr:`~alphalab.broker.transport.VenueCredentials.api_secret` at all. See
ADR-0031.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from alphalab.broker.account import BrokerAccount
from alphalab.broker.events import (
    BrokerConnected,
    BrokerDisconnected,
    BrokerEvent,
    ExecutionReceived,
    Heartbeat,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    OrderSubmitted,
)
from alphalab.broker.exceptions import BrokerError
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import (
    ExecutionDecision,
    ReconciliationLog,
    apply_execution,
)
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.broker.transport import (
    VenueProtocolError,
    VenueResponse,
    VenueTransport,
    VenueTransportError,
)
from alphalab.broker.validation import validate_order_submission
from alphalab.common.ids import new_id
from alphalab.core.enums import AssetType
from alphalab.core.enums import OrderStatus as CoreOrderStatus
from alphalab.core.enums import OrderType as CoreOrderType

__all__ = ["RestVenueBroker", "VenueConfig"]

_ORDERS: Final = "/v1/orders"
_EXECUTIONS: Final = "/v1/executions"
_ACCOUNT: Final = "/v1/account"
_POSITIONS: Final = "/v1/positions"


@dataclass(frozen=True, slots=True)
class VenueConfig:
    """How this adapter addresses and retries at its venue.

    Attributes:
        broker_name: Label recorded on connection events and on
            :attr:`~alphalab.broker.state.BrokerState.broker_name`.
        currency: Currency the venue denominates the account in.
        max_attempts: How many times one request may be sent before its failure
            is surfaced. Counts the first attempt, so ``1`` disables retrying.
            Only an unanswered request or a retryable status is ever repeated.
        account_id: Account to address at a multi-account venue. Empty for a
            venue that infers it from the credentials.
    """

    broker_name: str = "VENUE"
    currency: str = "USD"
    max_attempts: int = 3
    account_id: str = ""

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise BrokerError(
                f"VenueConfig.max_attempts must be at least 1, got {self.max_attempts}; "
                "a request that is never sent cannot fail informatively."
            )


def _require(payload: Any, where: str) -> Mapping[str, Any]:
    """The response body as an object, or a protocol error naming where it came from."""

    if not isinstance(payload, Mapping):
        raise VenueProtocolError(
            f"The venue's {where} response is {type(payload).__name__}, not an object. "
            "Nothing is read from it."
        )
    return payload


def _decimal(payload: Mapping[str, Any], key: str, where: str, default: str = "0") -> Decimal:
    """One numeric field, read through ``str`` so a float's binary expansion cannot leak in.

    The rule is :mod:`alphalab.market.normalization`'s, applied at the other
    admission boundary: ``Decimal(str(value))`` keeps the number the venue
    wrote, where ``Decimal(value)`` would keep a float's approximation of it.
    """

    raw = payload.get(key)
    if raw is None:
        raw = default
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise VenueProtocolError(
            f"The venue's {where} response carries {key}={raw!r}, which is not a number."
        ) from exc


def _text(payload: Mapping[str, Any], key: str, where: str) -> str:
    """One required string field."""

    raw = payload.get(key)
    if not isinstance(raw, str) or not raw:
        raise VenueProtocolError(
            f"The venue's {where} response is missing a usable {key!r}: got {raw!r}."
        )
    return raw


def _timestamp(payload: Mapping[str, Any], key: str, where: str) -> float:
    """One Unix-seconds timestamp, as reported by the venue.

    Taken from the venue rather than from a local clock: it is the time the
    *venue* says the thing happened, and it is what reaches
    :class:`~alphalab.execution.report.ExecutionReport` and the portfolio.
    """

    raw = payload.get(key)
    if raw is None:
        raise VenueProtocolError(f"The venue's {where} response carries no {key!r}.")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise VenueProtocolError(
            f"The venue's {where} response carries {key}={raw!r}, which is not a timestamp."
        ) from exc


def _order_status(payload: Mapping[str, Any], where: str) -> CoreOrderStatus:
    """The venue's status as a canonical one.

    :class:`~alphalab.core.enums.OrderStatus` is a ``StrEnum`` whose values are
    the wire spellings, so this is a lookup rather than a translation table that
    could drift from the enum it maps onto. An unrecognised status raises: an
    order in a state AlphaLab cannot name must not be silently treated as
    working, which would leave capital reserved against something the venue has
    already killed.
    """

    raw = _text(payload, "status", where)
    try:
        return CoreOrderStatus(raw)
    except ValueError as exc:
        raise VenueProtocolError(
            f"The venue reported order status {raw!r}, which is not a status AlphaLab "
            f"knows. Known statuses: {sorted(s.value for s in CoreOrderStatus)}."
        ) from exc


@dataclass(frozen=True, slots=True)
class RestVenueBroker:
    """A :class:`~alphalab.broker.protocol.BrokerProtocol` over a real HTTP venue.

    Holds a transport and a configuration, and **no domain state**: every method
    takes the :class:`~alphalab.broker.state.BrokerState` it operates on and
    returns the next one, exactly as ``PaperBroker`` does. The transport is a
    connection handle, not state the contract is about.

    Attributes:
        transport: The effectful seam. Any
            :class:`~alphalab.broker.transport.VenueTransport`; the real one is
            :class:`~alphalab.broker.transport.HttpVenueTransport`.
        config: Naming, denomination and retry policy.
    """

    transport: VenueTransport
    config: VenueConfig = field(default_factory=VenueConfig)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_id() -> str:
        return str(new_id())

    def _send(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> VenueResponse:
        """Send one request, retrying only what may be retried.

        Raises:
            VenueTransportError: If every attempt failed to get an answer. The
                outcome of the request is then genuinely unknown, and saying so
                is the only honest thing to do -- assuming it did not happen
                would lose an order, assuming it did would invent one.
        """

        last: VenueTransportError | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                response = self.transport.request(method, path, query=query, body=body)
            except VenueTransportError as exc:
                last = exc
                continue
            if response.retryable and attempt < self.config.max_attempts:
                continue
            return response

        # Reached only when every attempt raised: a retryable status on the last
        # attempt is returned above, not retried into this branch.
        raise VenueTransportError(
            f"{method} {path} failed on all {self.config.max_attempts} attempts: {last}"
        ) from last

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def connect(
        self, state: BrokerState, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Establish the venue connection by proving the credentials work.

        A connection to a REST venue is not a socket that stays open; it is the
        claim that this process can authenticate and the venue is answering. The
        claim is *tested* -- an account read -- rather than asserted, because
        :attr:`~alphalab.broker.state.ConnectionStatus.CONNECTED` is what the
        pre-trade gate in :func:`~alphalab.runtime.broker_routing.route_order`
        reads before sending an order. Marking a broken connection connected
        would send orders into nothing.

        A venue that answers with a refusal leaves the connection ``FAILED``, and
        one that does not answer at all leaves it ``RECONNECTING``: the first is
        a decision the venue made, the second is an absence that may resolve.
        """

        if state.connection_status is ConnectionStatus.CONNECTED:
            return state, ()

        try:
            response = self._send("GET", _ACCOUNT)
        except VenueTransportError as exc:
            evt = BrokerDisconnected(
                self._generate_id(), timestamp, state.broker_name, f"unreachable: {exc}"
            )
            return (
                replace(
                    state,
                    connection_status=ConnectionStatus.RECONNECTING,
                    events=state.events.append(evt),
                ),
                (evt,),
            )

        if not response.ok:
            evt = BrokerDisconnected(
                self._generate_id(),
                timestamp,
                state.broker_name,
                f"venue refused the connection with status {response.status}",
            )
            return (
                replace(
                    state,
                    connection_status=ConnectionStatus.FAILED,
                    events=state.events.append(evt),
                ),
                (evt,),
            )

        connected = BrokerConnected(self._generate_id(), timestamp, state.broker_name)
        return (
            replace(
                state,
                connection_status=ConnectionStatus.CONNECTED,
                account=self._account_from(response, state.account),
                events=state.events.append(connected),
            ),
            (connected,),
        )

    def disconnect(
        self, state: BrokerState, reason: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Stop treating this venue as reachable.

        Local only, and deliberately: there is no request that makes a venue
        forget an account, and orders already resting there keep resting. What
        this changes is that AlphaLab will not send more until ``connect``
        succeeds again.
        """

        evt = BrokerDisconnected(self._generate_id(), timestamp, state.broker_name, reason)
        return (
            replace(
                state,
                connection_status=ConnectionStatus.DISCONNECTED,
                events=state.events.append(evt),
            ),
            (evt,),
        )

    def heartbeat(
        self, state: BrokerState, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Confirm the venue is still answering, and record when it last did.

        A real request, not a local timestamp bump: a heartbeat that cannot fail
        reports liveness it never checked. A heartbeat that does not come back
        moves the connection to ``RECONNECTING`` and emits a disconnection,
        which closes the pre-trade gate.
        """

        try:
            response = self._send("GET", _ACCOUNT)
        except VenueTransportError as exc:
            evt = BrokerDisconnected(
                self._generate_id(), timestamp, state.broker_name, f"heartbeat failed: {exc}"
            )
            return (
                replace(
                    state,
                    connection_status=ConnectionStatus.RECONNECTING,
                    events=state.events.append(evt),
                ),
                (evt,),
            )

        if not response.ok:
            evt = BrokerDisconnected(
                self._generate_id(),
                timestamp,
                state.broker_name,
                f"heartbeat refused with status {response.status}",
            )
            return (
                replace(
                    state,
                    connection_status=ConnectionStatus.RECONNECTING,
                    events=state.events.append(evt),
                ),
                (evt,),
            )

        beat = Heartbeat(self._generate_id(), timestamp, state.broker_name)
        return (
            replace(
                state,
                account=self._account_from(response, state.account),
                events=state.events.append(beat),
                last_heartbeat=timestamp,
            ),
            (beat,),
        )

    def status(self, state: BrokerState) -> ConnectionStatus:
        """Current connectivity, as last established."""

        return state.connection_status

    # ------------------------------------------------------------------
    # Order flow
    # ------------------------------------------------------------------

    def submit_order(
        self, state: BrokerState, order: BrokerOrder, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Send one order to the venue and record what it answered.

        The order is addressed by ``broker_order_id``, which
        :func:`~alphalab.runtime.broker_routing.broker_order_id_for` derived from
        the OMS order id. The venue treats it as the idempotency key, so sending
        the same order twice -- after a lost response, say -- reaches the order
        that already exists instead of opening a second one.

        A refusal is applied, not raised: the order becomes ``REJECTED`` in this
        state and an :class:`~alphalab.broker.events.OrderRejected` is emitted,
        which is what lets the OMS retire it and release its reservation.

        Raises:
            BrokerValidationError: If the order is structurally invalid, or its
                ``broker_order_id`` is already in this state. Checked before the
                request, so a malformed order costs no round trip.
            VenueTransportError: If the venue never answered.
            VenueProtocolError: If it answered something unreadable.
        """

        validate_order_submission(state, order)

        submitted = OrderSubmitted(
            self._generate_id(), timestamp, order.broker_order_id, order.oms_order_id
        )
        response = self._send("POST", _ORDERS, body=self._submission(order))

        if not response.ok:
            reason = self._refusal(response)
            rejected = OrderRejected(self._generate_id(), timestamp, order.broker_order_id, reason)
            dead = replace(order, status=CoreOrderStatus.REJECTED, updated_at=timestamp)
            return (
                replace(
                    state,
                    orders=state.orders.set(order.broker_order_id, dead),
                    events=state.events.extend([submitted, rejected]),
                ),
                (submitted, rejected),
            )

        acknowledged = self._order_from(response, order, timestamp)
        accepted = OrderAccepted(self._generate_id(), timestamp, order.broker_order_id)
        return (
            replace(
                state,
                orders=state.orders.set(order.broker_order_id, acknowledged),
                events=state.events.extend([submitted, accepted]),
            ),
            (submitted, accepted),
        )

    def cancel_order(
        self, state: BrokerState, broker_order_id: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Ask the venue to cancel a working order.

        The venue is the authority on whether it could be cancelled: an order
        that filled while the request was in flight cannot be, and the venue
        says so with a refusal. A refusal therefore leaves the order exactly as
        it was and emits nothing, rather than marking locally cancelled
        something the venue still holds.

        Raises:
            BrokerValidationError: If this state has no such order.
            VenueTransportError: If the venue never answered.
        """

        if broker_order_id not in state.orders:
            raise BrokerError(
                f"Order {broker_order_id!r} is not known to this broker state, so there "
                "is nothing to cancel. Cancelling an order this process never sent "
                "would be a request about someone else's order."
            )

        response = self._send("DELETE", f"{_ORDERS}/{broker_order_id}")
        if not response.ok:
            return state, ()

        order = state.orders[broker_order_id]
        cancelled = replace(order, status=CoreOrderStatus.CANCELLED, updated_at=timestamp)
        evt = OrderCancelled(self._generate_id(), timestamp, broker_order_id)
        return (
            replace(
                state,
                orders=state.orders.set(broker_order_id, cancelled),
                events=state.events.append(evt),
            ),
            (evt,),
        )

    def replace_order(
        self,
        state: BrokerState,
        broker_order_id: str,
        new_quantity: Decimal,
        new_price: Decimal,
        timestamp: float,
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Amend a working order's quantity or price at the venue.

        The amended order is read back from the venue's answer rather than
        assumed: a venue may amend to less than was asked, and the quantity that
        matters is the one it is now working.

        Raises:
            BrokerError: If this state has no such order.
            VenueTransportError: If the venue never answered.
        """

        if broker_order_id not in state.orders:
            raise BrokerError(
                f"Order {broker_order_id!r} is not known to this broker state, so there "
                "is nothing to replace."
            )

        order = state.orders[broker_order_id]
        response = self._send(
            "PATCH",
            f"{_ORDERS}/{broker_order_id}",
            body={"quantity": str(new_quantity), "price": str(new_price)},
        )
        if not response.ok:
            return state, ()

        amended = self._order_from(response, order, timestamp)
        return replace(state, orders=state.orders.set(broker_order_id, amended)), ()

    def order_status(self, state: BrokerState, broker_order_id: str) -> BrokerOrder | None:
        """The order as AlphaLab currently believes the venue holds it.

        Reads local state and performs **no** request, which is what
        :class:`~alphalab.broker.protocol.BrokerProtocol` specifies -- the method
        is pure in every adapter. Asking the venue is
        :meth:`fetch_order`, which is effectful and named so.
        """

        return state.orders.get(broker_order_id)

    def fetch_order(self, state: BrokerState, broker_order_id: str) -> BrokerOrder:
        """Read one order back from the venue, refreshing what this state holds.

        The recovery primitive: after a lost response or a restart, this is how
        AlphaLab learns what the venue actually did with an order it may or may
        not have received.

        Raises:
            BrokerError: If this state has no such order.
            VenueTransportError: If the venue never answered.
            VenueProtocolError: If the venue's answer is unreadable.
        """

        if broker_order_id not in state.orders:
            raise BrokerError(f"Order {broker_order_id!r} is not known to this broker state.")

        response = self._send("GET", f"{_ORDERS}/{broker_order_id}")
        if not response.ok:
            raise VenueProtocolError(
                f"The venue answered {response.status} for order {broker_order_id!r}: "
                f"{self._refusal(response)}"
            )
        return self._order_from(response, state.orders[broker_order_id], 0.0)

    # ------------------------------------------------------------------
    # Execution reception
    # ------------------------------------------------------------------

    def apply_execution(
        self, state: BrokerState, execution: BrokerExecution, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Apply a fill the venue reported, once.

        Delegates entirely to
        :func:`~alphalab.broker.reconciliation.apply_execution`, which is the one
        implementation of the rules in AlphaLab: a redelivered fill is a
        ``DUPLICATE`` no-op, a fill for an unknown order is never applied, a fill
        against a terminal order is a break, and an overfill is refused. A live
        adapter reusing that function rather than restating it is what makes a
        venue redelivering its whole fill history after a reconnect safe.

        No event is emitted for a fill that was not applied. A duplicate is not
        something that happened.
        """

        next_state, decision, _ = apply_execution(state, execution)
        if not decision.applied:
            return next_state, ()

        evt = ExecutionReceived(
            self._generate_id(),
            timestamp,
            execution.execution_id,
            execution.broker_order_id,
            execution.fill_quantity,
            execution.fill_price,
        )
        return replace(next_state, events=next_state.events.append(evt)), (evt,)

    def poll_executions(
        self, state: BrokerState, timestamp: float, *, since: str = ""
    ) -> tuple[BrokerState, tuple[BrokerExecution, ...], tuple[BrokerEvent, ...]]:
        """Ask the venue for fills and apply every one it reports.

        The inbound leg for a REST venue, and deliberately *not* part of
        :class:`~alphalab.broker.protocol.BrokerProtocol`: how fills arrive is a
        venue's business, and a push-based venue delivers the same
        :class:`~alphalab.broker.execution.BrokerExecution` values to
        :meth:`apply_execution` without any polling at all.

        ``since`` is the venue's cursor. Passing it asks only for what is new;
        omitting it asks for everything the venue still holds, which is what a
        recovering process wants -- every already-seen fill is then classified as
        a duplicate and applied zero times.

        Returns the next state, the executions that were genuinely *applied*
        (not the ones the venue sent, which may include duplicates), and the
        events those applications produced.

        Raises:
            VenueTransportError: If the venue never answered.
            VenueProtocolError: If the fill list is unreadable.
        """

        query = {"since": since} if since else None
        response = self._send("GET", _EXECUTIONS, query=query)
        if not response.ok:
            raise VenueProtocolError(
                f"The venue answered {response.status} for executions: {self._refusal(response)}"
            )

        payload = _require(response.json(), "executions")
        raw = payload.get("executions", ())
        if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
            raise VenueProtocolError(
                f"The venue's executions response carries {type(raw).__name__} where a "
                "list of fills was expected."
            )

        current = state
        applied: list[BrokerExecution] = []
        events: list[BrokerEvent] = []
        for entry in raw:
            execution = self._execution_from(_require(entry, "executions"))
            current, produced = self.apply_execution(current, execution, timestamp)
            if produced:
                applied.append(execution)
                events.extend(produced)
        return current, tuple(applied), tuple(events)

    def reconcilable_executions(
        self, state: BrokerState, execution: BrokerExecution
    ) -> ExecutionDecision:
        """What would happen to ``execution`` without applying it.

        Exposes :func:`~alphalab.broker.reconciliation.classify_execution`'s
        answer so an operator can inspect a break before deciding, which is what
        :class:`~alphalab.broker.reconciliation.ReconciliationLog` records.
        """

        _, decision, _ = apply_execution(state, execution, ReconciliationLog())
        return decision

    # ------------------------------------------------------------------
    # Account and positions
    # ------------------------------------------------------------------

    def account(self, state: BrokerState) -> BrokerAccount:
        """The account as last read from the venue.

        Pure, as the contract requires. :meth:`connect` and :meth:`heartbeat`
        are what refresh it.
        """

        return state.account

    def positions(self, state: BrokerState) -> Sequence[BrokerPosition]:
        """Open positions as last read from the venue. Pure; see :meth:`account`."""

        return tuple(state.positions.values())

    def fetch_positions(self, state: BrokerState) -> BrokerState:
        """Read the venue's positions and replace what this state holds.

        The venue's book, for reconciliation against AlphaLab's own -- which is
        :func:`~alphalab.broker.reconciliation.reconcile`'s input, and which that
        function *states* the differences of rather than resolving them.

        Raises:
            VenueTransportError: If the venue never answered.
            VenueProtocolError: If the position list is unreadable.
        """

        response = self._send("GET", _POSITIONS)
        if not response.ok:
            raise VenueProtocolError(
                f"The venue answered {response.status} for positions: {self._refusal(response)}"
            )

        payload = _require(response.json(), "positions")
        raw = payload.get("positions", ())
        if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
            raise VenueProtocolError(
                f"The venue's positions response carries {type(raw).__name__} where a "
                "list of positions was expected."
            )

        book = state.positions
        for entry in raw:
            position = self._position_from(_require(entry, "positions"))
            book = book.set(position.symbol, position)
        return replace(state, positions=book)

    # ------------------------------------------------------------------
    # Wire translation
    # ------------------------------------------------------------------

    def _submission(self, order: BrokerOrder) -> Mapping[str, Any]:
        """The venue request body for one order.

        Quantities and prices are sent as **strings**, not JSON numbers: a
        ``Decimal`` serialized as a float would be rounded to binary on the way
        out, which is the same precision loss
        :mod:`alphalab.market.normalization` refuses on the way in.
        """

        body: dict[str, Any] = {
            "client_order_id": order.broker_order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.order_type.value,
            "quantity": str(order.quantity),
            "time_in_force": order.tif.value,
        }
        if order.order_type in {CoreOrderType.LIMIT, CoreOrderType.STOP_LIMIT}:
            body["price"] = str(order.price)
        if order.order_type in {CoreOrderType.STOP, CoreOrderType.STOP_LIMIT}:
            body["stop_price"] = str(order.stop_price)
        if self.config.account_id:
            body["account_id"] = self.config.account_id
        return body

    @staticmethod
    def _refusal(response: VenueResponse) -> str:
        """Why the venue refused, as a sentence, without ever failing itself.

        A refusal that cannot be read is still a refusal, so an unreadable body
        yields the status rather than raising: the order is rejected either way,
        and losing that fact to a parse error would leave it working forever.
        """

        try:
            payload = response.json()
        except VenueProtocolError:
            return f"status {response.status}"
        if isinstance(payload, Mapping):
            message = payload.get("message") or payload.get("error")
            if isinstance(message, str) and message:
                return f"status {response.status}: {message}"
        return f"status {response.status}"

    @staticmethod
    def _order_from(response: VenueResponse, sent: BrokerOrder, timestamp: float) -> BrokerOrder:
        """The venue's view of an order, folded onto the order that was sent.

        Identity stays AlphaLab's: ``broker_order_id`` is the derived client id
        this adapter addresses by, and ``oms_order_id`` is the order it belongs
        to. What the venue is authoritative for -- status, filled quantity,
        average price -- is taken from the answer. The venue's own internal
        handle, if it sent one, is kept on ``account_id``'s sibling field only
        where the contract has a home for it; it is not allowed to displace the
        client id, because every later request addresses that.
        """

        payload = _require(response.json(), "order")
        where = f"order {sent.broker_order_id!r}"
        filled = _decimal(payload, "filled_quantity", where)
        return replace(
            sent,
            status=_order_status(payload, where),
            filled_quantity=filled,
            average_fill_price=_decimal(payload, "average_fill_price", where),
            quantity=_decimal(payload, "quantity", where, default=str(sent.quantity)),
            updated_at=(
                _timestamp(payload, "updated_at", where) if "updated_at" in payload else timestamp
            ),
        )

    def _execution_from(self, payload: Mapping[str, Any]) -> BrokerExecution:
        """One venue fill as a canonical :class:`BrokerExecution`.

        ``execution_id`` is the venue's, verbatim, because it is the
        deduplication key -- see this module's docstring. ``broker_order_id`` is
        read from the venue's ``client_order_id``, which is what this adapter
        addressed the order by and therefore what this state is keyed on.
        """

        where = "executions"
        return BrokerExecution(
            execution_id=_text(payload, "execution_id", where),
            broker_order_id=_text(payload, "client_order_id", where),
            symbol=_text(payload, "symbol", where),
            fill_quantity=_decimal(payload, "fill_quantity", where),
            fill_price=_decimal(payload, "fill_price", where),
            commission=_decimal(payload, "commission", where),
            timestamp=_timestamp(payload, "timestamp", where),
            account_id=self.config.account_id,
            external_id=str(payload.get("venue_execution_id", "")),
        )

    def _account_from(self, response: VenueResponse, previous: BrokerAccount) -> BrokerAccount:
        """The venue's account snapshot, or the previous one if it sent nothing usable."""

        payload = _require(response.json(), "account")
        where = "account"
        return replace(
            previous,
            account_id=str(payload.get("account_id", previous.account_id)),
            cash=_decimal(payload, "cash", where, default=str(previous.cash)),
            equity=_decimal(payload, "equity", where, default=str(previous.equity)),
            buying_power=_decimal(
                payload, "buying_power", where, default=str(previous.buying_power)
            ),
            margin=_decimal(payload, "margin", where, default=str(previous.margin)),
            available_funds=_decimal(
                payload, "available_funds", where, default=str(previous.available_funds)
            ),
            currency=str(payload.get("currency", self.config.currency)),
        )

    @staticmethod
    def _position_from(payload: Mapping[str, Any]) -> BrokerPosition:
        """One venue position as a canonical :class:`BrokerPosition`."""

        where = "positions"
        raw_class = str(payload.get("asset_class", AssetType.EQUITY.value))
        try:
            asset_class = AssetType(raw_class)
        except ValueError as exc:
            raise VenueProtocolError(
                f"The venue reported asset class {raw_class!r}, which AlphaLab does not "
                f"know. Known classes: {sorted(a.value for a in AssetType)}."
            ) from exc

        return BrokerPosition(
            symbol=_text(payload, "symbol", where),
            quantity=_decimal(payload, "quantity", where),
            average_price=_decimal(payload, "average_price", where),
            market_value=_decimal(payload, "market_value", where),
            unrealized_pnl=_decimal(payload, "unrealized_pnl", where),
            realized_pnl=_decimal(payload, "realized_pnl", where),
            account_id=str(payload.get("account_id", "")),
            asset_class=asset_class,
            market_price=_decimal(payload, "market_price", where),
        )
