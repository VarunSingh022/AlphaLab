"""Deterministic constraint rules preventing illegal allocations."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class WeightConstraints:
    long_only: bool = True
    max_position_weight: float = 1.0
    min_position_weight: float = -1.0
    cash_reserve_weight: float = 0.0
    max_sector_exposure: Mapping[str, float] = field(default_factory=dict)
    max_asset_exposure: float = 1.0


@dataclass(frozen=True, slots=True)
class RiskConstraints:
    max_drawdown_limit: float = 1.0
    max_volatility_limit: float = 1.0
    max_tracking_error: float = 1.0
    max_leverage: float = 1.0
    max_turnover: float = 1.0
    max_concentration: float = 1.0


def apply_weight_constraints(
    raw_weights: Mapping[str, float], constraints: WeightConstraints
) -> dict[str, float]:
    """
    Applies hard constraints using an iterative projection algorithm.
    Ensures that weights never exceed max_position_weight or max_asset_exposure
    after convergence.
    """
    weights = dict(raw_weights)

    # Define effective bounds for each asset
    min_w = 0.0 if constraints.long_only else constraints.min_position_weight
    upper_bound = min(constraints.max_position_weight, constraints.max_asset_exposure)
    target_sum = 1.0 - constraints.cash_reserve_weight

    # 1. Initial Clip: Enforce hard boundaries immediately
    for s in weights:
        weights[s] = max(min_w, min(weights[s], upper_bound))

    # 2. Iterative Projection: Redistribute excess without violating boundaries
    # We iterate to find the Lagrange multiplier equivalent (the 'delta')
    for _ in range(100):
        current_sum = sum(weights.values())
        diff = target_sum - current_sum

        # Check convergence
        if abs(diff) < 1e-9:
            break

        # Determine which assets can absorb more weight or give up weight
        if diff > 0:
            # Need to add weight: only adjust assets < upper_bound
            adjustable = [s for s in weights if weights[s] < upper_bound]
        else:
            # Need to remove weight: only adjust assets > min_w
            adjustable = [s for s in weights if weights[s] > min_w]

        if not adjustable:
            # No room to adjust, cannot satisfy constraints exactly
            break

        # Distribute the difference evenly across adjustable assets
        step = diff / len(adjustable)
        for s in adjustable:
            weights[s] += step
            # Re-clip to ensure step didn't violate boundary
            weights[s] = max(min_w, min(weights[s], upper_bound))

    return {s: round(float(w), 8) for s, w in weights.items()}
