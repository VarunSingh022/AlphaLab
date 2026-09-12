"""The TLS floor an ``https://`` order submission is made with, pinned.

The companion of ``test_websocket_tls_policy.py``, for the other half of the
same boundary. CodeQL reported the WebSocket client because AlphaLab built the
``SSLContext`` there; it did **not** report this path, because
``urllib.request.urlopen`` builds the context *inside* urllib and there was no
construction in AlphaLab's dataflow to flag.

The property was the same, and worse where it mattered more. Without an explicit
``context=``, urlopen reaches for ``ssl._create_default_https_context``, whose
protocol floor is whatever the host's OpenSSL imposes -- so an **order
submission** inherited its security from a machine's configuration file. That is
the risk a static analyser could not see, which is the reason these tests assert
the behaviour rather than trusting the scanner to catch a regression.

:func:`test_the_request_path_passes_an_explicit_context` is the guard that
actually fails if the fix is removed.
"""

from __future__ import annotations

import inspect
import ssl
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from alphalab.broker.transport import (
    HttpVenueTransport,
    VenueCredentials,
    VenueTransportError,
)
from alphalab.common.tls import MINIMUM_TLS_VERSION, tls_context

_KEY = "TESTKEY-0001"
_SECRET = "test-signing-secret-not-a-real-credential"


def _transport(base: str = "https://venue.invalid", **kwargs: Any) -> HttpVenueTransport:
    return HttpVenueTransport(base, VenueCredentials(_KEY, _SECRET), **kwargs)


@pytest.fixture
def urlopen_spy(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Capture what ``urlopen`` was called with, without opening a socket.

    Raises ``OSError`` afterwards so the caller's own error handling runs, which
    keeps these tests on the real code path rather than a rewritten one.
    """

    seen: dict[str, Any] = {}

    def spy(request: Any, **kwargs: Any) -> Any:
        seen["request"] = request
        seen.update(kwargs)
        raise OSError("spy: no network in this test")

    monkeypatch.setattr(urllib.request, "urlopen", spy)
    yield seen


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_the_request_path_passes_an_explicit_context(urlopen_spy: dict[str, Any]) -> None:
    """**The regression guard.** Removing ``context=`` fails here.

    Without it urlopen silently falls back to the ambient default, which is the
    entire defect -- and which no assertion about ``tls_context()`` alone would
    catch, because that function would still be correct and simply unused.
    """

    with pytest.raises(VenueTransportError):
        _transport().request("GET", "/v1/account")

    context = urlopen_spy.get("context")
    assert context is not None, "urlopen was called without an explicit SSLContext"
    assert isinstance(context, ssl.SSLContext)


def test_the_context_passed_is_alphalabs_policy(urlopen_spy: dict[str, Any]) -> None:
    with pytest.raises(VenueTransportError):
        _transport().request("GET", "/v1/account")

    context: ssl.SSLContext = urlopen_spy["context"]
    assert context.minimum_version == MINIMUM_TLS_VERSION
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_the_floor_holds_even_when_the_host_default_is_weak(
    monkeypatch: pytest.MonkeyPatch, urlopen_spy: dict[str, Any]
) -> None:
    """The ambient default is made bad; the submitted context must still be good.

    This is what distinguishes a real fix from one that happens to look right on
    a well-configured machine.
    """

    def permissive() -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    monkeypatch.setattr(ssl, "_create_default_https_context", permissive, raising=False)

    with pytest.raises(VenueTransportError):
        _transport().request("GET", "/v1/account")

    assert urlopen_spy["context"].minimum_version >= ssl.TLSVersion.TLSv1_2


@pytest.mark.parametrize(
    "version",
    (ssl.TLSVersion.SSLv3, ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1_1),
    ids=lambda v: v.name,
)
def test_no_deprecated_protocol_is_offered(
    version: ssl.TLSVersion, urlopen_spy: dict[str, Any]
) -> None:
    """RFC 8996 protocols are below the floor on the execution path too."""

    with pytest.raises(VenueTransportError):
        _transport().request("POST", "/v1/orders", body={"client_order_id": "ALB-1"})

    assert urlopen_spy["context"].minimum_version > version


# ---------------------------------------------------------------------------
# Verification must not have been weakened to get there
# ---------------------------------------------------------------------------


def test_certificate_and_hostname_verification_are_preserved(
    urlopen_spy: dict[str, Any],
) -> None:
    with pytest.raises(VenueTransportError):
        _transport().request("GET", "/v1/account")

    context: ssl.SSLContext = urlopen_spy["context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_nothing_in_the_module_disables_verification() -> None:
    import alphalab.broker.transport as module

    source = inspect.getsource(module)
    for forbidden in ("CERT_NONE", "check_hostname = False", "_create_unverified_context"):
        assert forbidden not in source, f"{forbidden} appears in the venue transport"


def test_there_is_no_parameter_for_lowering_the_tls_floor() -> None:
    """Neither construction nor a request may opt down."""

    for target in (HttpVenueTransport.__init__, HttpVenueTransport.request):
        names = [p.lower() for p in inspect.signature(target).parameters]
        assert not any(
            token in name
            for name in names
            for token in ("tls", "ssl", "cert", "verify", "insecure", "context")
        ), f"{target.__qualname__} exposes a TLS-weakening parameter: {names}"


def test_one_policy_not_two() -> None:
    """The websocket and the venue transport must share a definition.

    Two floors written down separately is two floors that can drift, which is
    the failure this consolidation exists to prevent.
    """

    from alphalab.broker import transport as venue
    from alphalab.common import tls as shared
    from alphalab.marketdata import transport as feed
    from alphalab.marketdata import websocket as ws

    # `vars(...)` rather than attribute access: in the two transports
    # `tls_context` is an *imported* name, not part of their public surface, so
    # a type checker is right to refuse `venue.tls_context`. Looking in the
    # module namespace says what this actually asserts -- which function each
    # module consumes -- without publishing it as API.
    assert vars(venue)["tls_context"] is shared.tls_context
    assert vars(feed)["tls_context"] is shared.tls_context
    assert ws.tls_context is shared.tls_context, "the websocket does export it"
    assert ws.MINIMUM_TLS_VERSION is shared.MINIMUM_TLS_VERSION


# ---------------------------------------------------------------------------
# Everything else about the request must be unchanged
# ---------------------------------------------------------------------------


def test_the_timeout_is_still_passed(urlopen_spy: dict[str, Any]) -> None:
    with pytest.raises(VenueTransportError):
        _transport(timeout_seconds=3.5).request("GET", "/v1/account")

    assert urlopen_spy["timeout"] == 3.5


def test_the_hmac_headers_are_still_sent(urlopen_spy: dict[str, Any]) -> None:
    """Adding a TLS context must not have disturbed request signing."""

    with pytest.raises(VenueTransportError):
        _transport().request("POST", "/v1/orders", body={"client_order_id": "ALB-1"})

    headers = {name.lower(): value for name, value in urlopen_spy["request"].headers.items()}
    assert headers["X-alphalab-key".lower()] == _KEY
    assert headers["X-alphalab-timestamp".lower()]
    assert len(headers["X-alphalab-signature".lower()]) == 64, "hex sha256"


def test_the_signature_still_covers_the_path_and_body(urlopen_spy: dict[str, Any]) -> None:
    credentials = VenueCredentials(_KEY, _SECRET)

    with pytest.raises(VenueTransportError):
        _transport().request("GET", "/v1/executions", query={"since": "X1"})

    request = urlopen_spy["request"]
    headers = {name.lower(): value for name, value in request.headers.items()}
    sent_at = int(headers["x-alphalab-timestamp"])
    expected = credentials.sign(sent_at, "GET", "/v1/executions?since=X1", b"")
    assert headers["x-alphalab-signature"] == expected


def test_a_transport_failure_still_raises_without_the_secret(
    urlopen_spy: dict[str, Any],
) -> None:
    with pytest.raises(VenueTransportError) as caught:
        _transport().request("GET", "/v1/account")

    message = str(caught.value)
    assert _SECRET not in message
    assert "TEST..." in message


def test_an_http_base_url_still_works(urlopen_spy: dict[str, Any]) -> None:
    """The context is supplied unconditionally; urllib ignores it for ``http://``.

    Asserted because the alternative -- branching on the scheme -- would be a
    decision about which requests deserve a secure context.
    """

    with pytest.raises(VenueTransportError):
        _transport("http://127.0.0.1:1").request("GET", "/v1/account")

    assert isinstance(urlopen_spy["context"], ssl.SSLContext)


# ---------------------------------------------------------------------------
# Repo-wide: the class of defect, not just this instance
# ---------------------------------------------------------------------------


def test_every_urlopen_in_the_codebase_passes_an_explicit_context() -> None:
    """A local stand-in for the rule CodeQL cannot express here.

    ``urlopen`` without ``context=`` inherits the host's TLS floor, and no static
    analyser flags it because the context is built inside urllib. This asserts
    the property repo-wide so the next caller cannot reintroduce it.
    """

    import ast
    import pathlib

    offenders: list[str] = []
    for path in sorted(pathlib.Path("alphalab").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if name != "urlopen":
                continue
            if not any(keyword.arg == "context" for keyword in node.keywords):
                offenders.append(f"{path}:{node.lineno}")

    assert not offenders, (
        "urlopen called without an explicit context -- the TLS floor would be "
        f"inherited from the host's OpenSSL: {offenders}"
    )


def test_the_shared_policy_is_the_only_tls_definition() -> None:
    """Exactly one module may define a TLS floor."""

    import pathlib

    definers = [
        str(path)
        for path in sorted(pathlib.Path("alphalab").rglob("*.py"))
        if "minimum_version" in path.read_text(encoding="utf-8")
    ]
    assert definers == ["alphalab/common/tls.py"], (
        f"a TLS floor is set outside the shared policy module: {definers}"
    )


def test_the_shared_context_is_fresh_per_call() -> None:
    """A mutated context must not weaken anybody else's connection."""

    first = tls_context()
    first.minimum_version = ssl.TLSVersion.TLSv1_3
    assert tls_context().minimum_version == MINIMUM_TLS_VERSION
