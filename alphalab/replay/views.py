"""Pure queries exposing transparent Replay Engine access."""

from decimal import Decimal

from alphalab.replay.state import ReplayState


def current_timestamp(state: ReplayState) -> float:
    """Returns the current simulated time of the replay engine."""
    return state.current_timestamp


def remaining_events(state: ReplayState) -> int:
    """Returns the absolute number of events left in the sequence."""
    return state.metrics.remaining_events


def completion_ratio(state: ReplayState) -> Decimal:
    """Returns the ratio of completion mapped 0.0 to 1.0."""
    return state.metrics.completion_ratio


def processed_events(state: ReplayState) -> int:
    """Returns the total number of events emitted so far."""
    return state.metrics.events_processed


def elapsed_real_time(state: ReplayState) -> float:
    """Returns the wall-clock time spent actively processing the replay."""
    return state.metrics.elapsed_real_time


def current_throughput(state: ReplayState) -> float:
    """Returns events processed per real wall-clock second."""
    return state.metrics.throughput
