"""ESG (Environmental, Social, Governance) scoring.

`composite_score` defaults to equal weighting, one convention among several used by
real ESG rating providers, most of which use their own proprietary, undisclosed
weighting -- equal weighting is a transparent, defensible default, not a claim that
it matches any specific vendor's methodology.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.provenance import DataProvenance

_SCORE_MIN = Decimal("0")
_SCORE_MAX = Decimal("100")
_EQUAL_WEIGHTS = (
    Decimal("1") / Decimal("3"),
    Decimal("1") / Decimal("3"),
    Decimal("1") / Decimal("3"),
)
_WEIGHT_SUM_TOLERANCE = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class ESGScore:
    """A single ESG rating observation for one asset.

    Attributes:
        asset_id: Identifier of the asset this rating concerns.
        reference_period: Unix timestamp of the period this rating describes.
        release_date: Unix timestamp this rating became available.
        environmental_score: 0-100 environmental sub-score.
        social_score: 0-100 social sub-score.
        governance_score: 0-100 governance sub-score.
        source: Vendor and reliability information.
    """

    asset_id: str
    reference_period: float
    release_date: float
    environmental_score: Decimal
    social_score: Decimal
    governance_score: Decimal
    source: DataProvenance

    def __post_init__(self) -> None:
        if self.release_date < self.reference_period:
            raise AltDataInputError("release_date cannot be before reference_period.")
        for name, score in (
            ("environmental_score", self.environmental_score),
            ("social_score", self.social_score),
            ("governance_score", self.governance_score),
        ):
            if not (_SCORE_MIN <= score <= _SCORE_MAX):
                raise AltDataInputError(f"{name} must be between 0 and 100, got {score}.")


def composite_score(
    esg: ESGScore, weights: tuple[Decimal, Decimal, Decimal] = _EQUAL_WEIGHTS
) -> Decimal:
    """Computes a weighted composite of the three ESG sub-scores.

    Args:
        esg: The score to combine.
        weights: (environmental, social, governance) weights, must sum to 1.
            Defaults to equal weighting.

    Raises:
        AltDataInputError: If weights do not sum to 1 (within a small tolerance).
    """
    e_weight, s_weight, g_weight = weights
    total_weight = e_weight + s_weight + g_weight
    if abs(total_weight - Decimal("1")) > _WEIGHT_SUM_TOLERANCE:
        raise AltDataInputError(f"weights must sum to 1, got {total_weight}.")

    return (
        esg.environmental_score * e_weight
        + esg.social_score * s_weight
        + esg.governance_score * g_weight
    )
