"""The HTTP transport opens http:// and https:// and nothing else (SEC-002).

``HttpTransport.get`` handed its URL straight to ``urllib.request.urlopen``,
which opens far more than HTTP: given ``file:///etc/hosts`` it returned the
file's bytes as if a provider had sent them, and it would equally read
``data:`` URLs or reach ``ftp://`` hosts. A transport whose URL comes from
configuration is a trust boundary -- the class BDY-028 named for task paths --
so it now refuses every scheme but ``http`` and ``https`` before anything is
opened, and follows a redirect only to one of those, as the WebSocket client
has always refused anything but ``ws`` and ``wss``.
"""

from __future__ import annotations

import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

import pytest

from alphalab.marketdata import HttpTransport, MarketDataError

PAYLOAD = b'{"close": "101.5"}'


class _Provider(BaseHTTPRequestHandler):
    """A provider on a loopback port; some paths redirect elsewhere."""

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        target = {
            "/to-ftp": "ftp://127.0.0.1:1/quotes",
            "/to-data": "data:,secret",
            "/to-file": "file:///etc/hosts",
            "/to-http": "/quotes",
        }.get(path)
        if target is not None:
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = PAYLOAD if not query else PAYLOAD[:-1] + f', "query": "{query}"}}'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return None


@contextmanager
def _provider() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def nothing_is_opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every URL an opener was asked to open; a refusal leaves it empty."""

    opened: list[str] = []

    def record(self: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(url if isinstance(url, str) else url.full_url)
        raise AssertionError("an opener was used")

    monkeypatch.setattr(urllib_request.OpenerDirector, "open", record)
    return opened


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/hosts",
        "FILE:///etc/hosts",
        "ftp://127.0.0.1/quotes",
        "data:,secret",
        "jar:file:///tmp/x.jar!/quotes",
        "/etc/hosts",
        "127.0.0.1/quotes",
    ],
)
def test_a_url_that_is_not_http_is_refused_before_anything_is_opened(
    url: str, nothing_is_opened: list[str]
) -> None:
    with pytest.raises(MarketDataError, match="http:// and https:// only"):
        HttpTransport().get(url, {})

    assert nothing_is_opened == []


def test_an_http_url_without_a_host_is_refused(nothing_is_opened: list[str]) -> None:
    with pytest.raises(MarketDataError, match="names no host"):
        HttpTransport().get("http:///quotes", {})

    assert nothing_is_opened == []


def test_a_local_file_is_never_returned_as_a_response(tmp_path: Path) -> None:
    """The defect as found: before v4.3 this returned the file's bytes."""

    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"do not read")

    with pytest.raises(MarketDataError):
        HttpTransport().get(secret.as_uri(), {})


def test_an_http_response_is_returned_with_its_query() -> None:
    with _provider() as base:
        body = HttpTransport().get(f"{base}/quotes", {"symbol": "ACME"})

    assert body == PAYLOAD[:-1] + b', "query": "symbol=ACME"}'


def test_a_redirect_to_http_is_followed() -> None:
    with _provider() as base:
        assert HttpTransport().get(f"{base}/to-http", {}) == PAYLOAD


@pytest.mark.parametrize("path", ["/to-ftp", "/to-data", "/to-file"])
def test_a_redirect_to_another_scheme_is_refused(path: str) -> None:
    """``urllib`` would follow ``ftp://`` and refuse the others with an ``HTTPError``.

    The transport refuses all three the same way, before ``urllib`` looks.
    """

    with _provider() as base, pytest.raises(MarketDataError, match="redirect"):
        HttpTransport().get(f"{base}{path}", {})


def test_an_https_request_carries_alphalabs_tls_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opener is built with ``tls_context()``: a TLS 1.2 floor, certificates checked.

    The handler is stopped before it connects, so nothing leaves the machine.
    """

    seen: dict[str, ssl.SSLContext | None] = {}

    class Recording(urllib_request.HTTPSHandler):
        def __init__(self, context: ssl.SSLContext | None = None) -> None:
            seen["context"] = context
            super().__init__(context=context)

        def https_open(self, req: urllib_request.Request) -> Any:
            raise OSError("stopped before connecting")

    monkeypatch.setattr(urllib_request, "HTTPSHandler", Recording)
    with pytest.raises(OSError, match="stopped before connecting"):
        HttpTransport().get("https://provider.invalid/quotes", {"symbol": "ACME"})

    context = seen["context"]
    assert context is not None
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname
