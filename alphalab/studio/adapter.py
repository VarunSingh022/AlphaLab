"""Adapters mapping core engine outputs back to the Studio tracking models."""

from collections.abc import Mapping

from alphalab.studio.results import BacktestResult, PipelineResult, ResearchResult


class StudioAdapter:
    """Stateless translator isolating external engine logic from the Studio."""

    @staticmethod
    def to_backtest_result(
        res_id: str, bt_id: str, strat_id: str, metrics: Mapping[str, float]
    ) -> BacktestResult:
        return BacktestResult(
            res_id, bt_id, strat_id, 
            metrics.get("total_return", 0.0), 
            metrics.get("sharpe", 0.0), 
            metrics.get("max_drawdown", 0.0)
        )

    @staticmethod
    def to_research_result(
        res_id: str, strat_id: str, scores: Mapping[str, float]
    ) -> ResearchResult:
        return ResearchResult(
            res_id, strat_id, 
            scores.get("bias", 0.0), scores.get("robustness", 0.0), 
            scores.get("capacity", 0.0), scores.get("overall", 0.0)
        )

    @staticmethod
    def to_pipeline_result(
        res_id: str, 
        pipe_id: str, 
        success: bool, 
        duration: float, 
        metrics: Mapping[str, float]
    ) -> PipelineResult:
        return PipelineResult(res_id, pipe_id, success, duration, metrics)