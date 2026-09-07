"""The authority for what a provider symbol means.

Before v2.7 nothing owned this question. ``market.normalization.SymbolMap`` was
a ``Mapping[str, str]`` whose unmapped case returned the provider's symbol
unchanged, so ``"AAPL"`` became an ``asset_id``, travelled the whole execution
path, and was refused by ``core.Fill`` at the last stage. The mapping had no
notion of which provider a symbol belonged to, no metadata, and no way to mint
an identifier -- so the only configuration that reached a fill was one where an
operator hand-wrote a UUID per instrument.

An :class:`InstrumentRegistry` answers two questions and owns both:

``(provider, symbol) -> asset_id``
    Which instrument a given provider means by a given symbol.
``asset_id -> InstrumentRecord``
    What that instrument is.

Registration is explicit. Derivation alone would not be enough: a scheme that
derived an identifier from any symbol handed to it would turn every typo into a
new instrument, which is passthrough with extra steps. Determinism decides
*what* an identifier is; registration decides *whether there is one*.

Containers and complexity
-------------------------
Both indexes are :class:`~alphalab.common.persistent_map.PersistentMap`, so a
registration shares structure with the registry it came from instead of copying
it, and ``N`` registrations cost ``O(N)`` rather than ``O(N^2)`` -- the same
choice ``alphalab.model_registry`` and ``alphalab.deployment_manager`` made for
the same reason. Resolution is two keyed lookups and never scans: it runs once
per record on the normalization path, which is the hottest place in the system.

Refusal, not overwrite
----------------------
Registering the same record twice is a no-op, because a second copy of an
identical declaration says nothing new. Registering *different* content under an
identifier the registry already holds, or pointing one provider symbol at a
second instrument, raises. This follows
:func:`alphalab.lifecycle.promotion.record_evidence`, and for the same reason:
silently replacing an identity is how two runs come to disagree about what they
traded, long after either could be re-read.

Registration and classification are different questions
-------------------------------------------------------
That refusal is about *identity*, and until v2.11 it also blocked the one thing
ADR-0016 N5 said should be possible: changing a ``sector``. A record differing
only in its classification is different content, so ``register_instrument``
refused it, and a field documented as mutable was in practice immutable.

:func:`classify_instrument` is the answer, and it is a separate function rather
than a relaxation of the refusal. Relaxing it would make the rule depend on
*which* field differs, and would leave accidental content drift
indistinguishable from a deliberate reclassification. A separate function names
the intent, cannot reach an identity field, and leaves the refusal exactly as
strict as it was -- re-registering a stale, unclassified record over a
classified one is still refused, so a stale declaration cannot silently
un-classify an instrument. See ADR-0027.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

from alphalab.common.persistent_map import PersistentMap
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.record import InstrumentRecord, normalize_sector_label

__all__ = [
    "InstrumentRegistry",
    "classify_instrument",
    "classify_instruments",
    "get_instrument",
    "register_alias",
    "register_instrument",
    "register_instruments",
]


@dataclass(frozen=True, slots=True)
class InstrumentRegistry:
    """Immutable registry of declared instruments and their provider symbols.

    Build one with :func:`register_instrument` / :func:`register_instruments`
    rather than by hand: the two indexes are maintained together, the way
    :class:`alphalab.oms.book.OrderBook` maintains its own.

    Attributes:
        instruments: ``asset_id`` -> the record declaring that instrument, in
            registration order.
        by_provider: provider name -> that provider's symbol -> ``asset_id``.
            Indexed by the question the normalization path actually asks, so
            resolution is O(1) rather than a scan of every instrument.
    """

    instruments: PersistentMap[str, InstrumentRecord] = field(default_factory=PersistentMap)
    by_provider: PersistentMap[str, PersistentMap[str, str]] = field(default_factory=PersistentMap)

    def __post_init__(self) -> None:
        # The containers' own types only -- inspecting the entries here would
        # run on every registration and reintroduce the quadratic the
        # PersistentMap exists to remove. See ModelRegistry.__post_init__.
        if not isinstance(self.instruments, PersistentMap):
            object.__setattr__(self, "instruments", PersistentMap(self.instruments))
        if not isinstance(self.by_provider, PersistentMap):
            object.__setattr__(self, "by_provider", PersistentMap(self.by_provider))

    def resolve(self, provider: str, symbol: str) -> str | None:
        """The ``asset_id`` ``provider`` means by ``symbol``, or ``None``.

        Two keyed lookups, no scan. ``None`` means the pair is not registered;
        turning that into a refusal is the wire boundary's job, because only the
        boundary knows enough to say which record was being normalized. See
        :mod:`alphalab.market.normalization` and ADR-0016.
        """

        symbols = self.by_provider.get(provider)
        if symbols is None:
            return None
        return symbols.get(symbol)

    def record_for(self, asset_id: str) -> InstrumentRecord | None:
        """The instrument ``asset_id`` identifies, or ``None`` if unregistered."""

        return self.instruments.get(asset_id)


def get_instrument(registry: InstrumentRegistry, asset_id: str) -> InstrumentRecord:
    """The instrument ``asset_id`` identifies.

    Raises:
        InstrumentInputError: If no instrument is registered under ``asset_id``.
    """

    record = registry.instruments.get(asset_id)
    if record is None:
        raise InstrumentInputError(f"No instrument is registered under asset_id '{asset_id}'.")
    return record


def _with_alias(
    registry: InstrumentRegistry, provider: str, symbol: str, asset_id: str
) -> InstrumentRegistry:
    """Index one provider symbol, refusing to point it at a second instrument."""

    if not provider.strip():
        raise InstrumentInputError("provider cannot be empty.")
    if not symbol.strip():
        raise InstrumentInputError("provider symbol cannot be empty.")

    symbols: PersistentMap[str, str] = registry.by_provider.get(provider, PersistentMap())
    existing = symbols.get(symbol)
    if existing is not None and existing != asset_id:
        raise InstrumentRegistrationError(
            f"Provider '{provider}' symbol '{symbol}' already resolves to asset_id "
            f"'{existing}'; refusing to repoint it at '{asset_id}'. One provider "
            "symbol names one instrument."
        )
    if existing == asset_id:
        return registry

    return replace(
        registry, by_provider=registry.by_provider.set(provider, symbols.set(symbol, asset_id))
    )


def register_instrument(
    registry: InstrumentRegistry, record: InstrumentRecord
) -> InstrumentRegistry:
    """Register one instrument and index every provider symbol it declares.

    Registering an identical record again is a no-op: the identifier is derived
    from the declaration, so a second copy is the same instrument and storing it
    again changes nothing.

    Raises:
        InstrumentRegistrationError: If the registry already holds *different*
            content under this ``asset_id``, or if one of the record's aliases
            already resolves to another instrument. Nothing is written before
            either refusal.
    """

    existing = registry.instruments.get(record.asset_id)
    if existing is not None and existing != record:
        raise InstrumentRegistrationError(
            f"asset_id '{record.asset_id}' is already registered to a different "
            f"instrument ({existing.symbol} on {existing.exchange} in "
            f"{existing.currency}). Refusing to replace it."
        )

    updated = (
        registry
        if existing is not None
        else replace(registry, instruments=registry.instruments.set(record.asset_id, record))
    )
    for provider, symbol in record.aliases.items():
        updated = _with_alias(updated, provider, symbol, record.asset_id)
    return updated


def register_instruments(
    registry: InstrumentRegistry, records: Iterable[InstrumentRecord]
) -> InstrumentRegistry:
    """Register several instruments, in the order given."""

    for record in records:
        registry = register_instrument(registry, record)
    return registry


def register_alias(
    registry: InstrumentRegistry, asset_id: str, provider: str, symbol: str
) -> InstrumentRegistry:
    """Point one provider's symbol at an already-registered instrument.

    An alias is a lookup key, never an identity input: adding one does not
    change the instrument's ``asset_id``, because the identifier is derived from
    the declaration and aliases are not part of it.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered, or ``provider``
            or ``symbol`` is blank.
        InstrumentRegistrationError: If the pair already resolves elsewhere.
    """

    get_instrument(registry, asset_id)
    return _with_alias(registry, provider, symbol, asset_id)


def classify_instrument(
    registry: InstrumentRegistry, asset_id: str, sector: str | None
) -> InstrumentRegistry:
    """Declare what sector an already-registered instrument belongs to.

    The write ADR-0016 N5 kept the identity key clear for. ``sector`` is
    descriptive and mutable, so reclassifying is allowed, silently, with no
    refusal and no event -- and it cannot re-identify the instrument, because
    the identity is derived from ``asset_type``, ``exchange``, ``symbol`` and
    ``currency`` alone and none of them is reachable from this signature.
    ``asset_id``, ``canonical_key``, every alias and every provider resolution
    are byte-identical afterwards.

    Keyed by ``asset_id`` rather than by an
    :class:`~alphalab.instrument.record.InstrumentRecord`, because a record
    would admit one whose identity fields disagree with the registered
    instrument's -- a question with no good answer.

    ``sector=None`` unclassifies, and is a first-class operation rather than an
    error: an instrument whose classification has been withdrawn is
    unclassified, which is what ``None`` has always meant.

    Passing the label already in effect -- compared *after*
    :func:`~alphalab.instrument.record.normalize_sector_label`, so surrounding
    whitespace does not make a second classification -- returns the **same
    registry object** and writes nothing, matching
    :func:`register_instrument`'s no-op for an identical record.

    Copy-on-write, through the same :class:`PersistentMap` every other write
    here uses: ``set`` appends to the key's version chain and rebuilds nothing,
    so this is O(1), the registry it came from stays valid and unchanged, and
    ``N`` classifications cost ``O(N)``.

    Mints no identifier and emits no event.

    Args:
        registry: The registry to classify within.
        asset_id: The instrument to classify. Must already be registered.
        sector: The sector label, or ``None`` to unclassify.

    Returns:
        A registry in which ``asset_id`` carries ``sector``; the same object
        when that was already true.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered, or ``sector``
            is not a label :func:`normalize_sector_label` accepts.
    """

    record = get_instrument(registry, asset_id)
    label = None if sector is None else normalize_sector_label(sector)
    if record.sector == label:
        return registry

    return replace(
        registry, instruments=registry.instruments.set(asset_id, replace(record, sector=label))
    )


def classify_instruments(
    registry: InstrumentRegistry, classifications: Mapping[str, str | None]
) -> InstrumentRegistry:
    """Classify several instruments, in the mapping's iteration order.

    The plural of :func:`classify_instrument`, mirroring
    :func:`register_instruments`. Nothing partial survives a refusal: the
    entries are applied to successive values and the offending one raises before
    any later entry is reached, so the caller's own registry is untouched.
    """

    for asset_id, sector in classifications.items():
        registry = classify_instrument(registry, asset_id, sector)
    return registry
