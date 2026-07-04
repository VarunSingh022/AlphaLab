"""Heuristic-based Strategy Diagnostics."""

from dataclasses import dataclass

from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    too_few_trades: bool
    high_concentration: bool
    large_tail_risk: bool
    warnings: tuple[str, ...]


def generate_diagnostics(payload: ResearchPayload) -> DiagnosticReport:
    """Detects critical structural flaws in the research payload."""
    warnings = []

    too_few = len(payload.trades) < 50
    if too_few:
        warnings.append("Too few trades for statistical significance (<50).")

    concentration = False
    if payload.trades:
        max_pnl = max(t.pnl for t in payload.trades)
        total_pnl = sum(t.pnl for t in payload.trades if t.pnl > 0)
        if total_pnl > 0 and (max_pnl / total_pnl) > 0.30:
            concentration = True
            warnings.append("High concentration: Single trade accounts for >30% of gross profit.")

    # Tail risk proxy: Minimum return
    tail_risk = False
    if payload.returns and min(payload.returns) < -0.10:
        tail_risk = True
        warnings.append("Large tail risk detected: Single period drop > 10%.")

    return DiagnosticReport(too_few, concentration, tail_risk, tuple(warnings))
