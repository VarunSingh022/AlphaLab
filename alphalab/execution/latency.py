"""Immutable pure-functional latency models.

A latency model answers one question -- how long after the event does this
order's fill get stamped -- and it must answer it the same way in every process,
because a backtest that reproduces only within one interpreter does not
reproduce at all.

Why ``DeterministicLatency`` stopped using ``hash()``
-----------------------------------------------------

It was built on ``hash(order_id)``, which for a :class:`str` is salted per
process by PEP 456 unless ``PYTHONHASHSEED`` is pinned. The class was therefore
deterministic *within* a run and different on the next one: the same order id
drew a different latency each time the interpreter started, so a run's fill
timestamps -- and every ordering, analytic and parity baseline downstream of
them -- were not reproducible across processes. The name said otherwise.

It now derives the draw from :func:`hashlib.sha256`, the same stable digest
``alphalab.research.study`` uses to derive a study identity and
``alphalab.model_registry.artifact_store`` uses to address an artifact. A digest
is a pure function of the bytes, so the draw is fixed for an order id on every
machine and every interpreter, for ever. See ``nowandfuture.md`` section 14
invariant 13 -- no wall clock on the execution path -- of which this is the
other half: no process-local entropy either.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

__all__ = [
    "ConstantLatency",
    "DeterministicLatency",
    "LatencyModel",
]

#: Denominator of the pseudo-random draw. The digest is reduced modulo this and
#: divided by it, giving a fraction in ``[0, 1)`` with a resolution of one part
#: in a million -- fine enough that two order ids are very unlikely to draw the
#: same latency, coarse enough to read in a test failure.
_DRAW_RESOLUTION = 1_000_000


def _stable_fraction(key: str) -> float:
    """A fixed fraction in ``[0, 1)`` derived from ``key``.

    Pure, platform-independent and stable across processes: the same string
    always gives the same fraction.
    """

    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return (int.from_bytes(digest, "big") % _DRAW_RESOLUTION) / _DRAW_RESOLUTION


class LatencyModel(Protocol):
    def calculate(self, order_id: str, current_timestamp: float) -> float: ...


class ConstantLatency:
    """The same latency for every order."""

    __slots__ = ("_latency",)

    def __init__(self, latency: float) -> None:
        self._latency = latency

    def calculate(self, order_id: str, current_timestamp: float) -> float:
        return self._latency


class DeterministicLatency:
    """A fixed pseudo-random latency in ``[min_lat, max_lat]``, drawn per order.

    The draw is a pure function of ``order_id``: it does not move with the
    clock, the process, the platform or the order the orders arrived in, which
    is what lets a run with variable latency still reproduce exactly.
    """

    __slots__ = ("_max_lat", "_min_lat")

    def __init__(self, min_lat: float, max_lat: float) -> None:
        self._min_lat = min_lat
        self._max_lat = max_lat

    def calculate(self, order_id: str, current_timestamp: float) -> float:
        return self._min_lat + (self._max_lat - self._min_lat) * _stable_fraction(order_id)
