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

Run

    python examples/03_replay.py
"""

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


def load_historical_events() -> tuple[HistoricalEventProtocol, ...]:
    """
    Replace this placeholder with events produced by your loader.

    Typical sources include:

    - Event log
    - Exchange feed
    - Research replay
    - Recorded strategy events
    """
    return ()


def main() -> None:
    """Run a deterministic replay session."""

    # ------------------------------------------------------------
    # Step 1 : Create replay session
    # ------------------------------------------------------------

    session = ReplaySession(
        session_id="REPLAY-001",
        start_time=1_700_000_000.0,
        end_time=1_700_100_000.0,
        speed_multiplier=10.0,
    )

    # ------------------------------------------------------------
    # Step 2 : Load historical events
    # ------------------------------------------------------------

    events = load_historical_events()

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


if __name__ == "__main__":
    main()