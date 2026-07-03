"""Feed Engine serving as the top-level orchestration boundary."""

from alphalab.feed.connection import ConnectionSnapshot
from alphalab.feed.state import FeedState


class FeedEngine:
    """Facade orchestrating setup of arbitrary FeedProtocols."""

    @staticmethod
    def initialize(provider_id: str, provider_name: str) -> FeedState:
        """Constructs an empty base state for the feed layer."""
        conn = ConnectionSnapshot(
            connected=False,
            latency_ms=0.0,
            provider_name=provider_name,
            last_heartbeat=0.0,
        )

        return FeedState(
            provider_id=provider_id,
            connection=conn,
        )
