"""Scenario errors."""

from alphalab.common.exceptions import AlphaLabError

__all__ = [
    "ScenarioError",
    "ScenarioValidationError",
    "UnsupportedShockError",
]


class ScenarioError(AlphaLabError):
    """Base class for every scenario failure."""


class ScenarioValidationError(ScenarioError):
    """A scenario, a shock or a state was not well formed."""


class UnsupportedShockError(ScenarioError):
    """A shock was applied to a state that cannot express it.

    Raised rather than skipped. A volatility shock applied to exposures that
    carry no volatility, or an FX shock with no rate for a currency the book
    holds, has not been applied -- and a stress result that silently omitted it
    would understate the loss while still reporting the scenario's name, which
    is the most misleading of the available outcomes.
    """
