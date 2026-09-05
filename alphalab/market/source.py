"""The market-data adapter boundary: where records come from.

A :class:`MarketDataSource` yields canonical
:class:`~alphalab.market.record.MarketRecord` s and nothing else. That is the
whole contract, and it is deliberately small: the execution path should not be
able to tell a historical file from a socket, because the only thing it needs
from either is the next record.

The four environments differ *only* in which source they are handed:

======================== ===================================================
Historical               :class:`SequenceSource` over a stored dataset.
Replay                   :mod:`alphalab.replay`'s cursor, which owns pacing
                         and lifecycle and yields the same records.
Paper                    A live source; orders execute against the simulator.
Live                     A live source; orders execute against a broker.
======================== ===================================================

Guarantees a source must provide
--------------------------------

* **Identity.** Every record carries a ``event_id`` unique within the source.
  :class:`SequenceSource` derives them deterministically from ``source_id``, so
  the same inputs always produce the same identities and two runs are
  comparable record by record.
* **Timestamps.** Unix seconds as ``float``, and ``record.timestamp`` equals
  ``record.payload.timestamp``.
* **Order.** Records are yielded in non-decreasing timestamp order.
  :func:`validate_ordering` states the rule and is what
  :class:`~alphalab.backtesting.dataset.MarketDataset` enforces on construction.
  A live source cannot promise this the way a file can -- a venue can reorder --
  so :class:`OrderingGuarantee` lets a source say which it offers instead of
  pretending.
* **Product identity.** ``record.asset_id`` is AlphaLab's asset id, not a
  provider symbol. Mapping happens in :mod:`alphalab.market.normalization`,
  before a record exists.

What this module deliberately does not do
-----------------------------------------

No provider API is modelled here -- no HTTP, no websockets, no vendor
authentication, no reconnect loop. A vendor adapter implements
:class:`MarketDataSource` in its own package and normalizes into canonical
records on the way out. Inventing a vendor's API shape here would be guessing at
an interface AlphaLab cannot test.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, runtime_checkable

from alphalab.market.exceptions import MarketValidationError
from alphalab.market.record import MarketInput, MarketRecord, records_from_inputs

__all__ = [
    "MarketDataSource",
    "OrderingGuarantee",
    "SequenceSource",
    "validate_ordering",
]


class OrderingGuarantee(Enum):
    """What a source promises about the order its records arrive in."""

    #: Timestamps never decrease. A stored dataset can promise this.
    CHRONOLOGICAL = auto()

    #: Records may arrive out of order; the consumer must tolerate it. A live
    #: venue feed is the honest case for this.
    UNORDERED = auto()


@runtime_checkable
class MarketDataSource(Protocol):
    """Anything that can produce canonical market records in sequence."""

    @property
    def source_id(self) -> str:
        """Identifier for this stream, used to derive record identities."""
        ...

    @property
    def ordering(self) -> OrderingGuarantee:
        """What this source promises about record order."""
        ...

    def records(self) -> Iterator[MarketRecord]:
        """Yield the source's records, in the order it guarantees."""
        ...


@dataclass(frozen=True, slots=True)
class SequenceSource:
    """A source over records already in memory.

    This is what a historical dataset, a fixture and a captured live session all
    look like once read: a finite, ordered sequence. Being iterable more than
    once matters -- comparing two runs means replaying the same source twice.
    """

    source_id: str
    _records: tuple[MarketRecord, ...]
    ordering: OrderingGuarantee = OrderingGuarantee.CHRONOLOGICAL

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> Iterator[MarketRecord]:
        """Yield every record, in stored order."""

        return iter(self._records)

    @classmethod
    def of(cls, source_id: str, inputs: Sequence[MarketInput]) -> SequenceSource:
        """Build a source from canonical market inputs, assigning record ids.

        Identities are deterministic in ``source_id`` and position, matching
        :meth:`~alphalab.backtesting.dataset.MarketDataset.of`, so a dataset and
        a source built from the same inputs agree on every record id.
        """

        return cls(source_id, records_from_inputs(source_id, inputs))

    @classmethod
    def from_records(
        cls,
        source_id: str,
        records: Iterable[MarketRecord],
        ordering: OrderingGuarantee = OrderingGuarantee.CHRONOLOGICAL,
    ) -> SequenceSource:
        """Build a source from records that already carry their identities."""

        return cls(source_id, tuple(records), ordering)


def validate_ordering(records: Sequence[MarketRecord]) -> None:
    """Ensure records are chronological and unambiguously identified.

    The same two rules a dataset enforces, stated once so a source, a fixture or
    a captured live session can be held to them too.
    """

    seen: set[str] = set()
    previous = float("-inf")
    for record in records:
        if record.timestamp < previous:
            raise MarketValidationError(
                f"Records are not chronologically ordered: {record.event_id} at "
                f"{record.timestamp} follows an event at {previous}."
            )
        if record.event_id in seen:
            raise MarketValidationError(f"Duplicate record id: {record.event_id}")
        seen.add(record.event_id)
        previous = record.timestamp
