"""Adapter translating PerformanceReports to flat metric dictionaries."""

from typing import Any

from alphalab.optimizer.exceptions import OptimizerValidationError


class OptimizationAdapter:
    """Stateless translator mapping Analytics Engine reports to Optimizer metrics."""

    @staticmethod
    def extract_metrics(performance_report: Any) -> dict[str, float]:
        """
        Extracts core float metrics from an AlphaLab PerformanceReport.
        (Expects the structure defined in PR-015 Analytics Engine).
        """
        try:
            return {
                "sharpe_ratio": float(performance_report.risk.sharpe_ratio),
                "sortino_ratio": float(performance_report.risk.sortino_ratio),
                "calmar_ratio": float(performance_report.risk.calmar_ratio),
                "total_return": float(performance_report.returns.total_return),
                "cagr": float(performance_report.returns.cagr),
                "max_drawdown": float(performance_report.drawdowns.max_drawdown),
                "win_rate": float(performance_report.trades.win_rate),
            }
        except AttributeError as e:
            raise OptimizerValidationError(f"Invalid performance report structure: {e}") from e
