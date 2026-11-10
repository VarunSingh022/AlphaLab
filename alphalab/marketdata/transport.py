"""HTTP transport abstraction for market data provider clients.

Provider clients depend on `Transport`, never on a concrete HTTP library or a
hardcoded return value. This is what the previous generation of provider clients
was missing: every client's `request_history`/`latest_quote`/etc. returned the same
literal values regardless of symbol, timeframe, or provider -- silently fake data
with no seam to ever make it real. `StaticTransport` makes that same determinism
explicit and honest for tests; `HttpTransport` provides a genuine implementation.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from typing import IO, Any, Final, Protocol
from urllib import request as urllib_request
from urllib.parse import urlencode, urljoin, urlsplit

from alphalab.common.tls import tls_context
from alphalab.marketdata.exceptions import MarketDataError

#: The schemes an HTTP transport opens. ``urllib`` opens many more -- given
#: ``file:///etc/hosts`` it returns the file's bytes as if a provider had sent
#: them, and it reads ``data:`` URLs and reaches ``ftp://`` hosts -- so every
#: other URL is refused before anything is opened, and a redirect is followed
#: only to one of these (SEC-002: the trust-boundary class BDY-028 named).
_HTTP_SCHEMES: Final = frozenset({"http", "https"})


def _require_http(url: str) -> None:
    """Refuse ``url`` unless it is an ``http://`` or ``https://`` URL that names a host."""

    parts = urlsplit(url)
    if parts.scheme not in _HTTP_SCHEMES:
        raise MarketDataError(
            f"{url!r} is not an HTTP URL: HttpTransport opens http:// and https:// only, "
            f"got {parts.scheme or 'no scheme'!r}."
        )
    if not parts.hostname:
        raise MarketDataError(f"{url!r} names no host.")


class _HttpRedirectsOnly(urllib_request.HTTPRedirectHandler):
    """Follows a redirect only to ``http`` or ``https``.

    The check runs before ``urllib``'s own, which would follow a redirect to
    ``ftp://`` and refuses others with an ``HTTPError``: every redirect the
    transport will not follow is refused the same way, and the response it
    arrived on is closed first.
    """

    def http_error_302(
        self,
        req: urllib_request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
    ) -> Any:
        location = headers.get("location") or headers.get("uri")
        if location is not None:
            target = urljoin(req.full_url, location)
            if urlsplit(target).scheme not in _HTTP_SCHEMES:
                fp.close()
                raise MarketDataError(
                    f"{req.full_url!r} answered with a redirect to {target!r}: HttpTransport "
                    "follows a redirect to http:// or https:// only."
                )
        return super().http_error_302(req, fp, code, msg, headers)  # type: ignore[arg-type]

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class Transport(Protocol):
    """Minimal HTTP GET interface a market data client depends on."""

    def get(self, url: str, params: Mapping[str, str]) -> bytes: ...


@dataclass(frozen=True, slots=True)
class HttpTransport:
    """A real HTTP transport using only the standard library.

    IMPORTANT: AlphaLab's development and CI environment has no network egress to
    external market data or exchange APIs. This implementation is written to the
    documented shape of each provider's public REST API, but has not been, and
    cannot currently be, exercised against a live endpoint from within this
    environment. Treat it as unverified until run against a real endpoint
    somewhere with network access.

    An ``https://`` endpoint is reached with
    :func:`~alphalab.common.tls.tls_context` -- certificate and hostname
    verified, TLS 1.2 pinned as the floor rather than inherited from the host's
    OpenSSL. That is the same policy the WebSocket client uses, defined once.
    See ADR-0031.

    Only ``http://`` and ``https://`` URLs are opened, and a redirect is
    followed only to one of them (SEC-002). Anything else -- ``file://``,
    ``ftp://``, ``data:``, a URL with no scheme or no host -- is refused with a
    :class:`~alphalab.marketdata.exceptions.MarketDataError` before anything is
    opened, as the WebSocket client refuses anything but ``ws://`` and
    ``wss://``. A URL is configuration, and configuration crosses a trust
    boundary.
    """

    timeout_seconds: float = 10.0

    def get(self, url: str, params: Mapping[str, str]) -> bytes:
        _require_http(url)
        query = urlencode(params)
        full_url = f"{url}?{query}" if query else url
        # The TLS context is explicit for the reason `alphalab.common.tls`
        # gives: left to itself urllib inherits the host's TLS floor, which on a
        # permissive machine admits TLS 1.0. It is the opener `urlopen` builds
        # when given a context, with the redirect handler replaced.
        opener = urllib_request.build_opener(
            urllib_request.HTTPSHandler(context=tls_context()), _HttpRedirectsOnly()
        )
        with opener.open(full_url, timeout=self.timeout_seconds) as response:
            body: bytes = response.read()
            return body


@dataclass(frozen=True, slots=True)
class StaticTransport:
    """A deterministic, offline transport for tests.

    Never performs real network I/O. Looks up a canned response body by exact URL
    from `responses`; falls back to `default_response` for any URL not present.
    """

    responses: Mapping[str, bytes] = field(default_factory=dict)
    default_response: bytes = b"{}"

    def get(self, url: str, params: Mapping[str, str]) -> bytes:
        return self.responses.get(url, self.default_response)
