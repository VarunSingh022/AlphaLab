"""A real HTTP venue, on a real socket, for the execution transport to talk to.

AlphaLab's development and CI environment has no network egress and holds no
vendor credentials, so :class:`~alphalab.broker.transport.HttpVenueTransport`
cannot be pointed at a commercial venue from here. The alternative is not to
stub the transport -- a test that replaces the thing under test proves nothing
about it -- but to stand up the *other end* of the protocol locally and drive
the real transport into it over TCP.

Everything below the adapter is therefore genuine: a socket is opened, a signed
HTTP request is written to it, this server verifies the signature, applies the
request to its book, and writes a JSON response back. The only thing that is not
real is the venue's identity.

What this server checks, because a venue checks it
--------------------------------------------------
* **The signature.** Recomputed over ``timestamp + method + path + body`` with
  the shared secret and compared in constant time. A wrong secret, a tampered
  body or a replayed path gets ``401``.
* **The timestamp window.** A signature older than
  :data:`_MAX_CLOCK_SKEW_MS` is refused, which is what stops a captured request
  being replayed later.
* **The idempotency key.** ``client_order_id`` is unique per order. Submitting
  the same one twice returns the *existing* order with ``200`` rather than
  opening a second one -- the behaviour AlphaLab's derived client order id
  depends on for a retry after a lost response to be safe.

What it can be told to do
-------------------------
:class:`VenueScript` makes the failure modes reachable: refuse the next N
requests with a status, drop the connection without answering, reject a
submission, or fill an order partially and then fully. Without a script it is a
plain venue that accepts and rests every order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

from alphalab.broker.transport import VenueCredentials

#: How far a request's signature timestamp may be from the server's clock.
_MAX_CLOCK_SKEW_MS: Final = 30_000

_ENCODING: Final = "utf-8"


@dataclass
class VenueScript:
    """What the venue should do next, beyond simply working.

    Every field is consumed as it is used, so a script describes a *sequence* of
    conditions rather than a permanent mode: ``refuse_next=2`` refuses two
    requests and then behaves normally, which is what makes a retry test mean
    something.

    Attributes:
        refuse_next: Statuses to answer the next requests with, in order.
        drop_next: How many of the next requests to answer by closing the
            connection without a response. This is what a transport timeout and
            a dropped connection look like from the client side.
        reject_submissions: ``client_order_id`` -> the reason the venue refuses
            it. A rejection is a decision, and comes back as ``422``.
        fills: ``client_order_id`` -> fills to report for it, each a mapping of
            the execution fields. Appended to the venue's fill log when the
            order is submitted, so they are waiting to be polled.
    """

    refuse_next: list[int] = field(default_factory=list)
    drop_next: int = 0
    reject_submissions: dict[str, str] = field(default_factory=dict)
    fills: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class VenueBook:
    """The venue's own record of what it holds.

    Deliberately the venue's books and not AlphaLab's: the point of
    reconciliation is that the two can disagree, so this must be able to hold
    something different from what the run believes.
    """

    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    executions: list[dict[str, Any]] = field(default_factory=list)
    account: dict[str, Any] = field(
        default_factory=lambda: {
            "account_id": "ACCT-1",
            "cash": "100000",
            "equity": "100000",
            "buying_power": "200000",
            "margin": "0",
            "available_funds": "100000",
            "currency": "USD",
        }
    )
    positions: list[dict[str, Any]] = field(default_factory=list)

    #: Every request the server accepted, as ``(method, path)``. What proves a
    #: retry actually went down the wire rather than being answered from a cache.
    requests: list[tuple[str, str]] = field(default_factory=list)


class _Handler(BaseHTTPRequestHandler):
    """One request. Verifies it, applies it, answers it."""

    # Set by `run_venue`; the server object carries them.
    server: Any

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the default stderr access log; the tests read `book.requests`."""

    # -- verification ----------------------------------------------------

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _authentic(self, body: bytes) -> bool:
        credentials: VenueCredentials = self.server.credentials
        key = self.headers.get("X-ALPHALAB-KEY") or ""
        raw_timestamp = self.headers.get("X-ALPHALAB-TIMESTAMP") or ""
        signature = self.headers.get("X-ALPHALAB-SIGNATURE") or ""
        if key != credentials.api_key or not raw_timestamp or not signature:
            return False

        try:
            sent_at = int(raw_timestamp)
        except ValueError:
            return False
        if abs(self.server.now_ms() - sent_at) > _MAX_CLOCK_SKEW_MS:
            return False

        message = f"{sent_at}{self.command}{self.path}".encode(_ENCODING) + body
        expected = hmac.new(
            credentials.api_secret.encode(_ENCODING), message, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # -- answering -------------------------------------------------------

    def _respond(self, status: int, payload: Mapping[str, Any] | None = None) -> None:
        body = json.dumps(payload if payload is not None else {}).encode(_ENCODING)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        body = self._body()
        script: VenueScript = self.server.script
        book: VenueBook = self.server.book

        with self.server.lock:
            if script.drop_next > 0:
                script.drop_next -= 1
                # No response at all: the client sees the connection close
                # mid-request, which is exactly a transport failure.
                self.close_connection = True
                return

            if script.refuse_next:
                self._respond(script.refuse_next.pop(0), {"message": "scripted refusal"})
                return

            if not self._authentic(body):
                self._respond(401, {"message": "signature verification failed"})
                return

            book.requests.append((self.command, self.path))
            path = self.path.split("?", 1)[0]
            query = self.path.split("?", 1)[1] if "?" in self.path else ""

            if path == "/v1/account" and self.command == "GET":
                self._respond(200, book.account)
            elif path == "/v1/positions" and self.command == "GET":
                self._respond(200, {"positions": book.positions})
            elif path == "/v1/executions" and self.command == "GET":
                self._respond(200, {"executions": self._executions_since(query)})
            elif path == "/v1/orders" and self.command == "POST":
                self._submit(body)
            elif path.startswith("/v1/orders/"):
                self._order_request(path.rsplit("/", 1)[-1], body)
            else:
                self._respond(404, {"message": f"no route for {self.command} {path}"})

    def _executions_since(self, query: str) -> list[dict[str, Any]]:
        """Fills after the cursor, or all of them when none was given."""

        book: VenueBook = self.server.book
        cursor = ""
        for part in query.split("&"):
            name, _, value = part.partition("=")
            if name == "since":
                cursor = value
        if not cursor:
            return list(book.executions)

        ids = [entry["execution_id"] for entry in book.executions]
        if cursor not in ids:
            return list(book.executions)
        return list(book.executions[ids.index(cursor) + 1 :])

    def _submit(self, body: bytes) -> None:
        book: VenueBook = self.server.book
        script: VenueScript = self.server.script
        try:
            request = json.loads(body.decode(_ENCODING))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, {"message": "body is not JSON"})
            return

        client_order_id = request.get("client_order_id")
        if not client_order_id:
            self._respond(400, {"message": "client_order_id is required"})
            return

        # The idempotency rule. A resend of an order the venue already has
        # returns that order rather than opening a second one.
        existing = book.orders.get(client_order_id)
        if existing is not None:
            self._respond(200, existing)
            return

        reason = script.reject_submissions.get(client_order_id)
        if reason is not None:
            self._respond(422, {"message": reason})
            return

        order = {
            "client_order_id": client_order_id,
            "venue_order_id": f"V-{len(book.orders) + 1}",
            "symbol": request.get("symbol", ""),
            "side": request.get("side", "buy"),
            "type": request.get("type", "market"),
            "quantity": str(request.get("quantity", "0")),
            "filled_quantity": "0",
            "average_fill_price": "0",
            "status": "accepted",
            "updated_at": 1.0,
        }
        book.orders[client_order_id] = order
        for entry in script.fills.pop(client_order_id, []):
            _attach(book, client_order_id, entry)
        self._respond(201, order)

    def _order_request(self, client_order_id: str, body: bytes) -> None:
        book: VenueBook = self.server.book
        order = book.orders.get(client_order_id)
        if order is None:
            self._respond(404, {"message": f"no order {client_order_id}"})
            return

        if self.command == "GET":
            self._respond(200, order)
        elif self.command == "DELETE":
            if order["status"] in {"filled", "cancelled", "rejected"}:
                self._respond(409, {"message": f"order is {order['status']}"})
                return
            order["status"] = "cancelled"
            self._respond(200, order)
        elif self.command == "PATCH":
            amendment = json.loads(body.decode(_ENCODING)) if body else {}
            order["quantity"] = str(amendment.get("quantity", order["quantity"]))
            self._respond(200, order)
        else:
            self._respond(405, {"message": f"{self.command} not allowed"})

    # `BaseHTTPRequestHandler` dispatches by method name.
    do_GET = _handle
    do_POST = _handle
    do_DELETE = _handle
    do_PATCH = _handle


def fill(
    execution_id: str,
    quantity: str,
    price: str,
    *,
    timestamp: float = 2.0,
    commission: str = "0",
) -> dict[str, Any]:
    """One fill as the venue reports it, for :attr:`VenueScript.fills`."""

    return {
        "execution_id": execution_id,
        "venue_execution_id": f"VX-{execution_id}",
        "symbol": "",
        "fill_quantity": quantity,
        "fill_price": price,
        "commission": commission,
        "timestamp": timestamp,
    }


def _attach(book: VenueBook, client_order_id: str, entry: Mapping[str, Any]) -> None:
    """Add one fill to the venue's log, against the order it belongs to.

    The symbol comes from the order when the fill does not carry one: a venue
    knows what it filled, and a fill that named no instrument would be
    unreadable -- which the adapter correctly refuses.
    """

    order = book.orders[client_order_id]
    symbol = entry.get("symbol") or order["symbol"]
    book.executions.append({**entry, "client_order_id": client_order_id, "symbol": symbol})


def record_fill(book: VenueBook, client_order_id: str, entry: Mapping[str, Any]) -> None:
    """Report a fill for an order that is already at the venue.

    What a venue does when an order rests and later trades, and what makes a
    *second* poll return something the first did not.
    """

    _attach(book, client_order_id, entry)
    order = book.orders[client_order_id]
    filled = Decimal(order["filled_quantity"]) + Decimal(str(entry["fill_quantity"]))
    order["filled_quantity"] = str(filled)
    order["average_fill_price"] = str(entry["fill_price"])
    order["status"] = "filled" if filled >= Decimal(order["quantity"]) else "partially_filled"


@contextmanager
def run_venue(
    credentials: VenueCredentials,
    *,
    script: VenueScript | None = None,
    book: VenueBook | None = None,
    now_ms: Any = None,
) -> Iterator[tuple[str, VenueBook, VenueScript]]:
    """Serve a venue on a loopback port for the duration of the block.

    Yields ``(base_url, book, script)``. Port ``0`` lets the OS choose, so
    concurrent test runs never collide.
    """

    import time

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.credentials = credentials  # type: ignore[attr-defined]
    server.book = book if book is not None else VenueBook()  # type: ignore[attr-defined]
    server.script = script if script is not None else VenueScript()  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    server.now_ms = now_ms or (lambda: int(time.time() * 1000))  # type: ignore[attr-defined]

    # `serve_forever`'s default 0.5s poll interval is what `shutdown()` waits
    # on, so it would add half a second of pure latency to every test that
    # uses this server. The loop is idle between requests either way.
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    host, port = str(server.server_address[0]), server.server_address[1]
    try:
        yield f"http://{host}:{port}", server.book, server.script  # type: ignore[attr-defined]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
