"""Estimates strategy degradation at institutional capital scales."""

from dataclasses import dataclass

from alphalab.research.metrics import calculate_cagr
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class CapacityReport:
    base_aum: float
    base_cagr: float
    cagr_at_10m: float
    cagr_at_100m: float
    cagr_at_1b: float
    capacity_score: float


def estimate_capacity(payload: ResearchPayload) -> CapacityReport:
    """Projects performance decay due to slippage and market impact at scale."""
    base_cagr = calculate_cagr(payload.returns)
    trade_count = len(payload.trades)

    if trade_count == 0:
        return CapacityReport(payload.aum, base_cagr, 0.0, 0.0, 0.0, 0.0)

    # Heuristic: Higher frequency = higher slippage scaling penalty
    penalty_factor = trade_count / 10000.0

    # Deterministic degradation
    cagr_10m = max(0.0, base_cagr - (0.01 * penalty_factor))
    cagr_100m = max(0.0, base_cagr - (0.05 * penalty_factor))
    cagr_1b = max(0.0, base_cagr - (0.15 * penalty_factor))

    # Capacity score based on survival at 100M
    score = max(0.0, min(100.0, (cagr_100m / (base_cagr + 0.0001)) * 100.0))

    return CapacityReport(
        payload.aum,
        round(base_cagr, 4),
        round(cagr_10m, 4),
        round(cagr_100m, 4),
        round(cagr_1b, 4),
        round(score, 2),
    )
