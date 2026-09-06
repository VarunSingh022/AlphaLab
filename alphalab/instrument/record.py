"""What an instrument is, and the rules that keep its identity reproducible.

An :class:`InstrumentRecord` is the declaration a canonical ``asset_id`` is
derived from. Four of its fields form the identity; the rest describe the
instrument without changing what it *is*.

Why the field rules are strict
------------------------------

Every rule in :func:`normalize_key_field` exists so that two processes handed
the same instrument render the same key byte for byte. Non-ASCII is refused
outright, which removes Unicode normalization (NFC versus NFD) as a source of
divergence rather than picking a form and hoping every caller agrees; tickers,
MICs and ISO 4217 codes are ASCII, so this costs nothing real. Internal
whitespace is refused because ``"AAPL US"`` and ``"AAPL  US"`` would otherwise be
two instruments. Control characters -- the line separator especially -- are
refused rather than escaped, following
:func:`alphalab.lifecycle.identity.parse_ref`'s treatment of ``"@"``: an escaping
scheme is a second thing to get right and a second thing to keep compatible
forever.

There is no ``"UNKNOWN"`` sentinel for a missing exchange or currency. A
sentinel would derive one identity for two genuinely different instruments,
which is v2.6's "absent, not fabricated" rule violated at the layer where it
does the most damage. An instrument whose exchange or currency is not known is
not registerable.

What is *not* in the key
------------------------

``sector`` is descriptive and mutable: a reclassification would silently
re-identify the instrument and orphan every fill ever recorded against it.
Keeping it out of the key is what lets sector classification arrive later
without being a breaking change. It is declared here and left ``None``; nothing
in v2.7 populates it.

``aliases`` are lookup keys into the registry, not identity inputs -- adding a
second provider's spelling of an instrument must not change what that instrument
is. They are exempt from the key-field rules for the same reason: an alias has
to reproduce the provider's own spelling verbatim, whatever that spelling is.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.core.enums import AssetType
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.identity import canonical_instrument_key, derive_asset_id

__all__ = ["InstrumentRecord", "normalize_key_field"]


def normalize_key_field(value: str, field_name: str) -> str:
    """Normalize and validate one identity-key field. See ADR-0016 N3.

    Leading and trailing whitespace is stripped, and the result is uppercased.
    Everything else is refused rather than repaired.

    Args:
        value: Raw field value as declared by the caller.
        field_name: Name used in error messages.

    Returns:
        The normalized value: stripped, uppercase, printable ASCII.

    Raises:
        InstrumentInputError: If the value is empty, is not printable ASCII,
            or contains internal whitespace or control characters.
    """

    if not isinstance(value, str):
        raise InstrumentInputError(f"{field_name} must be a string, got {type(value).__name__}.")

    stripped = value.strip()
    if not stripped:
        raise InstrumentInputError(
            f"{field_name} cannot be empty. There is no 'unknown' sentinel: one "
            "would give two different instruments the same identity."
        )

    if not stripped.isascii():
        raise InstrumentInputError(
            f"{field_name} {value!r} contains non-ASCII characters. Identity keys "
            "are printable ASCII so that derivation cannot depend on Unicode "
            "normalization form."
        )

    if any(character.isspace() for character in stripped):
        raise InstrumentInputError(
            f"{field_name} {value!r} contains internal whitespace, which would make "
            "two spellings of one instrument into two instruments. A provider "
            "symbol that legitimately contains a space belongs in 'aliases'."
        )

    if not stripped.isprintable():
        raise InstrumentInputError(
            f"{field_name} {value!r} contains a control character. These are refused "
            "rather than escaped, so the canonical key needs no escaping scheme."
        )

    return stripped.upper()


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    """One declared instrument, and the canonical identity derived from it.

    ``symbol``, ``exchange`` and ``currency`` are normalized on construction, so
    a record always holds the canonical spelling rather than whatever the caller
    typed. ``asset_id`` is derived from those three plus ``asset_type`` and is
    not a constructor argument: it is a function of the declaration, and
    accepting one would let a caller assert an identity the fields do not
    support.

    Attributes:
        symbol: AlphaLab's canonical symbol, uppercase ASCII.
        asset_type: What kind of instrument this is.
        exchange: Listing venue, conventionally a MIC such as ``"XNAS"``.
        currency: Currency the instrument trades in, ISO 4217.
        sector: Sector the instrument belongs to, or ``None`` when unknown.
            AlphaLab has no security master that classifies instruments, so this
            is ``None`` for everything v2.7 can construct. It is deliberately
            not part of the identity key -- see the module docstring.
        aliases: Provider name -> that provider's symbol for this instrument.
            Verbatim, not normalized, and not part of the identity key.
        asset_id: The derived canonical identifier. UUIDv5, and therefore
            accepted by :func:`~alphalab.core.ids.validate_uuid_id`.
    """

    symbol: str
    asset_type: AssetType
    exchange: str
    currency: str
    sector: str | None = None
    aliases: Mapping[str, str] = field(default_factory=dict)
    asset_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.asset_type, AssetType):
            raise InstrumentInputError(
                f"asset_type must be an AssetType, got {type(self.asset_type).__name__}."
            )

        symbol = normalize_key_field(self.symbol, "symbol")
        exchange = normalize_key_field(self.exchange, "exchange")
        currency = normalize_key_field(self.currency, "currency")

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "aliases", dict(self.aliases))
        object.__setattr__(
            self,
            "asset_id",
            derive_asset_id(self.asset_type, exchange, symbol, currency),
        )

    @property
    def canonical_key(self) -> str:
        """The canonical key this record's ``asset_id`` was derived from.

        Exposed so that an identifier can be audited back to the declaration
        that produced it without re-deriving it by hand.
        """

        return canonical_instrument_key(self.asset_type, self.exchange, self.symbol, self.currency)
