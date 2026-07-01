"""Immutable pure-functional latency models."""

from typing import Protocol


class LatencyModel(Protocol):
    def calculate(self, order_id: str, current_timestamp: float) -> float: ...


class ConstantLatency:
    __slots__ = ("_latency",)

    def __init__(self, latency: float) -> None:
        self._latency = latency

    def calculate(self, order_id: str, current_timestamp: float) -> float:
        return self._latency


class DeterministicLatency:
    __slots__ = ("_max_lat", "_min_lat")

    def __init__(self, min_lat: float, max_lat: float) -> None:
        self._min_lat = min_lat
        self._max_lat = max_lat

    def calculate(self, order_id: str, current_timestamp: float) -> float:
        # Use a hash of the order_id for pure deterministic pseudo-random latency
        pseudo_random_val = (hash(order_id) % 1000) / 1000.0
        return self._min_lat + (self._max_lat - self._min_lat) * pseudo_random_val
