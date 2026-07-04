"""Immutable interface protocols for the Portfolio Optimizer."""

from collections.abc import Mapping
from typing import Protocol


class AlphaSignalProtocol(Protocol):
    """Pure functional interface defining incoming strategy alpha signals."""

    @property
    def expected_returns(self) -> Mapping[str, float]:
        """Returns the projected return for each asset."""
        ...

    @property
    def signal_confidence(self) -> Mapping[str, float]:
        """Returns the statistical confidence score of the signal per asset."""
        ...


class RiskModelProtocol(Protocol):
    """Pure functional interface defining incoming risk models."""

    @property
    def asset_volatilities(self) -> Mapping[str, float]:
        """Returns the annualized volatility for each asset."""
        ...

    @property
    def covariance_matrix(self) -> Mapping[str, Mapping[str, float]]:
        """Returns the asset covariance relationships as a nested mapping."""
        ...
