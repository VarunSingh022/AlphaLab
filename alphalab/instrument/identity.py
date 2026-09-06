"""The canonical instrument key, and the identifier derived from it.

A canonical ``asset_id`` is an opaque, UUID-shaped, AlphaLab-issued identifier
for one tradable instrument. It is not a ticker and not a provider symbol, and
nothing downstream parses it.

It is *derived*, not minted: ``uuid5`` over a canonical rendering of the
instrument's declared fields. That is what makes two independently configured
environments agree on the identity of the same instrument with no shared
database and no coordination -- a registry minting ``uuid4`` would make evidence
recorded in staging incomparable with evidence recorded in production, for a
reason that has nothing to do with what was measured.

UUIDv5 output is a valid UUID, so :func:`~alphalab.core.ids.validate_uuid_id`
accepts a derived identifier unchanged. ADR-0016 does not relax that check; it
supplies a producer that can satisfy it.

The rendering follows the two precedents AlphaLab already had for content
digests -- :func:`alphalab.lifecycle.evidence.evidence_id_for` and
:func:`alphalab.deployment_manager.packaging.compute_checksum` -- both of which
join ``label=value`` lines with newlines and hash the result. Field order is
fixed and part of the specification, not sorted: the field set is closed, and
``evidence_id_for`` likewise sorts only its open-ended metric mapping.

The scheme tag is the first line. A future change to the key's shape bumps it
and leaves every identifier derived under ``v1`` reproducible.
"""

from __future__ import annotations

from uuid import UUID, uuid5

from alphalab.core.enums import AssetType

__all__ = [
    "ALPHALAB_INSTRUMENT_NAMESPACE",
    "INSTRUMENT_KEY_SCHEME",
    "canonical_instrument_key",
    "derive_asset_id",
]

#: Namespace every canonical instrument identifier is derived under.
#:
#: This is ``uuid5(NAMESPACE_DNS, "instrument.alphalab.dev")``, written out as a
#: literal rather than recomputed at import so that the constant is a fixed
#: fact of the scheme rather than a value that could drift with the expression
#: that produced it. Any reader can recompute it to audit this claim. It is
#: frozen for the life of the scheme: changing it changes every ``asset_id`` in
#: existence and requires a new ADR.
ALPHALAB_INSTRUMENT_NAMESPACE = UUID("1935bdfa-e8c0-5611-ae10-607c3a67c19b")

#: Scheme tag, and the first line of every canonical key. See ADR-0016 N2.
INSTRUMENT_KEY_SCHEME = "alphalab.instrument.v1"


def canonical_instrument_key(
    asset_type: AssetType, exchange: str, symbol: str, currency: str
) -> str:
    """Render the canonical key an instrument's identifier is derived from.

    The arguments are expected to be already normalized -- uppercase, ASCII,
    free of internal whitespace. :class:`~alphalab.instrument.record.InstrumentRecord`
    normalizes them on construction and is the only thing that should be calling
    this; the function is public so that the rendering can be pinned by a test
    and read by anyone auditing an identifier.

    ``asset_type`` renders as its ``value``. :class:`~alphalab.core.enums.AssetType`
    is a ``@unique StrEnum`` whose values are its declared canonical spellings,
    so the value is the stable serialized form. ``evidence_id_for`` renders
    ``ValidationMethod`` by ``name`` for the same reason inverted -- that enum is
    ``auto()``-valued and has no declared string. The rule in both cases is to
    use the form that was declared.

    Args:
        asset_type: What kind of instrument this is.
        exchange: Listing venue, conventionally a MIC.
        symbol: AlphaLab's canonical symbol for the instrument.
        currency: Currency the instrument trades in.

    Returns:
        The canonical key, newline-separated, in the fixed field order.
    """

    return "\n".join(
        [
            INSTRUMENT_KEY_SCHEME,
            f"asset_type={asset_type.value}",
            f"exchange={exchange}",
            f"symbol={symbol}",
            f"currency={currency}",
        ]
    )


def derive_asset_id(asset_type: AssetType, exchange: str, symbol: str, currency: str) -> str:
    """Derive the canonical ``asset_id`` for one declared instrument.

    Deterministic: the same declared instrument yields the same identifier in
    every process, forever, with no shared state. The result is a UUIDv5 string
    and satisfies :func:`~alphalab.core.ids.validate_uuid_id`.
    """

    return str(
        uuid5(
            ALPHALAB_INSTRUMENT_NAMESPACE,
            canonical_instrument_key(asset_type, exchange, symbol, currency),
        )
    )
