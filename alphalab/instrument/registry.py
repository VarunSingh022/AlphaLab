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

Resolution is at an instant (v3.11)
-----------------------------------
A provider symbol can name different instruments at different dates -- a
rename, a ticker reused after a delisting (ledger DAT-004). A
:class:`~alphalab.instrument.record.DatedAlias` declares a symbol's meaning over
an interval, and :meth:`InstrumentRegistry.resolve_at` answers at the instant a
record is stamped with, which is what the wire boundary asks. The refusal rule
extends to time: two dated aliases of one provider symbol may not share an
instant, and a static alias -- which claims every instant -- may not coexist
with a dated one for the same symbol.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

from alphalab.common.persistent_map import PersistentMap
from alphalab.instrument.classification import (
    OPERATOR,
    SECTOR,
    ClassificationHistory,
    DimensionClassification,
    DimensionHistory,
    SectorClassification,
    normalize_dimension,
)
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.record import DatedAlias, InstrumentRecord, normalize_sector_label

__all__ = [
    "InstrumentRegistry",
    "classification_history",
    "classification_of",
    "classify_dimension",
    "classify_dimensions",
    "classify_instrument",
    "classify_instruments",
    "dimension_history",
    "get_instrument",
    "label_as_of",
    "register_alias",
    "register_dated_alias",
    "register_instrument",
    "register_instruments",
    "sector_as_of",
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
        classifications: ``asset_id`` -> every classification that instrument
            has been given, append-only, with the source and effective date of
            each. New in v2.15 and **additive**: an instrument classified before
            this existed, or constructed with a ``sector`` directly, simply has
            no history, and every reader of
            :attr:`~alphalab.instrument.record.InstrumentRecord.sector` is
            unaffected. The label in effect stays on the record, where the
            pipeline reads it in O(1); this answers the audit question beside
            it. See ADR-0031.
        dated: provider -> symbol -> every dated meaning of it (DAT-004).
        dimensions: dimension -> ``asset_id`` -> every classification that
            instrument has been given along that dimension (v3.12, ledger
            OFE-001). Every dimension but ``"sector"``, which stays on the
            record and in ``classifications``. See
            :func:`classify_dimension`.
        members: dimension -> label -> the instruments carrying that label now,
            ``"sector"`` included: what a limit on one bucket of a dimension
            reads, so its cost follows the bucket rather than the registry.
            An index over the records and ``dimensions`` -- derived, never
            declared: left ``None`` it is built from them, and every write
            here keeps it true.
    """

    instruments: PersistentMap[str, InstrumentRecord] = field(default_factory=PersistentMap)
    by_provider: PersistentMap[str, PersistentMap[str, str]] = field(default_factory=PersistentMap)
    classifications: PersistentMap[str, ClassificationHistory] = field(
        default_factory=PersistentMap
    )
    dated: PersistentMap[str, PersistentMap[str, tuple[tuple[DatedAlias, str], ...]]] = field(
        default_factory=PersistentMap
    )
    dimensions: PersistentMap[str, PersistentMap[str, DimensionHistory]] = field(
        default_factory=PersistentMap
    )
    members: PersistentMap[str, PersistentMap[str, PersistentMap[str, None]]] | None = None

    def __post_init__(self) -> None:
        # The containers' own types only -- inspecting the entries here would
        # run on every registration and reintroduce the quadratic the
        # PersistentMap exists to remove. See ModelRegistry.__post_init__.
        if not isinstance(self.instruments, PersistentMap):
            object.__setattr__(self, "instruments", PersistentMap(self.instruments))
        if not isinstance(self.by_provider, PersistentMap):
            object.__setattr__(self, "by_provider", PersistentMap(self.by_provider))
        if not isinstance(self.classifications, PersistentMap):
            object.__setattr__(self, "classifications", PersistentMap(self.classifications))
        if not isinstance(self.dated, PersistentMap):
            object.__setattr__(self, "dated", PersistentMap(self.dated))
        if not isinstance(self.dimensions, PersistentMap):
            object.__setattr__(self, "dimensions", PersistentMap(self.dimensions))
        if self.members is None:
            # Built once, when a registry is declared from mappings; every write
            # below passes the index on, kept true, so this never runs again.
            object.__setattr__(self, "members", _index(self.instruments, self.dimensions))

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

    def dated_windows(self, provider: str, symbol: str) -> tuple[tuple[DatedAlias, str], ...]:
        """Every dated meaning of one provider symbol, in time order, with its ``asset_id``."""

        symbols = self.dated.get(provider)
        if symbols is None:
            return ()
        return symbols.get(symbol, ())

    def resolve_at(self, provider: str, symbol: str, timestamp: float) -> str | None:
        """The ``asset_id`` ``provider`` means by ``symbol`` at ``timestamp``, or ``None``.

        A static alias answers at every instant. Otherwise the dated alias whose
        interval covers ``timestamp`` answers, and ``None`` means nothing is
        registered for that symbol *at that instant* -- which the wire boundary
        reports with the intervals that are registered.
        """

        static = self.resolve(provider, symbol)
        if static is not None:
            return static
        for alias, asset_id in self.dated_windows(provider, symbol):
            if alias.covers(timestamp):
                return asset_id
        return None

    def record_for(self, asset_id: str) -> InstrumentRecord | None:
        """The instrument ``asset_id`` identifies, or ``None`` if unregistered."""

        return self.instruments.get(asset_id)

    def label_of(self, asset_id: str, dimension: str) -> str | None:
        """The label ``asset_id`` carries along ``dimension`` now, or ``None``.

        ``dimension`` as :func:`~alphalab.instrument.classification.normalize_dimension`
        writes it; ``"sector"`` reads the record's own sector. Two keyed
        lookups, no scan.
        """

        if dimension == SECTOR:
            record = self.instruments.get(asset_id)
            return None if record is None else record.sector
        histories = self.dimensions.get(dimension)
        history = None if histories is None else histories.get(asset_id)
        return None if history is None else history.label

    def bucket_members(self, dimension: str, label: str) -> Mapping[str, None]:
        """Every instrument carrying ``label`` along ``dimension`` now, as a set of ``asset_id``.

        Empty for a label nobody carries. Keyed lookups into the index the
        registry keeps, so its cost is the bucket's, not the registry's.
        """

        index = self.members
        labels = None if index is None else index.get(dimension)
        found = None if labels is None else labels.get(label)
        return _NO_MEMBERS if found is None else found


_NO_MEMBERS: PersistentMap[str, None] = PersistentMap()


def _index(
    instruments: Mapping[str, InstrumentRecord],
    dimensions: Mapping[str, Mapping[str, DimensionHistory]],
) -> PersistentMap[str, PersistentMap[str, PersistentMap[str, None]]]:
    """The members index, built from what the registry declares."""

    index: dict[str, dict[str, dict[str, None]]] = {}
    for asset_id, record in instruments.items():
        if record.sector is not None:
            index.setdefault(SECTOR, {}).setdefault(record.sector, {})[asset_id] = None
    for dimension, histories in dimensions.items():
        for asset_id, history in histories.items():
            label = history.label
            if label is not None:
                index.setdefault(dimension, {}).setdefault(label, {})[asset_id] = None
    return PersistentMap(
        {
            dimension: PersistentMap(
                {label: PersistentMap(assets) for label, assets in labels.items()}
            )
            for dimension, labels in index.items()
        }
    )


def _relabelled(
    members: PersistentMap[str, PersistentMap[str, PersistentMap[str, None]]] | None,
    dimension: str,
    asset_id: str,
    before: str | None,
    after: str | None,
) -> PersistentMap[str, PersistentMap[str, PersistentMap[str, None]]]:
    """The members index with ``asset_id`` moved from bucket ``before`` to ``after``."""

    index = PersistentMap() if members is None else members
    if before == after:
        return index
    labels: PersistentMap[str, PersistentMap[str, None]] = index.get(dimension, PersistentMap())
    if before is not None:
        held = labels.get(before)
        if held is not None and asset_id in held:
            held = held.delete(asset_id)
            labels = labels.set(before, held) if held else labels.delete(before)
    if after is not None:
        labels = labels.set(after, labels.get(after, PersistentMap()).set(asset_id, None))
    if labels:
        return index.set(dimension, labels)
    return index.delete(dimension) if dimension in index else index


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
    dated = registry.dated_windows(provider, symbol)
    if dated:
        raise InstrumentRegistrationError(
            f"Provider '{provider}' symbol '{symbol}' has dated meanings "
            f"({', '.join(alias.describe() for alias, _ in dated)}); a static alias claims "
            "every instant and would contradict them. Declare this meaning as a DatedAlias."
        )
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


def _with_dated_alias(
    registry: InstrumentRegistry, alias: DatedAlias, asset_id: str
) -> InstrumentRegistry:
    """Index one dated meaning, refusing any instant claimed twice."""

    static = registry.resolve(alias.provider, alias.symbol)
    if static is not None:
        raise InstrumentRegistrationError(
            f"Provider '{alias.provider}' symbol '{alias.symbol}' is a static alias of "
            f"'{static}', which claims every instant; the dated alias {alias.describe()} "
            "would contradict or repeat it."
        )
    windows = registry.dated_windows(alias.provider, alias.symbol)
    if (alias, asset_id) in windows:
        return registry
    for held, held_asset in windows:
        if held.overlaps(alias):
            raise InstrumentRegistrationError(
                f"The dated alias {alias.describe()} for '{asset_id}' overlaps "
                f"{held.describe()} for '{held_asset}'. One provider symbol names one "
                "instrument at any instant."
            )
    ordered = tuple(
        sorted(
            (*windows, (alias, asset_id)),
            key=lambda item: -math.inf if item[0].valid_from is None else item[0].valid_from,
        )
    )
    symbols = registry.dated.get(alias.provider, PersistentMap())
    return replace(
        registry, dated=registry.dated.set(alias.provider, symbols.set(alias.symbol, ordered))
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
        else replace(
            registry,
            instruments=registry.instruments.set(record.asset_id, record),
            members=_relabelled(registry.members, SECTOR, record.asset_id, None, record.sector),
        )
    )
    for provider, symbol in record.aliases.items():
        updated = _with_alias(updated, provider, symbol, record.asset_id)
    for alias in record.dated_aliases:
        updated = _with_dated_alias(updated, alias, record.asset_id)
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


def register_dated_alias(
    registry: InstrumentRegistry, asset_id: str, alias: DatedAlias
) -> InstrumentRegistry:
    """Point one provider's symbol at a registered instrument over an interval.

    The dated counterpart of :func:`register_alias`: a lookup key, never an
    identity input, and refused if any instant it covers is already claimed.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered.
        InstrumentRegistrationError: If the symbol is a static alias, or any
            instant of the interval already resolves through another dated
            alias of the same symbol.
    """

    get_instrument(registry, asset_id)
    if not isinstance(alias, DatedAlias):
        raise InstrumentInputError(f"alias must be a DatedAlias, got {alias!r}.")
    return _with_dated_alias(registry, alias, asset_id)


def classify_instrument(
    registry: InstrumentRegistry,
    asset_id: str,
    sector: str | None,
    source: str = OPERATOR,
    as_of: float | None = None,
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
    whitespace does not make a second classification -- writes no new *label*.
    Since v2.15 it still records the act when it carries new provenance: the
    same sector re-declared by a second source, or with a different effective
    date, is a real event an audit needs. Re-declaring a label from the same
    source with the same date is a true no-op and returns the **same registry
    object**, matching :func:`register_instrument`'s no-op for an identical
    record.

    Copy-on-write, through the same :class:`PersistentMap` every other write
    here uses: ``set`` appends to the key's version chain and rebuilds nothing,
    so this is O(1), the registry it came from stays valid and unchanged, and
    ``N`` classifications cost ``O(N)``.

    Mints no identifier and emits no event. ``as_of`` is caller-supplied and no
    clock is read -- a registry that stamped itself with the wall clock would
    not be reproducible across two processes building it from one declaration
    file.

    Args:
        registry: The registry to classify within.
        asset_id: The instrument to classify. Must already be registered.
        sector: The sector label, or ``None`` to unclassify.
        source: Who this classification came from -- a vendor, a file, an
            analyst. Defaults to
            :data:`~alphalab.instrument.classification.OPERATOR`, which is the
            accurate answer when the caller names nobody: this registry's
            operator declared it.
        as_of: Unix timestamp the classification takes effect from, or ``None``
            when no effective date is declared. See
            :class:`~alphalab.instrument.classification.SectorClassification`.

    Returns:
        A registry in which ``asset_id`` carries ``sector`` and whose history
        records who said so; the same object when nothing at all changed.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered, if ``sector``
            is not a label :func:`normalize_sector_label` accepts, or if
            ``source`` / ``as_of`` is invalid.
    """

    record = get_instrument(registry, asset_id)
    label = None if sector is None else normalize_sector_label(sector)
    declaration = SectorClassification(sector=label, source=source, as_of=as_of)

    history = registry.classifications.get(asset_id, ClassificationHistory())
    unchanged = record.sector == label
    if unchanged and history.current == declaration:
        return registry
    if unchanged and label is None and not history:
        # Withdrawing a classification that was never given. There is nothing to
        # withdraw and no earlier provenance to supersede, so nothing happened --
        # recording an act here would put a "sector removed" entry in the audit
        # trail of an instrument that never had one. The v2.11 no-op, preserved.
        return registry

    updated = replace(
        registry,
        classifications=registry.classifications.set(asset_id, history.append(declaration)),
    )
    if record.sector == label:
        # New provenance for a label already in effect. The record is untouched
        # because nothing about the instrument changed -- only what is known
        # about how its classification came to be.
        return updated

    return replace(
        updated,
        instruments=updated.instruments.set(asset_id, replace(record, sector=label)),
        members=_relabelled(updated.members, SECTOR, asset_id, record.sector, label),
    )


def classify_instruments(
    registry: InstrumentRegistry,
    classifications: Mapping[str, str | None],
    source: str = OPERATOR,
    as_of: float | None = None,
) -> InstrumentRegistry:
    """Classify several instruments, in the mapping's iteration order.

    The plural of :func:`classify_instrument`, mirroring
    :func:`register_instruments`. ``source`` and ``as_of`` apply to every entry,
    which is what a vendor file being loaded actually looks like. Nothing
    partial survives a refusal: the entries are applied to successive values and
    the offending one raises before any later entry is reached, so the caller's
    own registry is untouched.
    """

    for asset_id, sector in classifications.items():
        registry = classify_instrument(registry, asset_id, sector, source, as_of)
    return registry


def classification_of(registry: InstrumentRegistry, asset_id: str) -> SectorClassification | None:
    """The classification currently in effect, with its provenance.

    ``None`` when the instrument has never been classified *through this
    registry* -- which includes an instrument whose ``sector`` was set by
    constructing an :class:`~alphalab.instrument.record.InstrumentRecord`
    directly. That is an honest absence rather than a fabricated attribution:
    nobody recorded where such a label came from, and inventing
    :data:`~alphalab.instrument.classification.OPERATOR` for it would claim
    provenance this registry does not have. Read
    :attr:`~alphalab.instrument.record.InstrumentRecord.sector` for the label
    itself, which is present either way.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered.
    """

    get_instrument(registry, asset_id)
    history = registry.classifications.get(asset_id)
    return None if history is None else history.current


def classification_history(registry: InstrumentRegistry, asset_id: str) -> ClassificationHistory:
    """Every classification this instrument has been given, oldest first.

    Empty for an instrument never classified through this registry. The log is
    append-only, so a correction appears as a new entry and the entry it
    corrected is still readable -- which is what makes a reclassification
    auditable rather than silent.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered.
    """

    get_instrument(registry, asset_id)
    return registry.classifications.get(asset_id, ClassificationHistory())


def sector_as_of(registry: InstrumentRegistry, asset_id: str, timestamp: float) -> str | None:
    """The sector this instrument was classified as at ``timestamp``.

    The registry's answer to a historical question, and deliberately **not**
    what portfolio attribution reads. A run's own attribution is frozen onto
    :class:`~alphalab.analytics.attribution.TradeRecord` at fill time (ADR-0027
    decision 5) and is never resolved back through a registry -- that is what
    makes reclassification unable to rewrite a finished run's P&L. This answers
    the different question of what the *reference data* said at an instant,
    which is what a data-quality review or a restated report asks.

    ``None`` when the instrument had no classification then, whether because it
    had not been classified yet or because its classification had been
    withdrawn.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered.
    """

    return classification_history(registry, asset_id).sector_at(timestamp)


def classify_dimension(
    registry: InstrumentRegistry,
    asset_id: str,
    dimension: str,
    label: str | None,
    source: str = OPERATOR,
    as_of: float | None = None,
) -> InstrumentRegistry:
    """Declare what an instrument is along a named dimension other than sector (OFE-001).

    The dimension's sibling of :func:`classify_instrument`, with its rules: the
    registry must hold ``asset_id``; ``label=None`` withdraws the label; every
    act that says something new -- a new label, or the same label from a new
    source or with a new effective date -- is appended to the instrument's
    history along that dimension, and one that says nothing new returns the
    **same registry object**; the identity is untouched; nothing reads a clock.
    The members index moves the instrument between buckets in the same write.

    ``"sector"`` is refused: the sector has its own path,
    :func:`classify_instrument`, because its label lives on the record and is
    frozen onto every fill. One dimension, one authority.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered, ``dimension``
            is not a dimension name or is ``"sector"``, or the label, source or
            ``as_of`` is invalid.
    """

    get_instrument(registry, asset_id)
    name = normalize_dimension(dimension)
    if name == SECTOR:
        raise InstrumentInputError(
            "The sector is classified by classify_instrument, whose label is the record's and "
            "is frozen onto every fill; it has no second path."
        )
    declaration = DimensionClassification(label, source, as_of)
    histories: PersistentMap[str, DimensionHistory] = registry.dimensions.get(name, PersistentMap())
    history = histories.get(asset_id, DimensionHistory())
    if history.current == declaration:
        return registry
    if declaration.label is None and not history:
        # Withdrawing a label never given: nothing happened.
        return registry
    return replace(
        registry,
        dimensions=registry.dimensions.set(
            name, histories.set(asset_id, history.append(declaration))
        ),
        members=_relabelled(registry.members, name, asset_id, history.label, declaration.label),
    )


def classify_dimensions(
    registry: InstrumentRegistry,
    dimension: str,
    labels: Mapping[str, str | None],
    source: str = OPERATOR,
    as_of: float | None = None,
) -> InstrumentRegistry:
    """Classify several instruments along one dimension, in the mapping's order.

    What loading one vendor file looks like: one dimension, one source, one
    effective date. An entry that is refused raises before any later one is
    applied, and the caller's registry is untouched.
    """

    for asset_id, label in labels.items():
        registry = classify_dimension(registry, asset_id, dimension, label, source, as_of)
    return registry


def dimension_history(
    registry: InstrumentRegistry, asset_id: str, dimension: str
) -> DimensionHistory:
    """Every classification ``asset_id`` has been given along ``dimension``, oldest first.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered, or ``dimension``
            is not a dimension name or is ``"sector"`` (whose history is
            :func:`classification_history`).
    """

    get_instrument(registry, asset_id)
    name = normalize_dimension(dimension)
    if name == SECTOR:
        raise InstrumentInputError(
            "The sector's history is classification_history(registry, asset_id)."
        )
    histories = registry.dimensions.get(name)
    history = None if histories is None else histories.get(asset_id)
    return DimensionHistory() if history is None else history


def label_as_of(
    registry: InstrumentRegistry, asset_id: str, dimension: str, timestamp: float
) -> str | None:
    """The label ``asset_id`` carried along ``dimension`` at ``timestamp``, or ``None``.

    The reference data's answer to a historical question -- ``"sector"``
    included, through :func:`sector_as_of` -- for the reasons that function
    gives.

    Raises:
        InstrumentInputError: If ``asset_id`` is not registered or ``dimension``
            is not a dimension name.
    """

    name = normalize_dimension(dimension)
    if name == SECTOR:
        return sector_as_of(registry, asset_id, timestamp)
    return dimension_history(registry, asset_id, name).label_at(timestamp)
