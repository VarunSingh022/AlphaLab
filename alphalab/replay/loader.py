"""Interfaces and protocols for historical data ingestion."""

from typing import Protocol


class HistoricalEventProtocol(Protocol):
    """
    Protocol representing a generic historical event (e.g. Tick, Quote, Bar).
    Requires a timestamp for chronological sorting and replay routing.
    """

    @property
    def event_id(self) -> str: ...

    @property
    def timestamp(self) -> float: ...


class DataLoaderProtocol(Protocol):
    """Protocol for components that load historical data blocks into memory."""

    def load(self, start_time: float, end_time: float) -> tuple[HistoricalEventProtocol, ...]: ...
