"""Historical universe membership, dated when it held and when it was known (v4.3, ADR-0059).

A backtest over an index's constituents is only as honest as the list it reads.
Today's members applied to the past leave out every company removed or delisted
-- survivorship -- and a list of effective dates alone lets a strategy act on an
addition before it was announced -- look-ahead. A
:class:`MembershipChange` is therefore dated twice: ``effective_at``, the first
instant it holds, and ``announced_at``, when it became known, at or before it.

A :class:`HistoricalUniverse` is one universe's changes. It refuses a history
that could not have happened -- an asset added while already a member, removed or
delisted while not one, or changed twice at one instant -- and
:meth:`HistoricalUniverse.members` answers the set that holds at an instant *as
known at another*: it reads only the changes announced by then. The run's
universes live in its :class:`~alphalab.instrument.registry.InstrumentRegistry`,
the authority on what a universe is (ADR-0026), and a strategy reads them through
:meth:`~alphalab.runtime.context_views.UniverseView.members`, with what is known
fixed at the strategy's own instant.

A delisting ends membership; it does not settle a position held through it
(FUT-050). AlphaLab ships no membership data (BDY-013): a history comes from the
researcher, or from a reference pack (:mod:`alphalab.data.packs`).
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any

from alphalab.data.packs import ReferencePack
from alphalab.instrument.exceptions import InstrumentInputError

__all__ = [
    "HistoricalUniverse",
    "MembershipChange",
    "MembershipKind",
    "universe_from_pack",
    "universe_from_primitives",
    "universe_to_primitives",
]


@unique
class MembershipKind(Enum):
    """What a membership change does."""

    #: The asset joins the universe.
    ADDITION = "ADDITION"
    #: The asset leaves it, and goes on trading.
    REMOVAL = "REMOVAL"
    #: The asset leaves it because it stops trading.
    DELISTING = "DELISTING"


#: How a change that is not an addition is written in a refusal.
_LEFT = {MembershipKind.REMOVAL: "removed", MembershipKind.DELISTING: "delisted"}


def _effective_at(change: MembershipChange) -> float:
    return change.effective_at


def _instant(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InstrumentInputError(
            f"MembershipChange.{name} must be a finite instant, got {value!r}."
        )
    return float(value)


@dataclass(frozen=True, slots=True)
class MembershipChange:
    """One change to one universe's membership, dated when it held and when it was known.

    Attributes:
        universe: The universe's name.
        asset_id: The instrument that joins or leaves it.
        kind: :class:`MembershipKind`.
        effective_at: The first instant the change holds.
        announced_at: When it became known: at or before ``effective_at``.

    Raises:
        InstrumentInputError: If a name is empty, ``kind`` is not a
            :class:`MembershipKind`, an instant is not finite, or the change is
            announced after it took effect. Such data was written later, and
            reading it as known earlier is the look-ahead this type exists to
            prevent; a change known only when it was announced is effective then.
    """

    universe: str
    asset_id: str
    kind: MembershipKind
    effective_at: float
    announced_at: float

    def __post_init__(self) -> None:
        for name in ("universe", "asset_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise InstrumentInputError(f"MembershipChange.{name} must be named, got {value!r}.")
        if not isinstance(self.kind, MembershipKind):
            raise InstrumentInputError(
                f"MembershipChange.kind must be a MembershipKind, got {self.kind!r}."
            )
        object.__setattr__(self, "effective_at", _instant(self.effective_at, "effective_at"))
        object.__setattr__(self, "announced_at", _instant(self.announced_at, "announced_at"))
        if self.announced_at > self.effective_at:
            raise InstrumentInputError(
                f"The {self.kind.name.lower()} of {self.asset_id} to {self.universe} is announced "
                f"at {self.announced_at}, after it took effect at {self.effective_at}. Read as "
                "known when it took effect, it would be look-ahead; a change known only when "
                "it was announced is effective then."
            )


@dataclass(frozen=True, slots=True)
class HistoricalUniverse:
    """One universe's membership changes, in the order they took effect (ADR-0059).

    Attributes:
        name: The universe's name; every change names it.
        changes: Its changes. They are kept in the order they took effect,
            whatever order they were given in.
        coverage: ``(valid_from, valid_to)``: the instants the history is
            stated for, the end exclusive, or ``None`` for every instant. A
            history taken from a :class:`~alphalab.data.packs.ReferencePack`
            covers what the pack claims, and no more.
        known_until: The latest instant whose knowledge the history holds --
            what was announced later is not in it -- or ``None``, when the
            history is complete.

    Raises:
        InstrumentInputError: If a change names another universe, two change one
            asset at one instant, or an asset is added while already a member,
            or removed or delisted while not one; if the coverage is not a range
            or a change takes effect at or after its end; or if a change was
            announced after ``known_until``.
    """

    name: str
    changes: tuple[MembershipChange, ...]
    coverage: tuple[float, float] | None = None
    known_until: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InstrumentInputError(f"A historical universe must be named, got {self.name!r}.")
        changes = tuple(self.changes)
        for change in changes:
            if not isinstance(change, MembershipChange):
                raise InstrumentInputError(
                    f"A universe's changes are MembershipChange values, got {change!r}."
                )
            if change.universe != self.name:
                raise InstrumentInputError(
                    f"The universe {self.name!r} was given a change to {change.universe!r}."
                )
        ordered = tuple(sorted(changes, key=lambda change: (change.effective_at, change.asset_id)))
        member: dict[str, bool] = {}
        last: dict[str, float] = {}
        for change in ordered:
            asset = change.asset_id
            if last.get(asset) == change.effective_at:
                raise InstrumentInputError(
                    f"{asset} changes membership of {self.name} twice at {change.effective_at}: "
                    "at one instant an asset is a member or it is not."
                )
            last[asset] = change.effective_at
            held = member.get(asset, False)
            if change.kind is MembershipKind.ADDITION and held:
                raise InstrumentInputError(
                    f"{asset} is added to {self.name} at {change.effective_at} while already a "
                    "member."
                )
            if change.kind is not MembershipKind.ADDITION and not held:
                raise InstrumentInputError(
                    f"{asset} is {_LEFT[change.kind]} from {self.name} at {change.effective_at} "
                    "while not a member."
                )
            member[asset] = change.kind is MembershipKind.ADDITION
        object.__setattr__(self, "changes", ordered)
        if self.coverage is not None:
            if not isinstance(self.coverage, tuple) or len(self.coverage) != 2:
                raise InstrumentInputError(
                    f"A universe's coverage is (valid_from, valid_to), got {self.coverage!r}."
                )
            start = _instant(self.coverage[0], "coverage")
            end = _instant(self.coverage[1], "coverage")
            if start >= end:
                raise InstrumentInputError(
                    f"The universe {self.name!r} covers [{start}, {end}), which is empty."
                )
            if ordered and ordered[-1].effective_at >= end:
                raise InstrumentInputError(
                    f"The universe {self.name!r} covers instants before {end} and holds a change "
                    f"effective at {ordered[-1].effective_at}."
                )
            object.__setattr__(self, "coverage", (start, end))
        if self.known_until is not None:
            known = _instant(self.known_until, "known_until")
            late = [change for change in ordered if change.announced_at > known]
            if late:
                raise InstrumentInputError(
                    f"The universe {self.name!r} holds what was known until {known}, and a change "
                    f"announced at {late[0].announced_at}."
                )
            object.__setattr__(self, "known_until", known)

    def members(self, at: float, *, as_of: float) -> frozenset[str]:
        """The assets in the universe at ``at``, as known at ``as_of``.

        Only the changes announced at or before ``as_of`` are read: each asset is
        a member when the last of its known changes to take effect by ``at`` is
        an addition. Reading the past with later knowledge is allowed, and says
        so (``as_of`` after ``at``); so is reading the future with what has been
        announced, which an announced change makes possible.

        Raises:
            InstrumentInputError: If either instant is not finite, ``at`` is
                outside the universe's coverage, or ``as_of`` is after what it
                holds knowledge of. Each would be an answer assumed, not read.
        """

        at = _instant(at, "at")
        as_of = _instant(as_of, "as_of")
        if self.coverage is not None and not self.coverage[0] <= at < self.coverage[1]:
            raise InstrumentInputError(
                f"The universe {self.name!r} is stated for [{self.coverage[0]}, "
                f"{self.coverage[1]}); its members at {at} would be assumed, not read."
            )
        if self.known_until is not None and as_of > self.known_until:
            raise InstrumentInputError(
                f"The universe {self.name!r} holds what was known until {self.known_until}; its "
                f"membership as known at {as_of} would assume nothing was announced since."
            )
        latest: dict[str, MembershipKind] = {}
        taken = bisect_right(self.changes, at, key=_effective_at)
        for change in self.changes[:taken]:
            if change.announced_at <= as_of:
                latest[change.asset_id] = change.kind
        return frozenset(asset for asset, kind in latest.items() if kind is MembershipKind.ADDITION)

    @property
    def assets(self) -> frozenset[str]:
        """Every asset the history ever names."""

        return frozenset(change.asset_id for change in self.changes)


def universe_from_pack(pack: ReferencePack, name: str) -> HistoricalUniverse:
    """The universe ``name`` as ``pack`` states it, holding the pack's range and knowledge.

    The universe covers ``[pack.valid_from, pack.valid_to)`` and knows what was
    announced by ``pack.as_of``, so a question outside either is refused inside a
    run as well. Cite ``pack.citation`` beside the run's record.

    Raises:
        DataValidationError: If the pack holds no universe of that name.
        InstrumentInputError: If the history it states could not have happened.
    """

    changes = tuple(
        MembershipChange(
            universe=entry["universe"],
            asset_id=entry["asset_id"],
            kind=MembershipKind[entry["kind"]],
            effective_at=entry["effective_at"],
            announced_at=entry["announced_at"],
        )
        for entry in pack.membership(name)
    )
    return HistoricalUniverse(
        name, changes, coverage=(pack.valid_from, pack.valid_to), known_until=pack.as_of
    )


def universe_to_primitives(universe: HistoricalUniverse) -> dict[str, Any]:
    """``universe`` as a snapshot writes it: its changes, and its bounds when it has them."""

    record: dict[str, Any] = {
        "changes": [
            {
                "announced_at": change.announced_at,
                "asset_id": change.asset_id,
                "effective_at": change.effective_at,
                "kind": change.kind.name,
            }
            for change in universe.changes
        ]
    }
    if universe.coverage is not None:
        record["coverage"] = list(universe.coverage)
    if universe.known_until is not None:
        record["known_until"] = universe.known_until
    return record


_CHANGE_FIELDS = frozenset({"announced_at", "asset_id", "effective_at", "kind"})


def universe_from_primitives(name: str, record: Mapping[str, Any]) -> HistoricalUniverse:
    """The universe ``name`` from what :func:`universe_to_primitives` writes.

    Raises:
        InstrumentInputError: If the record is not one, or the history it
            describes could not have happened.
    """

    if not isinstance(record, Mapping) or not {"changes"} <= set(record) <= {
        "changes",
        "coverage",
        "known_until",
    }:
        raise InstrumentInputError(f"Universe {name!r} is not a universe record.")
    changes = record["changes"]
    if not isinstance(changes, list | tuple):
        raise InstrumentInputError(f"Universe {name!r}'s changes are not a list.")
    decoded = []
    for index, change in enumerate(changes):
        if not isinstance(change, Mapping) or set(change) != _CHANGE_FIELDS:
            raise InstrumentInputError(
                f"Universe {name!r}'s change {index} is not a change record."
            )
        kind = change["kind"]
        if not isinstance(kind, str) or kind not in MembershipKind.__members__:
            raise InstrumentInputError(
                f"Universe {name!r}'s change {index} has no membership kind {kind!r}."
            )
        decoded.append(
            MembershipChange(
                universe=name,
                asset_id=change["asset_id"],
                kind=MembershipKind[kind],
                effective_at=change["effective_at"],
                announced_at=change["announced_at"],
            )
        )
    coverage = record.get("coverage")
    if coverage is not None:
        if not isinstance(coverage, list | tuple) or len(coverage) != 2:
            raise InstrumentInputError(f"Universe {name!r}'s coverage is not a range.")
        coverage = (coverage[0], coverage[1])
    return HistoricalUniverse(
        name, tuple(decoded), coverage=coverage, known_until=record.get("known_until")
    )
