"""Floaters, linkers, repos and credit, worked by hand (v4.3, ADR-0065 sections 4 and 6).

On flat curves stated here, under ACT/365F from 15 January 2025:

* **A floater at its reset is worth par** on its own curve: its projected
  coupons telescope, so at zero spread it is worth its notional, and a spread
  adds its own annuity. Once fixed, it is worth ``N * (1 + L * tau) * D(t1)``.
* **A linker's reference index**, worked from two monthly prints: monthly,
  and interpolated by the day of the month, in ``Decimal``. Its principal is
  floored at par when it says so, and its value is today's index ratio times
  its real flows at the real curve.
* **A repo** repays ``1 + rate * tau`` in ``Decimal``, and a forward-starting
  repo at its par rate is worth nothing.
* **Credit.** Hazards of 2% and 3% give survival ``exp(-0.02)`` and
  ``exp(-0.05)``, the hazard constant between them. A risky bond's value is its
  surviving flows plus its recovery on the payment grid. At zero recovery a
  hazard prices exactly as the same spread does.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from alphalab.common.arithmetic import accounting_context
from alphalab.conventions import BusinessDayConvention, Compounding, DayCount, Tenor
from alphalab.rates import (
    CreditCurve,
    DiscountCurve,
    FixedLeg,
    FloaterTerms,
    FloatingLeg,
    Indexation,
    Interpolation,
    LegSchedule,
    LinkerTerms,
    Pillar,
    RatesInputError,
    RepoTerms,
    hazard_curve,
    leg_periods,
    linker_principal,
    rates_terms_from_primitives,
    rates_terms_to_primitives,
    reference_index,
    repo_repayment,
    risky_value,
    spread_curve,
    spread_value,
    value_floater,
    value_linker,
    value_repo,
)

D = Decimal
ANCHOR = date(2025, 1, 15)
ACT365 = DayCount.ACT_365_FIXED
MODIFIED = BusinessDayConvention.MODIFIED_FOLLOWING


@dataclass(frozen=True)
class Weekdays:
    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays


CALENDARS = {"USNY": Weekdays()}
QUARTERLY = LegSchedule(Tenor.parse("3M"), MODIFIED, ("USNY",), False)
SEMI = LegSchedule(Tenor.parse("6M"), MODIFIED, ("USNY",), False)


def flat(
    rate: float, anchor: date = ANCHOR, name: str = "USD-FLAT", years: int = 12
) -> DiscountCurve:
    pillars = tuple(
        Pillar(date(anchor.year + n, anchor.month, anchor.day), math.exp(-rate * n))
        for n in range(1, years)
    )
    # Exactly n years under ACT/365F only on non-leap spans; read the pillars' own times.
    curve = DiscountCurve(name, "USD", anchor, ACT365, pillars, Interpolation.LOG_LINEAR_DISCOUNT)
    return DiscountCurve(
        name,
        "USD",
        anchor,
        ACT365,
        tuple(Pillar(p.day, math.exp(-rate * curve.time(p.day))) for p in pillars),
        Interpolation.LOG_LINEAR_DISCOUNT,
    )


def floater(spread: str = "0", start: date = ANCHOR) -> FloaterTerms:
    leg = FloatingLeg(D("100"), "USD-SOFR", D(spread), DayCount.ACT_360, QUARTERLY, 0, "TEST")
    return FloaterTerms("USD", start, Tenor.parse("2Y"), leg, D("100"))


# --------------------------------------------------------------------------- #
# Floaters
# --------------------------------------------------------------------------- #


def test_a_floater_at_its_reset_is_worth_par_and_a_spread_adds_its_annuity() -> None:
    curve = flat(0.04)
    at_par = value_floater(
        floater(), discount=curve, projection=curve, calendars=CALENDARS, fixings={}
    )
    assert at_par.value == pytest.approx(100.0, abs=1e-12)
    spread = value_floater(
        floater("0.005"), discount=curve, projection=curve, calendars=CALENDARS, fixings={}
    )
    periods = leg_periods(floater().leg, ANCHOR, Tenor.parse("2Y"), CALENDARS)
    annuity = sum(100 * p.accrual * curve.discount(p.end) for p in periods)
    assert spread.value - at_par.value == pytest.approx(0.005 * annuity, rel=1e-12)


def test_a_fixed_floater_is_worth_its_coupon_and_par_at_the_period_end() -> None:
    later = date(2025, 3, 3)
    curve = flat(0.04, later)
    first = leg_periods(floater().leg, ANCHOR, Tenor.parse("2Y"), CALENDARS)[0]
    assert first.fixing == ANCHOR  # a lag of nothing fixes each period at its start
    fixings = {("USD-SOFR", ANCHOR): D("0.0433")}
    valued = value_floater(
        floater(), discount=curve, projection=curve, calendars=CALENDARS, fixings=fixings
    )
    expected = 100 * (1 + 0.0433 * first.accrual) * curve.discount(first.end)
    assert valued.value == pytest.approx(expected, rel=1e-12)
    assert valued.accrued == pytest.approx(100 * 0.0433 * (later - ANCHOR).days / 360, rel=1e-14)
    with pytest.raises(RatesInputError, match="not supplied"):
        value_floater(floater(), discount=curve, projection=curve, calendars=CALENDARS, fixings={})


def test_a_coupon_paid_on_the_valuation_date_is_paid_not_valued() -> None:
    # On 15 April 2025 the first coupon is paid and the second period resets:
    # the floater is worth par again, whatever its first period was fixed at.
    paid = date(2025, 4, 15)
    curve = flat(0.04, paid)
    fixings = {("USD-SOFR", ANCHOR): D("0.0433")}
    valued = value_floater(
        floater(), discount=curve, projection=curve, calendars=CALENDARS, fixings=fixings
    )
    assert valued.value == pytest.approx(100.0, abs=1e-12)
    assert valued.accrued == 0.0


# --------------------------------------------------------------------------- #
# Linkers
# --------------------------------------------------------------------------- #

PRINTS = {
    ("US-CPI", date(2024, 10, 1)): D("315.0"),
    ("US-CPI", date(2024, 11, 1)): D("316.5"),
    ("US-CPI", date(2026, 10, 1)): D("330.0"),
    ("US-CPI", date(2026, 11, 1)): D("331.0"),
}


def linker(
    indexation: Indexation = Indexation.DAILY_INTERPOLATED, base: str = "300", floored: bool = True
) -> LinkerTerms:
    leg = FixedLeg(D("100"), D("0.0125"), DayCount.ACT_ACT_ICMA, SEMI)
    return LinkerTerms(
        "USD", ANCHOR, Tenor.parse("2Y"), leg, "US-CPI", 3, indexation, D(base), floored
    )


def test_a_reference_index_is_read_from_its_prints_by_lag_and_indexation() -> None:
    # 15 January: three months back is October, two November; day 15 of 31.
    assert reference_index(linker(Indexation.MONTHLY), ANCHOR, PRINTS) == D("315.0")
    with accounting_context():
        expected = D("315.0") + D(14) / D(31) * (D("316.5") - D("315.0"))
    assert reference_index(linker(), ANCHOR, PRINTS) == expected
    with pytest.raises(RatesInputError, match="print for November 2024 is not supplied"):
        reference_index(linker(), ANCHOR, {("US-CPI", date(2024, 10, 1)): D("315.0")})


def test_a_linkers_principal_is_floored_when_it_says_so() -> None:
    # Maturity 15 January 2027: its reference index is 330 + 14/31 of the way to 331.
    high = linker_principal(linker(base="300"), PRINTS, CALENDARS)
    with accounting_context():
        ratio = (D("330.0") + D(14) / D(31) * D("1.0")) / D("300")
        assert high == D("100") * ratio
    assert linker_principal(linker(base="340"), PRINTS, CALENDARS) == D("100")  # floored at par
    assert linker_principal(linker(base="340", floored=False), PRINTS, CALENDARS) < D("100")


def test_a_linker_is_worth_todays_index_ratio_times_its_real_flows() -> None:
    real = flat(0.015)
    valued = value_linker(linker(), real=real, calendars=CALENDARS, prints=PRINTS)
    periods = leg_periods(linker().leg, ANCHOR, Tenor.parse("2Y"), CALENDARS)
    ratio = float(reference_index(linker(), ANCHOR, PRINTS) / D("300"))
    real_flows = sum(100 * 0.0125 * p.accrual * real.discount(p.end) for p in periods)
    expected = ratio * (real_flows + 100 * real.discount(periods[-1].end))
    assert valued.value == pytest.approx(expected, rel=1e-13)
    assert [p.accrual for p in periods] == [0.5] * 4  # ACT/ACT (ICMA): a half each, exactly


# --------------------------------------------------------------------------- #
# Repos
# --------------------------------------------------------------------------- #


def repo(rate: str = "0.043", start: date = ANCHOR, end: date = date(2025, 1, 22)) -> RepoTerms:
    return RepoTerms("USD", "UST-2035", D(rate), start, end, DayCount.ACT_360, D("0.02"))


def test_a_repo_repays_its_rate_over_its_days() -> None:
    tau = D(repr(7 / 360))
    with accounting_context():
        assert repo_repayment(repo()) == 1 + D("0.043") * tau
    curve = flat(0.04)
    started = value_repo(repo(), discount=curve)
    assert started.value == pytest.approx(
        (1 + 0.043 * 7 / 360) * curve.discount(date(2025, 1, 22)), rel=1e-15
    )
    forward = repo(start=date(2025, 2, 3), end=date(2025, 3, 3))
    par = value_repo(forward, discount=curve).par_rate
    assert par is not None
    at_par = value_repo(repo(repr(par), date(2025, 2, 3), date(2025, 3, 3)), discount=curve)
    assert abs(at_par.value) < 1e-15


def test_a_repo_states_a_haircut_below_one() -> None:
    with pytest.raises(RatesInputError, match="below one"):
        RepoTerms("USD", "UST-2035", D("0.04"), ANCHOR, date(2025, 2, 15), DayCount.ACT_360, D("1"))
    with pytest.raises(RatesInputError, match="later date"):
        repo(end=ANCHOR)


# --------------------------------------------------------------------------- #
# Credit
# --------------------------------------------------------------------------- #

ONE, TWO = date(2026, 1, 15), date(2027, 1, 15)


def test_hazards_give_survival_and_hold_between_their_dates() -> None:
    credit = hazard_curve("ACME-HAZARD", "USD", ANCHOR, ACT365, [(ONE, 0.02), (TWO, 0.03)], 0.4)
    survival = credit.survival
    t1, t2 = survival.time(ONE), survival.time(TWO)
    assert survival.discount(ONE) == pytest.approx(math.exp(-0.02 * t1), rel=1e-15)
    assert survival.discount(TWO) == pytest.approx(
        math.exp(-0.02 * t1 - 0.03 * (t2 - t1)), rel=1e-15
    )
    middle = date(2026, 7, 16)
    hazard = -math.log(survival.discount(TWO) / survival.discount(middle)) / (
        t2 - survival.time(middle)
    )
    assert hazard == pytest.approx(0.03, rel=1e-12)


def test_a_risky_bond_is_its_surviving_flows_and_its_recovery() -> None:
    discount = flat(0.04)
    credit = hazard_curve("ACME-HAZARD", "USD", ANCHOR, ACT365, [(ONE, 0.02), (TWO, 0.02)], 0.4)
    flows = [(ONE, 5.0), (TWO, 105.0)]
    valued = risky_value(flows, discount=discount, credit=credit, notional=100.0)
    d1, d2 = discount.discount(ONE), discount.discount(TWO)
    q1, q2 = credit.survival.discount(ONE), credit.survival.discount(TWO)
    expected = 5 * d1 * q1 + 105 * d2 * q2 + 0.4 * 100 * (d1 * (1 - q1) + d2 * (q1 - q2))
    assert valued.value == pytest.approx(expected, rel=1e-15)
    assert dict(valued.legs)["recovery"] == pytest.approx(
        0.4 * 100 * (d1 * (1 - q1) + d2 * (q1 - q2)), rel=1e-15
    )


def test_at_zero_recovery_a_hazard_prices_as_the_same_spread() -> None:
    discount = flat(0.04)
    flows = [(ONE, 5.0), (TWO, 105.0)]
    hazard = hazard_curve("H", "USD", ANCHOR, ACT365, [(ONE, 0.02), (TWO, 0.02)], 0.0)
    spread = spread_curve(
        "S", "USD", ANCHOR, ACT365, [(ONE, 0.02), (TWO, 0.02)], Interpolation.LOG_LINEAR_DISCOUNT
    )
    risky = risky_value(flows, discount=discount, credit=hazard, notional=100.0)
    spread_valued = spread_value(flows, discount=discount, spread=spread)
    assert risky.value == pytest.approx(spread_valued.value, rel=1e-14)
    assert spread.zero_rate(TWO, Compounding.CONTINUOUS) == pytest.approx(0.02, rel=1e-14)
    assert risky.valuation_id != spread_valued.valuation_id


def test_credit_is_refused_where_it_cannot_be() -> None:
    with pytest.raises(RatesInputError, match="not negative"):
        hazard_curve("H", "USD", ANCHOR, ACT365, [(ONE, -0.01)], 0.4)
    with pytest.raises(RatesInputError, match=r"in \[0, 1\)"):
        hazard_curve("H", "USD", ANCHOR, ACT365, [(ONE, 0.01)], 1.0)
    rising = DiscountCurve(
        "Q",
        "USD",
        ANCHOR,
        ACT365,
        (Pillar(ONE, 0.98), Pillar(TWO, 0.99)),
        Interpolation.LOG_LINEAR_DISCOUNT,
    )
    with pytest.raises(RatesInputError, match="never rises"):
        CreditCurve(rising, 0.4)
    euro = DiscountCurve(
        "E", "EUR", ANCHOR, ACT365, (Pillar(ONE, 0.98),), Interpolation.LOG_LINEAR_DISCOUNT
    )
    with pytest.raises(RatesInputError, match="one currency"):
        spread_value([(ONE, 1.0)], discount=flat(0.04), spread=euro)


def test_the_new_terms_persist_and_state_their_conventions() -> None:
    for terms in (floater("0.001"), linker(), repo()):
        written = json.loads(json.dumps(rates_terms_to_primitives(terms)))
        assert rates_terms_from_primitives(written) == terms
    with pytest.raises(RatesInputError, match="positive number of months"):
        LinkerTerms(
            "USD",
            ANCHOR,
            Tenor.parse("2Y"),
            linker().leg,
            "US-CPI",
            0,
            Indexation.MONTHLY,
            D("300"),
            True,
        )
    with pytest.raises(RatesInputError, match="stub"):
        FloaterTerms("USD", ANCHOR, Tenor.parse("13M"), floater().leg, D("100"))
