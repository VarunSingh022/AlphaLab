"""Economic indicators with point-in-time correctness.

Economic data is revised after initial release -- using today's revised GDP figure
to evaluate a strategy decision made before that revision existed is a textbook
look-ahead bias source. IndicatorObservation separates `reference_period` (what
period the value describes) from `release_date` (when the value became publicly
known), and `known_as_of` lets a caller ask "what was actually known at this point
in time" instead of unintentionally querying the present.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.macro.enums import Frequency
from alphalab.macro.exceptions import MacroInputError


@dataclass(frozen=True, slots=True)
class IndicatorMetadata:
    """Identity and classification of an economic indicator.

    Attributes:
        indicator_id: Stable identifier, e.g. "US_CPI_YOY".
        name: Human-readable name.
        country: ISO-3166-alpha2-style country or region code, e.g. "US", "IN".
        frequency: How often this indicator is reported.
        units: Unit of the reported value, e.g. "%", "index", "USD billions".
        seasonally_adjusted: Whether reported values have seasonal adjustment
            applied.
    """

    indicator_id: str
    name: str
    country: str
    frequency: Frequency
    units: str
    seasonally_adjusted: bool = True


@dataclass(frozen=True, slots=True)
class IndicatorObservation:
    """A single reported value of an economic indicator.

    Attributes:
        indicator_id: Identifier of the indicator this observation belongs to.
        reference_period: Unix timestamp representing the period this value
            describes, e.g. the first day of the month for a monthly CPI reading.
        release_date: Unix timestamp this value became publicly known. Must be
            on or after reference_period.
        value: The reported value.
        is_revision: True if this observation revises an earlier release for the
            same reference_period. Both the original and every revision should be
            kept in the same series -- point-in-time queries need the original for
            dates before a later revision's release_date.
        consensus_estimate: The economist consensus/forecast value ahead of
            release, if known. Used to compute `surprise`.
    """

    indicator_id: str
    reference_period: float
    release_date: float
    value: Decimal
    is_revision: bool = False
    consensus_estimate: Decimal | None = None

    def __post_init__(self) -> None:
        if self.release_date < self.reference_period:
            raise MacroInputError(
                "release_date cannot be before reference_period -- a value cannot "
                "be known before the period it describes has occurred."
            )


def surprise(observation: IndicatorObservation) -> Decimal | None:
    """Computes actual minus consensus. Returns None if no consensus was recorded."""
    if observation.consensus_estimate is None:
        return None
    return observation.value - observation.consensus_estimate


def known_as_of(
    observations: tuple[IndicatorObservation, ...], as_of: float
) -> IndicatorObservation | None:
    """Returns the observation reflecting what was actually known at `as_of`.

    Filters to observations released on or before `as_of`, then prefers the most
    recent reference_period (the most current economic picture), and within that
    period, the most recent release (the latest revision known by that date).
    Returns None if nothing had been released yet by `as_of`.
    """
    known = tuple(obs for obs in observations if obs.release_date <= as_of)
    if not known:
        return None
    return max(known, key=lambda obs: (obs.reference_period, obs.release_date))
