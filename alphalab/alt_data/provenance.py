"""Shared data provenance tracking.

Unlike official macro statistics, which come from a single government agency with
known, consistent authority, alternative data comes from many vendors of variable
and often undisclosed methodology and coverage. Every alt-data observation carries
its provenance explicitly rather than being treated as equally authoritative.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.alt_data.exceptions import AltDataInputError


@dataclass(frozen=True, slots=True)
class DataProvenance:
    """Identifies where an alternative data observation came from and how reliable it is.

    Attributes:
        vendor: Name of the data provider.
        coverage_description: Free-text description of what this vendor's data
            covers and how, e.g. "US retail parking lots, weekly satellite passes."
        confidence: A 0-1 reliability/quality score for this vendor's data. Not a
            statistical confidence interval -- a coarse, analyst-assigned or
            vendor-reported quality signal, since alt-data quality varies far more
            than official statistics and rarely comes with a rigorous error bound.
    """

    vendor: str
    coverage_description: str
    confidence: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise AltDataInputError(f"confidence must be between 0 and 1, got {self.confidence}.")
