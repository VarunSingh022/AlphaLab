"""Configuration for a backtest run.

Everything that decides what a run produces is here and is a value: the
execution-path configuration it threads, the fill policy the venue applies, and
the seed that makes its identifiers reproducible. Two runs with equal configs
over the same dataset and strategy produce equal results -- that is the whole
contract, and :mod:`tests.regression.test_deterministic_backtest` holds it.
"""

from dataclasses import dataclass, field

from alphalab.execution.policy import FillPolicy, ImmediateFill
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Configuration for one backtest or replay run.

    Attributes:
        pipeline: Execution-path configuration (account, budget, limits,
            simulator, venue, currency) threaded through every event.
        fill_policy: How the venue responds to each order. Defaults to filling
            in full, which is what the pipeline did before policies existed.
        seed: Seed for the run's identifier stream. ``None`` leaves identifiers
            on ``uuid4``, so quantities reproduce but ids do not; set it for a
            run whose orders and fills must be comparable across executions.
        start_timestamp: Instant the portfolio is funded, before any event.
        years_elapsed: Period length handed to the analytics engine for
            annualised figures.
        risk_free_rate: Risk-free rate handed to the analytics engine.
        compile_analytics: Whether to compile a performance report at the end
            of the run.
    """

    pipeline: ExecutionPipelineConfig
    fill_policy: FillPolicy = field(default_factory=ImmediateFill)
    seed: int | None = None
    start_timestamp: float = 0.0
    years_elapsed: float = 1.0
    risk_free_rate: float = 0.0
    compile_analytics: bool = True
