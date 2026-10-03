"""What a v1 run evaluation is judged against, and what it measured (ledger RES-001).

Until v3.12 this module blended seven sub-scores into an ``overall_score`` with
weights of 0.20, 0.15 and 0.10 nobody had chosen, over sub-scores built the same
way: a capacity that decayed by 0.01, 0.05 and 0.15 per ten thousand trades, a
"look-ahead risk" read from a win rate, a robustness inferred from the number
of parameters alone. Each was a constant presented as a measurement -- the
single figure :mod:`alphalab.research.overfitting` explains why AlphaLab does
not publish, and one a promotion policy could gate on.

It is restated over the v3.2 methodology, which keeps three things apart:

* **Measurements** -- what the run's returns, trades and parameter search
  actually show. :func:`research_metrics` flattens them for evidence.
* **Bounds** -- a :class:`ResearchPolicy` the caller states in advance. There
  is no default policy, because a default is a research standard chosen by a
  library.
* **Findings** -- the diagnostics name each number that crossed a stated bound,
  and the bound.

Nothing here grades a strategy.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from alphalab.research.bias import BiasReport
from alphalab.research.bootstrap import BootstrapReport
from alphalab.research.capacity import CapacityReport
from alphalab.research.cross_validation import WalkForwardReport
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.metrics import calculate_sharpe
from alphalab.research.montecarlo import MonteCarloReport
from alphalab.research.protocol import ResearchPayload
from alphalab.research.regime import RegimeReport
from alphalab.research.sensitivity import RobustnessReport
from alphalab.research.stress import StressReport

__all__ = ["ResearchPolicy", "research_metrics"]


def _finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ResearchValidationError(f"ResearchPolicy.{name} is {value!r}; a bound is finite.")


@dataclass(frozen=True, slots=True)
class ResearchPolicy:
    """The bounds and shocks a run evaluation uses -- every one stated, none defaulted.

    Each of these was a literal inside the v1 engine until v3.12.

    Attributes:
        walk_forward_windows: How many consecutive windows the return series is
            cut into for the walk-forward consistency check.
        ruin_drawdown: A resampled path whose maximum drawdown exceeds this
            fraction counts as ruin in the Monte Carlo estimate.
        minimum_trades: Fewer trades than this is a finding.
        maximum_trade_share: A single trade earning more than this fraction of
            the gross profit is a finding.
        worst_period_return: A period return below this is a finding.
        shock_return: The one-period return the stress test adds to the middle
            of the series -- ``-0.10`` for a ten per cent crash.
        gain_multiplier: The liquidity stress scales every positive return by
            this ...
        loss_multiplier: ... and every negative return by this.

    Raises:
        ResearchValidationError: If a bound is not finite or out of its range.
    """

    walk_forward_windows: int
    ruin_drawdown: float
    minimum_trades: int
    maximum_trade_share: float
    worst_period_return: float
    shock_return: float
    gain_multiplier: float
    loss_multiplier: float

    def __post_init__(self) -> None:
        for name in ("walk_forward_windows", "minimum_trades"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ResearchValidationError(
                    f"ResearchPolicy.{name} is {value!r}; it is a positive whole number."
                )
        for name in (
            "ruin_drawdown",
            "maximum_trade_share",
            "worst_period_return",
            "shock_return",
            "gain_multiplier",
            "loss_multiplier",
        ):
            _finite(name, getattr(self, name))
        if not 0.0 < self.ruin_drawdown <= 1.0:
            raise ResearchValidationError(
                f"ResearchPolicy.ruin_drawdown is {self.ruin_drawdown!r}; a drawdown is a "
                "fraction in (0, 1]."
            )
        if not 0.0 < self.maximum_trade_share <= 1.0:
            raise ResearchValidationError(
                f"ResearchPolicy.maximum_trade_share is {self.maximum_trade_share!r}; a share "
                "is a fraction in (0, 1]."
            )
        if self.shock_return <= -1.0:
            raise ResearchValidationError(
                f"ResearchPolicy.shock_return is {self.shock_return!r}; a return of -100% or "
                "less leaves nothing to measure a drawdown from."
            )
        if self.gain_multiplier < 0.0 or self.loss_multiplier < 0.0:
            raise ResearchValidationError(
                "ResearchPolicy.gain_multiplier and loss_multiplier scale returns; neither is "
                "negative."
            )


def research_metrics(
    payload: ResearchPayload,
    bias: BiasReport,
    walk_forward: WalkForwardReport,
    monte_carlo: MonteCarloReport,
    bootstrap: BootstrapReport,
    robustness: RobustnessReport,
    regime: RegimeReport,
    capacity: CapacityReport,
    stress: StressReport,
) -> Mapping[str, float]:
    """Every measurement the evaluation made, flat, for evidence.

    A quantity that was not measured -- a volatility of one return, a sweep the
    payload did not carry -- is absent rather than zero: evidence that lacks a
    metric a policy asks for fails that check, and a zero would pass it.
    ``regime`` contributes one Sharpe ratio per regime label, keyed
    ``"sharpe_in_<label>"``.
    """

    candidates: dict[str, float | None] = {
        "annualized_return": capacity.base_cagr,
        "annualized_volatility": bias.annualized_volatility,
        "sharpe": calculate_sharpe(
            payload.returns, payload.periods_per_year, payload.risk_free_rate
        )
        if len(payload.returns) >= 2
        else None,
        "max_drawdown": stress.base_drawdown,
        "trade_count": float(bias.trade_count),
        "win_rate": bias.win_rate,
        "parameter_count": float(bias.parameter_count),
        "walk_forward_mean_sharpe": walk_forward.mean_sharpe,
        "walk_forward_sharpe_variance": walk_forward.sharpe_variance,
        "bootstrap_sharpe_p05": bootstrap.lower_bound_5th,
        "bootstrap_sharpe_p50": bootstrap.median_50th,
        "bootstrap_sharpe_p95": bootstrap.upper_bound_95th,
        "monte_carlo_median_drawdown": monte_carlo.median_drawdown,
        "monte_carlo_p95_drawdown": monte_carlo.percentile_95_drawdown,
        "monte_carlo_worst_drawdown": monte_carlo.worst_drawdown,
        "monte_carlo_ruin_probability": monte_carlo.ruin_probability,
        "stress_shock_drawdown": stress.shock_drawdown,
        "stress_liquidity_drawdown": stress.liquidity_drawdown,
        "parameter_sweep_trials": None if robustness.trials is None else float(robustness.trials),
        "parameter_sweep_sensitivity": robustness.sensitivity,
        "parameter_sweep_neighbour_drop": robustness.neighbour_drop,
    }
    for label, sharpe in regime.sharpe_by_regime.items():
        candidates[f"sharpe_in_{label}"] = sharpe
    return MappingProxyType(
        {name: value for name, value in sorted(candidates.items()) if value is not None}
    )
