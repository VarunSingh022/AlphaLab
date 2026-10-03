"""A finished run's returns, conditioned on regime labels somebody supplied.

Until v3.12 only ``BULL``, ``BEAR`` and ``SIDEWAYS`` were read -- any other label
was dropped without a word -- and the report closed with a "generalisation
score", the share of those three with a positive Sharpe ratio times 100 (ledger
RES-001). Every label present is now reported, each with its own count.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.metrics import calculate_sharpe
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class RegimeReport:
    """The Sharpe ratio within each regime label, and how many returns each holds.

    Attributes:
        observations_by_regime: Every label present, and its count.
        sharpe_by_regime: Each label with at least two returns, and the Sharpe
            ratio of those returns, annualized with the payload's periods.
    """

    observations_by_regime: Mapping[str, int]
    sharpe_by_regime: Mapping[str, float]


def analyze_regimes(payload: ResearchPayload) -> RegimeReport:
    """Group the returns by their regime label and measure each group."""

    if len(payload.returns) != len(payload.market_regimes):
        raise ResearchValidationError(
            f"{len(payload.returns)} returns and {len(payload.market_regimes)} regime labels; "
            "each return is labelled once."
        )
    grouped: dict[str, list[float]] = {}
    for ret, regime in zip(payload.returns, payload.market_regimes, strict=True):
        grouped.setdefault(regime, []).append(ret)

    labels = sorted(grouped)
    return RegimeReport(
        observations_by_regime=MappingProxyType({label: len(grouped[label]) for label in labels}),
        sharpe_by_regime=MappingProxyType(
            {
                label: calculate_sharpe(
                    grouped[label], payload.periods_per_year, payload.risk_free_rate
                )
                for label in labels
                if len(grouped[label]) >= 2
            }
        ),
    )
