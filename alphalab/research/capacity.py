"""The scale a finished run traded at -- not a capacity estimate.

Capacity is the capital at which a strategy's own trading runs into the market,
and it cannot be read from a return series and a trade count. Until v3.12 this
module tried: it degraded the CAGR by 0.01, 0.05 and 0.15 per ten thousand
trades at three fixed AUM levels and scored the survival at the middle one
(ledger RES-001) -- a table with no liquidity in it. The estimate is
:mod:`alphalab.execution.capacity`'s, which reads the liquidity and impact
models a fill is priced with. This report keeps the run's own scale.
"""

from dataclasses import dataclass

from alphalab.research.metrics import calculate_cagr
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class CapacityReport:
    """The capital the run was sized against, what it earned, and how often it traded.

    Attributes:
        base_aum: The assets the run was sized against.
        base_cagr: Its compound annual growth, annualized with the payload's
            periods; ``None`` without returns.
        trade_count: How many trades it made.
        trades_per_year: Trades per year of returns; ``None`` without returns.
    """

    base_aum: float
    base_cagr: float | None
    trade_count: int
    trades_per_year: float | None


def estimate_capacity(payload: ResearchPayload) -> CapacityReport:
    """The run's scale; see the module docstring for why nothing more."""

    observations = len(payload.returns)
    years = observations / payload.periods_per_year
    return CapacityReport(
        base_aum=payload.aum,
        base_cagr=(
            calculate_cagr(payload.returns, payload.periods_per_year) if observations else None
        ),
        trade_count=len(payload.trades),
        trades_per_year=len(payload.trades) / years if observations else None,
    )
