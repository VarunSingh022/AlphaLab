"""Reference-data packs: data with its evidence, in one canonical format (v4.3, ADR-0059).

The engine ships no market or reference data (BDY-013). What it ships is the
mechanism, and a format for the data to arrive in. A :class:`ReferencePack` is a
body of reference data -- a universe's dated membership; a venue's sessions,
holidays and settlement cycle -- with the evidence of where it came from:

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
entry *means* is its domain's:

* a membership change is read by
  :func:`alphalab.instrument.membership.universe_from_pack`, whose universe
  carries the pack's range and knowledge;
* a venue's sessions and holidays by :func:`calendar_from_pack`, whose calendar
  holds the pack's range as its coverage and refuses a day outside it;
* a settlement cycle by :func:`alphalab.instrument.reference.settlement_from_pack`.

So a question outside a pack's range is refused inside a run as well. This
package reaches nothing on the execution path.

A pack is written and read as JSON (:func:`pack_to_primitives`,
:func:`pack_from_primitives`, :func:`load_pack`), and nothing in it executes:
its entries are records, and its digest is checked before any is read.

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
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.data.exceptions import DataValidationError
from alphalab.data.time import resolve_zone

__all__ = [
    "HOLIDAY",
    "MEMBERSHIP_CHANGE",
    "MEMBERSHIP_KINDS",
    "REFERENCE_PACK_SCHEME",
    "SETTLEMENT_CYCLE",
    "VENUE_SESSION",
    "PackCitation",
    "PackSource",
    "ReferencePack",
    "calendar_from_pack",
    "load_pack",
    "pack_from_primitives",
    "pack_to_primitives",
    "reference_pack_digest",
]

#: The scheme a pack's content digest is taken under.
REFERENCE_PACK_SCHEME: Final = "alphalab.reference_pack.v1"

#: The type tag of a membership change entry.
MEMBERSHIP_CHANGE: Final = "MembershipChange"

#: The kinds a membership change entry may name.
MEMBERSHIP_KINDS: Final = frozenset({"ADDITION", "REMOVAL", "DELISTING"})

#: The type tag of one window of a venue's weekly trading sessions (M10).
VENUE_SESSION: Final = "VenueSession"
#: The type tag of a date a venue's calendar is closed on (M10).
HOLIDAY: Final = "Holiday"
#: The type tag of a venue's settlement cycle (M10).
SETTLEMENT_CYCLE: Final = "SettlementCycle"

_INSTANTS: Final = frozenset({"effective_at", "announced_at"})

#: Every entry type a pack holds: its fields, and which of them are instants.
_TYPES: Final[Mapping[str, tuple[frozenset[str], frozenset[str]]]] = MappingProxyType(
    {
        MEMBERSHIP_CHANGE: (
            frozenset({"type", "universe", "asset_id", "kind", "effective_at", "announced_at"}),
            _INSTANTS,
        ),
        VENUE_SESSION: (
            frozenset(
                {
                    "type",
                    "calendar_id",
                    "timezone",
                    "weekday",
                    "opens",
                    "closes",
                    "effective_at",
                    "announced_at",
                }
            ),
            _INSTANTS,
        ),
        HOLIDAY: (
            frozenset({"type", "calendar_id", "date", "name", "effective_at", "announced_at"}),
            _INSTANTS,
        ),
        SETTLEMENT_CYCLE: (
            frozenset({"type", "venue", "basis", "days", "effective_at", "announced_at"}),
            _INSTANTS,
        ),
    }
)

#: The fields each type's entries are ordered by, after their type.
_ORDER: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        MEMBERSHIP_CHANGE: ("universe", "effective_at", "asset_id"),
        VENUE_SESSION: ("calendar_id", "weekday", "opens", "effective_at"),
        HOLIDAY: ("calendar_id", "date", "effective_at"),
        SETTLEMENT_CYCLE: ("venue", "effective_at"),
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
    return (entry["type"], *(entry[name] for name in _ORDER[entry["type"]]))


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

    def of(self, kind: str) -> tuple[Mapping[str, Any], ...]:
        """The entries of type ``kind``, in their canonical order."""

        return tuple(entry for entry in self.entries if entry["type"] == kind)

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


# --------------------------------------------------------------------------- #
# Written and read
# --------------------------------------------------------------------------- #


def pack_to_primitives(pack: ReferencePack) -> dict[str, Any]:
    """``pack`` as JSON primitives: what :func:`pack_from_primitives` and :func:`load_pack` read."""

    return {
        "pack_id": pack.pack_id,
        "version": pack.version,
        "source": {"publisher": pack.source.publisher, "document": pack.source.document},
        "as_of": pack.as_of,
        "valid_from": pack.valid_from,
        "valid_to": pack.valid_to,
        "licence": pack.licence,
        "entries": [dict(entry) for entry in pack.entries],
        "digest": pack.digest,
    }


_FIELDS: Final = frozenset(
    {
        "pack_id",
        "version",
        "source",
        "as_of",
        "valid_from",
        "valid_to",
        "licence",
        "entries",
        "digest",
    }
)


def pack_from_primitives(payload: Any) -> ReferencePack:
    """The pack :func:`pack_to_primitives` wrote, its digest checked before an entry is read.

    Raises:
        DataValidationError: If a field is missing or extra, or the pack is not
            one :class:`ReferencePack` accepts.
    """

    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        found = sorted(payload) if isinstance(payload, Mapping) else type(payload).__name__
        raise DataValidationError(f"A pack has exactly the fields {sorted(_FIELDS)}; got {found}.")
    source = payload["source"]
    if not isinstance(source, Mapping) or set(source) != {"publisher", "document"}:
        raise DataValidationError(
            f"A pack's source names its publisher and document; got {source!r}."
        )
    entries = payload["entries"]
    if not isinstance(entries, list):
        raise DataValidationError(f"A pack's entries are a list, got {type(entries).__name__}.")
    return ReferencePack(
        pack_id=payload["pack_id"],
        version=payload["version"],
        source=PackSource(source["publisher"], source["document"]),
        as_of=payload["as_of"],
        valid_from=payload["valid_from"],
        valid_to=payload["valid_to"],
        licence=payload["licence"],
        entries=tuple(entries),
        digest=payload["digest"],
    )


def load_pack(path: str | Path) -> ReferencePack:
    """The pack the JSON file at ``path`` holds. Nothing in it executes.

    Raises:
        DataValidationError: If the file is not JSON, or not a pack
            :func:`pack_from_primitives` reads.
    """

    try:
        text = Path(path).read_text(encoding="utf-8")
        payload = json.loads(text, parse_constant=_no_constant)
    except (OSError, ValueError) as exc:
        raise DataValidationError(f"{path} is not a pack: {exc}") from exc
    return pack_from_primitives(payload)


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a number a pack holds")


# --------------------------------------------------------------------------- #
# A venue's calendar
# --------------------------------------------------------------------------- #


def _local_days(pack: ReferencePack, zone: str) -> tuple[date, date]:
    """The local dates the pack's range holds whole, ``[first, last)``, in ``zone``."""

    starts = datetime.fromtimestamp(pack.valid_from, resolve_zone(zone))
    first = starts.date() if starts.time() == time(0) else starts.date() + timedelta(days=1)
    return first, datetime.fromtimestamp(pack.valid_to, resolve_zone(zone)).date()


def _clock(text: str, where: str) -> time:
    try:
        return time.fromisoformat(text)
    except ValueError as exc:
        raise DataValidationError(f"{where} is not an ISO time: {text!r}.") from exc


def calendar_from_pack(pack: ReferencePack, calendar_id: str) -> MarketCalendar:
    """The calendar ``calendar_id`` as ``pack`` states it: its sessions, holidays and coverage.

    Its coverage is the local dates the pack's range holds whole, in the
    calendar's zone, so a day outside it is refused rather than answered. Cite
    ``pack.citation`` beside the run's record.

    Raises:
        DataValidationError: If the pack states no session of ``calendar_id``;
            its sessions name two zones, or a schedule that changes within the
            pack's range; a weekday, time or date does not read; or a holiday
            falls outside the pack's range.
    """

    sessions = [entry for entry in pack.of(VENUE_SESSION) if entry["calendar_id"] == calendar_id]
    if not sessions:
        held = sorted({entry["calendar_id"] for entry in pack.of(VENUE_SESSION)})
        raise DataValidationError(
            f"Pack {pack.pack_id} {pack.version} states no calendar {calendar_id!r} (it states "
            f"{', '.join(held) or 'none'}); a missing calendar is refused, never assumed."
        )
    zones = {entry["timezone"] for entry in sessions}
    if len(zones) != 1:
        raise DataValidationError(
            f"Pack {pack.pack_id} states {calendar_id}'s sessions in {sorted(zones)}: one "
            "calendar keeps one zone."
        )
    if any(entry["effective_at"] != pack.valid_from for entry in sessions):
        raise DataValidationError(
            f"Pack {pack.pack_id} states a schedule of {calendar_id} that changes within its "
            "range; a calendar holds one weekly schedule, and a change is refused rather than "
            "flattened."
        )
    (zone,) = zones
    weekly: dict[int, list[SessionWindow]] = {}
    for entry in sessions:
        weekday = entry["weekday"]
        if weekday not in {"0", "1", "2", "3", "4", "5", "6"}:
            raise DataValidationError(
                f"Pack {pack.pack_id} states {calendar_id} on weekday {weekday!r}; weekdays are "
                "0 (Monday) to 6 (Sunday)."
            )
        window = SessionWindow(
            _clock(entry["opens"], f"{calendar_id}'s opens"),
            _clock(entry["closes"], f"{calendar_id}'s closes"),
        )
        weekly.setdefault(int(weekday), []).append(window)
    first, last = _local_days(pack, zone)
    holidays: set[date] = set()
    for entry in pack.of(HOLIDAY):
        if entry["calendar_id"] != calendar_id:
            continue
        try:
            day = date.fromisoformat(entry["date"])
        except ValueError as exc:
            raise DataValidationError(
                f"Pack {pack.pack_id}'s holiday {entry['name']!r} has date {entry['date']!r}, "
                "not an ISO date."
            ) from exc
        if not first <= day < last:
            raise DataValidationError(
                f"Pack {pack.pack_id} states {calendar_id}'s holiday {day.isoformat()} outside "
                f"its range [{first.isoformat()}, {last.isoformat()})."
            )
        holidays.add(day)
    return MarketCalendar(
        calendar_id,
        zone,
        {
            weekday: tuple(sorted(windows, key=lambda w: w.opens))
            for weekday, windows in weekly.items()
        },
        frozenset(holidays),
        coverage=(first, last),
    )
