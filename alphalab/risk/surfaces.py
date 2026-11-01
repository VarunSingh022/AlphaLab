"""Volatility surfaces as they arrive during a run (ledger FUT-039, phase 5).

A surface is market or vendor data, published as of an instant and known to
the run from another: its delivery. Each delivery is kept as it arrived -- its
surface, its knowledge instant, its source and its version -- on the risk state,
so a run records exactly what it was given and when, and a restore keeps it.

Which one a margin check reads is :func:`select`'s rule: of the surfaces known
by the check's instant, the one as of the latest instant; of several as of the
same instant -- a revision -- the one known last. A surface arriving late, as of
an earlier instant than one already known, never displaces it, and one older
than a declared age is not read at all. Nothing is filled in: a point a surface
does not quote has no volatility from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from alphalab.options.volatility_surface import VolatilitySurface, VolPoint
from alphalab.risk.exceptions import RiskError

__all__ = [
    "ObservedSurface",
    "observed_from_primitives",
    "observed_to_primitives",
    "select",
]


@dataclass(frozen=True, slots=True)
class ObservedSurface:
    """One surface as the run received it (FUT-039).

    Attributes:
        surface: The surface, as of its own ``timestamp``.
        known_at: When the run received it: never read before.
        source: Who published it, for the record.
        version: The publisher's version of it, for the record.
    """

    surface: VolatilitySurface
    known_at: float
    source: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.surface, VolatilitySurface):
            raise RiskError("An observed surface is an options VolatilitySurface.")
        if not self.source or not self.version:
            raise RiskError("An observed surface names its source and its version.")
        if self.surface.timestamp > self.known_at:
            raise RiskError(
                f"A surface as of {self.surface.timestamp!r} is known at {self.known_at!r}, "
                "before it was true."
            )


def select(
    observed: Iterable[ObservedSurface], at: float, max_age: float | None = None
) -> ObservedSurface | None:
    """The surface a check at ``at`` reads, or ``None``.

    Known by ``at``; as of the latest instant; of a revision, the one known
    last; and, with ``max_age``, as of no more than that many seconds before.
    """

    best: ObservedSurface | None = None
    for candidate in observed:
        if candidate.known_at > at:
            continue
        if best is None or (candidate.surface.timestamp, candidate.known_at) > (
            best.surface.timestamp,
            best.known_at,
        ):
            best = candidate
    if best is None:
        return None
    if max_age is not None and at - best.surface.timestamp > max_age:
        return None
    return best


def observed_to_primitives(
    surfaces: Mapping[str, tuple[ObservedSurface, ...]],
) -> dict[str, list[dict[str, Any]]]:
    """The observed surfaces as plain primitives: points as repr'd floats."""

    return {
        underlying: [
            {
                "known_at": item.known_at,
                "source": item.source,
                "version": item.version,
                "timestamp": item.surface.timestamp,
                "points": [
                    [repr(p.strike), repr(p.expiry), repr(p.implied_vol)]
                    for p in sorted(item.surface.points, key=lambda p: (p.expiry, p.strike))
                ],
            }
            for item in items
        ]
        for underlying, items in sorted(surfaces.items())
    }


def observed_from_primitives(value: Any) -> dict[str, tuple[ObservedSurface, ...]]:
    """Read surfaces :func:`observed_to_primitives` wrote."""

    return {
        underlying: tuple(
            ObservedSurface(
                VolatilitySurface(
                    underlying,
                    float(item["timestamp"]),
                    tuple(VolPoint(float(k), float(e), float(v)) for k, e, v in item["points"]),
                ),
                float(item["known_at"]),
                item["source"],
                item["version"],
            )
            for item in items
        )
        for underlying, items in value.items()
    }
