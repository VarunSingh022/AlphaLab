"""A record that a checkpoint was taken. Not the checkpoint itself.

**This module stores no state and restores none.** Every field below is an
opaque string the caller supplies and this package never reads, writes, decodes
or validates. Creating a :class:`Checkpoint` records that someone claims to have
one; it does not produce one, and
:meth:`~alphalab.production.runtime.RuntimeOperations.restore` emits a
``CheckpointRestored`` event and changes nothing else.

Durable run state lives in :class:`~alphalab.persistence.run_store.RunStateStore`
as of v2.13 -- :class:`~alphalab.persistence.run_store.FileRunStateStore` for a
real local backend -- fed by ``capture`` and ``serialize`` from the snapshot
module that owns the state, and read back through ``from_primitives`` and
``restore``. That path is proven across a process boundary; this one is
bookkeeping. See ADR-0029.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A note that a checkpoint exists somewhere, and what the caller called it.

    The six state fields are **opaque strings**, supplied by the caller and never
    interpreted here. Nothing in this package produces them, parses them, or can
    turn one back into a running system. A ``Checkpoint`` whose fields are empty
    strings is as valid to this package as one whose fields hold real payloads.

    For state that can actually be restored, capture the run through its snapshot
    module and store the payload with
    :class:`~alphalab.persistence.run_store.RunStateStore` (ADR-0029).
    """

    checkpoint_id: str
    timestamp: float
    runtime_state: str
    portfolio_state: str
    orders_state: str
    positions_state: str
    research_state: str
    replay_state: str
    metadata: Mapping[str, str] = field(default_factory=dict)
