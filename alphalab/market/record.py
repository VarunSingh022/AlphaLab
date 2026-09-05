"""The canonical market record: one market input plus its stream identity.

A :class:`MarketRecord` pairs one canonical market input -- a
:class:`~alphalab.market.quote.Quote`, :class:`~alphalab.market.bar.Bar` or
:class:`~alphalab.market.tick.Tick` -- with the identity and timestamp anything
sequencing market data needs of it.

This type lives here, in the market package, rather than in the package that
first needed it. Before v2.3 it was defined in :mod:`alphalab.backtesting.dataset`,
which meant a live feed adapter could not produce one without importing the
backtesting package. The record is the currency every environment deals in --
historical, replay, paper and live -- so it belongs with the canonical market
models it carries, and :mod:`alphalab.backtesting.dataset` re-exports it
unchanged.

``MarketRecord`` satisfies :class:`~alphalab.replay.loader.HistoricalEventProtocol`,
which is what lets one record type feed both a dataset cursor and a replay cursor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.market.bar import Bar
from alphalab.market.quote import Quote
from alphalab.market.tick import Tick

__all__ = ["MarketInput", "MarketRecord", "records_from_inputs"]

#: The canonical market inputs a record can carry.
MarketInput = Quote | Bar | Tick


@dataclass(frozen=True, slots=True)
class MarketRecord:
    """One market input plus the identity and timestamp a stream needs of it."""

    event_id: str
    timestamp: float
    payload: MarketInput

    @property
    def asset_id(self) -> str:
        """Asset the underlying market input refers to."""

        return self.payload.asset_id


def records_from_inputs(stream_id: str, inputs: Sequence[MarketInput]) -> tuple[MarketRecord, ...]:
    """Assign deterministic record identities to ``inputs``.

    Ids are ``"<stream_id>-<index>"`` with a fixed-width index, so the same
    inputs always produce the same record identities -- which comparing two runs
    of one stream depends on.
    """

    width = max(len(str(max(len(inputs) - 1, 0))), 1)
    return tuple(
        MarketRecord(f"{stream_id}-{index:0{width}d}", item.timestamp, item)
        for index, item in enumerate(inputs)
    )
