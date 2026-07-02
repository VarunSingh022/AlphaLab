"""Validation rules ensuring structural and chronological data integrity."""

from alphalab.replay.exceptions import ReplayValidationError
from alphalab.replay.loader import HistoricalEventProtocol
from alphalab.replay.session import ReplaySession


def validate_session(session: ReplaySession) -> None:
    """Ensures a replay session is configured with valid limits and multipliers."""
    if not session.session_id:
        raise ReplayValidationError("Session must have a valid session_id.")
    if session.end_time < session.start_time:
        raise ReplayValidationError("Invalid replay range: end_time precedes start_time.")
    if session.speed_multiplier < 0.0:
        raise ReplayValidationError("Replay speed multiplier cannot be negative.")


def validate_events(events: tuple[HistoricalEventProtocol, ...]) -> None:
    """Verifies that all loaded historical events are strictly chronologically ordered."""
    if not events:
        raise ReplayValidationError("Empty datasets are not allowed for replay.")

    seen_ids = set()
    prev_ts = -1.0

    for e in events:
        if e.timestamp < prev_ts:
            raise ReplayValidationError(
                f"Events are not chronologically ordered. "
                f"Event {e.event_id} at {e.timestamp} followed an event at {prev_ts}."
            )
        if e.event_id in seen_ids:
            raise ReplayValidationError(f"Duplicate event_id detected: {e.event_id}")

        seen_ids.add(e.event_id)
        prev_ts = e.timestamp
