"""Drawdowns under shocks the caller declares.

Until v3.12 the shocks were literals -- a 10% crash, gains halved and losses
doubled -- and the report closed with a "survival score" that lost 30 points past
a 30% drawdown and 40 past 40% (ledger RES-001). The shocks are now the policy's,
and the drawdowns are the measurement.
"""

from dataclasses import dataclass

from alphalab.research.metrics import calculate_max_drawdown
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class StressReport:
    """The run's maximum drawdown, as it was and under each declared shock.

    ``None`` throughout without returns.

    Attributes:
        base_drawdown: As the run produced it.
        shock_return: The one-period return added at the middle of the series.
        shock_drawdown: The maximum drawdown with that shock in place.
        gain_multiplier: What every positive return was scaled by ...
        loss_multiplier: ... and every negative one.
        liquidity_drawdown: The maximum drawdown with both scalings applied.
    """

    base_drawdown: float | None
    shock_return: float
    shock_drawdown: float | None
    gain_multiplier: float
    loss_multiplier: float
    liquidity_drawdown: float | None


def apply_stress_tests(
    payload: ResearchPayload,
    shock_return: float,
    gain_multiplier: float,
    loss_multiplier: float,
) -> StressReport:
    """Measure the drawdown with a mid-series shock, and with returns rescaled."""

    if not payload.returns:
        return StressReport(None, shock_return, None, gain_multiplier, loss_multiplier, None)

    shocked = list(payload.returns)
    shocked[len(shocked) // 2] += shock_return
    rescaled = [r * gain_multiplier if r > 0 else r * loss_multiplier for r in payload.returns]
    return StressReport(
        base_drawdown=calculate_max_drawdown(payload.returns),
        shock_return=shock_return,
        shock_drawdown=calculate_max_drawdown(shocked),
        gain_multiplier=gain_multiplier,
        loss_multiplier=loss_multiplier,
        liquidity_drawdown=calculate_max_drawdown(rescaled),
    )
