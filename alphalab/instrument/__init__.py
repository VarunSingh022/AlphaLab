"""AlphaLab instrument identity: what a provider symbol actually means.

This package owns one concept -- the canonical identity of a tradable
instrument -- and it exists because nothing did. Until v2.7 a provider symbol
became an ``asset_id`` verbatim, travelled the entire execution path, and was
refused by :class:`alphalab.core.Fill` at the last stage, so the documented
default configuration could not reach a fill at all. See ADR-0016.

A canonical ``asset_id`` is opaque, UUID-shaped, and *derived* rather than
minted::

    InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD").asset_id
    # '2b670078-27a6-57c2-b359-4e64d8809ea2'

Deriving it with ``uuid5`` over a canonical key, instead of minting a ``uuid4``
at registration, is what lets two independently configured environments agree on
the identity of one instrument with no shared database. Registration is still
required: derivation alone would turn every typo into a new instrument.

Layering
--------
This package depends only on :mod:`alphalab.common` and
:mod:`alphalab.core.enums` / :mod:`alphalab.core.ids`. It imports no market,
strategy, execution, OMS or lifecycle module, and nothing in those layers is
imported in reverse -- :mod:`alphalab.market` depends on this package, not the
other way round.

Resolution is a pure lookup here. Refusing an unregistered ``(provider,
symbol)`` belongs to the wire boundary, which is why
:meth:`InstrumentRegistry.resolve` answers ``None`` and
:mod:`alphalab.market.normalization` raises
:class:`~alphalab.market.exceptions.InstrumentResolutionError`.

Strategies
----------
:mod:`alphalab.strategy` does not import this package, and
:class:`~alphalab.strategy.events.Intent` is unchanged and unvalidated. The
resolver is public so a strategy author *can* name the same instrument the
market data does; nothing intercepts an ``Intent`` that does not. A strategy
naming an instrument the run never priced produces no orders -- which is the
v2.6 behaviour, and detecting it is not v2.7 work.
"""

from alphalab.instrument.exceptions import (
    InstrumentError,
    InstrumentInputError,
    InstrumentRegistrationError,
)
from alphalab.instrument.identity import (
    ALPHALAB_INSTRUMENT_NAMESPACE,
    INSTRUMENT_KEY_SCHEME,
    canonical_instrument_key,
    derive_asset_id,
)
from alphalab.instrument.record import InstrumentRecord, normalize_key_field
from alphalab.instrument.registry import (
    InstrumentRegistry,
    get_instrument,
    register_alias,
    register_instrument,
    register_instruments,
)

__all__ = [
    "ALPHALAB_INSTRUMENT_NAMESPACE",
    "INSTRUMENT_KEY_SCHEME",
    "InstrumentError",
    "InstrumentInputError",
    "InstrumentRecord",
    "InstrumentRegistrationError",
    "InstrumentRegistry",
    "canonical_instrument_key",
    "derive_asset_id",
    "get_instrument",
    "normalize_key_field",
    "register_alias",
    "register_instrument",
    "register_instruments",
]
