"""Reference-data packs: data with its evidence, in one canonical format (v4.3, ADR-0059).

The engine ships no market or reference data (BDY-013). What it ships is the
mechanism, and a format for the data to arrive in. A :class:`ReferencePack` is a
body of reference data -- in v4.3, a universe's dated membership -- with the
evidence of where it came from:

* ``pack_id`` and ``version``;
* its source: the publisher, and the document it was taken from;
* ``as_of``: when it was taken, which is as far as its knowledge reaches;
* the range ``[valid_from, valid_to)`` it claims to cover;
* a licence note;
* its entries, each tagged with its type, and their content digest under
  :data:`REFERENCE_PACK_SCHEME`.

This module holds the format and the checks that need nothing but the pack: it
refuses entries that do not match the digest it states, an entry of a type it
does not know or of the wrong shape, an entry that takes effect outside the range
it claims or was announced after it was taken, and a question about a universe it
does not hold. A missing entry is refused, never assumed: a missing holiday would
otherwise be an extra session, and a missing constituent a survivor. What an
entry *means* is its domain's: a membership change is read by
:func:`alphalab.instrument.membership.universe_from_pack`, whose universe carries
the pack's range and knowledge, so a question outside them is refused inside a
run as well. This package reaches nothing on the execution path.

A pack is evidence, not truth. A run that read one cites :attr:`ReferencePack.citation`
-- its id, version and digest -- beside its record, as ADR-0053's provenance
cites a schedule. A digest that matches says the entries are the ones stated,
never that they are right. The loader and the data distributions are M10's.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from alphalab.data.exceptions import DataValidationError

__all__ = [
    "MEMBERSHIP_CHANGE",
    "MEMBERSHIP_KINDS",
    "REFERENCE_PACK_SCHEME",
    "PackCitation",
    "PackSource",
    "ReferencePack",
    "reference_pack_digest",
]

#: The scheme a pack's content digest is taken under.
REFERENCE_PACK_SCHEME: Final = "alphalab.reference_pack.v1"

#: The type tag of a membership change entry.
MEMBERSHIP_CHANGE: Final = "MembershipChange"

#: The kinds a membership change entry may name.
MEMBERSHIP_KINDS: Final = frozenset({"ADDITION", "REMOVAL", "DELISTING"})

#: Every entry type a pack holds: its fields, and which of them are instants.
_TYPES: Final[Mapping[str, tuple[frozenset[str], frozenset[str]]]] = MappingProxyType(
    {
        MEMBERSHIP_CHANGE: (
            frozenset({"type", "universe", "asset_id", "kind", "effective_at", "announced_at"}),
            frozenset({"effective_at", "announced_at"}),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class PackSource:
    """Where a pack's data was taken from.

    Attributes:
        publisher: Who published it: an index provider, an exchange.
        document: The document it was taken from: a methodology notice, a file.
    """

    publisher: str
    document: str

    def __post_init__(self) -> None:
        for name in ("publisher", "document"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DataValidationError(f"PackSource.{name} must be stated, got {value!r}.")


@dataclass(frozen=True, slots=True)
class PackCitation:
    """What a run that read a pack records beside its record: which pack, exactly."""

    pack_id: str
    version: str
    digest: str


def _instant(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise DataValidationError(f"{where} must be a finite instant, got {value!r}.")
    return float(value)


def _entry(value: Any, index: int) -> Mapping[str, Any]:
    """Entry ``index``, held to its type's shape, as a read-only mapping."""

    if not isinstance(value, Mapping):
        raise DataValidationError(f"Pack entry {index} is not a record, got {value!r}.")
    kind = value.get("type")
    shape = _TYPES.get(kind) if isinstance(kind, str) else None
    if shape is None:
        raise DataValidationError(
            f"Pack entry {index} has type {kind!r}; a pack holds {', '.join(sorted(_TYPES))}."
        )
    names, instants = shape
    if set(value) != names:
        raise DataValidationError(
            f"Pack entry {index}, a {kind}, has fields {sorted(value)}; it has exactly "
            f"{sorted(names)}."
        )
    entry: dict[str, Any] = {}
    for name in sorted(names):
        item = value[name]
        if name in instants:
            entry[name] = _instant(item, f"Pack entry {index}'s {name}")
        elif not isinstance(item, str) or not item.strip():
            raise DataValidationError(f"Pack entry {index}'s {name} must be named, got {item!r}.")
        else:
            entry[name] = item
    if kind == MEMBERSHIP_CHANGE and entry["kind"] not in MEMBERSHIP_KINDS:
        raise DataValidationError(
            f"Pack entry {index} names membership kind {entry['kind']!r}; the kinds are "
            f"{', '.join(sorted(MEMBERSHIP_KINDS))}."
        )
    return MappingProxyType(entry)


def _order(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    return (entry["type"], entry["universe"], entry["effective_at"], entry["asset_id"])


def _canonical(entries: tuple[Mapping[str, Any], ...]) -> tuple[Mapping[str, Any], ...]:
    return tuple(sorted((_entry(value, index) for index, value in enumerate(entries)), key=_order))


def reference_pack_digest(entries: tuple[Mapping[str, Any], ...]) -> str:
    """SHA-256 of ``entries``' canonical text under :data:`REFERENCE_PACK_SCHEME`.

    The text is canonical JSON -- sorted keys, no whitespace, no non-finite
    numbers -- the form AlphaLab's serializer writes these values in. The entries
    are written in one order whatever order they were given in, so two packs
    holding the same entries state one digest.

    Raises:
        DataValidationError: If an entry is not a record of a type a pack holds.
    """

    text = json.dumps(
        {
            "entries": [dict(entry) for entry in _canonical(entries)],
            "scheme": REFERENCE_PACK_SCHEME,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReferencePack:
    """Reference data with the evidence of where it came from (ADR-0059 section 4).

    Attributes:
        pack_id: The pack's identifier.
        version: Its version.
        source: Its publisher and the document it was taken from.
        as_of: When it was taken: what was announced later is not in it.
        valid_from: The first instant it claims to cover.
        valid_to: The instant its coverage ends, exclusive.
        licence: The terms its data may be used under, as stated.
        entries: Its entries, each a record tagged with its type; kept read-only,
            in one canonical order.
        digest: The content digest it states for them.

    Raises:
        DataValidationError: If a field is missing or malformed, the range is
            empty, an entry is of an unknown type or shape, takes effect at or
            after ``valid_to``, or was announced after the pack was taken, or the
            entries do not match the stated digest.
    """

    pack_id: str
    version: str
    source: PackSource
    as_of: float
    valid_from: float
    valid_to: float
    licence: str
    entries: tuple[Mapping[str, Any], ...]
    digest: str

    def __post_init__(self) -> None:
        for name in ("pack_id", "version", "licence", "digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DataValidationError(f"ReferencePack.{name} must be stated, got {value!r}.")
        if not isinstance(self.source, PackSource):
            raise DataValidationError(
                f"ReferencePack.source must be a PackSource, got {self.source!r}."
            )
        for name in ("as_of", "valid_from", "valid_to"):
            object.__setattr__(self, name, _instant(getattr(self, name), f"ReferencePack.{name}"))
        if self.valid_from >= self.valid_to:
            raise DataValidationError(
                f"A pack covers [valid_from, valid_to); [{self.valid_from}, {self.valid_to}) is "
                "empty."
            )
        entries = _canonical(tuple(self.entries))
        computed = reference_pack_digest(entries)
        if computed != self.digest:
            raise DataValidationError(
                f"Pack {self.pack_id} {self.version} states digest {self.digest}, and its "
                f"entries' digest is {computed}: they are not the entries it states, and it is "
                "refused rather than read."
            )
        for entry in entries:
            if entry["effective_at"] >= self.valid_to:
                raise DataValidationError(
                    f"Pack {self.pack_id} claims coverage to {self.valid_to} and holds an entry "
                    f"effective at {entry['effective_at']}: a pack claims the range it holds."
                )
            if entry["announced_at"] > self.as_of:
                raise DataValidationError(
                    f"Pack {self.pack_id} was taken at {self.as_of} and holds an entry "
                    f"announced at {entry['announced_at']}, after it was taken."
                )
        object.__setattr__(self, "entries", entries)

    @property
    def citation(self) -> PackCitation:
        """What a run that read this pack cites beside its record."""

        return PackCitation(self.pack_id, self.version, self.digest)

    @property
    def universes(self) -> tuple[str, ...]:
        """The universes the pack holds membership of, by name."""

        return tuple(
            sorted(
                {entry["universe"] for entry in self.entries if entry["type"] == MEMBERSHIP_CHANGE}
            )
        )

    def membership(self, universe: str) -> tuple[Mapping[str, Any], ...]:
        """The membership change entries of ``universe``, in the order they take effect.

        Raises:
            DataValidationError: If the pack holds none -- a universe it does not
                hold is refused, never assumed empty.
        """

        held = tuple(
            entry
            for entry in self.entries
            if entry["type"] == MEMBERSHIP_CHANGE and entry["universe"] == universe
        )
        if not held:
            raise DataValidationError(
                f"Pack {self.pack_id} {self.version} holds no universe {universe!r} (it holds "
                f"{', '.join(self.universes) or 'none'}); a missing universe is refused, never "
                "assumed empty."
            )
        return held
