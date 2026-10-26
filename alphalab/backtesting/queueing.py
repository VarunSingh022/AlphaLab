"""A queue model on the historical drivers: which resting limit orders reached the front.

Without a queue model a backtest fills a resting limit order when the market
reaches its limit, as if it had been first in line there -- optimistic for every
passive order, and most of all for a market maker's (ledger FUT-011). A
:class:`~alphalab.execution.queue.QueueModel` given to
:meth:`BacktestEngine.run <alphalab.backtesting.engine.BacktestEngine.run>` or
:meth:`ReplayBacktest.run <alphalab.backtesting.replay.ReplayBacktest.run>` keeps
each resting limit order's place at its price level, and the order fills only as
far as the model says has reached the front of it. Everything else about the run
is unchanged.

The path
--------
::

    strategy intent -> allocation -> risk -> OMS: a limit order, accepted
      -> its first turn, as before: it takes the market if that is within its
         limit, or rests
      -> resting: it joins its level's queue, from the event it was placed at
      -> at each later event of its asset, before the strategy decides there:
         the queue model advances its position from what the event showed,
         then the run's fill policy decides its fill within what the model
         allows; the simulator prices it at the limit, as a maker
      -> the fill settled as every fill is: OMS, portfolio, reservation,
         attribution, feedback

The queue book is driver state, threaded through
:meth:`RunEngine.advance <alphalab.runtime.run.RunEngine.advance>` as an
execution plan's book is, so the run's record and ``result_id`` are what they
would be without it, and :class:`~alphalab.backtesting.state.QueuedResult` names
the model in its execution assumptions. A queue model works simulated orders
only: it is refused beside an execution plan, whose children it would not see,
and on a run that routes to a venue.

What is governed, and what is not
---------------------------------
A limit order, or a stop-limit once triggered, that is neither immediate nor an
auction order. A market order, an immediate-or-cancel or fill-or-kill order and
an auction order never rest in a queue, and are worked exactly as before. An
order placed outside a market event of its asset -- by a timer, a slice or an
observation -- has no event to join at, and starts from an unknown queue: it
fills only once the market trades through its limit or its level is shown to be
empty. A cancelled, expired or filled order leaves the book; the simulated path
has no amend, so a changed order is a new one and joins at the back.

Persistence
-----------
:func:`capture_queue` writes a book as plain primitives with its model's type
and configuration, and :func:`queue_from_primitives` reads it back given the
model -- refused if it is another. A paused replay carries its book in its
payload.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final

from alphalab.backtesting.exceptions import BacktestError
from alphalab.backtesting.state import QueuedResult
from alphalab.common.persistent_map import PersistentMap
from alphalab.execution.queue import QueueBook, QueueModel, QueuePosition
from alphalab.runtime.assumptions import describe
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.run import RunConfig, RunEngine, RunState

__all__ = [
    "QUEUE_BOOK_SCHEMA",
    "QueueModelError",
    "capture_queue",
    "queue_from_primitives",
    "queued_result",
    "queued_run",
]

#: The version of the primitives :func:`capture_queue` writes.
QUEUE_BOOK_SCHEMA: Final = 1


class QueueModelError(BacktestError):
    """A queue model, or a queue book written under one, that cannot be used as given."""


def queued_run(config: RunConfig, model: QueueModel | None) -> QueueBook | None:
    """The empty book a run under ``model`` starts with, or ``None`` without one.

    Raises:
        QueueModelError: If the run does not simulate its orders -- it routes
            to a venue, or works them in an execution plan's children.
    """

    if model is None:
        return None
    routing = config.pipeline.routing
    if routing is not ExecutionRouting.SIMULATED:
        raise QueueModelError(
            f"A queue model governs simulated resting orders, and this run routes "
            f"{routing.name}: "
            + (
                "an execution plan's children are not governed by it. Give the run one or "
                "the other."
                if routing is ExecutionRouting.ALGORITHMIC
                else "its orders are its venue's to fill."
            )
        )
    return QueueBook(model)


def queued_result(state: RunState, queue: QueueBook) -> QueuedResult:
    """The finished run and its book, holding the orders still resting when it ended.

    An order the run ended after its asset's last event -- a settlement, a
    split, an expiry the data's end reached -- leaves the book here.
    """

    working = (str(order_id.value) for order_id in state.pipeline.oms.active_orders)
    return QueuedResult(run=RunEngine.finalize(state), queue=queue.keeping(working))


def capture_queue(book: QueueBook) -> dict[str, Any]:
    """The book as plain primitives, for a payload. Read back by :func:`queue_from_primitives`."""

    return {
        "schema_version": QUEUE_BOOK_SCHEMA,
        "model_type": type(book.model).__name__,
        "model": describe(book.model),
        "positions": [
            [
                order_id,
                str(position.limit_price),
                None if position.ahead is None else str(position.ahead),
            ]
            for order_id, position in book.positions.items()
        ],
    }


def queue_from_primitives(payload: Mapping[str, Any], model: QueueModel) -> QueueBook:
    """Read a book :func:`capture_queue` wrote, under the model it was produced by.

    Raises:
        QueueModelError: If the schema version is not this build's, or
            ``model`` is not the model -- the type and configuration -- the book
            was produced under.
    """

    version = payload.get("schema_version")
    if version != QUEUE_BOOK_SCHEMA:
        raise QueueModelError(
            f"A queue book of schema {version!r}; this build reads {QUEUE_BOOK_SCHEMA}."
        )
    recorded = payload["model"]
    supplied = describe(model)
    if payload["model_type"] != type(model).__name__ or recorded != supplied:
        raise QueueModelError(
            f"The queue book was produced under {recorded} and {supplied} was supplied; "
            "continuing it would decide the resting orders' fills another way."
        )
    positions: PersistentMap[str, QueuePosition] = PersistentMap()
    for order_id, limit_price, ahead in payload["positions"]:
        positions = positions.set(
            str(order_id),
            QueuePosition(Decimal(limit_price), None if ahead is None else Decimal(ahead)),
        )
    return QueueBook(model, positions)
