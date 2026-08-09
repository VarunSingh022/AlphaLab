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
from typing import Protocol
from urllib import request as urllib_request
from urllib.parse import urlencode


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
    """

    timeout_seconds: float = 10.0

    def get(self, url: str, params: Mapping[str, str]) -> bytes:
        query = urlencode(params)
        full_url = f"{url}?{query}" if query else url
        with urllib_request.urlopen(full_url, timeout=self.timeout_seconds) as response:
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
