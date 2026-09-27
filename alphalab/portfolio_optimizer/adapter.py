"""Adapters mapping external engine data to Portfolio Optimizer inputs."""

from collections.abc import Mapping, Sequence
from typing import Any

from alphalab.portfolio_optimizer.exceptions import OptimizationError
from alphalab.portfolio_optimizer.transactions import TargetTransaction


class PortfolioAdapter:
    """Stateless translator between Portfolio Optimizer and external engines."""

    @staticmethod
    def transaction_to_order(
        transaction: TargetTransaction, portfolio_id: str, timestamp: float
    ) -> dict[str, Any]:
        """Converts a TargetTransaction into a standard OMS order payload dictionary."""
        side = "BUY" if transaction.trade_weight > 0 else "SELL"

        return {
            "portfolio_id": portfolio_id,
            "symbol": transaction.symbol,
            "side": side,
            "order_type": "MARKET",  # Rebalances typically default to market execution
            "target_weight": transaction.target_weight,
            "trade_weight": abs(transaction.trade_weight),
            "estimated_cost": transaction.estimated_cost,
            "timestamp": timestamp,
        }

    @staticmethod
    def dict_to_covariance_matrix(
        symbols: Sequence[str], cov_dict: Mapping[str, Mapping[str, float]]
    ) -> tuple[tuple[float, ...], ...]:
        """Translates a nested dictionary covariance into a strict 2D tuple matrix.

        Raises:
            OptimizationError: If any pair of ``symbols`` has no entry. Until
                v3.8 a missing entry read as ``0.0`` -- a covariance of zero is a
                measured absence of co-movement, and a missing one is not a
                measurement, so the matrix the optimizer received claimed an
                independence nobody had estimated. v3.8's audit found it; the
                v3.8 construction path reads a complete
                :class:`~alphalab.analytics.risk_model.CovarianceMatrix` instead.
        """

        missing = [
            f"{s1}/{s2}" for s1 in symbols for s2 in symbols if s2 not in cov_dict.get(s1, {})
        ]
        if missing:
            raise OptimizationError(
                f"No covariance supplied for {missing[:5]}"
                f"{' and more' if len(missing) > 5 else ''}. A missing covariance is not zero."
            )
        return tuple(tuple(cov_dict[s1][s2] for s2 in symbols) for s1 in symbols)

    @staticmethod
    def dict_to_expected_returns(
        symbols: Sequence[str], expected_returns_dict: Mapping[str, float]
    ) -> tuple[float, ...]:
        """Translates an expected returns dictionary into an ordered tuple vector.

        Raises:
            OptimizationError: If a symbol has no expected return. Until v3.8 it
                read as ``0.0``, a forecast nobody made that a maximum-Sharpe
                optimization then weighted on.
        """

        missing = [symbol for symbol in symbols if symbol not in expected_returns_dict]
        if missing:
            raise OptimizationError(f"No expected return supplied for {', '.join(missing)}.")
        return tuple(expected_returns_dict[symbol] for symbol in symbols)
