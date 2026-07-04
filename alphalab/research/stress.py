"""Deterministic Stress Testing."""

from dataclasses import dataclass

from alphalab.research.metrics import calculate_max_drawdown
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class StressReport:
    flash_crash_drawdown: float
    liquidity_shock_drawdown: float
    stress_survival_score: float


def apply_stress_tests(payload: ResearchPayload) -> StressReport:
    """Applies systemic shocks to the return stream."""
    if not payload.returns:
        return StressReport(0.0, 0.0, 0.0)

    # Simulate Flash Crash (sudden -10% shock in middle of curve)
    fc_returns = list(payload.returns)
    if len(fc_returns) > 10:
        fc_returns[len(fc_returns) // 2] -= 0.10
    fc_dd = calculate_max_drawdown(fc_returns)

    # Simulate Liquidity Shock (all positive returns halved, negative doubled)
    ls_returns = [r * 0.5 if r > 0 else r * 2.0 for r in payload.returns]
    ls_dd = calculate_max_drawdown(ls_returns)

    # Survival score
    score = 100.0
    if fc_dd > 0.30:
        score -= 30.0
    if ls_dd > 0.40:
        score -= 40.0

    return StressReport(round(fc_dd, 4), round(ls_dd, 4), max(0.0, round(score, 2)))
