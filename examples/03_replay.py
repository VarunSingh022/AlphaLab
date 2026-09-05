"""
AlphaLab Examples
=================

Example 03 : Replay Engine

Difficulty : Beginner

Estimated Time : 5 minutes

Topics
------

• Deterministic Replay
• Replay Sessions
• Immutable State
• Replay Metrics
• Replay Views

What this shows
---------------

The replay *cursor* on its own: session lifecycle, chronological validation,
progress and throughput. This is what `alphalab.replay` owns.

To replay a dataset through the real execution path -- strategy, allocation,
risk, OMS, execution, portfolio, analytics -- use
`alphalab.backtesting.ReplayBacktest`, which drives this cursor and hands every
event it yields to the same step a backtest uses. See
`examples/11_unified_backtest.py`.

Run

    python examples/03_replay.py
"""

from decimal import Decimal

from alphalab.backtesting import MarketDataset
from alphalab.market.quote import Quote
from alphalab.replay import (
    HistoricalEventProtocol,
    ReplayEngine,
    ReplaySession,
    completion_ratio,
    current_timestamp,
    elapsed_real_time,
    processed_events,
    remaining_events,
)

ASSET_ID = "3f1b8b0e-4a6e-4a3e-9a4a-1f2c3d4e5f60"


def load_historical_events() -> tuple[HistoricalEventProtocol, ...]:
    """
    Load the events to replay.

    A `MarketDataset` record satisfies `HistoricalEventProtocol`, which is what
    lets one dataset feed both this cursor and the backtest loop. Replace this
    with your own loader: an event log, an exchange feed, or recorded strategy
    events -- anything carrying an `event_id` and a `timestamp`.
    """
    quotes = [
        Quote(
            asset_id=ASSET_ID,
            timestamp=1_700_000_000.0 + index,
            bid=Decimal("100.00") + Decimal(index),
            ask=Decimal("100.02") + Decimal(index),
            bid_size=Decimal("500"),
            ask_size=Decimal("500"),
            venue="SIM",
            currency="USD",
        )
        for index in range(10)
    ]
    return MarketDataset.of("EXAMPLE-REPLAY", quotes).records


def main() -> None:
    """Run a deterministic replay session."""

    # ------------------------------------------------------------
    # Step 1 : Create replay session
    # ------------------------------------------------------------

    events = load_historical_events()

    session = ReplaySession(
        session_id="REPLAY-001",
        start_time=events[0].timestamp,
        end_time=events[-1].timestamp,
        speed_multiplier=10.0,
    )

    # ------------------------------------------------------------
    # Step 2 : Historical events are already loaded above
    # ------------------------------------------------------------

    # ------------------------------------------------------------
    # Step 3 : Initialize replay engine
    # ------------------------------------------------------------

    state = ReplayEngine.initialize(
        session=session,
        events=events,
        real_time=0.0,
    )

    # ------------------------------------------------------------
    # Step 4 : Start replay
    # ------------------------------------------------------------

    state = ReplayEngine.start(
        state,
        real_time=0.0,
    )

    # ------------------------------------------------------------
    # Step 5 : Advance replay
    # ------------------------------------------------------------

    while state.status.name != "COMPLETED":
        step = ReplayEngine.step_one_event(
            state,
            real_time=state.real_current_time + 0.01,
        )
        state = step.state
        if step.event is None:
            break

    # ------------------------------------------------------------
    # Step 6 : Inspect replay metrics
    # ------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Replay Example")
    print("=" * 60)
    print()

    print(f"Session           : {state.session.session_id}")
    print(f"Status            : {state.status.name}")
    print(f"Processed Events  : {processed_events(state)}")
    print(f"Remaining Events  : {remaining_events(state)}")
    print(f"Completion Ratio  : {completion_ratio(state):.2%}")
    print(f"Replay Time       : {current_timestamp(state)}")
    print(f"Elapsed Real Time : {elapsed_real_time(state):.4f} sec")
    print(f"System Events     : {len(state.system_events)}")
    print()
    print("To turn these events into orders, fills and P&L, drive them through")
    print("alphalab.backtesting.ReplayBacktest -- see examples/11_unified_backtest.py.")


if __name__ == "__main__":
    main()
