"""Deterministic constraint rules preventing illegal allocations."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.portfolio_optimizer.exceptions import (
    ConstraintViolationError,
    PortfolioValidationError,
)


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


#: How close the projection's sum must come to its target: the loop's own
#: convergence test, stated once and used for the final check too.
_SUM_TOLERANCE = 1e-9


def apply_weight_constraints(
    raw_weights: Mapping[str, float], constraints: WeightConstraints
) -> dict[str, float]:
    """
    Applies hard constraints using an iterative projection algorithm.
    Ensures that weights never exceed max_position_weight or max_asset_exposure
    after convergence.

    This is the v1 post-hoc projection: it clips weights an optimizer already
    produced and redistributes the excess evenly, which is a heuristic rather
    than an optimization over the constraints. v3.8's
    :func:`~alphalab.portfolio_optimizer.construction.construct` solves *over*
    its constraints instead.

    The target sum is ``1 - cash_reserve_weight``. When the upper bounds cannot
    reach it, the shortfall is **cash**, by design: a long-only book capped at
    40% a name in two names is 80% invested. The engine records that clipping
    happened (``ConstraintViolated``, with the amount clipped).

    Raises:
        PortfolioValidationError: If ``max_sector_exposure`` is set. This
            projection has no classification to apply a sector cap with, and
            until v3.8 it ignored one silently -- a limit that was stated and
            never enforced. Use a ``ConstraintSet`` with ``GroupBound``.
        ConstraintViolationError: If the *lower* bounds force the weights to
            sum above the target -- more capital than the target deploys, which
            no reading of the constraints makes cash. Until v3.8 it was returned
            as though the constraints held.
    """
    if constraints.max_sector_exposure:
        raise PortfolioValidationError(
            "WeightConstraints.max_sector_exposure cannot be applied here: this projection "
            "has no classification of assets into sectors, and a sector cap it cannot see "
            "would be ignored rather than enforced. Construct with a ConstraintSet whose "
            "GroupBound names the classification."
        )
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
        if abs(diff) < _SUM_TOLERANCE:
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

    excess = sum(weights.values()) - target_sum
    if excess >= _SUM_TOLERANCE:
        raise ConstraintViolationError(
            f"The lower bound {min_w!r} on {len(weights)} asset(s) forces the weights to sum to "
            f"{sum(weights.values())!r}, above the target {target_sum!r}. A shortfall is cash; an "
            "excess is capital the target does not have, and is refused."
        )
    return {s: round(float(w), 8) for s, w in weights.items()}
