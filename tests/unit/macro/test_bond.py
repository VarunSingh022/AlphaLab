"""Bond analytics: cash flows, accrued interest, clean/dirty, yield, duration, convexity."""

from datetime import date
from decimal import Decimal

import pytest

from alphalab.conventions import Compounding, DayCount
from alphalab.macro import (
    Bond,
    accrued_interest,
    cash_flows,
    clean_price,
    convexity,
    dirty_price,
    discount_factor_at,
    macaulay_duration,
    modified_duration,
    yield_from_clean_price,
)
from alphalab.macro.exceptions import MacroComputationError, MacroInputError
from alphalab.macro.yield_curve import YieldCurve, YieldCurvePoint


def _bond(
    coupon: float = 0.05,
    frequency: Compounding = Compounding.SEMI_ANNUAL,
    day_count: DayCount = DayCount.ACT_365_FIXED,
) -> Bond:
    return Bond(
        face=Decimal("100"),
        coupon_rate=coupon,
        frequency=frequency,
        issue_date=date(2020, 1, 15),
        maturity=date(2030, 1, 15),
        day_count=day_count,
        currency="USD",
    )


_SETTLEMENT = date(2025, 1, 15)


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def test_a_continuous_coupon_schedule_is_refused() -> None:
    with pytest.raises(MacroInputError, match="no bond pays continuously"):
        _bond(frequency=Compounding.CONTINUOUS)


def test_a_negative_coupon_is_refused_and_a_negative_yield_is_not() -> None:
    with pytest.raises(MacroInputError, match="not a coupon"):
        _bond(coupon=-0.01)
    # A negative yield is real: it is a price above par.
    assert clean_price(_bond(), -0.005, _SETTLEMENT) > Decimal("100")


def test_a_bond_names_its_currency() -> None:
    with pytest.raises(MacroInputError, match="names its currency"):
        Bond(
            Decimal("100"),
            0.05,
            Compounding.ANNUAL,
            date(2020, 1, 15),
            date(2030, 1, 15),
            DayCount.ACT_365_FIXED,
            "   ",
        )


def test_maturity_must_follow_issue() -> None:
    with pytest.raises(MacroInputError, match="does not follow"):
        Bond(
            Decimal("100"),
            0.05,
            Compounding.ANNUAL,
            date(2030, 1, 15),
            date(2020, 1, 15),
            DayCount.ACT_365_FIXED,
            "USD",
        )


# --------------------------------------------------------------------------- #
# Cash flows
# --------------------------------------------------------------------------- #


def test_a_semi_annual_ten_year_bond_pays_twenty_coupons() -> None:
    flows = cash_flows(_bond())
    assert len(flows) == 20
    assert flows[0].amount == Decimal("2.50")
    assert flows[0].payment_date == date(2020, 7, 15)


def test_the_final_flow_carries_the_coupon_and_the_principal() -> None:
    flows = cash_flows(_bond())
    assert flows[-1].is_principal
    assert flows[-1].amount == Decimal("102.50")
    assert flows[-1].payment_date == date(2030, 1, 15)
    assert sum(flow.is_principal for flow in flows) == 1


def test_the_schedule_is_generated_backwards_from_maturity() -> None:
    """Which is how a bond is structured, and what places a stub first period."""

    odd = Bond(
        face=Decimal("100"),
        coupon_rate=0.04,
        frequency=Compounding.SEMI_ANNUAL,
        issue_date=date(2024, 3, 1),
        maturity=date(2026, 1, 15),
        day_count=DayCount.ACT_365_FIXED,
        currency="USD",
    )
    dates = [flow.payment_date for flow in cash_flows(odd)]
    assert dates[-1] == date(2026, 1, 15)
    assert dates[0] == date(2024, 7, 15)
    assert all(day > odd.issue_date for day in dates)


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        (Compounding.ANNUAL, 10),
        (Compounding.SEMI_ANNUAL, 20),
        (Compounding.QUARTERLY, 40),
        (Compounding.MONTHLY, 120),
    ],
)
def test_the_frequency_decides_the_number_of_coupons(frequency: Compounding, expected: int) -> None:
    assert len(cash_flows(_bond(frequency=frequency))) == expected


# --------------------------------------------------------------------------- #
# Accrued interest, clean and dirty
# --------------------------------------------------------------------------- #


def test_nothing_has_accrued_on_a_coupon_date() -> None:
    assert accrued_interest(_bond(), _SETTLEMENT) == Decimal("0E-8")


def test_accrued_interest_grows_through_the_period() -> None:
    quarter = accrued_interest(_bond(), date(2025, 4, 15))
    nearly = accrued_interest(_bond(), date(2025, 7, 14))
    assert Decimal("0") < quarter < nearly < _bond().coupon_amount


def test_the_day_count_basis_changes_the_accrual() -> None:
    settlement = date(2025, 4, 15)
    by_actual = accrued_interest(_bond(day_count=DayCount.ACT_365_FIXED), settlement)
    by_thirty = accrued_interest(_bond(day_count=DayCount.THIRTY_360_US), settlement)
    assert by_actual != by_thirty


def test_clean_plus_accrued_is_dirty() -> None:
    """Three numbers, and the identity between them is checked not assumed."""

    for settlement in (date(2025, 1, 15), date(2025, 4, 15), date(2025, 7, 14)):
        assert clean_price(_bond(), 0.05, settlement) + accrued_interest(
            _bond(), settlement
        ) == dirty_price(_bond(), 0.05, settlement)


def test_a_bond_yielding_its_coupon_prices_at_par_on_a_coupon_date() -> None:
    assert clean_price(_bond(), 0.05, _SETTLEMENT) == Decimal("100.00000000")


def test_price_falls_as_yield_rises() -> None:
    prices = [float(clean_price(_bond(), rate, _SETTLEMENT)) for rate in (0.03, 0.05, 0.07)]
    assert prices[0] > prices[1] > prices[2]


def test_a_settlement_after_maturity_is_refused() -> None:
    with pytest.raises(MacroInputError, match="matured bond"):
        clean_price(_bond(), 0.05, date(2031, 1, 15))


def test_a_settlement_before_issue_is_refused() -> None:
    with pytest.raises(MacroInputError, match="does not exist yet"):
        accrued_interest(_bond(), date(2019, 1, 1))


# --------------------------------------------------------------------------- #
# Yield inversion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("rate", [-0.01, 0.0, 0.02, 0.05, 0.09, 0.25])
def test_the_inversion_recovers_the_yield_it_was_priced_at(rate: float) -> None:
    price = clean_price(_bond(), rate, _SETTLEMENT)
    assert yield_from_clean_price(_bond(), price, _SETTLEMENT) == pytest.approx(rate, abs=1e-8)


def test_the_inversion_is_deterministic() -> None:
    price = clean_price(_bond(), 0.0637, _SETTLEMENT)
    first = yield_from_clean_price(_bond(), price, _SETTLEMENT)
    second = yield_from_clean_price(_bond(), price, _SETTLEMENT)
    assert first == second


def test_a_price_above_the_reachable_range_is_refused_rather_than_bounded() -> None:
    with pytest.raises(MacroComputationError, match="present a limit as a measurement"):
        yield_from_clean_price(_bond(), Decimal("100000"), _SETTLEMENT)


def test_a_price_below_the_reachable_range_is_refused() -> None:
    with pytest.raises(MacroComputationError, match="below"):
        yield_from_clean_price(_bond(), Decimal("0.0000001"), _SETTLEMENT)


def test_a_non_positive_price_is_a_credit_event_not_a_yield() -> None:
    with pytest.raises(MacroInputError, match="credit event"):
        yield_from_clean_price(_bond(), Decimal("0"), _SETTLEMENT)


# --------------------------------------------------------------------------- #
# Duration and convexity
# --------------------------------------------------------------------------- #


def test_a_zero_coupon_bonds_duration_is_its_maturity() -> None:
    zero = _bond(coupon=0.0, frequency=Compounding.ANNUAL)
    assert macaulay_duration(zero, 0.05, _SETTLEMENT) == pytest.approx(5.0, abs=1e-9)


def test_a_coupon_bonds_duration_is_shorter_than_its_maturity() -> None:
    assert 0.0 < macaulay_duration(_bond(), 0.05, _SETTLEMENT) < 5.0


def test_modified_duration_is_macaulay_discounted_by_one_period() -> None:
    macaulay = macaulay_duration(_bond(), 0.05, _SETTLEMENT)
    assert modified_duration(_bond(), 0.05, _SETTLEMENT) == pytest.approx(macaulay / (1 + 0.05 / 2))


def test_duration_predicts_the_price_move_and_convexity_corrects_it() -> None:
    """The reason both are reported: duration alone is a first-order estimate."""

    base = float(dirty_price(_bond(), 0.05, _SETTLEMENT))
    shifted = float(dirty_price(_bond(), 0.06, _SETTLEMENT))
    duration = modified_duration(_bond(), 0.05, _SETTLEMENT)
    curvature = convexity(_bond(), 0.05, _SETTLEMENT)

    linear = base * (1 - duration * 0.01)
    quadratic = base * (1 - duration * 0.01 + 0.5 * curvature * 0.01**2)
    assert abs(quadratic - shifted) < abs(linear - shifted)


def test_a_longer_bond_has_more_duration_and_more_convexity() -> None:
    short = Bond(
        Decimal("100"),
        0.05,
        Compounding.SEMI_ANNUAL,
        date(2020, 1, 15),
        date(2027, 1, 15),
        DayCount.ACT_365_FIXED,
        "USD",
    )
    assert macaulay_duration(short, 0.05, _SETTLEMENT) < macaulay_duration(
        _bond(), 0.05, _SETTLEMENT
    )
    assert convexity(short, 0.05, _SETTLEMENT) < convexity(_bond(), 0.05, _SETTLEMENT)


def test_duration_and_convexity_are_different_dimensions() -> None:
    """Years and years squared. They are never added, and the docstrings say so."""

    duration = macaulay_duration(_bond(), 0.05, _SETTLEMENT)
    curvature = convexity(_bond(), 0.05, _SETTLEMENT)
    assert curvature > duration**1.5  # a units check by magnitude, not a coincidence


# --------------------------------------------------------------------------- #
# Curve discount factors
# --------------------------------------------------------------------------- #


def _curve() -> YieldCurve:
    return YieldCurve(
        country="US",
        currency="USD",
        timestamp=0.0,
        points=(
            YieldCurvePoint(Decimal("1"), Decimal("0.04")),
            YieldCurvePoint(Decimal("10"), Decimal("0.05")),
        ),
    )


def test_the_compounding_convention_changes_the_discount_factor_materially() -> None:
    factors = {
        compounding: discount_factor_at(_curve(), Decimal("10"), compounding)
        for compounding in Compounding
    }
    assert len(set(factors.values())) == len(Compounding)
    assert factors[Compounding.ANNUAL] == pytest.approx(1.05**-10)


def test_a_tenor_outside_the_observed_range_has_no_discount_factor() -> None:
    """None, not an extrapolated one: that tenor was never quoted."""

    assert discount_factor_at(_curve(), Decimal("30"), Compounding.ANNUAL) is None
    assert discount_factor_at(_curve(), Decimal("0.25"), Compounding.ANNUAL) is None


def test_an_interpolated_tenor_uses_the_curves_one_reading() -> None:
    from alphalab.macro import yield_at_tenor

    observed = yield_at_tenor(_curve(), Decimal("5"))
    assert observed is not None
    factor = discount_factor_at(_curve(), Decimal("5"), Compounding.ANNUAL)
    assert factor == pytest.approx(float(1 + observed) ** -5)


def test_a_negative_tenor_is_refused() -> None:
    with pytest.raises(MacroInputError, match="forward period"):
        discount_factor_at(_curve(), Decimal("-1"), Compounding.ANNUAL)
