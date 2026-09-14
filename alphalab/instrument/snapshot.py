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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import AssetType
from alphalab.instrument.classification import ClassificationHistory, SectorClassification
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry
from alphalab.persistence.decode import (
    as_float,
    as_mapping,
    as_optional_str,
    as_sequence,
    as_str,
    as_str_mapping,
    as_value_enum,
    require,
    require_schema_version,
)
from alphalab.persistence.exceptions import StateDecodeError

__all__ = [
    "INSTRUMENT_SNAPSHOT_SCHEMA",
    "ClassificationRecord",
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
#: New in v2.15, so it has nothing to be compatible with: no legacy shape, no
#: migration, and no "missing means 1".
INSTRUMENT_SNAPSHOT_SCHEMA: Final = 1

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


def capture(registry: InstrumentRegistry) -> InstrumentRegistrySnapshot:
    """Project ``registry`` onto a snapshot that serializes deterministically.

    Instruments are captured in registration order, which
    :class:`~alphalab.common.persistent_map.PersistentMap` preserves, so two
    captures of one registry produce identical payloads.
    """

    return InstrumentRegistrySnapshot(
        instruments=tuple(
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
            )
            for asset_id, record in registry.instruments.items()
        ),
        schema_version=INSTRUMENT_SNAPSHOT_SCHEMA,
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(snapshot: InstrumentRegistrySnapshot) -> InstrumentRegistry:
    """Rebuild the registry a snapshot describes.

    Each record is reconstructed from its declaration, so every ``asset_id`` is
    re-derived rather than read -- a snapshot cannot assert an identity. The
    provider index is rebuilt from the aliases, and the classification histories
    are restored entry for entry.

    Raises:
        StateDecodeError: If two records in the snapshot derive the same
            ``asset_id``, or if one record's aliases would point a provider
            symbol at two instruments. Both mean the payload describes a
            registry that could not have been built, and it is refused rather
            than half-applied.
    """

    instruments: dict[str, InstrumentRecord] = {}
    by_provider: dict[str, dict[str, str]] = {}
    classifications: dict[str, ClassificationHistory] = {}

    for entry in snapshot.instruments:
        record = InstrumentRecord(
            symbol=entry.symbol,
            asset_type=entry.asset_type,
            exchange=entry.exchange,
            currency=entry.currency,
            sector=entry.sector,
            aliases=dict(entry.aliases),
        )
        if record.asset_id in instruments:
            raise StateDecodeError(
                f"The instrument snapshot declares {entry.symbol} on {entry.exchange} "
                f"in {entry.currency} twice: both derive asset_id "
                f"'{record.asset_id}'. A registry cannot hold one identifier twice."
            )
        instruments[record.asset_id] = record

        for provider, symbol in record.aliases.items():
            symbols = by_provider.setdefault(provider, {})
            existing = symbols.get(symbol)
            if existing is not None and existing != record.asset_id:
                raise StateDecodeError(
                    f"The instrument snapshot points provider '{provider}' symbol "
                    f"'{symbol}' at both '{existing}' and '{record.asset_id}'. One "
                    "provider symbol names one instrument."
                )
            symbols[symbol] = record.asset_id

        if entry.classifications:
            classifications[record.asset_id] = ClassificationHistory(
                tuple(
                    SectorClassification(item.sector, item.source, item.as_of)
                    for item in entry.classifications
                )
            )

    return InstrumentRegistry(
        instruments=PersistentMap(instruments),
        by_provider=PersistentMap(
            {provider: PersistentMap(symbols) for provider, symbols in by_provider.items()}
        ),
        classifications=PersistentMap(classifications),
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
    )


def from_primitives(payload: Mapping[str, Any]) -> InstrumentRegistrySnapshot:
    """Decode a JSON-decoded snapshot payload back into a typed snapshot.

    Raises:
        StateDecodeError: If the payload is not an object, declares no schema
            version or one this build does not read, is missing a field, or
            holds a value of the wrong type. The message names the field.
    """

    payload = as_mapping(payload, "instrument registry snapshot")
    require_schema_version(payload, INSTRUMENT_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    records: Sequence[Any] = as_sequence(require(payload, "instruments"), "instruments")
    return InstrumentRegistrySnapshot(
        instruments=tuple(_record(item, index) for index, item in enumerate(records)),
        schema_version=INSTRUMENT_SNAPSHOT_SCHEMA,
    )
