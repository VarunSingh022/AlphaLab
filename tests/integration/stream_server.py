"""A real RFC 6455 server, on a real socket, for the streaming client to talk to.

The counterpart of ``tests/integration/venue_server.py``, and the same argument:
this environment has no network egress, so the way to prove
:mod:`alphalab.marketdata.websocket` works is to stand up the other end of the
protocol and drive the real client into it over TCP. Nothing is stubbed. The
handshake is computed, frames are built byte by byte, client masks are removed,
and the closing handshake is completed.

What it can be told to do
-------------------------
:class:`StreamScript` makes each lifecycle branch reachable:

* ``messages`` -- what to send once a client subscribes.
* ``drop_after`` -- close the socket abruptly after N messages, which is a
  venue falling over mid-stream.
* ``silent`` -- accept the subscription and then say nothing, which is the
  failure a heartbeat exists to reveal and is invisible without one.
* ``fragment`` -- split each message across two frames, exercising
  continuation reassembly.
* ``ping_before`` -- send a Ping before the messages, which the client must
  answer with a Pong carrying the same payload.

``connections`` records every subscription the server received, which is what
proves a reconnect *resubscribed* rather than merely reopening a socket.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Final

_ACCEPT_GUID: Final = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_ENCODING: Final = "utf-8"


@dataclass
class StreamScript:
    """What the venue should do on each connection it accepts.

    Attributes:
        messages: Messages to send after a subscription, as mappings. Sent in
            order, as JSON text frames.
        drop_after: Close the socket without a handshake after this many
            messages. ``None`` sends them all and waits.
        drop_connections: Which connections (1-based) ``drop_after`` applies to.
            ``None`` means every one. Naming the first only is how a test says
            "drop once, then behave" -- without it a reconnect is dropped again
            before it can deliver anything, and the stream never continues.
        silent: Send nothing at all after acknowledging the subscription.
        fragment: Split each message across two continuation frames.
        ping_before: Send a Ping with this payload before the messages.
        per_connection: Messages keyed by connection number (1-based), used
            when a reconnect should deliver something different from the first
            connection -- a replay from an earlier sequence, typically.
    """

    messages: Sequence[dict[str, Any]] = field(default_factory=tuple)
    drop_after: int | None = None
    drop_connections: Sequence[int] | None = None
    silent: bool = False
    fragment: bool = False
    ping_before: bytes = b""
    per_connection: dict[int, Sequence[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class StreamLog:
    """What the server observed. Read by tests to assert on the lifecycle."""

    #: One entry per accepted connection: the symbols it subscribed to.
    connections: list[list[str]] = field(default_factory=list)
    #: Payloads of every Pong the client sent back.
    pongs: list[bytes] = field(default_factory=list)
    #: Close codes the client sent, in order.
    closes: list[int] = field(default_factory=list)


def _frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """One unmasked server frame. Servers never mask; the RFC forbids it."""

    header = bytearray([(0x80 if fin else 0x00) | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 1 << 16:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    return bytes(header) + payload


def _unmask(payload: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))


class _Peer:
    """One accepted client connection."""

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection
        self._buffer = bytearray()

    def _fill(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._connection.recv(65536)
            if not chunk:
                raise ConnectionError("client closed")
            self._buffer.extend(chunk)
        taken = bytes(self._buffer[:count])
        del self._buffer[:count]
        return taken

    def read_frame(self) -> tuple[int, bytes]:
        """One client frame as ``(opcode, payload)``, unmasked."""

        header = self._fill(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._fill(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._fill(8))[0]
        key = self._fill(4) if masked else b""
        payload = self._fill(length) if length else b""
        return opcode, _unmask(payload, key) if masked else payload

    def handshake(self) -> None:
        """Complete the opening handshake, computing the accept token properly."""

        request = bytearray()
        while b"\r\n\r\n" not in request:
            chunk = self._connection.recv(4096)
            if not chunk:
                raise ConnectionError("client closed during handshake")
            request.extend(chunk)

        key = ""
        for line in request.decode(_ENCODING, "replace").split("\r\n"):
            name, separator, value = line.partition(":")
            if separator and name.strip().lower() == "sec-websocket-key":
                key = value.strip()

        accept = base64.b64encode(
            hashlib.sha1(f"{key}{_ACCEPT_GUID}".encode("ascii")).digest()
        ).decode("ascii")
        self._connection.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode(_ENCODING)
        )

    def send_text(self, payload: str, *, fragment: bool = False) -> None:
        data = payload.encode(_ENCODING)
        if not fragment or len(data) < 2:
            self._connection.sendall(_frame(0x1, data))
            return
        half = len(data) // 2
        self._connection.sendall(_frame(0x1, data[:half], fin=False))
        self._connection.sendall(_frame(0x0, data[half:], fin=True))

    def send_ping(self, payload: bytes) -> None:
        self._connection.sendall(_frame(0x9, payload))

    def close(self, abrupt: bool = False) -> None:
        if not abrupt:
            with suppress(OSError):
                self._connection.sendall(_frame(0x8, struct.pack("!H", 1000)))
        with suppress(OSError):
            self._connection.close()


def _serve_one(connection: socket.socket, script: StreamScript, log: StreamLog, index: int) -> None:
    """Handshake, read the subscription, then follow the script."""

    peer = _Peer(connection)
    try:
        peer.handshake()
        connection.settimeout(5.0)

        opcode, payload = peer.read_frame()
        if opcode != 0x1:
            peer.close()
            return
        request = json.loads(payload.decode(_ENCODING))
        log.connections.append(list(request.get("symbols", [])))
        peer.send_text(json.dumps({"type": "subscribed"}))

        if script.ping_before:
            peer.send_ping(script.ping_before)

        if script.silent:
            # Say nothing and wait. The client's liveness timer is the only
            # thing that can notice this.
            _drain(peer, log)
            return

        drops = script.drop_connections is None or index in script.drop_connections
        messages = script.per_connection.get(index, script.messages)
        for sent, message in enumerate(messages, start=1):
            peer.send_text(json.dumps(message), fragment=script.fragment)
            if drops and script.drop_after is not None and sent >= script.drop_after:
                peer.close(abrupt=True)
                return

        _drain(peer, log)
    except (OSError, ConnectionError, json.JSONDecodeError, IndexError):
        pass
    finally:
        with suppress(OSError):
            connection.close()


def _drain(peer: _Peer, log: StreamLog) -> None:
    """Read client control frames until it goes away.

    This is what records the Pong answering our Ping and the client's Close
    code, both of which are the protocol behaviour under test.
    """

    while True:
        try:
            opcode, payload = peer.read_frame()
        except (OSError, ConnectionError):
            return
        if opcode == 0xA:
            log.pongs.append(payload)
        elif opcode == 0x8:
            code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 0
            log.closes.append(code)
            peer.close()
            return


@contextmanager
def run_stream(
    script: StreamScript | None = None,
) -> Iterator[tuple[str, StreamLog, StreamScript]]:
    """Serve a WebSocket venue on a loopback port for the duration of the block.

    Yields ``(url, log, script)``. Port ``0`` lets the OS choose, so concurrent
    test runs never collide.
    """

    active = script if script is not None else StreamScript()
    log = StreamLog()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.02)
    host, port = listener.getsockname()
    running = True
    index = 0

    def accept_loop() -> None:
        nonlocal index
        while running:
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            index += 1
            worker = threading.Thread(
                target=_serve_one, args=(connection, active, log, index), daemon=True
            )
            worker.start()

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    try:
        yield f"ws://{host}:{port}/stream", log, active
    finally:
        running = False
        with suppress(OSError):
            listener.close()
        thread.join(timeout=5)


def quote(symbol: str, sequence: int, bid: str, ask: str, timestamp: float) -> dict[str, Any]:
    """One quote message as the venue sends it."""

    return {
        "type": "quote",
        "symbol": symbol,
        "sequence": sequence,
        "timestamp": timestamp,
        "bid": float(bid),
        "ask": float(ask),
        "bid_size": 100.0,
        "ask_size": 100.0,
    }


def heartbeat() -> dict[str, Any]:
    """One liveness message, carrying no market data."""

    return {"type": "heartbeat"}
