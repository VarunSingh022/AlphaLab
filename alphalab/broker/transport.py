"""The effectful seam between a broker adapter and a real venue.

:mod:`alphalab.broker.protocol` is pure: every method takes a
:class:`~alphalab.broker.state.BrokerState` and returns the next one. That is
what lets one adapter be driven by a backtest, a paper run and a live session
without behaving differently, and it is deliberately silent about *how* an
adapter reaches a venue -- "HTTP, FIX, a socket, a vendor SDK", none of it
visible at the contract.

This module is that "how", named and made testable, for the HTTP case. It is
the same shape :mod:`alphalab.marketdata.transport` established for market data
and :mod:`alphalab.persistence.run_store` for durability: a narrow protocol
naming the minimal effectful operation, one real implementation over the
standard library, and no second one. There is deliberately **no in-module
canned transport**: a double that answers ``accepted`` to every submission is
the "silently fake data with no seam to ever make it real" failure that module's
docstring names, and for an order router it is worse than useless. The tests
run against a real local HTTP server speaking the protocol below, over a real
socket -- see ``tests/integration/venue_server.py``.

Why market data's transport could not be reused
-----------------------------------------------
:class:`~alphalab.marketdata.transport.Transport` is ``get(url, params)``:
unauthenticated, GET-only, and returning bytes with no status. Order submission
needs a request body, a method that is not GET, an authenticated signature, and
the status code -- because a venue refusing an order (``4xx``) and a venue being
unreachable (a socket error) are different facts that must never be conflated.
Widening market data's transport to carry all of that would have put order
authentication into the vocabulary of every price client.

The wire protocol this speaks
-----------------------------
A JSON-over-HTTP venue with HMAC request signing, which is what the public
trading APIs of real venues look like. Concretely:

======================================  ===========================================
``POST   /v1/orders``                   submit; body carries ``client_order_id``
``GET    /v1/orders/{client_order_id}`` current state of one order
``DELETE /v1/orders/{client_order_id}`` cancel
``PATCH  /v1/orders/{client_order_id}`` replace quantity and/or price
``GET    /v1/executions``               fills since a cursor
``GET    /v1/account``                  account snapshot
``GET    /v1/positions``                open positions
======================================  ===========================================

**Orders are addressed by the client order id, not the venue's.** That is what
makes submission idempotent over a lost response:
:func:`~alphalab.runtime.broker_routing.broker_order_id_for` derives the client
id from the OMS order id, so a retry addresses the same order rather than
creating a second one, and the venue -- not AlphaLab's memory of it -- is the
thing that deduplicates.

Authentication, and what is never written down
----------------------------------------------
Each request is signed ``HMAC-SHA256(secret, timestamp + method + path + body)``
and carries the key, timestamp and signature in headers. The secret signs and is
never transmitted.

Transport security
------------------
An ``https://`` venue is reached with :func:`~alphalab.common.tls.tls_context`
-- the same policy :mod:`alphalab.marketdata.websocket` uses, defined once in
:mod:`alphalab.common.tls`. It verifies the server certificate and the hostname,
and pins TLS 1.2 as the floor rather than inheriting whatever the host's OpenSSL
allows. Passing it explicitly is the point: ``urlopen`` would otherwise build
its own context from ``ssl._create_default_https_context``, and an order
submission must not inherit its security properties from a machine's
configuration. See ADR-0031.

:class:`VenueCredentials` renders as ``VenueCredentials(api_key='ABCD...')`` and
nothing else: the secret is excluded from ``repr``, from ``str``, from equality
and from every error raised here. A credential value has no path into a log
line, a traceback, a snapshot or a test fixture, because the object that holds
it will not render it. See ADR-0031.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol
from urllib.parse import urlencode

from alphalab.broker.exceptions import BrokerError
from alphalab.common.constants import DEFAULT_ENCODING
from alphalab.common.tls import tls_context

__all__ = [
    "HttpVenueTransport",
    "VenueCredentials",
    "VenueProtocolError",
    "VenueResponse",
    "VenueTransport",
    "VenueTransportError",
]

#: Header carrying the public key identifying the account.
_KEY_HEADER: Final = "X-ALPHALAB-KEY"

#: Header carrying the Unix-millisecond timestamp the signature covers.
_TIMESTAMP_HEADER: Final = "X-ALPHALAB-TIMESTAMP"

#: Header carrying the hex HMAC-SHA256 signature of the canonical request.
_SIGNATURE_HEADER: Final = "X-ALPHALAB-SIGNATURE"

#: How many leading characters of an API key an error or ``repr`` may show.
_KEY_PREFIX: Final = 4


class VenueTransportError(BrokerError):
    """The venue could not be reached, or did not answer in time.

    Distinct from a venue *refusing* something, which arrives as a ``4xx``
    :class:`VenueResponse` and is a fact about the order. This is the absence of
    an answer: the request may or may not have been acted on, which is exactly
    why submission is idempotent in the client order id.
    """


class VenueProtocolError(BrokerError):
    """The venue answered, and the answer was not something this can read.

    Malformed JSON, a body that is not an object, or a field missing where the
    protocol requires one. Raised rather than defaulted: an order response whose
    status could not be read is not an order in an unknown state, it is a
    response that must not be acted on.
    """


@dataclass(frozen=True)
class VenueCredentials:
    """An API key and the secret that signs with it.

    ``api_secret`` is excluded from ``repr`` and from equality. It is read by
    :meth:`sign` and by nothing else in AlphaLab, and it is never placed in an
    error message, an event, a snapshot or a log line.

    Not ``slots=True``: the redacted ``repr`` is generated by ``dataclass``
    through ``field(repr=False)``, and nothing here needs the layout.

    Attributes:
        api_key: The public identifier of the account. Appears in request
            headers and, truncated, in errors.
        api_secret: The signing secret. Never transmitted and never rendered.

    Raises:
        BrokerError: If either value is empty. A blank credential would sign
            every request identically and fail at the venue with an error that
            says nothing about the cause.
    """

    api_key: str
    api_secret: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise BrokerError("VenueCredentials.api_key cannot be empty.")
        if not self.api_secret.strip():
            raise BrokerError(
                "VenueCredentials.api_secret cannot be empty. An unsigned request "
                "is refused by the venue, not accepted unauthenticated."
            )

    @property
    def redacted_key(self) -> str:
        """The key as it may appear in an error: a short prefix and nothing more."""

        return f"{self.api_key[:_KEY_PREFIX]}..."

    def sign(self, timestamp_ms: int, method: str, path: str, body: bytes) -> str:
        """The hex HMAC-SHA256 signature of one canonical request.

        The signed string is ``timestamp + method + path + body``, in that order
        and with no separators, so a signature is bound to the exact request:
        replaying it against another path, another method or another body does
        not verify.
        """

        message = f"{timestamp_ms}{method.upper()}{path}".encode(DEFAULT_ENCODING) + body
        return hmac.new(
            self.api_secret.encode(DEFAULT_ENCODING), message, hashlib.sha256
        ).hexdigest()

    def __str__(self) -> str:
        return f"VenueCredentials(api_key={self.redacted_key!r})"


@dataclass(frozen=True, slots=True)
class VenueResponse:
    """One venue answer: the status it returned and the bytes it sent.

    Carrying the status rather than raising on it is deliberate. ``422 order
    would exceed buying power`` is the venue rejecting an order -- a lifecycle
    fact the adapter turns into a rejection event -- and not a transport
    failure. Only the absence of an answer is an exception here.
    """

    status: int
    body: bytes

    @property
    def ok(self) -> bool:
        """Whether the venue accepted and acted on the request."""

        return 200 <= self.status < 300

    @property
    def retryable(self) -> bool:
        """Whether re-sending this exact request could plausibly succeed.

        A ``5xx`` is the venue failing to answer for its own reasons, and ``429``
        is it asking for less traffic. Every other refusal is a decision about
        the request, and repeating it would only be refused again.
        """

        return self.status >= 500 or self.status == 429

    def json(self) -> Any:
        """The body decoded as JSON.

        Raises:
            VenueProtocolError: If the body is not valid UTF-8 JSON. Nothing
                partial is returned from a body that could not be read.
        """

        try:
            return json.loads(self.body.decode(DEFAULT_ENCODING))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VenueProtocolError(
                f"The venue answered {self.status} with a body that is not readable "
                f"JSON: {exc}. Nothing is read from it."
            ) from exc


class VenueTransport(Protocol):
    """The one effectful operation a venue adapter needs.

    Deliberately a single method. Everything a venue is asked -- submit, cancel,
    replace, status, executions, account, positions -- is one authenticated
    request/response, and naming each of them here would put the venue's REST
    surface into the transport contract, which is the layering
    :class:`~alphalab.broker.protocol.BrokerProtocol` exists to avoid.
    """

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> VenueResponse:
        """Send one signed request and return what the venue answered.

        Raises:
            VenueTransportError: If no answer arrived.
        """
        ...


@dataclass(frozen=True, slots=True)
class HttpVenueTransport:
    """A real HTTP venue transport. Standard library only.

    Genuinely effectful: it opens a socket, signs the request, sends it and
    reads the answer. There is no branch in this class that returns a value
    without having done so.

    **Not verified against any commercial venue.** AlphaLab's development and CI
    environment has no network egress, and no vendor credentials exist in this
    repository. What *is* verified is the protocol: the integration suite runs
    this class against a local HTTP server over a real socket, and that server
    checks the signature, the timestamp window and the idempotency key the way a
    venue does. Pointing this at a vendor additionally requires that vendor's
    request shapes, which differ per venue and are the adapter's business rather
    than the transport's.

    Attributes:
        base_url: Venue root, without a trailing slash.
        credentials: The key and secret every request is signed with.
        timeout_seconds: How long to wait for an answer before giving up. A
            request that times out has an unknown outcome, which is why
            submissions are addressed by a derived client order id.
    """

    base_url: str
    credentials: VenueCredentials
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise BrokerError("HttpVenueTransport.base_url cannot be empty.")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        if self.timeout_seconds <= 0:
            raise BrokerError(
                f"HttpVenueTransport.timeout_seconds must be positive, got "
                f"{self.timeout_seconds}. A non-positive timeout would either "
                "fail every request or wait forever."
            )

    @staticmethod
    def _now_ms() -> int:
        """The wall clock, in Unix milliseconds.

        The one clock reading in this module, and it is a *signature* input
        rather than a domain timestamp: a venue rejects a request signed too far
        from its own clock. Nothing on the execution path reads it -- domain
        timestamps come from the venue's own response. See ADR-0030's rule that
        the only clock in a run is the ``now`` a driver passes to ``advance``.
        """

        return int(time.time() * 1000)

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> VenueResponse:
        """Send one signed request over HTTP and return the venue's answer.

        The signature covers the path *including* the query string, so a request
        cannot be replayed against different parameters.

        Raises:
            VenueTransportError: If the connection fails, the request times out,
                or the response cannot be read. The error names the method, the
                path and the redacted key -- never the secret, and never the
                request body, which carries order details.
        """

        verb = method.upper()
        encoded = urlencode(dict(query)) if query else ""
        signed_path = f"{path}?{encoded}" if encoded else path
        payload = (
            json.dumps(dict(body), separators=(",", ":"), sort_keys=True).encode(DEFAULT_ENCODING)
            if body is not None
            else b""
        )

        timestamp_ms = self._now_ms()
        request = urllib.request.Request(
            f"{self.base_url}{signed_path}",
            data=payload if payload else None,
            method=verb,
            headers={
                "Content-Type": "application/json",
                _KEY_HEADER: self.credentials.api_key,
                _TIMESTAMP_HEADER: str(timestamp_ms),
                _SIGNATURE_HEADER: self.credentials.sign(timestamp_ms, verb, signed_path, payload),
            },
        )

        try:
            # `context` is passed explicitly rather than letting urllib reach for
            # `ssl._create_default_https_context`, whose protocol floor is
            # whatever the host's OpenSSL imposes. An order submission is the
            # last place to inherit a security property from a machine's
            # configuration. It is supplied for `http://` bases too, where
            # urllib simply does not use it -- a conditional here would be a
            # branch on which one is safe, which is the wrong question.
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds, context=tls_context()
            ) as response:
                status = int(response.status)
                return VenueResponse(status, response.read())
        except urllib.error.HTTPError as exc:
            # A status the venue chose to return. Not a transport failure: the
            # body usually says why the order was refused, and the adapter reads
            # it. `HTTPError` is itself a readable response object.
            return VenueResponse(int(exc.code), exc.read())
        except OSError as exc:
            # `URLError` and `TimeoutError` are both `OSError`, so this one clause
            # covers a refused connection, a DNS failure and a timeout alike. What
            # they share is the thing that matters: no answer came back.
            raise VenueTransportError(
                f"{verb} {path} to the venue at {self.base_url} did not answer "
                f"(key {self.credentials.redacted_key}): {exc}. The request may or "
                "may not have been acted on; a retry addresses the same order "
                "because the client order id is derived, not minted."
            ) from exc
