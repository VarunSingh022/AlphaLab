"""The TLS policy every outbound AlphaLab connection is made with.

**One policy, one definition.** Two packages open TLS connections to somebody
else's server -- :mod:`alphalab.marketdata.websocket` for a ``wss://`` market
feed and :mod:`alphalab.broker.transport` for an ``https://`` order submission
-- and a security floor that is written down twice is a security floor that can
drift. It lives here because it belongs to neither of them: it is shared
infrastructure, which is what :mod:`alphalab.common` is for, and both packages
already depend on this one.

Why an explicit floor is necessary
----------------------------------
:func:`ssl.create_default_context` does the two things that matter most -- it
verifies the server certificate against the system trust store and checks the
hostname against it -- and both are preserved untouched here. What it does
**not** do is pin a protocol floor::

    ssl.create_default_context().minimum_version  # whatever OpenSSL says

The value that comes back is inherited from the host's OpenSSL build and its
``openssl.cnf``. On one machine that is TLS 1.2; on a machine with a permissive
crypto policy it is TLS 1.0, and the default context sets neither
``OP_NO_TLSv1`` nor ``OP_NO_TLSv1_1`` to prevent it. CodeQL's "Use of insecure
SSL/TLS version" rule reports exactly that, and it is right to: a guarantee that
holds because of how a machine happens to be configured is not a guarantee the
code has. It is the same failure this codebase refuses everywhere else, and the
same reason ``UnresolvedIdentity`` is refused at the source boundary rather than
trusted to be configured correctly.

Setting the floor explicitly makes it AlphaLab's, identical on every host, and
auditable by reading this module instead of a system configuration file.

Why TLS 1.2 and not 1.3
-----------------------
TLS 1.0 and 1.1 are deprecated by RFC 8996 and are not offered. TLS 1.2 is the
floor rather than 1.3 because 1.3 is not yet universal at trading venues, and
refusing a venue that only speaks 1.2 would be a functional break rather than a
security gain -- TLS 1.2 with a modern cipher suite is not the weakness RFC 8996
retired. No ceiling is set, so 1.3 is negotiated whenever the peer offers it.

There is deliberately **no way to lower this**. A ``minimum_tls_version``
parameter would exist only to be set to something worse, and a fallback that
retries a refused handshake on an older protocol is precisely the downgrade an
attacker wants to provoke. See ADR-0031.
"""

from __future__ import annotations

import ssl
from typing import Final

__all__ = ["MINIMUM_TLS_VERSION", "tls_context"]

#: The oldest TLS version any outbound AlphaLab connection may negotiate.
MINIMUM_TLS_VERSION: Final = ssl.TLSVersion.TLSv1_2


def tls_context() -> ssl.SSLContext:
    """A verified TLS client context with AlphaLab's protocol floor applied.

    Certificate verification (``verify_mode = CERT_REQUIRED``) and hostname
    checking (``check_hostname = True``) come from
    :func:`ssl.create_default_context` and are left exactly as it sets them.
    Nothing here relaxes either, and no parameter could: this function takes
    none.

    A **fresh** context is returned on every call, so a caller that mutates one
    cannot weaken the connection any other caller makes.

    Returns:
        A context whose ``minimum_version`` is :data:`MINIMUM_TLS_VERSION` and
        whose ``maximum_version`` is left uncapped, so TLS 1.3 is used whenever
        the peer supports it.
    """

    context = ssl.create_default_context()
    context.minimum_version = MINIMUM_TLS_VERSION
    return context
