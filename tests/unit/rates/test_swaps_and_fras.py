"""Swaps and FRAs by declared terms, valued off curves (v4.3, ADR-0065 section 4).

* **Parity at inception.** A swap struck at a quoted par rate, on the curve
  bootstrapped from that quote, is worth nothing to either side, on one curve
  and against a stated OIS discount curve; so is an FRA struck at its forward.
* **What is known is not projected.** Valued after its first fixing, a swap
  reads that fixing as supplied: a fixing a basis point higher costs the
  receiver one basis point of the period's discounted accrual, exactly, and a
  fixing not supplied is refused.
* **Accrual by convention**, worked by hand: each leg's notional, rate and the
  year fraction from its period's start under its own basis.
* **An FRA's settlement** is the period's difference discounted at the fixing,
  in ``Decimal``.
* **The terms persist** as primitives, and read back equal.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pytest

from alphalab.conventions import BusinessDayConvention, Compounding, DayCount, Tenor
from alphalab.rates import (
    DepositQuote,
    DiscountCurve,
    FixedLeg,
    FloatingLeg,
    FraTerms,
    Interpolation,
    LegSchedule,
    Pillar,
    RateQuote,
    RatesInputError,
    SwapQuote,
    SwapTerms,
    bootstrap_curve,
    fra_settlement,
    leg_periods,
    rates_terms_from_primitives,
    rates_terms_to_primitives,
    swap_accrued,
    value_fra,
    value_swap,
)

D = Decimal
ANCHOR, SPOT = date(2025, 1, 15), date(2025, 1, 17)
MODIFIED = BusinessDayConvention.MODIFIED_FOLLOWING


@dataclass(frozen=True)
class Weekdays:
    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays


CALENDARS = {"USNY": Weekdays()}


def schedule(step: str) -> LegSchedule:
    return LegSchedule(Tenor.parse(step), MODIFIED, ("USNY",), False)


def swap_terms(rate: str, length: str = "5Y", start: date = SPOT, spread: str = "0") -> SwapTerms:
    return SwapTerms(
        "USD",
        start,
        Tenor.parse(length),
        FixedLeg(D("1"), D(rate), DayCount.THIRTY_360_US, schedule("1Y")),
        FloatingLeg(D("1"), "USD-LIBOR-3M", D(spread), DayCount.ACT_360, schedule("3M"), 2, "TEST"),
    )


def quote(length: str, rate: float, step: str = "3M") -> SwapQuote:
    return SwapQuote(
        SPOT,
        Tenor.parse(length),
        rate,
        Tenor.parse("1Y"),
        DayCount.THIRTY_360_US,
        Tenor.parse(step),
        MODIFIED,
        (Weekdays(),),
        end_of_month=False,
    )


SWAP_RATES = {"2Y": 0.0405, "3Y": 0.0410, "5Y": 0.0418, "7Y": 0.0424, "10Y": 0.0431}


def curve(
    name: str = "USD-3M", discount: DiscountCurve | None = None, shift: float = 0.0
) -> DiscountCurve:
    quotes: list[RateQuote] = [
        DepositQuote(SPOT, SPOT + timedelta(days=days), rate + shift, DayCount.ACT_360)
        for days, rate in [(1, 0.0430), (91, 0.0436)]
    ]
    quotes += [quote(length, rate + shift) for length, rate in SWAP_RATES.items()]
    return bootstrap_curve(
        name,
        "USD",
        ANCHOR,
        DayCount.ACT_365_FIXED,
        Interpolation.LOG_LINEAR_DISCOUNT,
        quotes,
        tolerance=1e-15,
        max_iterations=100,
        discount=discount,
    ).curve


@pytest.mark.parametrize("length", list(SWAP_RATES))
def test_a_swap_struck_at_its_par_rate_is_worth_nothing_on_its_own_curve(length: str) -> None:
    single = curve()
    valued = value_swap(
        swap_terms(str(SWAP_RATES[length]), length),
        discount=single,
        projection=single,
        calendars=CALENDARS,
        fixings={},
    )
    assert abs(valued.value) < 1e-13
    assert valued.par_rate == pytest.approx(SWAP_RATES[length], abs=1e-13)
    assert valued.accrued == 0.0  # nothing has accrued before the start


def test_parity_holds_against_a_stated_ois_discount_curve() -> None:
    ois_quotes: list[RateQuote] = [
        DepositQuote(SPOT, SPOT + timedelta(days=1), 0.0425, DayCount.ACT_360)
    ]
    ois_quotes += [quote(length, rate - 0.0015, "1Y") for length, rate in SWAP_RATES.items()]
    ois = bootstrap_curve(
        "USD-OIS",
        "USD",
        ANCHOR,
        DayCount.ACT_365_FIXED,
        Interpolation.LOG_LINEAR_DISCOUNT,
        ois_quotes,
        tolerance=1e-15,
        max_iterations=100,
    ).curve
    projection = curve(discount=ois)
    terms = swap_terms(str(SWAP_RATES["7Y"]), "7Y")
    valued = value_swap(terms, discount=ois, projection=projection, calendars=CALENDARS, fixings={})
    assert abs(valued.value) < 1e-13
    # Discounting at the OIS curve and projecting off the 3M curve is another valuation
    # than one curve for both: the same swap is not at par on the 3M curve alone.
    alone = value_swap(
        terms, discount=projection, projection=projection, calendars=CALENDARS, fixings={}
    )
    assert abs(alone.value) > 1e-5
    assert valued.valuation_id != alone.valuation_id


def test_a_fixing_already_made_is_read_as_supplied_and_refused_when_absent() -> None:
    terms = swap_terms("0.0418")
    first = leg_periods(terms.floating, terms.start, terms.length, CALENDARS)[0]
    assert first.fixing == ANCHOR  # two business days before the 17th
    # On 3 February the first period's rate is fixed: a flat 4.2% curve from then.
    moved = DiscountCurve(
        "USD-3M",
        "USD",
        date(2025, 2, 3),
        DayCount.ACT_365_FIXED,
        tuple(Pillar(date(2025 + n, 2, 3), math.exp(-0.042 * n)) for n in range(1, 7)),
        Interpolation.LOG_LINEAR_DISCOUNT,
    )
    with pytest.raises(RatesInputError, match="fixing on 2025-01-15 is not supplied"):
        value_swap(terms, discount=moved, projection=moved, calendars=CALENDARS, fixings={})
    key = ("USD-LIBOR-3M", ANCHOR)
    low = value_swap(
        terms, discount=moved, projection=moved, calendars=CALENDARS, fixings={key: D("0.0436")}
    )
    high = value_swap(
        terms, discount=moved, projection=moved, calendars=CALENDARS, fixings={key: D("0.0437")}
    )
    # A basis point more on the first period's fixing: the receiver pays one basis
    # point of that period's accrual more, discounted from its end.
    expected = 0.0001 * first.accrual * moved.discount(first.end)
    assert low.value - high.value == pytest.approx(expected, rel=1e-10)
    assert "fixing=USD-LIBOR-3M|2025-01-15|0.0436" in low.canonical_key.splitlines()
    assert low.valuation_id != high.valuation_id


def test_what_a_swap_has_accrued_is_stated_by_its_conventions() -> None:
    terms = swap_terms("0.04", spread="0.001")
    # 1 March 2025: 44 days into the annual fixed period under 30/360 US, and 43
    # days into the first quarterly floating period under ACT/360.
    day = date(2025, 3, 1)
    fixings = {("USD-LIBOR-3M", ANCHOR): D("0.035")}
    accrued = swap_accrued(terms, day, CALENDARS, fixings)
    fixed = 0.04 * (44 / 360)  # 30/360 US: 17 January to 1 March counts 44 days
    floating = (0.035 + 0.001) * (43 / 360)  # ACT/360: 43 days
    assert accrued == pytest.approx(fixed - floating, rel=1e-14)
    with pytest.raises(RatesInputError, match="not supplied"):
        swap_accrued(terms, day, CALENDARS, {})


def test_a_receiver_loses_about_its_annuity_for_each_basis_point() -> None:
    single = curve()
    terms = swap_terms(str(SWAP_RATES["10Y"]), "10Y")
    base = value_swap(terms, discount=single, projection=single, calendars=CALENDARS, fixings={})
    bumped = single.shifted(0.0001)
    moved = value_swap(terms, discount=bumped, projection=bumped, calendars=CALENDARS, fixings={})
    assert base.annuity is not None
    dv01 = moved.value - base.value
    assert dv01 < 0
    assert dv01 == pytest.approx(-base.annuity * 0.0001, rel=0.05)


def test_an_fra_at_its_forward_is_worth_nothing_and_settles_by_its_formula() -> None:
    single = curve()
    start, end = date(2025, 7, 17), date(2025, 10, 17)
    forward = single.forward_rate(start, end, DayCount.ACT_360)
    fra = FraTerms(
        "USD",
        D("1000000"),
        D(repr(forward)),
        start,
        end,
        DayCount.ACT_360,
        "USD-LIBOR-3M",
        2,
        ("USNY",),
        "TEST",
    )
    valued = value_fra(fra, discount=single, projection=single, calendars=CALENDARS, fixings={})
    assert abs(valued.value) < 1e-6  # on a million
    assert valued.par_rate == pytest.approx(forward, rel=1e-15)
    struck = FraTerms(
        "USD",
        D("1000000"),
        D("0.04"),
        start,
        end,
        DayCount.ACT_360,
        "USD-LIBOR-3M",
        2,
        ("USNY",),
        "TEST",
    )
    # 92 days at ACT/360, fixed at 4.5% against 4%.
    tau = D(92) / D(360)
    expected = D("1000000") * (D("0.045") - D("0.04")) * tau / (1 + D("0.045") * tau)
    assert abs(fra_settlement(struck, D("0.045")) - expected) < D("1e-9")  # on 1,264.27
    settled = DiscountCurve(
        "USD-3M",
        "USD",
        start,
        DayCount.ACT_365_FIXED,
        (Pillar(end, 0.99),),
        Interpolation.LOG_LINEAR_DISCOUNT,
    )
    with pytest.raises(RatesInputError, match="settled at its start"):
        value_fra(struck, discount=settled, projection=settled, calendars=CALENDARS, fixings={})


def test_the_terms_persist_and_read_back_equal() -> None:
    swap = swap_terms("0.0418", spread="-0.0005")
    fra = FraTerms(
        "USD",
        D("1"),
        D("0.04"),
        date(2025, 7, 17),
        date(2025, 10, 17),
        DayCount.ACT_360,
        "USD-LIBOR-3M",
        2,
        ("USNY",),
        "TEST",
    )
    for terms in (swap, fra):
        written = json.loads(json.dumps(rates_terms_to_primitives(terms)))
        assert rates_terms_from_primitives(written) == terms
    with pytest.raises(RatesInputError, match="not one of swap, fra, floater, linker, repo"):
        rates_terms_from_primitives({"kind": "cap"})
    broken = rates_terms_to_primitives(swap)
    broken["fixed"]["day_count"] = "ACT_999"
    with pytest.raises(RatesInputError, match="not rates terms"):
        rates_terms_from_primitives(broken)


def test_terms_state_their_conventions_and_their_calendars() -> None:
    with pytest.raises(RatesInputError, match="stub"):
        swap_terms("0.04", length="18M")  # an annual fixed leg over eighteen months
    with pytest.raises(RatesInputError, match="positive"):
        FixedLeg(D("0"), D("0.04"), DayCount.THIRTY_360_US, schedule("1Y"))
    with pytest.raises(RatesInputError, match="finite Decimal"):
        FixedLeg(D("1"), 0.04, DayCount.THIRTY_360_US, schedule("1Y"))  # type: ignore[arg-type]
    with pytest.raises(RatesInputError, match="no calendar is assumed open"):
        LegSchedule(Tenor.parse("1Y"), MODIFIED, (), False)
    with pytest.raises(RatesInputError, match="no calendar of that id is supplied"):
        leg_periods(swap_terms("0.04").fixed, SPOT, Tenor.parse("5Y"), {})
    single = curve()
    with pytest.raises(RatesInputError, match="is in USD"):
        euro = SwapTerms(
            "EUR", SPOT, Tenor.parse("2Y"), swap_terms("0.04").fixed, swap_terms("0.04").floating
        )
        value_swap(euro, discount=single, projection=single, calendars=CALENDARS, fixings={})
    assert single.zero_rate(date(2030, 1, 17), Compounding.CONTINUOUS) > 0
