"""The facts a bias review of a finished run starts from.

Look-ahead and survivorship cannot be read from a return series after the fact.
Until v3.12 this module tried: it called a win rate above 95% on short trades
"look-ahead", a volatility under 5% "survivorship", and turned both into risks
and a score by constants nobody chose (ledger RES-001). AlphaLab prevents those
biases where they arise instead -- point-in-time datasets read ``as_of`` an
instant, delisting returns kept, purged and embargoed splits -- and this report
carries what the run itself shows, for a reviewer to read.
"""

from dataclasses import dataclass

from alphalab.research.metrics import calculate_volatility
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class BiasReport:
    """What the run shows that bears on bias -- measured, not scored.

    Attributes:
        win_rate: The fraction of trades with a positive profit; ``None``
            without trades.
        mean_trade_duration_seconds: The mean holding time; ``None`` without
            trades.
        annualized_volatility: Of the returns, annualized with the payload's
            periods; ``None`` with fewer than two returns.
        parameter_count: How many parameters the configuration has.
        trade_count: How many trades the run made.
        observations: How many returns the run produced.
        trades_per_parameter: ``trade_count / parameter_count``; ``None``
            without parameters.
    """

    win_rate: float | None
    mean_trade_duration_seconds: float | None
    annualized_volatility: float | None
    parameter_count: int
    trade_count: int
    observations: int
    trades_per_parameter: float | None


def detect_bias(payload: ResearchPayload) -> BiasReport:
    """Measure the run's facts a bias review reads; see the module docstring."""

    trades = payload.trades
    count = len(trades)
    parameters = len(payload.parameters)
    return BiasReport(
        win_rate=sum(1 for t in trades if t.pnl > 0) / count if count else None,
        mean_trade_duration_seconds=(
            sum(t.duration_seconds for t in trades) / count if count else None
        ),
        annualized_volatility=(
            calculate_volatility(payload.returns, payload.periods_per_year)
            if len(payload.returns) >= 2
            else None
        ),
        parameter_count=parameters,
        trade_count=count,
        observations=len(payload.returns),
        trades_per_parameter=count / parameters if parameters else None,
    )
