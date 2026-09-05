"""The adapter that puts a market-data provider on the execution path.

v2.3 built both ends of this and never joined them. ``alphalab.market.source``
defined what the execution path consumes -- a :class:`MarketDataSource` yielding
canonical :class:`~alphalab.market.record.MarketRecord` s -- and
``alphalab.market.normalization`` defined how a provider's wire record becomes
canonical. Between them there was nothing, so ``normalize_wire_*`` had no
production caller and :class:`~alphalab.market.source.SequenceSource` was the
only source in the repository. A real provider client existed too
(:mod:`alphalab.marketdata.binance`, over
:class:`~alphalab.marketdata.transport.HttpTransport`) and could not reach a
:class:`~alphalab.runtime.session.TradingSession`.

This module is that link, and only that link. It adds no HTTP, models no vendor
API, and implements no second provider: everything it uses already existed and
was already tested.

::

    provider adapter        marketdata.binance.binanceAdapter
        -> wire bars        marketdata.feed.Bar          (float, provider symbol)
        -> normalization    market.normalization         (Decimal, asset_id, venue)
        -> MarketRecord     market.record
        -> ProviderHistorySource                         (a MarketDataSource)
        -> TradingSession   runtime.session

What it is not
--------------
A *history* source: it asks a provider for a finite, closed range of bars and
yields them. It does not poll, subscribe, reconnect or stream, because a
streaming source needs a clock and a loop that AlphaLab does not have (ADR-0012
and ADR-0014). Being finite and re-iterable is also what lets two runs over the
same source be compared record by record.

Ordering
--------
One symbol's history from a provider is chronological, and the source says so.
Several symbols are *interleaved* by timestamp, ties broken by ``asset_id``:
that is a merge of already-sorted inputs, deterministic and repeatable, not a
reordering of an unordered stream. :func:`~alphalab.market.source.validate_ordering`
is run over the result either way, so a provider that returned records out of
order is caught here rather than silently marking a portfolio backwards.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from alphalab.market.bar import Bar
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.normalization import DEFAULT_POLICY, NormalizationPolicy, normalize_wire_bar
from alphalab.market.record import MarketRecord, records_from_inputs
from alphalab.market.source import OrderingGuarantee, validate_ordering
from alphalab.marketdata.feed import Bar as WireBar
from alphalab.marketdata.timeframe import Timeframe

__all__ = ["BarHistoryProvider", "ProviderHistorySource", "normalize_wire_bars"]


class BarHistoryProvider(Protocol):
    """The one method this adapter needs of a provider.

    Deliberately narrower than any provider adapter's full surface. A source is
    built from historical bars, so ``request_history`` is the whole contract --
    and anything satisfying it, including a test double, can be a source without
    implementing connect/subscribe/quote/book as well.
    """

    def request_history(
        self, symbol: str, timeframe: Timeframe, start: float, end: float
    ) -> tuple[WireBar, ...]: ...


def normalize_wire_bars(
    bars: Sequence[WireBar], policy: NormalizationPolicy = DEFAULT_POLICY
) -> tuple[Bar, ...]:
    """Lift a provider's wire bars into canonical bars.

    Every rule stays where v2.3 put it: precision through ``Decimal(str(...))``,
    the venue, currency and timeframe from ``policy`` because the wire cannot
    carry them, and ``vwap`` / ``trade_count`` left at zero and documented as
    unreported rather than invented. This function only maps the sequence.
    """

    return tuple(normalize_wire_bar(bar, policy) for bar in bars)


@dataclass(frozen=True, slots=True)
class ProviderHistorySource:
    """A :class:`~alphalab.market.source.MarketDataSource` over provider history.

    Built by :meth:`of`, which does the fetching. The instance itself holds only
    canonical records, so it is finite, re-iterable and independent of the
    provider it came from -- iterating it twice cannot produce two different
    runs, and cannot make a second network call.
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
    def of(
        cls,
        provider: BarHistoryProvider,
        symbols: Sequence[str],
        timeframe: Timeframe,
        start: float,
        end: float,
        source_id: str,
        policy: NormalizationPolicy = DEFAULT_POLICY,
    ) -> ProviderHistorySource:
        """Fetch, normalize and identify a provider's bars for ``symbols``.

        Record ids are ``"<source_id>-<index>"`` with a fixed-width index, the
        same scheme :meth:`~alphalab.market.source.SequenceSource.of` and
        :meth:`~alphalab.backtesting.dataset.MarketDataset.of` use, so the same
        provider response always produces the same record identities and two
        runs over one source are comparable record by record.

        Raises:
            MarketValidationError: If ``symbols`` is empty, if the provider
                returned no bars at all, or if the normalized records are not
                chronological -- a provider that returned history out of order
                is a broken response, not something to quietly sort around.
        """

        if not symbols:
            raise MarketValidationError("A provider source needs at least one symbol.")

        canonical: list[Bar] = []
        for symbol in symbols:
            canonical.extend(
                normalize_wire_bars(provider.request_history(symbol, timeframe, start, end), policy)
            )

        if not canonical:
            raise MarketValidationError(
                f"The provider returned no bars for {list(symbols)} between {start} and {end}; "
                "a source with no records cannot be replayed or compared."
            )

        # A merge of per-symbol histories, each already chronological. The
        # asset_id tie-break makes the interleave total, so the same response
        # always produces the same order.
        canonical.sort(key=lambda bar: (bar.timestamp, bar.asset_id))

        records = records_from_inputs(source_id, canonical)
        validate_ordering(records)
        return cls(source_id, records)
