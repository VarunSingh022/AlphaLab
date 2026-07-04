"""Adapters converting Strategy Studio state to UI ViewModels safely."""

from typing import Any

from alphalab.studio import StrategyStudioState


class WorkbenchAdapter:
    """Stateless translator mapping underlying backend capabilities to the GUI."""

    @staticmethod
    def format_project_view(
        studio_state: StrategyStudioState, project_id: str
    ) -> dict[str, Any]:
        """Provides a safe read-only payload for the UI Project Explorer."""
        proj = studio_state.projects.get(project_id)
        if not proj:
            return {}
        return {
            "project_id": proj.project_id,
            "name": proj.name,
            "strategies_count": len(proj.strategies),
            "pipelines_count": len(proj.pipelines),
            "backtests_count": len(proj.backtests),
            "created_at": proj.created_at
        }

    @staticmethod
    def format_backtest_view(
        studio_state: StrategyStudioState, result_id: str
    ) -> dict[str, Any]:
        """Maps quantitative output to the Backtest Viewer Panel."""
        res = studio_state.backtest_results.get(result_id)
        if not res:
            return {}
        return {
            "result_id": res.result_id,
            "strategy_id": res.strategy_id,
            "sharpe_ratio": res.sharpe_ratio,
            "max_drawdown": res.max_drawdown,
            "total_return": res.total_return
        }