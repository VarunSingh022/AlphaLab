"""Chronological Out-of-Sample Walk Forward Analysis."""

from dataclasses import dataclass

from alphalab.research.metrics import calculate_sharpe
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    windows_evaluated: int
    avg_oos_sharpe: float
    sharpe_variance: float
    degradation_score: float


def walk_forward_analysis(payload: ResearchPayload, num_windows: int = 5) -> WalkForwardReport:
    """Chronologically splits returns and evaluates Out-Of-Sample consistency."""
    returns = payload.returns
    if len(returns) < num_windows * 2:
        return WalkForwardReport(0, 0.0, 0.0, 100.0)

    chunk_size = len(returns) // num_windows
    oos_sharpes = []

    for i in range(num_windows):
        oos_chunk = returns[i * chunk_size : (i + 1) * chunk_size]
        oos_sharpes.append(calculate_sharpe(oos_chunk))

    avg_sharpe = sum(oos_sharpes) / len(oos_sharpes)
    variance = sum((s - avg_sharpe) ** 2 for s in oos_sharpes) / len(oos_sharpes)

    # Degradation: Compare last window to first window
    raw_degradation = ((oos_sharpes[0] - oos_sharpes[-1]) / (abs(oos_sharpes[0]) + 0.001)) * 100.0
    degradation = max(0.0, min(100.0, raw_degradation))

    return WalkForwardReport(
        num_windows, round(avg_sharpe, 4), round(variance, 4), round(degradation, 2)
    )
