"""The v3.2 feature framework: definitions, identity, computation and lineage.

Four things are established here, and they are the four the release's success
criteria name for features.

**A definition refuses to be ambiguous.** Every way of building one that would
leave the computation under-specified -- a missing window, a window on a kind
that takes none, a parameter the kind does not read -- raises, with the
alternative named in the message.

**Identity is derived and sensitive.** Two identically-described features have
the same version in any process; changing anything that changes the computation
changes the version. The canonical rendering is pinned so a later refactor
cannot quietly re-identify every feature in existence.

**The arithmetic is right.** Each kind is checked against a value worked out by
hand on a short series, not against itself.

**Warmup is exact.** The first index a kind can produce a value for is asserted
per kind, because an off-by-one there is a look-ahead bug that produces
entirely plausible numbers.
"""

import math
import re

import pytest

from alphalab.data.feed import Bar as WireBar
from alphalab.data.feed import CorporateAction, FundamentalRecord, Quote, Trade
from alphalab.factor_library import (
    KIND_REQUIREMENTS,
    FactorInputError,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeaturePanel,
    FeatureScope,
    FeatureSeries,
    MissingPolicy,
    ObservationFrame,
    canonical_feature_key,
    compute_feature,
    compute_features,
    compute_panel,
    derive_feature_version,
    observations_from_price_series,
    observations_from_records,
    to_factor_results,
)
from alphalab.factor_library.exceptions import FactorComputationError
from alphalab.feature_store import FeatureValueAdapter, FeatureValueProtocol

DAY = 86400.0
START = 1_735_689_600.0  # 2025-01-01T00:00:00Z


def _bars(symbol: str, closes: list[float], volumes: list[float] | None = None) -> list[WireBar]:
    sizes = volumes if volumes is not None else [1000.0] * len(closes)
    return [
        WireBar(
            symbol=symbol,
            timestamp=START + index * DAY,
            open=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            volume=volume,
        )
        for index, (close, volume) in enumerate(zip(closes, sizes, strict=True))
    ]


def _frame(
    closes: list[float], symbol: str = "AAA", field: FeatureField = FeatureField.CLOSE
) -> ObservationFrame:
    return observations_from_records(_bars(symbol, closes), field, "UTC", "ds@v1")


def _series(definition: FeatureDefinition, closes: list[float]) -> FeatureSeries:
    return compute_feature(definition, _frame(closes))[0]


# --------------------------------------------------------------------------- #
# Definitions refuse ambiguity
# --------------------------------------------------------------------------- #


def test_a_kind_that_needs_a_window_refuses_to_default_one() -> None:
    with pytest.raises(FactorInputError, match="no default"):
        FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE)


def test_a_kind_that_takes_no_window_refuses_one() -> None:
    """Accepting and ignoring it would leave the caller believing they set it."""

    with pytest.raises(FactorInputError, match="accepted and ignored"):
        FeatureDefinition("t", FeatureKind.TIME_OF_DAY, FeatureField.CLOSE, window=5)


def test_a_window_below_the_kinds_minimum_is_refused() -> None:
    with pytest.raises(FactorInputError, match="at least 2"):
        FeatureDefinition("s", FeatureKind.ROLLING_STD, FeatureField.CLOSE, window=1)


def test_an_unread_parameter_is_refused_rather_than_ignored() -> None:
    """It would change the derived version, giving one computation two identities."""

    with pytest.raises(FactorInputError, match="does not read the parameter"):
        FeatureDefinition(
            "m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=5, parameters={"typo": 1.0}
        )


def test_a_required_parameter_must_be_supplied() -> None:
    with pytest.raises(FactorInputError, match="long_window"):
        FeatureDefinition("r", FeatureKind.VOLATILITY_REGIME, FeatureField.CLOSE, window=5)


def test_a_volatility_regime_needs_its_long_window_to_be_longer() -> None:
    with pytest.raises(FactorInputError, match="exceed its short one"):
        FeatureDefinition(
            "r",
            FeatureKind.VOLATILITY_REGIME,
            FeatureField.CLOSE,
            window=10,
            parameters={"long_window": 10.0},
        )


def test_the_declared_scope_may_not_contradict_the_kind() -> None:
    with pytest.raises(FactorInputError, match="not the caller's to override"):
        FeatureDefinition(
            "x",
            FeatureKind.CROSS_SECTIONAL_RANK,
            FeatureField.CLOSE,
            scope=FeatureScope.TIME_SERIES,
        )


def test_an_empty_or_at_bearing_name_is_refused() -> None:
    with pytest.raises(FactorInputError, match="must be named"):
        FeatureDefinition("  ", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)
    with pytest.raises(FactorInputError, match="may not contain '@'"):
        FeatureDefinition("a@b", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)


def test_parameters_are_genuinely_immutable() -> None:
    """The ADR-0034 idiom: copied, then wrapped, so neither route mutates it."""

    supplied = {"skip_periods": 2.0}
    definition = FeatureDefinition(
        "m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=5, parameters=supplied
    )

    supplied["skip_periods"] = 99.0
    assert definition.parameters["skip_periods"] == 2.0
    with pytest.raises(TypeError):
        definition.parameters["skip_periods"] = 99.0  # type: ignore[index]


def test_every_feature_kind_appears_in_the_requirements_table() -> None:
    """The table is what validation and warmup both read; a gap is a crash."""

    assert set(KIND_REQUIREMENTS) == set(FeatureKind)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_the_same_description_derives_the_same_version() -> None:
    first = FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20)
    second = FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20)

    assert first.feature_version == second.feature_version
    assert first.feature_version.startswith("m@")


@pytest.mark.parametrize(
    "changed",
    [
        {"feature_id": "other"},
        {"kind": FeatureKind.ROLLING_MEAN},
        {"source_field": FeatureField.OPEN},
        {"window": 21},
        {"missing": MissingPolicy.REFUSE},
        {"parameters": {"skip_periods": 1.0}},
    ],
)
def test_anything_that_changes_the_computation_changes_the_version(
    changed: dict[str, object],
) -> None:
    base: dict[str, object] = {
        "feature_id": "m",
        "kind": FeatureKind.MOMENTUM,
        "source_field": FeatureField.CLOSE,
        "window": 20,
    }
    original = FeatureDefinition(**base)  # type: ignore[arg-type]
    modified = FeatureDefinition(**{**base, **changed})  # type: ignore[arg-type]

    assert modified.feature_version != original.feature_version


def test_the_canonical_key_rendering_is_pinned() -> None:
    """Changing this re-identifies every feature in existence; it needs an ADR."""

    definition = FeatureDefinition(
        "mom_20",
        FeatureKind.MOMENTUM,
        FeatureField.CLOSE,
        window=20,
        parameters={"skip_periods": 1.0},
    )

    assert canonical_feature_key(definition) == "\n".join(
        [
            "alphalab.feature.v1",
            "feature_id=mom_20",
            "kind=MOMENTUM",
            "field=CLOSE",
            "window=20",
            "missing=SKIP",
            "scope=TIME_SERIES",
            "parameters",
            "skip_periods=1.0",
        ]
    )
    assert definition.feature_version == f"mom_20@{_sha256(canonical_feature_key(definition))}"


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_derive_feature_version_is_the_property() -> None:
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=3)
    assert derive_feature_version(definition) == definition.feature_version


# --------------------------------------------------------------------------- #
# The arithmetic of each kind
# --------------------------------------------------------------------------- #


def test_return_is_the_simple_ratio_over_the_window() -> None:
    definition = FeatureDefinition("r", FeatureKind.RETURN, FeatureField.CLOSE, window=2)
    series = _series(definition, [100.0, 110.0, 120.0, 150.0])

    assert series.values == (120.0 / 100.0 - 1.0, 150.0 / 110.0 - 1.0)
    assert series.warmup_periods == 2


def test_log_return_is_the_logarithm_of_the_same_ratio() -> None:
    definition = FeatureDefinition("l", FeatureKind.LOG_RETURN, FeatureField.CLOSE, window=1)
    series = _series(definition, [100.0, 110.0, 121.0])

    assert series.values == (math.log(1.1), math.log(1.1))


def test_rolling_mean_min_max_and_sum_read_the_trailing_window() -> None:
    closes = [1.0, 2.0, 3.0, 10.0]
    cases = {
        FeatureKind.ROLLING_MEAN: (2.0, 5.0),
        FeatureKind.ROLLING_MIN: (1.0, 2.0),
        FeatureKind.ROLLING_MAX: (3.0, 10.0),
        FeatureKind.ROLLING_SUM: (6.0, 15.0),
    }
    for kind, expected in cases.items():
        series = _series(FeatureDefinition(kind.name, kind, FeatureField.CLOSE, window=3), closes)
        assert series.values == expected, kind
        assert series.warmup_periods == 2, kind


def test_rolling_std_uses_the_shared_unbiased_estimator() -> None:
    from alphalab.common.statistics import sample_variance

    closes = [1.0, 2.0, 3.0, 10.0]
    series = _series(
        FeatureDefinition("s", FeatureKind.ROLLING_STD, FeatureField.CLOSE, window=3), closes
    )

    assert series.values[0] == math.sqrt(sample_variance([1.0, 2.0, 3.0]))
    assert series.values[1] == math.sqrt(sample_variance([2.0, 3.0, 10.0]))


def test_momentum_skips_the_most_recent_periods_when_asked() -> None:
    """The standard 12-1 construction, checked on a short series."""

    closes = [10.0, 20.0, 30.0, 40.0, 50.0]
    plain = _series(
        FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=2), closes
    )
    skipped = _series(
        FeatureDefinition(
            "m",
            FeatureKind.MOMENTUM,
            FeatureField.CLOSE,
            window=2,
            parameters={"skip_periods": 1.0},
        ),
        closes,
    )

    assert plain.values == (30.0 / 10.0 - 1.0, 40.0 / 20.0 - 1.0, 50.0 / 30.0 - 1.0)

    # At index 3 the skipped form measures values[2] / values[0], not
    # values[3] / values[0]: the most recent observation is excluded from both
    # ends of the window, which is what "skip" means and what makes a 12-1
    # momentum different from a 12-0 one.
    assert skipped.warmup_periods == 3
    assert skipped.values == (30.0 / 10.0 - 1.0, 40.0 / 20.0 - 1.0)
    assert skipped.values[0] != plain.values[1], "skipping must change the value"


def test_mean_reversion_is_the_negated_rolling_z_score() -> None:
    closes = [1.0, 2.0, 9.0, 4.0, 5.0]
    z = _series(
        FeatureDefinition("z", FeatureKind.ROLLING_ZSCORE, FeatureField.CLOSE, window=3), closes
    )
    reversion = _series(
        FeatureDefinition("v", FeatureKind.MEAN_REVERSION, FeatureField.CLOSE, window=3), closes
    )

    assert reversion.values == tuple(-value for value in z.values)


def test_rolling_ratio_compares_the_value_with_its_own_trailing_mean() -> None:
    volumes = [100.0, 100.0, 400.0]
    frame = observations_from_records(
        _bars("AAA", [1.0, 1.0, 1.0], volumes), FeatureField.VOLUME, "UTC", "ds@v1"
    )
    series = compute_feature(
        FeatureDefinition("v", FeatureKind.ROLLING_RATIO, FeatureField.VOLUME, window=3), frame
    )[0]

    assert series.values == (400.0 / 200.0,)


def test_realized_volatility_annualizes_the_window_returns() -> None:
    from alphalab.common.statistics import sample_variance

    closes = [100.0, 110.0, 99.0, 108.9]
    series = _series(
        FeatureDefinition("v", FeatureKind.REALIZED_VOLATILITY, FeatureField.CLOSE, window=3),
        closes,
    )
    window_returns = [110.0 / 100.0 - 1.0, 99.0 / 110.0 - 1.0, 108.9 / 99.0 - 1.0]

    assert series.warmup_periods == 3
    assert series.values[0] == pytest.approx(
        math.sqrt(sample_variance(window_returns)) * math.sqrt(252.0)
    )


def test_exponential_mean_is_seeded_with_the_first_windows_simple_mean() -> None:
    """Seeding with values[0] would leave the whole series dependent on one point."""

    closes = [1.0, 3.0, 5.0, 7.0]
    series = _series(
        FeatureDefinition("e", FeatureKind.EXPONENTIAL_MEAN, FeatureField.CLOSE, window=2),
        closes,
    )
    alpha = 2.0 / 3.0

    assert series.values[0] == 2.0
    assert series.values[1] == pytest.approx(alpha * 5.0 + (1 - alpha) * 2.0)


def test_volatility_regime_rises_when_short_term_volatility_rises() -> None:
    """A calm stretch followed by a violent one must push the ratio above 1.

    The property worth asserting is directional rather than an exact float: the
    ratio's job is to say "recent volatility is high relative to the usual",
    and a test pinning one number would pass for an implementation that
    computed the two windows the wrong way round.
    """

    calm = [100.0, 100.5, 100.0, 100.5, 100.0, 100.5, 100.0]
    violent = [110.0, 95.0, 115.0]
    definition = FeatureDefinition(
        "r",
        FeatureKind.VOLATILITY_REGIME,
        FeatureField.CLOSE,
        window=3,
        parameters={"long_window": 8.0},
    )
    disturbed = _series(definition, calm + violent)
    steady = _series(definition, [*calm, 100.0, 100.5, 100.0])

    assert disturbed.warmup_periods == 8
    assert len(disturbed) == 2
    assert all(value > 1.0 for value in disturbed.values), (
        "a short window inside the violent stretch must read above its own history"
    )
    assert all(value < 1.5 for value in steady.values), (
        "a series whose volatility never changes must not report a regime shift"
    )
    assert min(disturbed.values) > max(steady.values)


def test_calendar_features_read_the_declared_zone_not_the_machine() -> None:
    """16:00Z is 11:00 in New York, and the feature must say 11:00."""

    bars = [
        WireBar("AAA", 1_735_747_200.0, 1.0, 1.0, 1.0, 1.0, 1.0)  # 2025-01-01T16:00:00Z
    ]
    new_york = observations_from_records(bars, FeatureField.CLOSE, "America/New_York", "ds@v1")
    utc = observations_from_records(bars, FeatureField.CLOSE, "UTC", "ds@v1")

    definition = FeatureDefinition("t", FeatureKind.TIME_OF_DAY, FeatureField.CLOSE)

    assert compute_feature(definition, new_york)[0].values == (11.0 * 3600.0,)
    assert compute_feature(definition, utc)[0].values == (16.0 * 3600.0,)


def test_day_of_week_is_monday_zero() -> None:
    """2025-01-01 is a Wednesday."""

    frame = observations_from_records(
        [WireBar("AAA", 1_735_747_200.0, 1.0, 1.0, 1.0, 1.0, 1.0)],
        FeatureField.CLOSE,
        "UTC",
        "ds@v1",
    )
    definition = FeatureDefinition("d", FeatureKind.DAY_OF_WEEK, FeatureField.CLOSE)

    assert compute_feature(definition, frame)[0].values == (2.0,)


# --------------------------------------------------------------------------- #
# Undefined values raise rather than being substituted
# --------------------------------------------------------------------------- #


def test_a_return_off_a_non_positive_base_is_refused() -> None:
    definition = FeatureDefinition("r", FeatureKind.RETURN, FeatureField.CLOSE, window=1)
    with pytest.raises(FactorComputationError, match=re.escape("base value is 0.0")):
        _series(definition, [0.0, 10.0])


def test_a_z_score_over_a_constant_window_is_refused() -> None:
    definition = FeatureDefinition("z", FeatureKind.ROLLING_ZSCORE, FeatureField.CLOSE, window=3)
    with pytest.raises(FactorComputationError, match="no dispersion"):
        _series(definition, [5.0, 5.0, 5.0])


def test_a_ratio_over_a_zero_mean_is_refused() -> None:
    frame = observations_from_records(
        _bars("AAA", [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]), FeatureField.VOLUME, "UTC", "ds@v1"
    )
    definition = FeatureDefinition("v", FeatureKind.ROLLING_RATIO, FeatureField.VOLUME, window=3)
    with pytest.raises(FactorComputationError, match="mean is zero"):
        compute_feature(definition, frame)


# --------------------------------------------------------------------------- #
# Missing data is skipped or refused, never filled
# --------------------------------------------------------------------------- #


def test_skip_produces_a_shorter_series_that_reports_its_own_warmup() -> None:
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=4)
    series = _series(definition, [1.0, 2.0, 3.0, 4.0, 5.0])

    assert len(series) == 2
    assert series.observations == 5
    assert series.warmup_periods == 3
    assert series.timestamps == (START + 3 * DAY, START + 4 * DAY)


def test_refuse_raises_when_a_symbol_has_less_history_than_the_warmup() -> None:
    definition = FeatureDefinition(
        "m",
        FeatureKind.ROLLING_MEAN,
        FeatureField.CLOSE,
        window=10,
        missing=MissingPolicy.REFUSE,
    )
    with pytest.raises(FactorInputError, match="produces no value at all"):
        _series(definition, [1.0, 2.0, 3.0])


def test_a_symbol_with_no_usable_history_still_gets_an_empty_series_under_skip() -> None:
    """So a caller can tell "no history" from "not in the universe"."""

    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=10)
    series = _series(definition, [1.0, 2.0, 3.0])

    assert len(series) == 0
    assert series.observations == 3
    assert series.warmup_periods == 3


# --------------------------------------------------------------------------- #
# Observation frames
# --------------------------------------------------------------------------- #


def test_a_field_a_record_type_does_not_carry_is_refused_by_name() -> None:
    records = [FundamentalRecord("AAA", START, "revenue", 5.0)]
    with pytest.raises(FactorInputError, match="has no CLOSE field"):
        observations_from_records(records, FeatureField.CLOSE, "UTC", "ds@v1")


def test_a_record_type_with_no_numeric_observation_is_refused() -> None:
    records = [CorporateAction("AAA", START, "SPLIT", "2:1")]
    with pytest.raises(FactorInputError, match="carries no numeric observation"):
        observations_from_records(records, FeatureField.OBSERVATION, "UTC", "ds@v1")


def test_a_quote_derives_its_mid_and_does_not_store_one() -> None:
    records = [Quote("AAA", START, bid=10.0, ask=12.0, bid_size=1.0, ask_size=1.0)]
    frame = observations_from_records(records, FeatureField.MID, "UTC", "ds@v1")

    assert frame.series["AAA"].values == (11.0,)


def test_two_record_types_for_one_symbol_are_ambiguous_and_refused() -> None:
    records = [
        WireBar("AAA", START, 1.0, 1.0, 1.0, 1.0, 1.0),
        Trade("AAA", START + DAY, price=1.0, size=1.0),
    ]
    with pytest.raises(FactorInputError, match="which series a feature should read"):
        observations_from_records(records, FeatureField.CLOSE, "UTC", "ds@v1")


def test_two_records_at_one_instant_are_refused() -> None:
    records = [
        WireBar("AAA", START, 1.0, 1.0, 1.0, 1.0, 1.0),
        WireBar("AAA", START, 2.0, 2.0, 2.0, 2.0, 2.0),
    ]
    with pytest.raises(FactorInputError, match="DuplicatePolicy"):
        observations_from_records(records, FeatureField.CLOSE, "UTC", "ds@v1")


def test_records_are_read_in_chronological_order_whatever_order_they_arrive_in() -> None:
    bars = _bars("AAA", [1.0, 2.0, 3.0])
    shuffled = [bars[2], bars[0], bars[1]]
    frame = observations_from_records(shuffled, FeatureField.CLOSE, "UTC", "ds@v1")

    assert frame.series["AAA"].values == (1.0, 2.0, 3.0)


def test_the_price_series_bridge_reaches_the_same_arithmetic() -> None:
    """The v2 input shape is connected, not replaced."""

    from decimal import Decimal

    from alphalab.factor_library import PriceSeries
    from alphalab.market.bar import Bar as DomainBar
    from alphalab.market.bar import TimeFrame

    domain = PriceSeries(
        asset_id="AAA",
        bars=tuple(
            DomainBar(
                asset_id="AAA",
                timestamp=START + index * DAY,
                open=Decimal(value),
                high=Decimal(value),
                low=Decimal(value),
                close=Decimal(value),
                volume=Decimal("1000"),
                vwap=Decimal(value),
                trade_count=1,
                timeframe=TimeFrame.D1,
            )
            for index, value in enumerate([100, 110, 120])
        ),
    )
    frame = observations_from_price_series(domain, FeatureField.CLOSE, "UTC")

    assert frame.series["AAA"].values == (100.0, 110.0, 120.0)
    assert frame.dataset_version is None, "a PriceSeries records no provenance"


# --------------------------------------------------------------------------- #
# Lineage
# --------------------------------------------------------------------------- #


def test_a_series_names_the_dataset_the_definition_and_the_symbol() -> None:
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)
    series = _series(definition, [1.0, 2.0, 3.0])

    assert series.is_traceable
    assert series.require_lineage() == series.lineage_id
    assert series.feature_version == definition.feature_version
    assert series.dataset_version == "ds@v1"


def test_the_same_inputs_derive_the_same_lineage_id() -> None:
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)

    first = _series(definition, [1.0, 2.0, 3.0])
    second = _series(definition, [1.0, 2.0, 3.0])

    assert first.lineage_id == second.lineage_id


def test_a_different_dataset_or_definition_changes_the_lineage_id() -> None:
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)
    other = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=3)

    base = _series(definition, [1.0, 2.0, 3.0])
    changed_definition = _series(other, [1.0, 2.0, 3.0])
    changed_data = compute_feature(
        definition,
        observations_from_records(
            _bars("AAA", [1.0, 2.0, 3.0]), FeatureField.CLOSE, "UTC", "ds@v2"
        ),
    )[0]

    assert base.lineage_id != changed_definition.lineage_id
    assert base.lineage_id != changed_data.lineage_id


def test_an_untraceable_dataset_produces_no_lineage_and_says_so() -> None:
    """The ``require_provenance`` rule, one layer up."""

    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)
    frame = observations_from_records(
        _bars("AAA", [1.0, 2.0, 3.0]), FeatureField.CLOSE, "UTC", None
    )
    series = compute_feature(definition, frame)[0]

    assert not series.is_traceable
    assert series.lineage_id is None
    with pytest.raises(FactorInputError, match="carries no provenance"):
        series.require_lineage()


# --------------------------------------------------------------------------- #
# Panels and the Feature Store seam
# --------------------------------------------------------------------------- #


def test_a_panel_indexes_a_universe_by_instant() -> None:
    records = _bars("AAA", [1.0, 2.0, 3.0]) + _bars("BBB", [10.0, 20.0, 30.0])
    frame = observations_from_records(records, FeatureField.CLOSE, "UTC", "ds@v1")
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)

    panel = compute_panel(definition, frame)

    assert panel.symbols == ("AAA", "BBB")
    assert panel.cross_section(START + DAY) == {"AAA": 1.5, "BBB": 15.0}
    assert panel.cross_section(START) == {}, "the warmup instant holds nothing"


def test_a_panel_refuses_to_mix_two_definitions() -> None:
    frame = _frame([1.0, 2.0, 3.0])
    one = compute_feature(
        FeatureDefinition("a", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2), frame
    )[0]
    two = compute_feature(
        FeatureDefinition("b", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2), frame
    )[0]

    with pytest.raises(FactorInputError, match="A panel holds one feature"):
        FeaturePanel.of([one, two])


def test_a_panel_refuses_series_from_two_datasets() -> None:
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)
    first = compute_feature(definition, _frame([1.0, 2.0, 3.0], "AAA"))[0]
    second = compute_feature(
        definition,
        observations_from_records(
            _bars("BBB", [1.0, 2.0, 3.0]), FeatureField.CLOSE, "UTC", "ds@v2"
        ),
    )[0]

    with pytest.raises(FactorInputError, match="must come from one dataset version"):
        FeaturePanel.of([first, second])


def test_a_cross_sectional_feature_is_computed_instant_by_instant() -> None:
    records = _bars("AAA", [1.0, 2.0]) + _bars("BBB", [10.0, 5.0])
    frame = observations_from_records(records, FeatureField.CLOSE, "UTC", "ds@v1")
    definition = FeatureDefinition(
        "rank",
        FeatureKind.CROSS_SECTIONAL_RANK,
        FeatureField.CLOSE,
        scope=FeatureScope.CROSS_SECTIONAL,
    )

    panel = compute_panel(definition, frame)

    assert panel.cross_section(START) == {"AAA": 1.0, "BBB": 2.0}
    assert panel.cross_section(START + DAY) == {"AAA": 1.0, "BBB": 2.0}


def test_a_cross_section_of_one_is_skipped_or_refused() -> None:
    frame = _frame([1.0, 2.0])
    skip = FeatureDefinition(
        "r",
        FeatureKind.CROSS_SECTIONAL_RANK,
        FeatureField.CLOSE,
        scope=FeatureScope.CROSS_SECTIONAL,
    )
    refuse = FeatureDefinition(
        "r",
        FeatureKind.CROSS_SECTIONAL_RANK,
        FeatureField.CLOSE,
        missing=MissingPolicy.REFUSE,
        scope=FeatureScope.CROSS_SECTIONAL,
    )

    assert compute_feature(skip, frame)[0].values == ()
    with pytest.raises(FactorInputError, match="cross-section of one"):
        compute_feature(refuse, frame)


def test_a_feature_series_converts_into_feature_store_values() -> None:
    """``FactorResult`` structurally satisfies the seam; nothing imports back."""

    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)
    series = _series(definition, [1.0, 2.0, 3.0])

    results = to_factor_results(series, version=3)

    assert len(results) == 2
    written: FeatureValueProtocol = results[0]
    value = FeatureValueAdapter.to_feature_value(written)
    assert value.feature_id == "m"
    assert value.version == 3
    assert value.asset_id == "AAA"
    assert value.value == 1.5


def test_a_zero_registration_version_is_refused() -> None:
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)
    with pytest.raises(FactorInputError, match="starts at 1"):
        to_factor_results(_series(definition, [1.0, 2.0, 3.0]), version=0)


def test_computing_a_feature_over_the_wrong_field_is_refused() -> None:
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.VOLUME, window=2)
    with pytest.raises(FactorInputError, match="reads VOLUME and the frame holds CLOSE"):
        compute_feature(definition, _frame([1.0, 2.0, 3.0]))


def test_requesting_the_same_computation_twice_is_refused() -> None:
    definition = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)
    with pytest.raises(FactorInputError, match="requested twice"):
        compute_features([definition, definition], _frame([1.0, 2.0, 3.0]))


def test_several_features_are_keyed_by_derived_version_not_by_name() -> None:
    """Two definitions sharing an id cannot overwrite each other."""

    frame = _frame([1.0, 2.0, 3.0, 4.0])
    short = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=2)
    long = FeatureDefinition("m", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=3)

    computed = compute_features([short, long], frame)

    assert set(computed) == {short.feature_version, long.feature_version}
    assert len(computed) == 2
