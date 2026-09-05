"""Ingestion and continuous update logic for the live snapshot states."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.live.events import SnapshotUpdated, TickReceived
from alphalab.live.message import QuoteTick, TradeTick
from alphalab.live.snapshot import MarketSnapshot
from alphalab.live.state import LiveState
from alphalab.live.validation import validate_tick_routing


class LiveFeed:
    """Stateless processor that merges continuous ticks into discrete snapshots."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def process_trade(state: LiveState, tick: TradeTick) -> LiveState:
        """Applies a TradeTick to update the latest price and volume."""
        validate_tick_routing(state, tick.provider_id, tick.symbol)

        current = state.snapshots.get(tick.symbol, MarketSnapshot(tick.symbol, tick.timestamp))

        updated = replace(
            current,
            timestamp=tick.timestamp,
            last_trade_price=tick.price,
            volume=current.volume + tick.size,
        )

        return LiveFeed._finalize_update(state, updated, tick.provider_id, "TRADE")

    @staticmethod
    def process_quote(state: LiveState, tick: QuoteTick) -> LiveState:
        """Applies a QuoteTick to update the best bid and ask."""
        validate_tick_routing(state, tick.provider_id, tick.symbol)

        current = state.snapshots.get(tick.symbol, MarketSnapshot(tick.symbol, tick.timestamp))

        updated = replace(
            current,
            timestamp=tick.timestamp,
            best_bid=tick.bid,
            best_ask=tick.ask,
        )

        return LiveFeed._finalize_update(state, updated, tick.provider_id, "QUOTE")

    @staticmethod
    def _finalize_update(
        state: LiveState, snapshot: MarketSnapshot, provider_id: str, tick_type: str
    ) -> LiveState:
        """Helper to commit snapshot updates and append telemetry metrics."""
        tick_evt = TickReceived(
            LiveFeed._create_id(), snapshot.timestamp, provider_id, snapshot.symbol, tick_type
        )
        snap_evt = SnapshotUpdated(
            LiveFeed._create_id(), snapshot.timestamp, snapshot.symbol, snapshot.last_trade_price
        )

        new_stats = replace(
            state.statistics,
            total_ticks_processed=state.statistics.total_ticks_processed + 1,
            total_snapshots_updated=state.statistics.total_snapshots_updated + 1,
        )

        return replace(
            state,
            snapshots=state.snapshots.set(snapshot.symbol, snapshot),
            statistics=new_stats,
            events=state.events.extend((tick_evt, snap_evt)),
        )
