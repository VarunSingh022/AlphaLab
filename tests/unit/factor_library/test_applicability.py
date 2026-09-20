"""Multi-asset applicability: what each class carries, and what it does not.

Phase 13 of the v3.2 roadmap requires that a feature which is not meaningful
for an asset class says so explicitly rather than being forced into "one fake
universal formula". These tests hold the two tables to that, and the second one
is the case that matters: an interest rate has a close, so a naive
implementation computes a percentage change on it happily and produces a number
that means nothing.
"""

import pytest

from alphalab.data.symbols import DataAssetClass
from alphalab.factor_library import (
    FIELD_AVAILABILITY,
    MULTIPLICATIVE_CLASSES,
    RATIO_KINDS,
    FactorInputError,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureScope,
    Verdict,
    feature_applicability,
    require_applicable,
)

#: The ratio kinds in enum-definition order, which is deterministic. ``sorted``
#: with a key is avoided because an Enum has no natural order and the typed
#: overload of ``sorted`` widens its element type in consequence.
RATIO_KIND_CASES = [kind for kind in FeatureKind if kind in RATIO_KINDS]


def _definition(kind: FeatureKind, field: FeatureField) -> FeatureDefinition:
    from alphalab.factor_library.definition import KIND_REQUIREMENTS

    requirement = KIND_REQUIREMENTS[kind]
    parameters = {"long_window": 10.0} if "long_window" in requirement.required_parameters else {}
    return FeatureDefinition(
        feature_id=kind.name.lower(),
        kind=kind,
        source_field=field,
        window=5 if requirement.needs_window else None,
        parameters=parameters,
        scope=requirement.scope,
    )


def test_every_asset_class_declares_its_fields() -> None:
    """A class missing from the table would crash rather than answer."""

    assert set(FIELD_AVAILABILITY) == set(DataAssetClass)


def test_every_asset_class_carries_at_least_one_field() -> None:
    for asset_class in DataAssetClass:
        assert FIELD_AVAILABILITY[asset_class], (
            f"{asset_class.name} carries no readable field at all"
        )


def test_a_volume_feature_does_not_apply_to_an_index_or_a_rate() -> None:
    """An index is a published level and a rate is a quote; neither trades."""

    definition = _definition(FeatureKind.ROLLING_MEAN, FeatureField.VOLUME)

    for asset_class in (DataAssetClass.INDEX, DataAssetClass.RATE, DataAssetClass.FOREX):
        verdict = feature_applicability(definition, asset_class)
        assert verdict.verdict is Verdict.NO_SUCH_FIELD
        assert "carries no VOLUME" in verdict.reason
        assert not verdict.applies


def test_a_volume_feature_applies_to_an_equity() -> None:
    definition = _definition(FeatureKind.ROLLING_MEAN, FeatureField.VOLUME)
    assert feature_applicability(definition, DataAssetClass.EQUITY).applies


@pytest.mark.parametrize("kind", RATIO_KIND_CASES)
def test_no_ratio_kind_applies_to_a_rate(kind: FeatureKind) -> None:
    """The case a naive implementation gets wrong and still produces a number.

    A rate is quoted in percent, can be zero and can be negative, so
    ``rate[t] / rate[t-1] - 1`` is arithmetic that runs and a quantity that
    means nothing.
    """

    verdict = feature_applicability(_definition(kind, FeatureField.CLOSE), DataAssetClass.RATE)

    assert verdict.verdict is Verdict.NOT_RATIO_SCALED
    assert "not ratio-scaled" in verdict.reason
    assert "ROLLING_MEAN" in verdict.reason, "the reason names what to use instead"


@pytest.mark.parametrize("kind", RATIO_KIND_CASES)
def test_every_ratio_kind_applies_to_an_equity(kind: FeatureKind) -> None:
    verdict = feature_applicability(_definition(kind, FeatureField.CLOSE), DataAssetClass.EQUITY)
    assert verdict.applies


def test_a_dispersion_kind_applies_to_a_rate_because_it_takes_a_difference() -> None:
    """The escape hatch the refusal points at must itself be applicable."""

    for kind in (FeatureKind.ROLLING_MEAN, FeatureKind.ROLLING_STD, FeatureKind.ROLLING_ZSCORE):
        assert feature_applicability(
            _definition(kind, FeatureField.CLOSE), DataAssetClass.RATE
        ).applies


def test_the_non_market_classes_carry_only_an_observation() -> None:
    for asset_class in (
        DataAssetClass.FUNDAMENTAL,
        DataAssetClass.ECONOMIC,
        DataAssetClass.ALTERNATIVE,
    ):
        assert FIELD_AVAILABILITY[asset_class] == frozenset({FeatureField.OBSERVATION})
        assert not feature_applicability(
            _definition(FeatureKind.ROLLING_MEAN, FeatureField.CLOSE), asset_class
        ).applies


def test_an_economic_series_is_not_ratio_scaled_either() -> None:
    """An unemployment rate or a net change has no meaningful percentage change."""

    definition = _definition(FeatureKind.RETURN, FeatureField.OBSERVATION)
    verdict = feature_applicability(definition, DataAssetClass.ECONOMIC)

    assert verdict.verdict is Verdict.NOT_RATIO_SCALED


def test_crypto_and_fx_are_ratio_scaled_and_equities_are_too() -> None:
    assert DataAssetClass.CRYPTO in MULTIPLICATIVE_CLASSES
    assert DataAssetClass.FOREX in MULTIPLICATIVE_CLASSES
    assert DataAssetClass.EQUITY in MULTIPLICATIVE_CLASSES
    assert DataAssetClass.RATE not in MULTIPLICATIVE_CLASSES


def test_an_applicable_verdict_still_carries_a_reason() -> None:
    """A recorded "why it applies" is auditable in a way a bare True is not."""

    verdict = feature_applicability(
        _definition(FeatureKind.MOMENTUM, FeatureField.CLOSE), DataAssetClass.ETF
    )

    assert verdict.applies
    assert verdict.reason
    assert "ETF" in verdict.reason


def test_the_gate_form_raises_and_carries_the_same_reason() -> None:
    definition = _definition(FeatureKind.RETURN, FeatureField.CLOSE)

    require_applicable(definition, DataAssetClass.EQUITY)
    with pytest.raises(FactorInputError, match="not ratio-scaled"):
        require_applicable(definition, DataAssetClass.RATE)


def test_applicability_is_advice_and_does_not_block_a_computation() -> None:
    """The computation layer refuses what it cannot compute; this layer judges.

    A library that silently blocked a defined computation would be making a
    research decision on the caller's behalf, so the two are kept apart.
    """

    from alphalab.data.feed import Bar as WireBar
    from alphalab.factor_library import compute_feature, observations_from_records

    definition = _definition(FeatureKind.RETURN, FeatureField.CLOSE)
    assert not feature_applicability(definition, DataAssetClass.RATE).applies

    rate_like = [
        WireBar("US10Y", 1_735_689_600.0 + index * 86400.0, v, v, v, v, 0.0)
        for index, v in enumerate([4.2, 4.3, 4.1, 4.4, 4.5, 4.6, 4.7])
    ]
    frame = observations_from_records(rate_like, FeatureField.CLOSE, "UTC", "ds@v1")

    series = compute_feature(definition, frame)[0]
    assert len(series) > 0, "the arithmetic still runs; applicability is the judgement"


def test_a_cross_sectional_kind_is_applicable_wherever_its_field_is() -> None:
    definition = FeatureDefinition(
        "rank",
        FeatureKind.CROSS_SECTIONAL_RANK,
        FeatureField.OBSERVATION,
        scope=FeatureScope.CROSS_SECTIONAL,
    )

    assert feature_applicability(definition, DataAssetClass.FUNDAMENTAL).applies
    assert not feature_applicability(definition, DataAssetClass.EQUITY).applies
