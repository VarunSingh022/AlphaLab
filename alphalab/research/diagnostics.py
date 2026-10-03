"""Findings: each number in a finished run that crossed a bound the caller stated.

Until v3.12 the bounds were literals -- 50 trades, a 30% share of gross profit, a
10% one-period loss (ledger RES-001). They are the
:class:`~alphalab.research.research.ResearchPolicy`'s now, and every warning
names the number and the bound it crossed.
"""

from dataclasses import dataclass

from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Which stated bounds the run crossed, and a sentence for each."""

    too_few_trades: bool
    high_concentration: bool
    large_tail_risk: bool
    warnings: tuple[str, ...]


def generate_diagnostics(
    payload: ResearchPayload,
    minimum_trades: int,
    maximum_trade_share: float,
    worst_period_return: float,
) -> DiagnosticReport:
    """Compare the run's trade count, profit concentration and worst period with the bounds."""

    warnings = []

    count = len(payload.trades)
    too_few = count < minimum_trades
    if too_few:
        warnings.append(f"{count} trades, fewer than the stated minimum of {minimum_trades}.")

    concentration = False
    gross_profit = sum(t.pnl for t in payload.trades if t.pnl > 0)
    if gross_profit > 0:
        share = max(t.pnl for t in payload.trades) / gross_profit
        if share > maximum_trade_share:
            concentration = True
            warnings.append(
                f"One trade earned {share:.4f} of the gross profit, above the stated "
                f"{maximum_trade_share}."
            )

    tail_risk = False
    if payload.returns:
        worst = min(payload.returns)
        if worst < worst_period_return:
            tail_risk = True
            warnings.append(
                f"The worst period returned {worst}, below the stated {worst_period_return}."
            )

    return DiagnosticReport(too_few, concentration, tail_risk, tuple(warnings))
