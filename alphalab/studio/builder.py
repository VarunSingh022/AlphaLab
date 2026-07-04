"""Pure functions to construct orchestration sequences."""

from alphalab.studio.backtest import BacktestConfiguration
from alphalab.studio.pipeline import PipelineDefinition, PipelineStep


def build_pipeline(
    pipeline_id: str, project_id: str, name: str, steps: tuple[PipelineStep, ...]
) -> PipelineDefinition:
    """Deterministically constructs a Pipeline."""
    return PipelineDefinition(pipeline_id, project_id, name, steps)

def build_backtest_config(
    backtest_id: str, strategy_id: str, datasets: tuple[str, ...], 
    start: float, end: float, capital: float
) -> BacktestConfiguration:
    """Deterministically constructs a Backtest setup."""
    return BacktestConfiguration(backtest_id, strategy_id, datasets, start, end, capital)