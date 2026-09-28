"""A WebSocket connection closes its socket on every way it can end (ledger REL-001).

Until v3.10 three failure paths -- the peer hanging up, the peer sending a Close
frame, and a failed write -- marked the connection closed without closing its
socket, and :meth:`~alphalab.marketdata.websocket.WebSocketConnection.close`
then returned early because the connection already read as closed. Every
reconnect of a live stream leaked a file descriptor until garbage collection
finalized it, which surfaced only as a ``ResourceWarning`` -- invisible unless
the suite runs with warnings as errors, and it did not.

These tests read the socket's state directly rather than waiting for the
collector, so they fail deterministically if a path stops releasing it.
"""

import socket
import struct

import pytest

from alphalab.marketdata.websocket import WebSocketConnection, WebSocketError


def _connection() -> tuple[WebSocketConnection, socket.socket]:
    ours, peer = socket.socketpair()
    ours.settimeout(1.0)
    return WebSocketConnection("ws://test.invalid/stream", ours), peer


def _released(sock: socket.socket) -> bool:
    return sock.fileno() == -1


def test_a_peer_that_hangs_up_releases_the_socket() -> None:
    connection, peer = _connection()
    socket_ = connection._socket
    peer.close()

    with pytest.raises(WebSocketError, match="closed the connection"):
        connection.poll()

    assert connection.closed
    assert _released(socket_)
    connection.close()  # idempotent, and safe after the peer ended it
    assert _released(socket_)


def test_a_close_frame_from_the_peer_releases_the_socket() -> None:
    connection, peer = _connection()
    socket_ = connection._socket
    # An unmasked server Close frame carrying status 1000.
    peer.sendall(bytes([0x88, 0x02]) + struct.pack("!H", 1000))

    assert connection.poll() is None

    assert connection.closed
    assert _released(socket_)
    peer.close()


def test_a_failed_write_releases_the_socket() -> None:
    connection, peer = _connection()
    socket_ = connection._socket
    peer.close()
    # A write to a socket whose peer is gone fails once the kernel knows it.
    with pytest.raises(WebSocketError, match="Writing to"):
        for _ in range(64):
            connection.send("subscribe")

    assert connection.closed
    assert _released(socket_)


def test_close_releases_the_socket_it_owns() -> None:
    connection, peer = _connection()
    socket_ = connection._socket

    connection.close()

    assert connection.closed
    assert _released(socket_)
    peer.close()
