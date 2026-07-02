"""Drawdown analysis and underwater curve calculations."""

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DrawdownMetrics:
    """Immutable record of drawdown statistics."""

    drawdowns: tuple[float, ...]
    max_drawdown: float
    ulcer_index: float


def calculate_drawdowns(equity_curve: tuple[float, ...]) -> DrawdownMetrics:
    """Calculates high-water marks, drawdowns, and the Ulcer Index."""
    if not equity_curve:
        return DrawdownMetrics((), 0.0, 0.0)

    peak = equity_curve[0]
    drawdowns = []
    max_dd = 0.0

    for value in equity_curve:
        if value > peak:
            peak = value

        dd = (peak - value) / peak if peak > 0.0 else 0.0

        drawdowns.append(dd)
        if dd > max_dd:
            max_dd = dd

    # Ulcer Index = sqrt( mean( drawdown^2 ) )
    if drawdowns:
        sq_dd = sum(d * d for d in drawdowns)
        ulcer_index = math.sqrt(sq_dd / len(drawdowns))
    else:
        ulcer_index = 0.0

    return DrawdownMetrics(tuple(drawdowns), max_dd, ulcer_index)
