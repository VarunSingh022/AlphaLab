"""Which features mean something for which asset classes, said out loud.

v3.2 runs on the v3.1 data foundation, and that foundation ingests twelve
:class:`~alphalab.data.symbols.DataAssetClass` values. The roadmap's
instruction for them is precise: *do not force incompatible fields into one
fake universal formula*, and *where a feature is not meaningful for an asset
class, its applicability must be explicit*.

This module is that explicitness. It answers one question --
:func:`feature_applicability` -- and it answers it with a reason rather than a
boolean, because "volume does not apply to an index" and "a simple return does
not apply to an interest rate" are true for completely different reasons and a
caller needs to know which they have hit.

Two rules, composed
-------------------

Rather than a hand-written matrix of nineteen kinds against eleven fields
against twelve classes -- which would be two and a half thousand cells, most of
them copied, and stale within a release -- applicability is the composition of
two small tables that each state one fact:

1. :data:`FIELD_AVAILABILITY` -- which observation fields an asset class has at
   all. An index is a published level with no volume and no book; a fundamental
   observation is one number with no open or close.
2. :data:`MULTIPLICATIVE_CLASSES` -- which asset classes carry a strictly
   positive, ratio-scaled level. This is the one that catches the subtle case:
   an interest rate is quoted in percent, it can be zero and it can be
   negative, so ``rate[t] / rate[t-1] - 1`` is arithmetic that runs and a
   number that means nothing. A rate's momentum is a *difference*, not a
   return, and the honest answer is that the return kinds do not apply to it.

Everything that is neither a ratio nor field-specific -- a rolling mean, a
standard deviation, a rank, a time-of-day -- applies wherever its field does,
because those are defined on any real-valued series.

This is advice, not a gate
--------------------------

Nothing here is enforced inside :func:`~alphalab.factor_library.compute.compute_feature`.
The computation layer refuses what it cannot compute -- a field a record does
not carry, a return off a non-positive base -- and that refusal is mechanical.
Applicability is a *methodological* judgement about whether a defined number is
worth reading, and a library that silently blocked a caller from computing one
would be making a research decision on their behalf. :func:`require_applicable`
exists for a caller who wants it to be a gate, and a study records the answer
either way.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from alphalab.data.symbols import DataAssetClass
from alphalab.factor_library.definition import FeatureDefinition, FeatureField, FeatureKind
from alphalab.factor_library.exceptions import FactorInputError

__all__ = [
    "FIELD_AVAILABILITY",
    "MULTIPLICATIVE_CLASSES",
    "RATIO_KINDS",
    "Applicability",
    "Verdict",
    "feature_applicability",
    "require_applicable",
]


class Verdict(Enum):
    """How a feature stands with respect to an asset class."""

    #: Defined and meaningful.
    APPLIES = auto()
    #: The asset class has no such field. The number cannot be computed at all.
    NO_SUCH_FIELD = auto()
    #: The field exists but the arithmetic does not mean what it usually means
    #: -- a ratio taken on a series that is not ratio-scaled.
    NOT_RATIO_SCALED = auto()


@dataclass(frozen=True, slots=True)
class Applicability:
    """A verdict and the sentence explaining it.

    Attributes:
        verdict: Which of the three cases this is.
        reason: One sentence a caller can put in a report. Non-empty always,
            including when the feature applies -- a result that records *why*
            something was considered applicable is auditable in a way that a
            bare ``True`` is not.
    """

    verdict: Verdict
    reason: str

    @property
    def applies(self) -> bool:
        """Whether the feature is meaningful for this asset class."""

        return self.verdict is Verdict.APPLIES


#: Which observation fields each asset class actually carries.
#:
#: FOREX and RATE have no consolidated volume: FX trades over the counter with
#: no single tape, and a quoted rate is a level rather than something that
#: changes hands. INDEX is a published number nobody trades. The three
#: non-market classes carry one unnamed value each and no OHLC at all.
FIELD_AVAILABILITY: Final[Mapping[DataAssetClass, frozenset[FeatureField]]] = {
    DataAssetClass.EQUITY: frozenset(FeatureField) - {FeatureField.OBSERVATION},
    DataAssetClass.ETF: frozenset(FeatureField) - {FeatureField.OBSERVATION},
    DataAssetClass.FUTURE: frozenset(FeatureField) - {FeatureField.OBSERVATION},
    DataAssetClass.OPTION: frozenset(FeatureField) - {FeatureField.OBSERVATION},
    DataAssetClass.CRYPTO: frozenset(FeatureField) - {FeatureField.OBSERVATION},
    DataAssetClass.COMMODITY: frozenset(FeatureField) - {FeatureField.OBSERVATION},
    DataAssetClass.FOREX: frozenset(
        {
            FeatureField.OPEN,
            FeatureField.HIGH,
            FeatureField.LOW,
            FeatureField.CLOSE,
            FeatureField.PRICE,
            FeatureField.BID,
            FeatureField.ASK,
            FeatureField.MID,
        }
    ),
    DataAssetClass.RATE: frozenset(
        {
            FeatureField.OPEN,
            FeatureField.HIGH,
            FeatureField.LOW,
            FeatureField.CLOSE,
            FeatureField.PRICE,
        }
    ),
    DataAssetClass.INDEX: frozenset(
        {
            FeatureField.OPEN,
            FeatureField.HIGH,
            FeatureField.LOW,
            FeatureField.CLOSE,
            FeatureField.PRICE,
        }
    ),
    DataAssetClass.FUNDAMENTAL: frozenset({FeatureField.OBSERVATION}),
    DataAssetClass.ECONOMIC: frozenset({FeatureField.OBSERVATION}),
    DataAssetClass.ALTERNATIVE: frozenset({FeatureField.OBSERVATION}),
}

#: Kinds whose arithmetic divides one observation by another.
#:
#: Each of these is only meaningful on a series where a ratio is meaningful --
#: where doubling the number means twice as much of the thing. See
#: :data:`MULTIPLICATIVE_CLASSES`.
RATIO_KINDS: Final[frozenset[FeatureKind]] = frozenset(
    {
        FeatureKind.RETURN,
        FeatureKind.LOG_RETURN,
        FeatureKind.MOMENTUM,
        FeatureKind.REALIZED_VOLATILITY,
        FeatureKind.VOLATILITY_REGIME,
        FeatureKind.ROLLING_RATIO,
    }
)

#: Asset classes whose price-like fields are strictly positive and ratio-scaled.
#:
#: RATE is excluded: a rate is quoted in percent, can be zero and can be
#: negative, so a percentage change in it is not a return and a "volatility of
#: returns" computed from it is not a volatility. ECONOMIC is excluded for the
#: same reason -- an unemployment rate, a net change, a diffusion index -- and
#: ALTERNATIVE because a sentiment score has an arbitrary origin and scale.
#: FUNDAMENTAL is excluded because the same column can hold revenue, which is
#: ratio-scaled, and an operating margin, which is not; the class cannot tell
#: the two apart, so the honest verdict is that it does not know.
MULTIPLICATIVE_CLASSES: Final[frozenset[DataAssetClass]] = frozenset(
    {
        DataAssetClass.EQUITY,
        DataAssetClass.ETF,
        DataAssetClass.FUTURE,
        DataAssetClass.OPTION,
        DataAssetClass.FOREX,
        DataAssetClass.CRYPTO,
        DataAssetClass.INDEX,
        DataAssetClass.COMMODITY,
    }
)


def feature_applicability(
    definition: FeatureDefinition, asset_class: DataAssetClass
) -> Applicability:
    """Whether ``definition`` means something computed on ``asset_class``.

    Never raises for an inapplicable combination: the verdict *is* the answer,
    and a caller who wants a refusal calls :func:`require_applicable`.
    """

    available = FIELD_AVAILABILITY[asset_class]

    if definition.source_field not in available:
        return Applicability(
            Verdict.NO_SUCH_FIELD,
            f"{asset_class.name} carries no {definition.source_field.name}; it offers "
            f"{sorted(field.name for field in available)}. A field an asset class does not "
            "have is not zero, it is absent.",
        )

    if definition.kind in RATIO_KINDS and asset_class not in MULTIPLICATIVE_CLASSES:
        return Applicability(
            Verdict.NOT_RATIO_SCALED,
            f"{definition.kind.name} divides one observation by another, and a "
            f"{asset_class.name} series is not ratio-scaled -- it can be zero or negative, "
            "and doubling it does not mean twice as much of anything. Measure a difference "
            "of levels instead: ROLLING_MEAN and ROLLING_STD are defined on any real series.",
        )

    return Applicability(
        Verdict.APPLIES,
        f"{definition.kind.name} on {definition.source_field.name} is defined for "
        f"{asset_class.name}.",
    )


def require_applicable(definition: FeatureDefinition, asset_class: DataAssetClass) -> None:
    """Raise unless ``definition`` is applicable to ``asset_class``.

    The gate form, for a caller who wants one. A study that runs one feature
    set across a mixed universe calls this per class and records what it
    excluded, rather than computing a number for every class and discovering
    afterwards that a third of them were meaningless.

    Raises:
        FactorInputError: If the feature does not apply, carrying the verdict's
            own reason.
    """

    verdict = feature_applicability(definition, asset_class)
    if not verdict.applies:
        raise FactorInputError(
            f"{definition.feature_id!r} does not apply to {asset_class.name}: {verdict.reason}"
        )
