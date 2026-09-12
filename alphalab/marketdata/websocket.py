"""A WebSocket client, because a streaming venue is not a sequence of GETs.

:mod:`alphalab.marketdata.transport` is request/response: one GET, one body,
connection closed. Every market-data client in AlphaLab was built on it, and
:meth:`~alphalab.marketdata.binance.client.binanceClient.subscribe` is a
documented no-op as a result -- "this client polls REST endpoints, it does not
stream". A subscription that yields nothing until asked is not a stream, and
wrapping a polling loop in a class called ``Stream`` would be the same absence
with a better name.

This is the missing half: a connection that stays open and pushes. RFC 6455 over
the standard library, and nothing else -- no dependency is added to a package
that has none.

What is implemented
-------------------
The client side of RFC 6455, to the extent a market-data consumer uses it:

=========================  ==================================================
Opening handshake          ``Sec-WebSocket-Key`` / ``Accept`` verified, not
                           merely sent. A server that answers with the wrong
                           accept token is refused.
Framing                    FIN, opcode, and all three payload-length forms
                           (7-bit, 16-bit, 64-bit).
Masking                    Every client frame is masked with a fresh 32-bit
                           key, as the RFC requires of clients.
Continuation               Fragmented messages are reassembled.
Control frames             Ping is answered with Pong carrying the same
                           payload. Pong is accepted as liveness. Close is
                           echoed and ends the connection.
Close                      The closing handshake, both directions.
=========================  ==================================================

What is deliberately not implemented: ``permessage-deflate`` (an extension,
negotiated only if asked for, and this never asks), and the server side.

Transport security
------------------
A ``wss://`` connection is made with :func:`tls_context`, which verifies the
server certificate against the system trust store, checks the hostname against
it, and **states its own protocol floor** of TLS 1.2 rather than inheriting
whatever the host's OpenSSL happens to allow. Read that function for why the
inherited floor was not good enough; RFC 8996 is the short answer.

Shutdown, and why the socket has a timeout
------------------------------------------
:meth:`WebSocketConnection.messages` blocks waiting for the venue to say
something, which is the whole point of a stream and also the thing that makes a
stream hard to stop. The socket carries a read timeout and the loop re-checks
:attr:`~WebSocketConnection.closed` on every expiry, so
:meth:`~WebSocketConnection.close` from another thread is observed within one
poll interval rather than never. A stream that cannot be shut down is a process
that cannot be shut down.

Not verified against a commercial venue
---------------------------------------
The same statement :class:`~alphalab.marketdata.transport.HttpTransport` makes,
and for the same reason: this environment has no network egress. What *is*
verified is the protocol -- the integration suite runs this client against a
local RFC 6455 server over a real socket, including fragmentation, ping/pong,
an abrupt drop and the closing handshake.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
from collections.abc import Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Final
from urllib.parse import urlsplit

from alphalab.common.constants import DEFAULT_ENCODING

# Re-exported unchanged: the TLS floor is shared with
# ``alphalab.broker.transport``, so it is defined once in ``alphalab.common``
# and named here for callers who reach it through this module. Defining a
# second copy is how two security floors come to disagree.
from alphalab.common.tls import MINIMUM_TLS_VERSION, tls_context
from alphalab.marketdata.exceptions import MarketDataError

__all__ = [
    "MINIMUM_TLS_VERSION",
    "WebSocketConnection",
    "WebSocketError",
    "WebSocketTransport",
    "connect_websocket",
    "tls_context",
]

#: The constant RFC 6455 section 1.3 appends to the client key before hashing.
_ACCEPT_GUID: Final = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

#: Frames larger than this are refused rather than allocated. A venue does not
#: send a 64MB tick, and a length field that says it does is either corruption
#: or someone trying to exhaust this process's memory.
_MAX_FRAME_BYTES: Final = 16 * 1024 * 1024

#: How long a blocking read waits before returning to check whether the caller
#: has asked the connection to close.
_POLL_SECONDS: Final = 0.25


class _Opcode(IntEnum):
    """RFC 6455 section 5.2 opcodes."""

    CONTINUATION = 0x0
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA


class WebSocketError(MarketDataError):
    """The connection failed, or the peer broke the protocol.

    One type for both, deliberately: to a consumer of market data, a venue that
    closed the socket and a venue that sent a frame violating the RFC are the
    same event -- this connection can no longer be trusted and must be
    re-established. What differs is only the message.
    """


def _mask(payload: bytes, key: bytes) -> bytes:
    """XOR ``payload`` with a repeating 4-byte key. Its own inverse."""

    return bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))


@dataclass(slots=True)
class WebSocketConnection:
    """One open WebSocket, and the frames that cross it.

    Stateful and **not** a domain object: it owns a socket. Everything in
    AlphaLab that is immutable stays immutable, and this is deliberately on the
    other side of the line -- the same side
    :class:`~alphalab.marketdata.transport.HttpTransport` is on. Canonical
    market state is built *from* what this yields, not by it.

    Attributes:
        url: The ``ws://`` or ``wss://`` endpoint this is connected to.
    """

    url: str
    _socket: socket.socket
    _buffer: bytearray = field(default_factory=bytearray)
    _closed: bool = False
    #: Payload of the most recent Pong, which is how a caller confirms that the
    #: Ping it sent came back rather than merely that bytes arrived.
    last_pong: bytes = b""
    #: Frames of a fragmented message received so far. On the instance rather
    #: than in a local, because `poll` returns between frames and a message
    #: split across two polls must survive the gap.
    _fragments: list[bytes] = field(default_factory=list)
    _fragment_opcode: _Opcode = _Opcode.TEXT

    @property
    def closed(self) -> bool:
        """Whether this connection has been closed, from either end."""

        return self._closed

    # -- reading ---------------------------------------------------------

    def _fill(self, count: int) -> bytes:
        """Read exactly ``count`` bytes, returning to the caller on a poll timeout.

        Raises:
            WebSocketError: If the peer closed mid-frame. A partial frame cannot
                be interpreted, and guessing at the rest is how a corrupt price
                reaches a portfolio.
            TimeoutError: If no byte arrived within the poll interval and no
                partial frame is outstanding, which is the caller's cue to check
                whether it has been asked to stop.
        """

        while len(self._buffer) < count:
            try:
                chunk = self._socket.recv(65536)
            except TimeoutError:
                if self._buffer:
                    # Mid-frame: the rest is still coming, keep waiting.
                    continue
                raise
            except OSError as exc:
                raise WebSocketError(f"Reading from {self.url} failed: {exc}") from exc
            if not chunk:
                self._closed = True
                raise WebSocketError(
                    f"The peer at {self.url} closed the connection"
                    f"{' mid-frame' if self._buffer else ''}."
                )
            self._buffer.extend(chunk)

        taken = bytes(self._buffer[:count])
        del self._buffer[:count]
        return taken

    def _read_frame(self) -> tuple[int, bool, bytes]:
        """One frame: ``(opcode, fin, payload)``.

        Raises:
            WebSocketError: On a masked server frame (the RFC forbids it), a
                reserved bit, or a length beyond :data:`_MAX_FRAME_BYTES`.
        """

        header = self._fill(2)
        first, second = header[0], header[1]

        if first & 0x70:
            raise WebSocketError(
                f"The peer at {self.url} set a reserved frame bit. No extension was "
                "negotiated, so nothing may set one."
            )

        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F

        if length == 126:
            length = struct.unpack("!H", self._fill(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._fill(8))[0]

        if length > _MAX_FRAME_BYTES:
            raise WebSocketError(
                f"The peer at {self.url} announced a {length}-byte frame, beyond the "
                f"{_MAX_FRAME_BYTES}-byte limit. It is refused rather than allocated."
            )

        key = self._fill(4) if masked else b""
        payload = self._fill(length) if length else b""
        if masked:
            payload = _mask(payload, key)

        return opcode, fin, payload

    def poll(self) -> str | None:
        """Read until one complete text message arrives, or the interval expires.

        ``None`` means *nothing arrived*, which is a fact a caller needs and a
        blocking read cannot express. Two callers need it for different reasons:
        a consumer wanting to notice :meth:`close` from another thread, and
        :class:`~alphalab.market.stream.StreamingSource`, which measures silence
        against a liveness deadline. A venue that has stopped sending and a
        venue that is sending slowly are different, and only the passage of time
        distinguishes them.

        Control frames are handled here and never returned: a Ping is answered
        with a Pong before anything else is read, a Pong updates
        :attr:`last_pong`, and a Close completes the closing handshake and marks
        the connection closed. Liveness is therefore maintained whether or not
        the caller is paying attention.

        Binary frames are consumed and discarded: this client negotiates a text
        protocol, and a binary frame is the peer doing something unagreed.

        Raises:
            WebSocketError: If the connection fails or the peer breaks framing.
                Reconnecting is :class:`~alphalab.market.stream.StreamingSource`'s
                job, one layer up, because only that layer knows what to
                resubscribe to.
        """

        while not self._closed:
            try:
                opcode, fin, payload = self._read_frame()
            except TimeoutError:
                return None

            if opcode == _Opcode.PING:
                self._send_frame(_Opcode.PONG, payload)
                continue
            if opcode == _Opcode.PONG:
                self.last_pong = payload
                continue
            if opcode == _Opcode.CLOSE:
                self._closed = True
                # The peer may already be gone; the handshake is attempted, not
                # required -- there is nobody left to be polite to.
                with suppress(WebSocketError):
                    self._send_frame(_Opcode.CLOSE, payload[:2])
                return None

            if opcode == _Opcode.CONTINUATION:
                if not self._fragments:
                    raise WebSocketError(
                        f"The peer at {self.url} sent a continuation frame with no "
                        "message to continue."
                    )
                self._fragments.append(payload)
            elif opcode in {_Opcode.TEXT, _Opcode.BINARY}:
                if self._fragments:
                    raise WebSocketError(
                        f"The peer at {self.url} began a new message before finishing "
                        "the fragmented one in progress."
                    )
                self._fragment_opcode = _Opcode(opcode)
                self._fragments = [payload]
            else:
                raise WebSocketError(f"The peer at {self.url} sent opcode {opcode:#x}.")

            if not fin:
                continue

            complete = b"".join(self._fragments)
            self._fragments = []
            if self._fragment_opcode is not _Opcode.TEXT:
                continue
            try:
                return complete.decode(DEFAULT_ENCODING)
            except UnicodeDecodeError as exc:
                raise WebSocketError(
                    f"The peer at {self.url} sent a text frame that is not valid "
                    f"{DEFAULT_ENCODING}: {exc}."
                ) from exc

        return None

    def messages(self) -> Iterator[str]:
        """Yield each complete text message the peer sends, until it stops.

        A thin loop over :meth:`poll` for a consumer that has nothing to do
        while idle. A consumer that *does* -- checking a deadline, say -- should
        call :meth:`poll` and decide for itself what silence means.
        """

        while not self._closed:
            message = self.poll()
            if message is not None:
                yield message

    # -- writing ---------------------------------------------------------

    def _send_frame(self, opcode: _Opcode, payload: bytes) -> None:
        """Write one masked client frame.

        Raises:
            WebSocketError: If the socket refuses the write.
        """

        length = len(payload)
        header = bytearray([0x80 | int(opcode)])
        if length < 126:
            header.append(0x80 | length)
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))

        key = os.urandom(4)
        header.extend(key)
        try:
            self._socket.sendall(bytes(header) + _mask(payload, key))
        except OSError as exc:
            self._closed = True
            raise WebSocketError(f"Writing to {self.url} failed: {exc}") from exc

    def send(self, message: str) -> None:
        """Send one text message -- a subscription request, typically.

        Raises:
            WebSocketError: If the connection is closed or the write fails.
        """

        if self._closed:
            raise WebSocketError(
                f"The connection to {self.url} is closed; nothing is sent on it. "
                "Reconnect and resubscribe."
            )
        self._send_frame(_Opcode.TEXT, message.encode(DEFAULT_ENCODING))

    def ping(self, payload: bytes = b"alphalab") -> None:
        """Ask the peer to prove it is still there.

        The answer lands on :attr:`last_pong`, read by the layer that decides
        what to do about silence.
        """

        self._send_frame(_Opcode.PING, payload)

    def close(self, code: int = 1000) -> None:
        """Begin the closing handshake and release the socket.

        Idempotent, and safe to call from another thread than the one iterating
        :meth:`messages` -- which is the point, because that thread is blocked.
        """

        if self._closed:
            return
        self._closed = True
        try:
            self._send_frame(_Opcode.CLOSE, struct.pack("!H", code))
        except WebSocketError:
            # Already gone. The socket still needs closing.
            pass
        finally:
            with suppress(OSError):
                self._socket.close()


def connect_websocket(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 10.0,
) -> WebSocketConnection:
    """Open a WebSocket to ``url`` and complete the opening handshake.

    The server's ``Sec-WebSocket-Accept`` is **verified**, not merely required
    to be present: it must be the base64 SHA-1 of the key this client sent plus
    the RFC's GUID. A server that cannot produce it is not speaking WebSocket,
    and accepting it would mean parsing whatever it sends next as frames.

    Args:
        url: ``ws://host:port/path`` or ``wss://...``.
        headers: Extra request headers -- an API key, typically. Never logged
            by this module.
        timeout_seconds: How long to wait for the handshake. Afterwards the
            socket carries a short poll timeout instead, so that a blocked
            reader can still notice a close.

    Raises:
        WebSocketError: If the URL is not a WebSocket URL, the connection fails,
            or the handshake is not answered correctly.
    """

    parts = urlsplit(url)
    if parts.scheme not in {"ws", "wss"}:
        raise WebSocketError(
            f"{url!r} is not a WebSocket URL: expected a ws:// or wss:// scheme, "
            f"got {parts.scheme!r}."
        )
    if not parts.hostname:
        raise WebSocketError(f"{url!r} names no host.")

    secure = parts.scheme == "wss"
    port = parts.port or (443 if secure else 80)
    resource = parts.path or "/"
    if parts.query:
        resource = f"{resource}?{parts.query}"

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = [
        f"GET {resource} HTTP/1.1",
        f"Host: {parts.hostname}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    request.extend(f"{name}: {value}" for name, value in (headers or {}).items())
    payload = ("\r\n".join(request) + "\r\n\r\n").encode(DEFAULT_ENCODING)

    try:
        raw = socket.create_connection((parts.hostname, port), timeout=timeout_seconds)
    except OSError as exc:
        raise WebSocketError(f"Cannot reach {parts.hostname}:{port}: {exc}") from exc

    try:
        if secure:
            # `server_hostname` is what `check_hostname` validates against, so
            # it is required here and not optional. See `tls_context` for the
            # protocol floor and why it is stated rather than inherited.
            raw = tls_context().wrap_socket(raw, server_hostname=parts.hostname)
        raw.sendall(payload)
        response = _read_handshake(raw, url)
    except WebSocketError:
        raw.close()
        raise
    except OSError as exc:
        raw.close()
        raise WebSocketError(f"The handshake with {url} failed: {exc}") from exc

    # SHA-1 here is RFC 6455 section 4.1, not a security choice: the accept
    # token proves the peer read the key, and the RFC fixes the algorithm.
    expected = base64.b64encode(
        hashlib.sha1(f"{key}{_ACCEPT_GUID}".encode("ascii")).digest()
    ).decode("ascii")
    accepted = response.get("sec-websocket-accept", "")
    if accepted != expected:
        raw.close()
        raise WebSocketError(
            f"{url} answered the handshake with Sec-WebSocket-Accept {accepted!r}, "
            f"and this client sent a key requiring {expected!r}. The peer is not "
            "speaking WebSocket, and nothing it sends will be read as frames."
        )

    # Short timeout from here on: `messages()` uses its expiry to notice `close()`.
    raw.settimeout(_POLL_SECONDS)
    return WebSocketConnection(url, raw)


def _read_handshake(raw: socket.socket, url: str) -> dict[str, str]:
    """Read the HTTP response to the upgrade request and return its headers.

    Raises:
        WebSocketError: If the status is not ``101``, or the response ends
            before its headers do.
    """

    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = raw.recv(4096)
        if not chunk:
            raise WebSocketError(f"{url} closed the connection during the handshake.")
        buffer.extend(chunk)
        if len(buffer) > 65536:
            raise WebSocketError(f"{url} sent an oversized handshake response.")

    head, _, _ = buffer.partition(b"\r\n\r\n")
    lines = head.decode(DEFAULT_ENCODING, errors="replace").split("\r\n")
    status = lines[0]
    if " 101" not in status:
        raise WebSocketError(
            f"{url} refused the upgrade: {status!r}. A WebSocket handshake is "
            "answered with 101 Switching Protocols."
        )

    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    return headers


class WebSocketTransport:
    """Opens WebSocket connections. The seam a streaming source depends on.

    One method, for the same reason
    :class:`~alphalab.broker.transport.VenueTransport` has one: everything a
    stream does after connecting is done *on* the connection, and naming those
    operations here would put the protocol into the factory.

    :class:`~alphalab.market.stream.StreamingSource` depends on this rather than
    on :func:`connect_websocket` directly, so that the reconnect loop can be
    driven against a peer that fails on demand without any of it being faked.
    """

    __slots__ = ("_headers", "_timeout_seconds")

    def __init__(
        self, headers: Mapping[str, str] | None = None, timeout_seconds: float = 10.0
    ) -> None:
        self._headers = dict(headers or {})
        self._timeout_seconds = timeout_seconds

    def connect(self, url: str) -> WebSocketConnection:
        """Open one connection to ``url``.

        Raises:
            WebSocketError: If it cannot be opened.
        """

        return connect_websocket(url, headers=self._headers, timeout_seconds=self._timeout_seconds)
