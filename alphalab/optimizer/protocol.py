"""Protocols ensuring loose coupling to analytics and execution engines."""

from typing import Any, Protocol


class TrialEvaluatorProtocol(Protocol):
    """
    Protocol for external components orchestrating Replay and Analytics.
    Returns a dictionary of standard float metrics (sharpe, drawdown, etc.).
    """

    def evaluate(self, parameters: dict[str, Any]) -> dict[str, float]: ...
