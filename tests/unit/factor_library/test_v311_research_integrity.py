"""v3.11 research integrity: joint neutralization, implementation lag, delistings, HAC t.

Ledger OFE-004 (neutralize against several exposures at once, refusing an
ill-conditioned design), DAT-003 (an implementation lag, required and carried),
DAT-002 (a delisted symbol realizes its terminal return rather than vanishing)
and OFE-006 (a t-statistic on the IC that accounts for overlapping windows).
Each is checked against an answer known by construction.
"""

import math
import random

import pytest

from alphalab.data.feed import Bar as WireBar
from alphalab.factor_library import (
    MAXIMUM_EXPOSURE_CONDITION,
    Delisting,
    FactorComputationError,
    FactorInputError,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeaturePanel,
    ObservationFrame,
    delisting_set_id,
    factor_decay,
    forward_returns,
    information_coefficient,
    neutralize_beta,
    neutralize_exposures,
    observations_from_records,
)

DAY = 86400.0
START = 1_735_689_600.0


def _frame(paths: dict[str, list[float]], version: str = "ds@v1") -> ObservationFrame:
    records = [
        WireBar(symbol, START + index * DAY, close, close, close, close, 1000.0)
        for symbol, closes in paths.items()
        for index, close in enumerate(closes)
    ]
    return observations_from_records(records, FeatureField.CLOSE, "UTC", version)


def _panel_of(rows: dict[float, dict[str, float]]) -> FeaturePanel:
    definition = FeatureDefinition("f", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=1)
    return FeaturePanel(
        definition=definition,
        dataset_version="ds@v1",
        timezone_name="UTC",
        rows={stamp: dict(row) for stamp, row in sorted(rows.items())},
        symbols=tuple(sorted({asset for row in rows.values() for asset in row})),
    )


ASSETS = [f"A{index:02d}" for index in range(12)]


def _exposures(seed: int) -> tuple[dict[str, float], dict[str, float]]:
    rng = random.Random(seed)
    return (
        {asset: rng.uniform(0.5, 1.5) for asset in ASSETS},
        {asset: rng.gauss(0.0, 1.0) for asset in ASSETS},
    )


# --------------------------------------------------------------------------- #
# Joint neutralization
# --------------------------------------------------------------------------- #


def test_a_factor_built_from_its_exposures_neutralizes_to_its_noise() -> None:
    beta, size = _exposures(1)
    rng = random.Random(9)
    noise = {asset: rng.gauss(0.0, 0.1) for asset in ASSETS}
    factor = {asset: 0.3 + 2.0 * beta[asset] - 1.5 * size[asset] + noise[asset] for asset in ASSETS}
    report = neutralize_exposures(
        _panel_of({START: factor}),
        {"beta": {START: beta}, "size": {START: size}},
        minimum_assets=4,
    )
    residual = report.panel.cross_section(START)
    # The residual is the part of the noise the exposures cannot explain: it is
    # orthogonal to the intercept and to both exposures.
    for exposure in (None, beta, size):
        dot = math.fsum(
            residual[asset] * (1.0 if exposure is None else exposure[asset]) for asset in ASSETS
        )
        assert abs(dot) < 1e-12
    assert report.instants_neutralized == 1
    assert report.refusals == ()
    assert "neutralize_exposures(exposures=beta,size" in report.panel.lineage


def test_one_exposure_is_beta_neutralization_computed_by_qr() -> None:
    beta, _ = _exposures(2)
    rng = random.Random(3)
    factor = {asset: rng.gauss(0.0, 1.0) + beta[asset] for asset in ASSETS}
    panel = _panel_of({START: factor})
    joint = neutralize_exposures(panel, {"beta": {START: beta}}, minimum_assets=3)
    single = neutralize_beta(panel, {START: beta})
    for asset in ASSETS:
        assert joint.panel.cross_section(START)[asset] == pytest.approx(
            single.panel.cross_section(START)[asset], abs=1e-12
        )


def test_joint_neutralization_removes_what_sequential_passes_leave_behind() -> None:
    """Neutralizing against correlated exposures in turn is not a joint fit."""

    rng = random.Random(4)
    first = {asset: rng.gauss(0.0, 1.0) for asset in ASSETS}
    second = {asset: 0.8 * first[asset] + 0.6 * rng.gauss(0.0, 1.0) for asset in ASSETS}
    factor = {asset: first[asset] + second[asset] for asset in ASSETS}
    panel = _panel_of({START: factor})
    joint = neutralize_exposures(
        panel, {"first": {START: first}, "second": {START: second}}, minimum_assets=4
    )
    in_turn = neutralize_beta(
        neutralize_beta(panel, {START: first}).panel, {START: second}
    ).panel.cross_section(START)
    joint_residual = joint.panel.cross_section(START)
    assert max(abs(value) for value in joint_residual.values()) < 1e-12
    assert max(abs(value) for value in in_turn.values()) > 1e-3


def test_collinear_exposures_are_refused_per_instant_with_the_condition_named() -> None:
    beta, size = _exposures(5)
    twin = {asset: 2.0 * value + 1.0 for asset, value in beta.items()}
    rng = random.Random(6)
    factor = {asset: rng.gauss(0.0, 1.0) for asset in ASSETS}
    later = START + DAY
    panel = _panel_of({START: factor, later: factor})
    report = neutralize_exposures(
        panel,
        {
            "beta": {START: beta, later: beta},
            "other": {START: twin, later: size},
        },
        minimum_assets=4,
    )
    assert report.instants_neutralized == 1
    assert report.instants_skipped == 1
    ((stamp, reason),) = report.refusals
    assert stamp == START
    assert "too collinear" in reason
    assert f"{MAXIMUM_EXPOSURE_CONDITION:g}" in reason


def test_a_constant_exposure_is_refused_rather_than_fitted() -> None:
    beta, _ = _exposures(7)
    flat = dict.fromkeys(ASSETS, 1.0)
    panel = _panel_of({START: beta})
    with pytest.raises(FactorInputError, match="constant across the cross-section"):
        neutralize_exposures(
            panel, {"beta": {START: beta}, "flat": {START: flat}}, minimum_assets=4
        )


@pytest.mark.parametrize(
    ("exposures", "minimum", "maximum", "message"),
    [
        ({}, 4, 1e6, "at least one exposure"),
        ({"a": {START: {}}, "b": {START: {}}}, 3, 1e6, "at least 4"),
        ({"a": {START: {}}}, 3, 0.5, "maximum_condition"),
    ],
)
def test_malformed_neutralizations_are_refused(
    exposures: dict[str, dict[float, dict[str, float]]], minimum: int, maximum: float, message: str
) -> None:
    beta, _ = _exposures(8)
    with pytest.raises(FactorInputError, match=message):
        neutralize_exposures(
            _panel_of({START: beta}),
            exposures,
            minimum_assets=minimum,
            maximum_condition=maximum,
        )


def test_a_missing_exposure_value_is_refused_not_zeroed() -> None:
    beta, size = _exposures(9)
    del size["A03"]
    with pytest.raises(FactorInputError, match="A03"):
        neutralize_exposures(
            _panel_of({START: beta}),
            {"beta": {START: beta}, "size": {START: size}},
            minimum_assets=4,
        )


# --------------------------------------------------------------------------- #
# Implementation lag
# --------------------------------------------------------------------------- #


def test_lag_zero_is_the_v310_forward_return() -> None:
    frame = _frame({"A": [10.0, 11.0, 12.1, 13.31], "B": [5.0, 4.0, 5.0, 6.0]})
    panel = forward_returns(frame, 1, lag=0, delistings=())
    assert panel.cross_section(START) == {"A": 11.0 / 10.0 - 1.0, "B": 4.0 / 5.0 - 1.0}
    assert panel.lag == 0
    assert panel.unrealized_instants == 2
    assert panel.delisted_instants == 0
    assert panel.unenterable_instants == 0


def test_a_lag_enters_later_and_is_keyed_by_the_factor_instant() -> None:
    frame = _frame({"A": [10.0, 20.0, 30.0, 60.0, 90.0]})
    panel = forward_returns(frame, 1, lag=2, delistings=())
    # the factor at START enters at observation 2 (30.0) and exits at 3 (60.0)
    assert panel.cross_section(START) == {"A": 1.0}
    assert panel.cross_section(START + DAY) == {"A": 0.5}
    assert panel.timestamps == (START, START + DAY)
    assert panel.unrealized_instants == 3


def test_the_lag_is_carried_on_every_diagnostic() -> None:
    paths = {f"S{index}": [100.0 + index * step for step in range(8)] for index in range(6)}
    frame = _frame(paths)
    factor = _panel_of(
        {START + day * DAY: {s: float(i) for i, s in enumerate(paths)} for day in range(8)}
    )
    ic = information_coefficient(factor, forward_returns(frame, 2, lag=1, delistings=()), 3)
    assert ic.lag == 1
    decay = factor_decay(factor, frame, [1, 2], 3, lag=1, delistings=())
    assert {result.lag for _, result in decay.ordered} == {1}


@pytest.mark.parametrize("lag", [-1, True, 1.0])
def test_an_ill_formed_lag_is_refused(lag: object) -> None:
    with pytest.raises(FactorInputError, match="implementation lag"):
        forward_returns(_frame({"A": [1.0, 2.0, 3.0]}), 1, lag=lag, delistings=())  # type: ignore[arg-type]


def test_the_lag_is_required() -> None:
    with pytest.raises(TypeError, match="lag"):
        forward_returns(_frame({"A": [1.0, 2.0, 3.0]}), 1, delistings=())  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Delistings
# --------------------------------------------------------------------------- #


def test_a_delisted_symbol_realizes_its_terminal_return() -> None:
    frame = _frame({"LIVE": [10.0, 11.0, 12.0, 13.0], "DEAD": [10.0, 8.0, 4.0]})
    bankrupt = Delisting("DEAD", START + 2.5 * DAY, -1.0)
    without = forward_returns(frame, 2, lag=0, delistings=())
    with_event = forward_returns(frame, 2, lag=0, delistings=(bankrupt,))

    # without the event, DEAD's last two instants vanish: the survivorship bias
    assert "DEAD" not in without.cross_section(START + DAY)
    # with it, a window spanning the delisting exits at zero
    assert with_event.cross_section(START + DAY)["DEAD"] == pytest.approx(-1.0)
    assert with_event.cross_section(START + 2 * DAY)["DEAD"] == pytest.approx(-1.0)
    assert with_event.cross_section(START)["DEAD"] == pytest.approx(4.0 / 10.0 - 1.0)
    assert with_event.delisted_instants == 2
    assert with_event.unrealized_instants == without.unrealized_instants - 2
    assert with_event.delistings == (bankrupt,)


def test_an_acquisition_at_a_premium_realizes_the_premium() -> None:
    frame = _frame({"TGT": [50.0, 52.0, 60.0]})
    deal = Delisting.at_value("TGT", START + 3 * DAY, last_value=60.0, terminal_value=66.0)
    assert deal.terminal_return == pytest.approx(0.1)
    panel = forward_returns(frame, 5, lag=0, delistings=(deal,))
    assert panel.cross_section(START)["TGT"] == pytest.approx(66.0 / 50.0 - 1.0)
    assert panel.cross_section(START + 2 * DAY)["TGT"] == pytest.approx(0.1)


def test_an_entry_after_the_delisting_is_unenterable_not_unrealized() -> None:
    frame = _frame({"DEAD": [10.0, 9.0, 8.0]})
    event = Delisting("DEAD", START + 3 * DAY, -0.5)
    panel = forward_returns(frame, 1, lag=2, delistings=(event,))
    # factor at START enters at obs 2 (8.0) and exits at the terminal value 4.0
    assert panel.cross_section(START)["DEAD"] == pytest.approx(-0.5)
    assert panel.delisted_instants == 1
    assert panel.unenterable_instants == 2
    assert panel.unrealized_instants == 0


def test_the_delisting_set_has_an_order_free_identity() -> None:
    one = Delisting("A", 1.0, -1.0)
    two = Delisting("B", 2.0, 0.25)
    assert delisting_set_id([one, two]) == delisting_set_id([two, one])
    assert delisting_set_id([one]) != delisting_set_id([one, two])
    assert delisting_set_id([Delisting("A", 1.0, -0.0)]) == delisting_set_id(
        [Delisting("A", 1.0, 0.0)]
    )


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ((Delisting("GHOST", START + 9 * DAY, -1.0),), "does not hold"),
        ((Delisting("A", START + DAY, -1.0),), "contradicts"),
        ((Delisting("A", START + 9 * DAY, -1.0), Delisting("A", START + 9 * DAY, 0.0)), "twice"),
        (("A",), "Delisting values"),
    ],
)
def test_an_inconsistent_delisting_set_is_refused(events: tuple[object, ...], message: str) -> None:
    frame = _frame({"A": [1.0, 2.0, 3.0]})
    with pytest.raises(FactorInputError, match=message):
        forward_returns(frame, 1, lag=0, delistings=events)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("symbol", "stamp", "value", "message"),
    [
        ("", 1.0, -1.0, "symbol"),
        ("A", math.nan, -1.0, "finite"),
        ("A", 1.0, -1.5, "more than everything"),
        ("A", 1.0, math.inf, "more than everything"),
        ("A", True, -1.0, "number"),
    ],
)
def test_a_malformed_delisting_is_refused(
    symbol: str, stamp: float, value: float, message: str
) -> None:
    with pytest.raises(FactorInputError, match=message):
        Delisting(symbol, stamp, value)


def test_a_value_delisting_refuses_a_non_positive_base_or_negative_payout() -> None:
    with pytest.raises(FactorInputError, match="non-positive base"):
        Delisting.at_value("A", 1.0, 0.0, 1.0)
    with pytest.raises(FactorInputError, match="less than nothing"):
        Delisting.at_value("A", 1.0, 1.0, -0.5)


def test_a_non_positive_entry_is_still_refused() -> None:
    with pytest.raises(FactorComputationError, match="base value"):
        forward_returns(_frame({"A": [1.0, 0.0, 3.0, 4.0]}), 1, lag=1, delistings=())


# --------------------------------------------------------------------------- #
# The IC's Newey-West t-statistic
# --------------------------------------------------------------------------- #


def _noisy_study(seed: int, instants: int) -> tuple[FeaturePanel, ObservationFrame]:
    rng = random.Random(seed)
    symbols = [f"S{index:02d}" for index in range(10)]
    paths = {symbol: [100.0] for symbol in symbols}
    for _ in range(instants + 10):
        for symbol in symbols:
            paths[symbol].append(paths[symbol][-1] * math.exp(rng.gauss(0.0, 0.01)))
    factor = _panel_of(
        {
            START + day * DAY: {symbol: rng.gauss(0.0, 1.0) for symbol in symbols}
            for day in range(instants)
        }
    )
    return factor, _frame(paths)


def test_the_t_statistic_uses_the_overlap_lag_and_is_reported_with_it() -> None:
    factor, frame = _noisy_study(1, 120)
    ic = information_coefficient(factor, forward_returns(frame, 5, lag=0, delistings=()), 5)
    assert ic.newey_west_lag == 4
    assert ic.rank_standard_error is not None
    assert ic.rank_t_statistic is not None
    assert ic.mean_rank is not None
    assert ic.rank_t_statistic == pytest.approx(ic.mean_rank / ic.rank_standard_error)


def test_too_few_instants_for_the_lag_report_no_t_statistic() -> None:
    factor, frame = _noisy_study(2, 4)
    ic = information_coefficient(factor, forward_returns(frame, 5, lag=0, delistings=()), 5)
    assert ic.instants_measured == 4
    assert ic.rank_standard_error is None
    assert ic.rank_t_statistic is None
