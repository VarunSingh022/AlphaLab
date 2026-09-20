"""Venue conditions, funding accrual and the gap between a 24/7 clock and 24/7 data."""

from decimal import Decimal

import pytest

from alphalab.crypto import (
    BASIS_POINT,
    CryptoInputError,
    FeeSchedule,
    FundingRate,
    FundingRateHistory,
    LiquidityRole,
    PriceSource,
    VenueSpecification,
    accrued_funding,
    annualized_funding_rate,
    compute_funding_payment,
    coverage,
    cross_venue_dispersion,
    funding_instants,
    observation_gaps,
    trading_fee,
)

HOUR = 3600.0


def _venue(**overrides: object) -> VenueSpecification:
    defaults: dict[str, object] = {
        "venue": "binance",
        "funding_interval_hours": 8,
        "fees": FeeSchedule(maker_bps=Decimal("-1"), taker_bps=Decimal("5")),
        "price_source": PriceSource.INDEX,
        "minimum_notional": Decimal("5"),
        "settlement_asset": "USDT",
    }
    defaults.update(overrides)
    return VenueSpecification(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Venue specification
# --------------------------------------------------------------------------- #


def test_every_venue_field_is_required() -> None:
    import inspect

    for parameter in inspect.signature(VenueSpecification).parameters.values():
        assert parameter.default is inspect.Parameter.empty, (
            f"VenueSpecification.{parameter.name} is defaulted"
        )


def test_a_non_positive_funding_interval_is_refused() -> None:
    with pytest.raises(CryptoInputError, match="annualize to infinity"):
        _venue(funding_interval_hours=0)


def test_two_venues_can_declare_different_intervals() -> None:
    assert _venue(funding_interval_hours=8).funding_intervals_per_year == Decimal("1095")
    assert _venue(funding_interval_hours=1).funding_intervals_per_year == Decimal("8760")


def test_a_taker_rebate_is_refused() -> None:
    with pytest.raises(CryptoInputError, match="no venue does"):
        FeeSchedule(maker_bps=Decimal("1"), taker_bps=Decimal("-1"))


def test_a_maker_rebate_is_allowed_and_pays_the_trader() -> None:
    assert trading_fee(_venue(), Decimal("10000"), LiquidityRole.MAKER) == Decimal("1")
    assert trading_fee(_venue(), Decimal("10000"), LiquidityRole.TAKER) == Decimal("-5")


def test_a_fee_is_charged_on_size_not_direction() -> None:
    with pytest.raises(CryptoInputError, match="credit the sell side"):
        trading_fee(_venue(), Decimal("-10000"), LiquidityRole.TAKER)


def test_basis_points_convert_once() -> None:
    assert Decimal("0.0001") == BASIS_POINT
    assert _venue().fees.rate_for(LiquidityRole.TAKER) == Decimal("0.0005")


# --------------------------------------------------------------------------- #
# Cross-venue dispersion
# --------------------------------------------------------------------------- #


def test_dispersion_needs_at_least_two_venues() -> None:
    with pytest.raises(CryptoInputError, match="at least two"):
        cross_venue_dispersion({"binance": Decimal("50000")})


def test_dispersion_reports_a_price_and_a_fraction_as_separate_fields() -> None:
    result = cross_venue_dispersion(
        {"binance": Decimal("50000"), "coinbase": Decimal("50100"), "kraken": Decimal("49900")}
    )
    assert result.lowest_venue == "kraken"
    assert result.highest_venue == "coinbase"
    assert result.spread == Decimal("200")
    assert result.relative_spread == Decimal("200") / Decimal("49900")


def test_a_tie_breaks_on_the_venue_name_so_the_answer_is_deterministic() -> None:
    prices = {"zeta": Decimal("100"), "alpha": Decimal("100")}
    assert cross_venue_dispersion(prices).lowest_venue == "alpha"
    assert cross_venue_dispersion(prices) == cross_venue_dispersion(prices)


def test_a_non_price_is_refused() -> None:
    with pytest.raises(CryptoInputError, match="not a price"):
        cross_venue_dispersion({"a": Decimal("100"), "b": Decimal("0")})


# --------------------------------------------------------------------------- #
# Funding
# --------------------------------------------------------------------------- #


def test_funding_instants_follow_the_declared_anchor() -> None:
    """Two venues on the same interval fund at different times."""

    at_midnight = funding_instants(0.0, 24 * HOUR, 8, anchor=0.0)
    at_one = funding_instants(0.0, 24 * HOUR, 8, anchor=HOUR)
    assert at_midnight == (0.0, 8 * HOUR, 16 * HOUR)
    assert at_one == (HOUR, 9 * HOUR, 17 * HOUR)


def test_the_window_is_half_open() -> None:
    assert funding_instants(0.0, 8 * HOUR, 8, anchor=0.0) == (0.0,)


def test_an_anchor_before_the_window_still_lands_on_the_grid() -> None:
    assert funding_instants(10 * HOUR, 26 * HOUR, 8, anchor=0.0) == (16 * HOUR, 24 * HOUR)


def test_a_backwards_window_is_refused() -> None:
    with pytest.raises(CryptoInputError, match="runs forwards"):
        funding_instants(10.0, 0.0, 8, anchor=0.0)


def test_a_long_pays_and_a_short_receives_when_funding_is_positive() -> None:
    long = compute_funding_payment(Decimal("1"), Decimal("50000"), Decimal("0.0001"), Decimal("1"))
    short = compute_funding_payment(
        Decimal("-1"), Decimal("50000"), Decimal("0.0001"), Decimal("1")
    )
    assert long == Decimal("-5")
    assert short == Decimal("5")
    assert long + short == Decimal("0")


def test_contract_size_scales_the_payment() -> None:
    one = compute_funding_payment(Decimal("1"), Decimal("50000"), Decimal("0.0001"), Decimal("1"))
    ten = compute_funding_payment(Decimal("1"), Decimal("50000"), Decimal("0.0001"), Decimal("10"))
    assert ten == one * 10


def _history(count: int = 3, interval: int = 8) -> FundingRateHistory:
    return FundingRateHistory(
        instrument_symbol="BINANCE_BTC_USDT_PERPETUAL",
        rates=tuple(
            FundingRate(
                instrument_symbol="BINANCE_BTC_USDT_PERPETUAL",
                rate=Decimal("0.0001"),
                timestamp=index * interval * HOUR,
                interval_hours=interval,
            )
            for index in range(count)
        ),
    )


def test_accrual_charges_each_instant_against_its_own_mark() -> None:
    marks = {0.0: Decimal("50000"), 8 * HOUR: Decimal("51000"), 16 * HOUR: Decimal("49000")}
    summary = accrued_funding(_history(), Decimal("1"), marks, Decimal("1"))
    assert summary.intervals == 3
    assert [accrual.mark_price for accrual in summary.accruals] == [
        Decimal("50000"),
        Decimal("51000"),
        Decimal("49000"),
    ]
    assert summary.total == Decimal("-15.0")


def test_a_missing_mark_is_refused_rather_than_carried_forward() -> None:
    with pytest.raises(CryptoInputError, match="never marked"):
        accrued_funding(_history(), Decimal("1"), {0.0: Decimal("50000")}, Decimal("1"))


def test_annualization_uses_the_declared_interval_and_refuses_a_mixture() -> None:
    hourly = _history(count=2, interval=1)
    eight_hourly = _history(count=2, interval=8)
    assert annualized_funding_rate(hourly) == annualized_funding_rate(eight_hourly) * 8

    mixed = FundingRateHistory(
        instrument_symbol="X",
        rates=(
            FundingRate("X", Decimal("0.0001"), 0.0, 8),
            FundingRate("X", Decimal("0.0001"), HOUR, 1),
        ),
    )
    with pytest.raises(CryptoInputError, match="uniform interval_hours"):
        annualized_funding_rate(mixed)


# --------------------------------------------------------------------------- #
# 24/7 clock versus 24/7 data
# --------------------------------------------------------------------------- #


def test_no_gaps_when_every_expected_observation_arrived() -> None:
    timestamps = [float(index) * 60 for index in range(10)]
    assert observation_gaps(timestamps, 60.0) == ()


def test_a_missing_observation_is_a_gap_and_jitter_is_not() -> None:
    assert observation_gaps([0.0, 70.0, 130.0], 60.0) == ()
    gaps = observation_gaps([0.0, 180.0], 60.0)
    assert len(gaps) == 1
    assert gaps[0].after == 0.0
    assert gaps[0].before == 180.0
    assert gaps[0].missing == 2


def test_unsorted_or_duplicated_observations_are_refused() -> None:
    with pytest.raises(CryptoInputError, match="did not happen"):
        observation_gaps([0.0, 60.0, 60.0], 60.0)
    with pytest.raises(CryptoInputError, match="did not happen"):
        observation_gaps([60.0, 0.0], 60.0)


def test_a_non_positive_cadence_is_refused() -> None:
    with pytest.raises(CryptoInputError, match="infinitely many"):
        observation_gaps([0.0, 60.0], 0.0)


def test_coverage_measures_observations_against_a_theoretical_clock() -> None:
    """The expected count comes from the window, never from the data -- computing
    it from the data would make coverage identically 1.0."""

    present = [float(index) * 60 for index in range(60)]
    report = coverage(present, 0.0, 3600.0, 60.0)
    assert report.expected == 60
    assert report.observed == 60
    assert report.coverage == 1.0
    assert report.missing == 0

    outage = [value for value in present if not 600 <= value < 1200]
    degraded = coverage(outage, 0.0, 3600.0, 60.0)
    assert degraded.expected == 60
    assert degraded.observed == 50
    assert degraded.coverage == pytest.approx(50 / 60)
    assert degraded.missing == 10
    assert len(degraded.gaps) == 1


def test_observations_outside_the_window_are_ignored_not_counted() -> None:
    report = coverage([-600.0, 0.0, 60.0, 4000.0], 0.0, 3600.0, 60.0)
    assert report.observed == 2


def test_a_window_implying_no_observations_reports_none_rather_than_a_ratio() -> None:
    """An unmeasurable statistic is None, never 0.0 or 1.0."""

    report = coverage([], 0.0, 30.0, 60.0)
    assert report.expected == 0
    assert report.coverage is None


def test_a_window_with_no_duration_is_refused() -> None:
    with pytest.raises(CryptoInputError, match="no coverage to measure"):
        coverage([], 10.0, 10.0, 60.0)


def test_nothing_here_invents_an_observation() -> None:
    """A gap is reported as a count of absences, never as timestamps that could
    be turned into rows."""

    gaps = observation_gaps([0.0, 600.0], 60.0)
    assert gaps[0].missing == 9
    assert not hasattr(gaps[0], "timestamps")
    assert coverage([0.0, 600.0], 0.0, 660.0, 60.0).observed == 2
