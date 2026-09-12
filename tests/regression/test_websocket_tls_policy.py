"""The TLS floor a ``wss://`` connection is made with, pinned.

CodeQL reported "Use of insecure SSL/TLS version" against the original line::

    raw = ssl.create_default_context().wrap_socket(raw, server_hostname=...)

and was right. :func:`ssl.create_default_context` sets certificate and hostname
verification, but it does **not** pin a protocol floor: it sets neither
``OP_NO_TLSv1`` nor ``OP_NO_TLSv1_1``, and ``minimum_version`` comes back as
whatever the host's OpenSSL build and ``openssl.cnf`` impose. On the machine
this was written on that is TLS 1.2; on a host with a permissive crypto policy
the same code negotiates TLS 1.0, which RFC 8996 deprecated.

The guarantee was environmental rather than stated. These tests make it stated,
and :func:`test_the_floor_is_ours_not_inherited_from_openssl` is the one that
actually fails if the fix is reverted -- the others would still pass on a
correctly configured host, which is exactly how the original defect hid.
"""

from __future__ import annotations

import inspect
import socket
import ssl
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager, suppress

import pytest

from alphalab.marketdata.websocket import (
    MINIMUM_TLS_VERSION,
    WebSocketError,
    connect_websocket,
    tls_context,
)

#: Everything RFC 8996 deprecated, which must never be negotiable.
_DEPRECATED = (ssl.TLSVersion.SSLv3, ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1_1)


# ---------------------------------------------------------------------------
# The floor
# ---------------------------------------------------------------------------


def test_the_minimum_tls_version_is_at_least_1_2() -> None:
    assert tls_context().minimum_version >= ssl.TLSVersion.TLSv1_2


def test_the_declared_constant_is_at_least_1_2() -> None:
    """The constant is the contract; the context is built from it."""

    assert ssl.TLSVersion.TLSv1_2 <= MINIMUM_TLS_VERSION
    assert tls_context().minimum_version == MINIMUM_TLS_VERSION


@pytest.mark.parametrize("version", _DEPRECATED, ids=lambda v: v.name)
def test_no_deprecated_protocol_can_be_negotiated(version: ssl.TLSVersion) -> None:
    """SSLv3, TLS 1.0 and TLS 1.1 are all below the floor. RFC 8996."""

    assert tls_context().minimum_version > version


def test_the_floor_is_ours_not_inherited_from_openssl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The regression guard.** Reverting to a bare default context fails here.

    The original defect was invisible on a correctly configured host, because
    OpenSSL supplied a safe floor for free. This makes the host supply a *bad*
    one and asserts the floor holds anyway -- which it can only do if the code
    sets it explicitly.
    """

    def permissive_default() -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        with warnings.catch_warnings():
            # Naming TLSv1 is the whole point here -- this is the bad host being
            # simulated, not a protocol this code would ever offer.
            warnings.simplefilter("ignore", DeprecationWarning)
            with suppress(ValueError):  # some builds refuse to go this low
                context.minimum_version = ssl.TLSVersion.TLSv1
        return context

    monkeypatch.setattr(ssl, "create_default_context", permissive_default)

    # Sanity: the host default really is weak under this patch.
    assert ssl.create_default_context().minimum_version <= ssl.TLSVersion.TLSv1_1

    # And ours is not.
    assert tls_context().minimum_version >= ssl.TLSVersion.TLSv1_2


def test_the_maximum_version_is_not_capped() -> None:
    """Pinning a floor must not also pin a ceiling and lock out TLS 1.3."""

    assert tls_context().maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED


# ---------------------------------------------------------------------------
# What must not have been weakened to achieve it
# ---------------------------------------------------------------------------


def test_certificate_verification_is_required() -> None:
    assert tls_context().verify_mode == ssl.CERT_REQUIRED


def test_hostname_verification_is_on() -> None:
    assert tls_context().check_hostname is True


def test_the_client_protocol_is_used() -> None:
    """`PROTOCOL_TLS_CLIENT` is what turns hostname checking on by default."""

    assert tls_context().protocol == ssl.PROTOCOL_TLS_CLIENT


def test_a_fresh_context_is_returned_each_call() -> None:
    """Callers must not be able to mutate a shared context into a weaker one."""

    first, second = tls_context(), tls_context()
    assert first is not second

    first.minimum_version = ssl.TLSVersion.TLSv1_3
    assert tls_context().minimum_version == MINIMUM_TLS_VERSION


# ---------------------------------------------------------------------------
# No route to a weaker connection
# ---------------------------------------------------------------------------


def test_there_is_no_parameter_for_lowering_the_tls_version() -> None:
    """A knob here would exist only to be turned the wrong way."""

    for function in (tls_context, connect_websocket):
        names = [p.lower() for p in inspect.signature(function).parameters]
        assert not any(
            token in name
            for name in names
            for token in ("tls", "ssl", "cert", "verify", "insecure", "protocol")
        ), f"{function.__name__} exposes a TLS-weakening parameter: {names}"


def test_nothing_in_the_module_disables_verification() -> None:
    """A grep-level guard against the usual ways this gets switched off."""

    import alphalab.marketdata.websocket as module

    source = inspect.getsource(module)
    for forbidden in ("CERT_NONE", "check_hostname = False", "_create_unverified_context"):
        assert forbidden not in source, f"{forbidden} appears in the websocket client"


def test_the_connect_path_does_not_build_its_own_context() -> None:
    """`connect_websocket` must go through `tls_context`, not around it."""

    source = inspect.getsource(connect_websocket)
    assert "tls_context()" in source
    assert "create_default_context" not in source


# ---------------------------------------------------------------------------
# The wss path genuinely uses it
# ---------------------------------------------------------------------------


@contextmanager
def _plain_tcp_listener() -> Iterator[int]:
    """A socket that accepts a TCP connection and speaks no TLS.

    Enough to get `connect_websocket` past `socket.create_connection` and into
    the TLS wrap, which is the line under test. The handshake then fails, which
    is the expected outcome and is asserted.
    """

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5.0)

    def accept_once() -> None:
        with suppress(OSError):
            connection, _ = listener.accept()
            connection.close()

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()
    try:
        yield int(listener.getsockname()[1])
    finally:
        with suppress(OSError):
            listener.close()
        thread.join(timeout=5)


def test_a_wss_connection_is_wrapped_with_our_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the hardened context reaches the socket, not just that it exists."""

    import alphalab.marketdata.websocket as module

    used: list[ssl.SSLContext] = []

    def spy() -> ssl.SSLContext:
        context = tls_context()
        used.append(context)
        return context

    monkeypatch.setattr(module, "tls_context", spy)

    # The peer speaks no TLS, so the handshake fails -- after the wrap, which
    # is the point: the context was built and applied before it failed.
    with _plain_tcp_listener() as port, pytest.raises(WebSocketError):
        connect_websocket(f"wss://127.0.0.1:{port}/stream", timeout_seconds=3.0)

    assert used, "the wss path did not build a TLS context"
    assert used[0].minimum_version >= ssl.TLSVersion.TLSv1_2
    assert used[0].check_hostname is True


def test_a_plain_ws_connection_builds_no_tls_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ws://` is unencrypted by definition; it must not silently look secure."""

    import alphalab.marketdata.websocket as module

    used: list[ssl.SSLContext] = []

    def spy() -> ssl.SSLContext:
        context = tls_context()
        used.append(context)
        return context

    monkeypatch.setattr(module, "tls_context", spy)

    with _plain_tcp_listener() as port, pytest.raises(WebSocketError):
        connect_websocket(f"ws://127.0.0.1:{port}/stream", timeout_seconds=3.0)

    assert used == [], "a ws:// connection built a TLS context"


# ---------------------------------------------------------------------------
# A local stand-in for the CodeQL rule
# ---------------------------------------------------------------------------


def test_every_ssl_context_in_the_codebase_pins_a_secure_floor() -> None:
    """Emulates `py/insecure-protocol` repo-wide, so the class cannot reappear.

    The CodeQL CLI is not available in this environment, so this asserts the
    property the rule checks rather than the rule itself: **every** construction
    of an :class:`ssl.SSLContext` -- ``create_default_context()`` included -- must
    have an explicit ``minimum_version`` assigned in the same function.

    Repo-wide rather than module-local on purpose. The module-local tests above
    protect the connection that exists today; this protects the next one
    somebody writes, which is where the same mistake would be made again.
    """

    import ast
    import pathlib

    builders = {"create_default_context", "SSLContext", "_create_unverified_context"}
    offenders: list[str] = []

    for path in sorted(pathlib.Path("alphalab").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for scope in ast.walk(tree):
            if not isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module):
                continue

            constructs = [
                node
                for node in ast.walk(scope)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in builders
            ]
            if not constructs:
                continue

            pins_floor = any(
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Attribute) and target.attr == "minimum_version"
                    for target in node.targets
                )
                for node in ast.walk(scope)
            )
            if not pins_floor:
                where = getattr(scope, "name", "<module>")
                offenders.append(f"{path}:{constructs[0].lineno} in {where}()")

    assert not offenders, (
        "SSLContext built without an explicit minimum_version -- the TLS floor "
        "would be inherited from the host's OpenSSL, which is the CodeQL "
        f"'Use of insecure SSL/TLS version' finding: {offenders}"
    )
