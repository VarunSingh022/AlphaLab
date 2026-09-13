"""The live-run envelope: a run snapshot and the venue binding beside it.

A live run is durable when **both halves** of it are. The run half has been
since v2.13 -- :mod:`alphalab.runtime.run_snapshot` over
:data:`~alphalab.runtime.run_snapshot.RUN_SNAPSHOT_SCHEMA`. The venue half
arrives in v2.16 as :mod:`alphalab.broker.snapshot`. This module is the envelope
that keeps them together, because keeping them apart is the failure:

* restore the run without the mapping and the restart re-sends every working
  order to a venue that already holds it;
* restore the mapping without the run and nothing knows which OMS order a
  returning fill belongs to.

**Two schema constants, not one, and the nesting is deliberate.**
:data:`LIVE_SNAPSHOT_SCHEMA` versions the *pairing*;
``RUN_SNAPSHOT_SCHEMA`` and ``BROKER_SNAPSHOT_SCHEMA`` version their own halves
and are unmoved by this module. That is the shape ADR-0030 decision 6 chose when
it put ``PIPELINE_SNAPSHOT_SCHEMA`` inside the run envelope rather than merging
them: a backtest payload written by this build is byte-identical to one written
before it, because the run envelope did not change. Adding broker fields to
``RunState`` instead would have moved a schema every driver shares in order to
serve the one driver that needs it.

What a restore does not do
--------------------------

It does not reconnect, and it does not reconcile. The restored
:class:`~alphalab.broker.state.BrokerState` is *what AlphaLab believed* when it
stopped; what the venue actually holds is a question only
:func:`~alphalab.broker.reconciliation.reconcile` against a fresh fetch answers.
A restore that silently reconnected would make the first health check report a
connection this process never opened.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from alphalab.broker.snapshot import (
    BrokerSnapshot,
)
from alphalab.broker.snapshot import (
    capture as capture_broker,
)
from alphalab.broker.snapshot import (
    from_primitives as broker_from_primitives,
)
from alphalab.broker.snapshot import (
    restore as restore_broker,
)
from alphalab.core.enums import OrderType
from alphalab.persistence.decode import as_mapping, require, require_schema_version
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.broker_routing import RoutingConfig
from alphalab.runtime.live import LiveRunState
from alphalab.runtime.run_snapshot import (
    RunObjects,
    RunSnapshot,
)
from alphalab.runtime.run_snapshot import (
    capture as capture_run,
)
from alphalab.runtime.run_snapshot import (
    from_primitives as run_from_primitives,
)
from alphalab.runtime.run_snapshot import (
    restore as restore_run,
)

__all__ = [
    "LIVE_SNAPSHOT_SCHEMA",
    "LiveRunSnapshot",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version of the *pairing*. The two halves carry their own; see the
#: module docstring for why they are nested rather than merged.
LIVE_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM: Final = "live run"


@dataclass(frozen=True, slots=True)
class LiveRunSnapshot:
    """Complete, JSON-serializable projection of a :class:`LiveRunState`.

    ``routed`` and ``settled`` -- the aggregate's own observability logs -- are
    **not** carried. Every fact in them is derivable from the two halves that
    are: the mapping says which orders reached the venue, and
    ``BrokerState.executions`` plus the reconciliation log say what every
    returning fill turned out to be. Carrying them again would give one fact two
    homes that could disagree after a restore, which is the defect
    ``source_id`` was consolidated to remove in v2.14.
    """

    run: RunSnapshot
    broker: BrokerSnapshot
    venue: str
    currency: str
    order_type: OrderType
    schema_version: int = LIVE_SNAPSHOT_SCHEMA


def capture(state: LiveRunState) -> LiveRunSnapshot:
    """Project ``state`` into its complete serializable snapshot.

    The mapping and the reconciliation log are passed to the broker capture
    explicitly, because a live run without them is the restart that duplicates
    orders. See :func:`alphalab.broker.snapshot.capture`.
    """

    return LiveRunSnapshot(
        run=capture_run(state.run),
        broker=capture_broker(state.broker, state.mapping, state.reconciliation),
        venue=state.routing.venue,
        currency=state.routing.currency,
        order_type=state.routing.order_type,
        schema_version=LIVE_SNAPSHOT_SCHEMA,
    )


def restore(snapshot: LiveRunSnapshot, objects: RunObjects) -> LiveRunState:
    """Rebuild the live run a snapshot was captured from.

    ``objects`` carries the live objects no payload can hold -- strategy
    instances, the sizing model, the simulator, the instrument registry, the
    fill policy. **The broker adapter is not among them**: it is supplied to
    each :class:`~alphalab.runtime.live.LiveSession` call, not held in state, so
    a restored run takes whichever adapter the new process constructs.

    Returns:
        The reconstructed live run. Nothing is connected and nothing is
        reconciled; continuing is
        :meth:`~alphalab.runtime.live.LiveSession.resume` plus
        :meth:`~alphalab.runtime.live.LiveSession.connect`.
    """

    broker_state, mapping, log = restore_broker(snapshot.broker)
    return LiveRunState(
        run=restore_run(snapshot.run, objects),
        broker=broker_state,
        mapping=mapping,
        reconciliation=log,
        routing=RoutingConfig(
            venue=snapshot.venue,
            currency=snapshot.currency,
            order_type=snapshot.order_type,
        ),
    )


def _order_type(value: Any) -> OrderType:
    """Decode an ``OrderType``, which is a ``StrEnum`` and serializes by value.

    Both shapes are read -- ``"market"`` as ``alphalab.persistence.serialize``
    writes it, and ``"MARKET"`` as a hand-written payload would -- and anything
    else is refused rather than defaulted to ``MARKET``. Routing an order as the
    wrong type is a real instruction to a real venue.
    """

    raw = str(value)
    member = OrderType.__members__.get(raw)
    if member is not None:
        return member
    try:
        return OrderType(raw)
    except ValueError:
        raise StateDecodeError(
            f"live run snapshot order_type {value!r} is not an OrderType. "
            f"Known: {sorted(OrderType.__members__)}"
        ) from None


def from_primitives(payload: Mapping[str, Any]) -> LiveRunSnapshot:
    """Decode a JSON-decoded payload back into :class:`LiveRunSnapshot`.

    Each half is decoded by its own module, so each checks its own schema
    version and produces its own message. A payload whose halves were written by
    different builds is refused by whichever half disagrees first.

    Raises:
        StateDecodeError: If the envelope declares a version this build does not
            read, is missing a field, or names an order type that does not exist.
    """

    payload = as_mapping(payload, _SUBSYSTEM)
    require_schema_version(payload, LIVE_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    return LiveRunSnapshot(
        run=run_from_primitives(as_mapping(require(payload, "run"), "run")),
        broker=broker_from_primitives(as_mapping(require(payload, "broker"), "broker")),
        venue=str(require(payload, "venue")),
        currency=str(require(payload, "currency")),
        order_type=_order_type(require(payload, "order_type")),
        schema_version=LIVE_SNAPSHOT_SCHEMA,
    )
