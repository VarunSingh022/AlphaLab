"""Curves under a required, named interpolation (v4.3, ADR-0065 section 2).

Each method is held to its definition on a curve stated here, under ACT/365F
from 1 January 2025, so a pillar a year on is at exactly one year:

* **Log-linear on discount factors**: the forward rate is one constant across a
  segment, so two equal spans inside it discount alike.
* **Linear on zero rates**: the zero rate at a segment's mid-point is the mean
  of its ends', and flat at the first pillar's from the anchor.
* **A pillar reads back its own discount factor**, bit for bit, and the anchor
  reads one.
* **Extrapolation is refused unless stated**, and each stated kind extends by
  its definition.
* **A parallel shift** moves the zero rate at every date by exactly its amount.
* **Identity** covers the stated pillars, and a computed curve's derivation in
  place of its discount factors.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from alphalab.conventions import Compounding, DayCount
from alphalab.rates import (
    CurveRangeError,
    DiscountCurve,
    Extrapolation,
    Interpolation,
    Pillar,
    RatesInputError,
    canonical_curve_key,
)

ANCHOR = date(2025, 1, 1)
ONE, TWO, THREE = date(2026, 1, 1), date(2027, 1, 1), date(2028, 1, 1)
PILLARS = (Pillar(ONE, 0.96), Pillar(TWO, 0.91), Pillar(THREE, 0.87))


def curve(
    interpolation: Interpolation,
    extrapolation: Extrapolation | None = None,
    pillars: tuple[Pillar, ...] = PILLARS,
    derivation_id: str | None = None,
) -> DiscountCurve:
    return DiscountCurve(
        "USD-TEST",
        "USD",
        ANCHOR,
        DayCount.ACT_365_FIXED,
        pillars,
        interpolation,
        extrapolation,
        derivation_id,
    )


def years(day: date) -> float:
    return (day - ANCHOR).days / 365


def test_log_linear_holds_one_forward_rate_across_a_segment() -> None:
    log_linear = curve(Interpolation.LOG_LINEAR_DISCOUNT)
    # 2026-01-01 is 365 days on and 2027-01-01 730: the segment from one year to two.
    forward = math.log(0.96 / 0.91)  # per year, continuously compounded
    for start, end in [
        (date(2026, 2, 1), date(2026, 5, 1)),
        (date(2026, 8, 1), date(2026, 10, 30)),
    ]:
        span = years(end) - years(start)
        ratio = log_linear.discount(start) / log_linear.discount(end)
        assert math.log(ratio) / span == pytest.approx(forward, rel=1e-13)
    # A point in the segment, by the definition: ln P linear in time.
    day = date(2026, 7, 2)
    weight = years(day) - 1.0
    expected = math.exp((1 - weight) * math.log(0.96) + weight * math.log(0.91))
    assert log_linear.discount(day) == pytest.approx(expected, rel=1e-15)


def test_linear_zero_holds_the_zero_rate_linear_and_flat_before_the_first_pillar() -> None:
    linear = curve(Interpolation.LINEAR_ZERO)
    first, second = -math.log(0.96) / 1.0, -math.log(0.91) / 2.0
    day = date(2026, 7, 2)  # 547 days: a weight of 182/365 into the segment
    weight = years(day) - 1.0
    zero = linear.zero_rate(day, Compounding.CONTINUOUS)
    assert zero == pytest.approx(first + weight * (second - first), rel=1e-14)
    # Before the first pillar the first pillar's zero rate holds, under both methods.
    early = date(2025, 4, 1)
    for interpolation in Interpolation:
        held = curve(interpolation).zero_rate(early, Compounding.CONTINUOUS)
        assert held == pytest.approx(first, rel=1e-14)
    # The two methods agree at pillars and differ between them.
    log_linear = curve(Interpolation.LOG_LINEAR_DISCOUNT)
    assert log_linear.discount(day) != pytest.approx(linear.discount(day), rel=1e-6)


def test_a_pillar_reads_back_its_own_discount_factor_and_the_anchor_reads_one() -> None:
    for interpolation in Interpolation:
        stated = curve(interpolation)
        assert stated.discount(ANCHOR) == 1.0
        for pillar in PILLARS:
            assert stated.discount(pillar.day) == pillar.discount  # bit for bit


def test_a_read_beyond_the_last_pillar_is_refused_unless_an_extrapolation_is_stated() -> None:
    beyond = date(2029, 1, 1)
    with pytest.raises(CurveRangeError, match="states no extrapolation"):
        curve(Interpolation.LOG_LINEAR_DISCOUNT).discount(beyond)
    with pytest.raises(CurveRangeError, match="before the curve's anchor"):
        curve(Interpolation.LOG_LINEAR_DISCOUNT).discount(date(2024, 12, 31))
    # Flat forward: the last segment's forward, (ln 0.91 - ln 0.87) a year, continues.
    flat_forward = curve(Interpolation.LINEAR_ZERO, Extrapolation.FLAT_FORWARD)
    forward = math.log(0.91) - math.log(0.87)
    expected = 0.87 * math.exp(-forward * (years(beyond) - years(THREE)))
    assert flat_forward.discount(beyond) == pytest.approx(expected, rel=1e-14)
    # Flat zero: the last pillar's zero rate continues.
    flat_zero = curve(Interpolation.LOG_LINEAR_DISCOUNT, Extrapolation.FLAT_ZERO)
    last = -math.log(0.87) / years(THREE)
    assert flat_zero.zero_rate(beyond, Compounding.CONTINUOUS) == pytest.approx(last, rel=1e-14)


def test_rates_and_forwards_by_their_conventions() -> None:
    stated = curve(Interpolation.LOG_LINEAR_DISCOUNT)
    assert stated.zero_rate(ONE, Compounding.ANNUAL) == pytest.approx(1 / 0.96 - 1, rel=1e-14)
    semi = stated.zero_rate(TWO, Compounding.SEMI_ANNUAL)
    assert semi == pytest.approx(2 * (0.91 ** (-1 / 4) - 1), rel=1e-14)
    forward = stated.forward_rate(ONE, TWO, DayCount.ACT_360)
    assert forward == pytest.approx((0.96 / 0.91 - 1) / (365 / 360), rel=1e-14)
    with pytest.raises(CurveRangeError, match="needs time"):
        stated.zero_rate(ANCHOR, Compounding.CONTINUOUS)
    with pytest.raises(RatesInputError, match="later date"):
        stated.forward_rate(TWO, ONE, DayCount.ACT_360)


def test_a_parallel_shift_moves_every_zero_rate_by_its_amount() -> None:
    shift = 0.0025
    days = [date(2025, 3, 1), ONE, date(2026, 9, 15), THREE, date(2030, 6, 1)]
    for interpolation in Interpolation:
        for extrapolation in Extrapolation:
            base = curve(interpolation, extrapolation)
            moved = base.shifted(shift)
            for day in days:
                before = base.zero_rate(day, Compounding.CONTINUOUS)
                after = moved.zero_rate(day, Compounding.CONTINUOUS)
                assert after - before == pytest.approx(shift, abs=1e-14)
            assert moved.derivation_id is not None
            assert moved.curve_id != base.curve_id
            assert moved.curve_id == base.shifted(shift).curve_id


def test_the_identity_covers_what_was_stated() -> None:
    base = curve(Interpolation.LOG_LINEAR_DISCOUNT)
    assert base.curve_id == curve(Interpolation.LOG_LINEAR_DISCOUNT).curve_id
    others = [
        curve(Interpolation.LINEAR_ZERO),
        curve(Interpolation.LOG_LINEAR_DISCOUNT, Extrapolation.FLAT_FORWARD),
        curve(Interpolation.LOG_LINEAR_DISCOUNT, pillars=(*PILLARS[:2], Pillar(THREE, 0.8700001))),
    ]
    assert len({base.curve_id, *(other.curve_id for other in others)}) == 4
    assert "pillar=2028-01-01|0.87" in canonical_curve_key(base).splitlines()


def test_a_computed_curve_is_identified_by_its_derivation_not_its_last_bits() -> None:
    derivation = "a" * 64
    solved = curve(Interpolation.LOG_LINEAR_DISCOUNT, derivation_id=derivation)
    bits = (*PILLARS[:2], Pillar(THREE, math.nextafter(0.87, 1.0)))
    other_host = curve(Interpolation.LOG_LINEAR_DISCOUNT, pillars=bits, derivation_id=derivation)
    assert solved.curve_id == other_host.curve_id
    key = canonical_curve_key(solved).splitlines()
    assert f"derivation={derivation}" in key and "pillar=2028-01-01" in key


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"interpolation": None}, "a method it names, never by a default"),
        ({"day_count": DayCount.ACT_ACT_ICMA}, "cannot be a curve's time axis"),
        ({"pillars": ()}, "at least one Pillar"),
        ({"pillars": (Pillar(TWO, 0.91), Pillar(ONE, 0.96))}, "follow its anchor in order"),
        ({"pillars": (Pillar(ANCHOR, 1.0),)}, "follow its anchor in order"),
        ({"currency": "usd"}, "ISO 4217"),
        ({"derivation_id": "not-a-digest"}, "SHA-256 digest"),
        ({"extrapolation": "flat"}, "an Extrapolation or None"),
    ],
)
def test_a_malformed_curve_is_refused(change: dict[str, object], message: str) -> None:
    fields: dict[str, object] = {
        "name": "USD-TEST",
        "currency": "USD",
        "anchor": ANCHOR,
        "day_count": DayCount.ACT_365_FIXED,
        "pillars": PILLARS,
        "interpolation": Interpolation.LOG_LINEAR_DISCOUNT,
        **change,
    }
    with pytest.raises(RatesInputError, match=message):
        DiscountCurve(**fields)  # type: ignore[arg-type]


def test_two_dates_at_one_time_on_the_axis_cannot_both_be_pillars() -> None:
    # Under 30E/360 the 30th and the 31st of March are one day count from the anchor.
    with pytest.raises(RatesInputError, match="one time on the curve's axis"):
        DiscountCurve(
            "EUR-TEST",
            "EUR",
            ANCHOR,
            DayCount.THIRTY_E_360,
            (Pillar(date(2025, 3, 30), 0.99), Pillar(date(2025, 3, 31), 0.98)),
            Interpolation.LINEAR_ZERO,
        )


def test_a_discount_factor_is_positive_and_finite() -> None:
    for value in (0.0, -0.5, math.inf, math.nan):
        with pytest.raises(RatesInputError):
            Pillar(ONE, value)
