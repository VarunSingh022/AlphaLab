"""Chronological out-of-sample consistency of a finished return series.

The series is cut into consecutive windows and each window's Sharpe ratio is
reported. Until v3.12 the report reduced them to a ``degradation_score`` -- the
first window against the last, with 0.001 added to the denominator, clipped to
[0, 100] -- and the window count defaulted to five (ledger RES-001). The windows
are now the policy's, and the per-window figures are the measurement.
"""

from dataclasses import dataclass

from alphalab.common.statistics import sample_variance
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.metrics import calculate_sharpe
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    """Each window's Sharpe ratio, and how they spread.

    Attributes:
        windows_evaluated: How many windows were scored; ``0`` when the series
            has fewer than two returns per window.
        window_sharpes: Each window's Sharpe ratio, in order.
        mean_sharpe: Their mean; ``None`` when no window was scored.
        sharpe_variance: Their sample variance; ``None`` with fewer than two.
        first_to_last_change: The last window's Sharpe ratio minus the
            first's; ``None`` with fewer than two.
    """

    windows_evaluated: int
    window_sharpes: tuple[float, ...]
    mean_sharpe: float | None
    sharpe_variance: float | None
    first_to_last_change: float | None


def walk_forward_analysis(payload: ResearchPayload, num_windows: int) -> WalkForwardReport:
    """Score ``num_windows`` consecutive windows of the payload's returns.

    Raises:
        ResearchValidationError: If ``num_windows`` is not positive.
    """

    if num_windows < 1:
        raise ResearchValidationError(f"num_windows is {num_windows}; it is positive.")
    returns = payload.returns
    if len(returns) < num_windows * 2:
        return WalkForwardReport(0, (), None, None, None)

    chunk_size = len(returns) // num_windows
    sharpes = tuple(
        calculate_sharpe(
            returns[i * chunk_size : (i + 1) * chunk_size],
            payload.periods_per_year,
            payload.risk_free_rate,
        )
        for i in range(num_windows)
    )
    return WalkForwardReport(
        windows_evaluated=num_windows,
        window_sharpes=sharpes,
        mean_sharpe=sum(sharpes) / len(sharpes),
        sharpe_variance=sample_variance(sharpes) if len(sharpes) >= 2 else None,
        first_to_last_change=sharpes[-1] - sharpes[0] if len(sharpes) >= 2 else None,
    )
