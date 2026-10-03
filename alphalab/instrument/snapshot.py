"""Complete, restorable snapshots of an :class:`~alphalab.instrument.registry.InstrumentRegistry`.

ADR-0027 listed "persisting the ``InstrumentRegistry``" as an explicit non-goal,
and the reasoning was sound for what the registry then held: it was
*configuration*, every field of it re-derivable from the operator's own
declarations, so a snapshot would have been a second copy of a file they
already had. ``ConfigRecord.instruments_type`` records the type and
``restore`` asks the caller for the object back, exactly as it does for
``sizing_model`` and ``simulator``.

**v2.15 changes the premise, and only the premise.** The registry now also
holds :class:`~alphalab.instrument.classification.ClassificationHistory` -- who
classified each instrument, from what source, and as of when. That is *not*
re-derivable from a declaration file: it is the record of a sequence of acts,
and re-running the declarations would produce a history with one entry where the
real one has four. A registry that cannot be written down loses it on exit, and
an audit trail that does not survive the process is not an audit trail.

So this module exists for the history, and carries the identity and metadata
along because a history keyed by ``asset_id`` is unreadable without the
instruments it refers to.

What has *not* changed
----------------------
* The registry is still configuration, and ``restore`` of a **run** still asks
  the caller to supply one rather than rebuilding it from run state. ADR-0027
  decision 5's "``restore`` does not check that a supplied registry classifies
  instruments the way the captured run did, and must not" is untouched.
* A run's own attribution is still frozen onto
  :class:`~alphalab.analytics.attribution.TradeRecord` at fill time. Nothing
  here is read to resolve a historical sector for a finished run, and a
  reclassification still cannot rewrite one.
* ``asset_id`` is still derived, never stored as an input: :func:`restore`
  rebuilds each record from its declaration and the identifier falls out of it.
  A snapshot that carried an ``asset_id`` and set it would let an edited file
  assert an identity the fields do not support.

Every alias survives (v3.11)
----------------------------
Until v3.10 the snapshot carried each record's *declared* aliases and rebuilt
the provider index from them, so an alias added afterwards with
:func:`~alphalab.instrument.registry.register_alias` was silently dropped by a
round trip: the restored registry no longer resolved it, and
``restore(capture(registry)) == registry`` was false for any registry that had
used the function. Schema 2 carries those too -- ``later_aliases`` and
``later_dated_aliases`` beside the declaration -- and the dated aliases of
v3.11 (ledger DAT-004). A schema-1 payload is read with none of either: it
recorded none, and an alias it lost cannot be recovered from it.

Round trip
----------
``restore(capture(registry)) == registry``, the one contract every snapshot
module in AlphaLab holds. Across JSON, decode with
:func:`~alphalab.persistence.serializer.deserialize` and pass the result through
:func:`from_primitives`::

    payload = serialize(capture(registry))
    assert restore(from_primitives(deserialize(payload))) == registry
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from alphalab.common.persistent_map import PersistentMap
from alphalab.conventions.economics import economics_from_primitives
from alphalab.conventions.exceptions import ConventionInputError
from alphalab.core.enums import AssetType
from alphalab.instrument.classification import (
    SECTOR,
    ClassificationHistory,
    DimensionClassification,
    DimensionHistory,
    SectorClassification,
    normalize_dimension,
)
from alphalab.instrument.economics import InstrumentEconomics
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.record import DatedAlias, InstrumentRecord
from alphalab.instrument.registry import (
    InstrumentRegistry,
    register_alias,
    register_dated_alias,
    register_instrument,
)
from alphalab.persistence.decode import (
    as_float,
    as_mapping,
    as_optional_str,
    as_sequence,
    as_str,
    as_str_mapping,
    as_value_enum,
    require,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.upgrade import SchemaHistory, SchemaStep

__all__ = [
    "INSTRUMENT_SCHEMA_HISTORY",
    "INSTRUMENT_SNAPSHOT_SCHEMA",
    "ClassificationRecord",
    "DatedAliasRecord",
    "InstrumentRegistryRecord",
    "InstrumentRegistrySnapshot",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. See ADR-0023.
#:
#: A module-local literal rather than ``DEFAULT_SCHEMA_VERSION``, for the reason
#: v2.6 gave for the portfolio and v2.9 for the pipeline: that constant also
#: versions ``BaseEvent``, so bumping it would version every
#: event in the system as a side effect of one subsystem's change.
#:
#: New in v2.15 at version 1. Version 2 (v3.11) carries dated aliases, the
#: aliases registered after a record's declaration, and each instrument's
#: economics (ledger ACC-005); see the module docstring. Version 3 (v3.12)
#: carries each instrument's classifications along every dimension but sector
#: (ledger OFE-001); the index of each dimension's buckets is rebuilt, not
#: carried.
INSTRUMENT_SNAPSHOT_SCHEMA: Final = 3

_SUBSYSTEM: Final = "instrument"


@dataclass(frozen=True, slots=True)
class ClassificationRecord:
    """One classification act, as a snapshot carries it.

    A separate type from
    :class:`~alphalab.instrument.classification.SectorClassification` for the
    reason every snapshot module keeps one: the domain value validates on
    construction, and a decoder needs to report *which field of which entry* was
    wrong before that validation runs.
    """

    sector: str | None
    source: str
    as_of: float | None


@dataclass(frozen=True, slots=True)
class DimensionRecord:
    """One classification act along a named dimension, as a snapshot carries it."""

    label: str | None
    source: str
    as_of: float | None


@dataclass(frozen=True, slots=True)
class DatedAliasRecord:
    """One dated alias, as a snapshot carries it."""

    provider: str
    symbol: str
    valid_from: float | None
    valid_to: float | None


@dataclass(frozen=True, slots=True)
class InstrumentRegistryRecord:
    """One declared instrument, as a snapshot carries it.

    ``asset_id`` is deliberately **absent**. It is derived from the four
    identity fields, and storing it would let an edited snapshot claim an
    identity its own declaration does not produce -- the thing
    :class:`~alphalab.instrument.record.InstrumentRecord` refuses by making
    ``asset_id`` non-constructible.
    """

    symbol: str
    asset_type: AssetType
    exchange: str
    currency: str
    sector: str | None
    aliases: Mapping[str, str]
    classifications: tuple[ClassificationRecord, ...]
    dated_aliases: tuple[DatedAliasRecord, ...] = ()
    later_aliases: tuple[tuple[str, str], ...] = ()
    later_dated_aliases: tuple[DatedAliasRecord, ...] = ()
    economics: InstrumentEconomics | None = None
    #: Dimension -> every classification along it, oldest first; sorted by
    #: dimension so a payload does not depend on the order they were declared in.
    dimensions: Mapping[str, tuple[DimensionRecord, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstrumentRegistrySnapshot:
    """Complete, JSON-serializable projection of an :class:`InstrumentRegistry`.

    ``by_provider`` is **not** carried: it is an index over the aliases each
    record declares, and :func:`restore` rebuilds it by re-registering them --
    the same choice :mod:`alphalab.oms.snapshot` makes for the order book's
    asset and strategy indices. One fact, one home; an index written alongside
    the thing it indexes is a second copy to keep true.
    """

    instruments: tuple[InstrumentRegistryRecord, ...]
    schema_version: int = INSTRUMENT_SNAPSHOT_SCHEMA


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def _dated_record(alias: DatedAlias) -> DatedAliasRecord:
    return DatedAliasRecord(alias.provider, alias.symbol, alias.valid_from, alias.valid_to)


def capture(registry: InstrumentRegistry) -> InstrumentRegistrySnapshot:
    """Project ``registry`` onto a snapshot that serializes deterministically.

    Instruments are captured in registration order, which
    :class:`~alphalab.common.persistent_map.PersistentMap` preserves, so two
    captures of one registry produce identical payloads. An alias the provider
    index holds that the record did not declare is captured as a later alias,
    sorted, so the order in which later aliases were added does not change the
    payload.
    """

    static: dict[str, list[tuple[str, str]]] = {}
    for provider, symbols in registry.by_provider.items():
        for symbol, asset_id in symbols.items():
            static.setdefault(asset_id, []).append((provider, symbol))
    dated: dict[str, list[DatedAlias]] = {}
    for symbols_by_provider in registry.dated.values():
        for windows in symbols_by_provider.values():
            for alias, asset_id in windows:
                dated.setdefault(asset_id, []).append(alias)

    entries = []
    for asset_id, record in registry.instruments.items():
        declared = set(record.aliases.items())
        later = sorted(pair for pair in static.get(asset_id, ()) if pair not in declared)
        later_dated = sorted(
            (alias for alias in dated.get(asset_id, ()) if alias not in record.dated_aliases),
            key=lambda alias: (
                alias.provider,
                alias.symbol,
                -math.inf if alias.valid_from is None else alias.valid_from,
            ),
        )
        entries.append(
            InstrumentRegistryRecord(
                symbol=record.symbol,
                asset_type=record.asset_type,
                exchange=record.exchange,
                currency=record.currency,
                sector=record.sector,
                aliases=dict(record.aliases),
                classifications=tuple(
                    ClassificationRecord(entry.sector, entry.source, entry.as_of)
                    for entry in registry.classifications.get(asset_id, ClassificationHistory())
                ),
                dated_aliases=tuple(_dated_record(alias) for alias in record.dated_aliases),
                later_aliases=tuple(later),
                later_dated_aliases=tuple(_dated_record(alias) for alias in later_dated),
                economics=record.economics,
                dimensions={
                    dimension: tuple(
                        DimensionRecord(entry.label, entry.source, entry.as_of)
                        for entry in histories[asset_id]
                    )
                    for dimension, histories in sorted(registry.dimensions.items())
                    if asset_id in histories
                },
            )
        )
    return InstrumentRegistrySnapshot(
        instruments=tuple(entries), schema_version=INSTRUMENT_SNAPSHOT_SCHEMA
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def _dated(record: DatedAliasRecord) -> DatedAlias:
    return DatedAlias(record.provider, record.symbol, record.valid_from, record.valid_to)


def restore(snapshot: InstrumentRegistrySnapshot) -> InstrumentRegistry:
    """Rebuild the registry a snapshot describes.

    Each record is reconstructed from its declaration and registered, so every
    ``asset_id`` is re-derived rather than read -- a snapshot cannot assert an
    identity -- and every refusal registration applies is applied again. The
    later aliases are then registered, and the classification histories
    restored entry for entry.

    Raises:
        StateDecodeError: If two records in the snapshot derive the same
            ``asset_id``, or the aliases would point a provider symbol at two
            instruments at one instant. Both mean the payload describes a
            registry that could not have been built, and it is refused rather
            than half-applied.
    """

    registry = InstrumentRegistry()
    classifications: dict[str, ClassificationHistory] = {}
    dimensions: dict[str, dict[str, DimensionHistory]] = {}
    records: list[tuple[InstrumentRegistryRecord, InstrumentRecord]] = []

    try:
        for entry in snapshot.instruments:
            record = InstrumentRecord(
                symbol=entry.symbol,
                asset_type=entry.asset_type,
                exchange=entry.exchange,
                currency=entry.currency,
                sector=entry.sector,
                aliases=dict(entry.aliases),
                dated_aliases=tuple(_dated(item) for item in entry.dated_aliases),
                economics=entry.economics,
            )
            if record.asset_id in registry.instruments:
                raise StateDecodeError(
                    f"The instrument snapshot declares {entry.symbol} on {entry.exchange} "
                    f"in {entry.currency} twice: both derive asset_id "
                    f"'{record.asset_id}'. A registry cannot hold one identifier twice."
                )
            registry = register_instrument(registry, record)
            records.append((entry, record))
            if entry.classifications:
                classifications[record.asset_id] = ClassificationHistory(
                    tuple(
                        SectorClassification(item.sector, item.source, item.as_of)
                        for item in entry.classifications
                    )
                )
            for dimension, items in entry.dimensions.items():
                dimensions.setdefault(dimension, {})[record.asset_id] = DimensionHistory(
                    tuple(
                        DimensionClassification(item.label, item.source, item.as_of)
                        for item in items
                    )
                )
        for entry, record in records:
            for provider, symbol in entry.later_aliases:
                registry = register_alias(registry, record.asset_id, provider, symbol)
            for item in entry.later_dated_aliases:
                registry = register_dated_alias(registry, record.asset_id, _dated(item))
    except (InstrumentRegistrationError, InstrumentInputError) as error:
        raise StateDecodeError(
            f"The instrument snapshot describes a registry that could not have been built: {error}"
        ) from None

    # The members index is derived from the records and the histories, so it is
    # rebuilt here rather than read: one fact, one home.
    return InstrumentRegistry(
        instruments=registry.instruments,
        by_provider=registry.by_provider,
        classifications=PersistentMap(classifications),
        dated=registry.dated,
        dimensions=PersistentMap(
            {name: PersistentMap(histories) for name, histories in sorted(dimensions.items())}
        ),
    )


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _classification(value: Any, where: str) -> ClassificationRecord:
    """One classification entry from a JSON-decoded payload."""

    payload = as_mapping(value, where)
    raw_as_of = payload.get("as_of")
    return ClassificationRecord(
        sector=as_optional_str(payload.get("sector"), f"{where}.sector"),
        source=as_str(require(payload, "source"), f"{where}.source"),
        as_of=None if raw_as_of is None else as_float(raw_as_of, f"{where}.as_of"),
    )


def _dimension(value: Any, where: str) -> DimensionRecord:
    payload = as_mapping(value, where)
    raw_as_of = require(payload, "as_of")
    return DimensionRecord(
        label=as_optional_str(require(payload, "label"), f"{where}.label"),
        source=as_str(require(payload, "source"), f"{where}.source"),
        as_of=None if raw_as_of is None else as_float(raw_as_of, f"{where}.as_of"),
    )


def _dimensions(value: Any, where: str) -> dict[str, tuple[DimensionRecord, ...]]:
    declared: dict[str, tuple[DimensionRecord, ...]] = {}
    for name, items in as_mapping(value, where).items():
        try:
            dimension = normalize_dimension(name)
        except InstrumentInputError as exc:
            raise StateDecodeError(f"{where} names no dimension: {exc}") from exc
        if dimension != name or dimension == SECTOR:
            raise StateDecodeError(
                f"{where} holds {name!r}, which is not a dimension written as one is "
                "(the sector is carried as classifications)."
            )
        declared[name] = tuple(
            _dimension(item, f"{where}.{name}[{position}]")
            for position, item in enumerate(as_sequence(items, f"{where}.{name}"))
        )
    return declared


def _dated_alias(value: Any, where: str) -> DatedAliasRecord:
    payload = as_mapping(value, where)
    start = require(payload, "valid_from")
    end = require(payload, "valid_to")
    return DatedAliasRecord(
        provider=as_str(require(payload, "provider"), f"{where}.provider"),
        symbol=as_str(require(payload, "symbol"), f"{where}.symbol"),
        valid_from=None if start is None else as_float(start, f"{where}.valid_from"),
        valid_to=None if end is None else as_float(end, f"{where}.valid_to"),
    )


def _pair(value: Any, where: str) -> tuple[str, str]:
    items = as_sequence(value, where)
    if len(items) != 2:
        raise StateDecodeError(f"{where} is not a [provider, symbol] pair: {value!r}")
    return as_str(items[0], f"{where}[0]"), as_str(items[1], f"{where}[1]")


def _record(value: Any, index: int) -> InstrumentRegistryRecord:
    """One instrument entry from a JSON-decoded payload."""

    where = f"instruments[{index}]"
    payload = as_mapping(value, where)
    return InstrumentRegistryRecord(
        symbol=as_str(require(payload, "symbol"), f"{where}.symbol"),
        asset_type=as_value_enum(AssetType, require(payload, "asset_type"), f"{where}.asset_type"),
        exchange=as_str(require(payload, "exchange"), f"{where}.exchange"),
        currency=as_str(require(payload, "currency"), f"{where}.currency"),
        sector=as_optional_str(payload.get("sector"), f"{where}.sector"),
        aliases=as_str_mapping(payload.get("aliases", {}), f"{where}.aliases"),
        classifications=tuple(
            _classification(item, f"{where}.classifications[{position}]")
            for position, item in enumerate(
                as_sequence(payload.get("classifications", ()), f"{where}.classifications")
            )
        ),
        dated_aliases=tuple(
            _dated_alias(item, f"{where}.dated_aliases[{position}]")
            for position, item in enumerate(
                as_sequence(require(payload, "dated_aliases"), f"{where}.dated_aliases")
            )
        ),
        later_aliases=tuple(
            _pair(item, f"{where}.later_aliases[{position}]")
            for position, item in enumerate(
                as_sequence(require(payload, "later_aliases"), f"{where}.later_aliases")
            )
        ),
        later_dated_aliases=tuple(
            _dated_alias(item, f"{where}.later_dated_aliases[{position}]")
            for position, item in enumerate(
                as_sequence(require(payload, "later_dated_aliases"), f"{where}.later_dated_aliases")
            )
        ),
        economics=_economics(require(payload, "economics"), f"{where}.economics"),
        dimensions=_dimensions(require(payload, "dimensions"), f"{where}.dimensions"),
    )


def _economics(value: Any, where: str) -> InstrumentEconomics | None:
    if value is None:
        return None
    try:
        return economics_from_primitives(value, where)
    except ConventionInputError as exc:
        raise StateDecodeError(f"{where} are not an instrument's economics: {exc}") from exc


#: How every instrument payload a release has written is read by this one. See
#: :mod:`alphalab.persistence.upgrade`.
def _v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """A version-1 record declared no dated alias, no later alias and no economics.

    Each is what the payload says: v2.15 to v3.10 had no dated aliases, their
    writer did not record aliases registered after a declaration -- a registry
    that had used ``register_alias`` lost them when it was written, and no
    reading of the payload can bring them back -- and nothing declared an
    instrument's economics, so none is recorded rather than one assumed.
    """

    return {
        **payload,
        "instruments": [
            {
                **entry,
                "dated_aliases": [],
                "later_aliases": [],
                "later_dated_aliases": [],
                "economics": None,
            }
            for entry in payload["instruments"]
        ],
    }


def _v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    """A version-2 instrument was classified along no dimension but sector.

    Nothing before v3.12 could classify along another (ledger OFE-001), so
    each record's dimensions are empty -- what the payload says.
    """

    return {
        **payload,
        "instruments": [{**entry, "dimensions": {}} for entry in payload["instruments"]],
    }


INSTRUMENT_SCHEMA_HISTORY = SchemaHistory(
    _SUBSYSTEM,
    INSTRUMENT_SNAPSHOT_SCHEMA,
    (
        SchemaStep(
            1,
            "version 2 carries dated aliases, aliases registered after a declaration, and each "
            "instrument's economics",
            upgrade=_v1_to_v2,
        ),
        SchemaStep(
            2,
            "version 3 carries each instrument's classifications along dimensions other "
            "than sector; no earlier registry had any",
            upgrade=_v2_to_v3,
        ),
    ),
)


def from_primitives(payload: Mapping[str, Any]) -> InstrumentRegistrySnapshot:
    """Decode a JSON-decoded snapshot payload back into a typed snapshot.

    Raises:
        StateDecodeError: If the payload is not an object, declares no schema
            version or one this build does not read, is missing a field, or
            holds a value of the wrong type. The message names the field.
    """

    payload = as_mapping(payload, "instrument registry snapshot")
    payload = INSTRUMENT_SCHEMA_HISTORY.upgrade(payload)

    records: Sequence[Any] = as_sequence(require(payload, "instruments"), "instruments")
    return InstrumentRegistrySnapshot(
        instruments=tuple(_record(item, index) for index, item in enumerate(records)),
        schema_version=INSTRUMENT_SNAPSHOT_SCHEMA,
    )
