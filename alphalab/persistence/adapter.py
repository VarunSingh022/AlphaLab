"""Adapter translating AlphaLab domain objects to Persistence objects.

The write direction has existed since v1.0: hand it a state, get back a
:class:`~alphalab.persistence.snapshot.Snapshot` whose payload is deterministic
JSON. :meth:`PersistenceAdapter.snapshot_payload` is the read direction, added in
v2.5 so a stored snapshot can be handed to a domain package's ``from_primitives``
and become typed values again.

It stops at primitives deliberately. This module knows nothing about portfolios
or lifecycles, and must not: the decoders live with the domain types they
reconstruct (:mod:`alphalab.portfolio.snapshot`,
:mod:`alphalab.lifecycle.snapshot`, :mod:`alphalab.oms.snapshot`), and the
dependency runs domain -> persistence. Inverting it would make this package
import half of AlphaLab to be able to read anything.
"""

from collections.abc import Mapping
from typing import Any

from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.persistence.snapshot import Snapshot, StoredEvent


class PersistenceAdapter:
    """Stateless translator mapping generic framework objects to storable records."""

    @staticmethod
    def to_stored_event(event_id: str, timestamp: float, domain_event: Any) -> StoredEvent:
        """Serializes an arbitrary domain event into an immutable StoredEvent."""
        event_type = type(domain_event).__name__
        payload = serialize(domain_event)
        return StoredEvent(
            event_id=event_id,
            timestamp=timestamp,
            event_type=event_type,
            payload=payload,
        )

    @staticmethod
    def to_snapshot(
        snapshot_id: str, subsystem: str, timestamp: float, state_object: Any
    ) -> Snapshot:
        """Serializes an arbitrary system state into an immutable Snapshot."""
        payload = serialize(state_object)
        return Snapshot(
            snapshot_id=snapshot_id,
            subsystem=subsystem,
            timestamp=timestamp,
            payload=payload,
        )

    @staticmethod
    def snapshot_payload(snapshot: Snapshot) -> Mapping[str, Any]:
        """Decode a stored snapshot's payload into the primitives a decoder reads.

        The inverse of :meth:`to_snapshot` as far as this layer can go: JSON in,
        plain mappings out. Turning those into typed values is the owning domain
        package's job, and this returns a ``Mapping`` rather than ``Any`` so the
        next step has something it can actually check.

        Raises:
            StateDecodeError: If the payload is not valid JSON describing an
                object. A snapshot whose payload is an array or a bare number
                cannot be any subsystem's state, and saying so here is better
                than letting a decoder report a confusing missing field.
        """

        decoded = deserialize(snapshot.payload)
        if not isinstance(decoded, Mapping):
            raise StateDecodeError(
                f"Snapshot {snapshot.snapshot_id!r} for subsystem "
                f"{snapshot.subsystem!r} does not contain an object: "
                f"{type(decoded).__name__}"
            )
        return decoded
