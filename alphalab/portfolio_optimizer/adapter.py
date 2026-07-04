"""Adapters mapping external engine data to Portfolio Optimizer inputs."""

from collections.abc import Mapping, Sequence
from typing import Any

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
        """Translates a nested dictionary covariance into a strict 2D tuple matrix."""
        matrix = []
        for s1 in symbols:
            row = []
            for s2 in symbols:
                # Safely extract covariance; defaults to 0.0 if not found
                row.append(cov_dict.get(s1, {}).get(s2, 0.0))
            matrix.append(tuple(row))
        return tuple(matrix)

    @staticmethod
    def dict_to_expected_returns(
        symbols: Sequence[str], expected_returns_dict: Mapping[str, float]
    ) -> tuple[float, ...]:
        """Translates an expected returns dictionary into an ordered tuple vector."""
        return tuple(expected_returns_dict.get(s, 0.0) for s in symbols)
