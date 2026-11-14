"""The world's day counts (v4.3, N21, GAP-36).

Each basis is held to its published definition (ISDA 2006 Definitions, section
4.16; ICMA Rule 251), restated here independently as an exact ``Fraction`` over
the calendar, on hand-checkable periods and on a few hundred random ones. The
terms rule is held too: a basis that needs the instrument's terms refuses
without them, a term it does not use is refused, and the three bases with no
single year never divide by one. The consumers follow: a bond accrues under
ACT/ACT (ICMA) by its coupon period, and a daily accrual refuses what cannot
weight a day.
"""

from __future__ import annotations

import calendar
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction

import pytest

from alphalab.conventions.daycount import (
    DayCount,
    DayCountTerms,
    day_count_days,
    require_accrual_basis,
    required_terms,
    year_fraction,
)
from alphalab.conventions.exceptions import ConventionInputError

# --------------------------------------------------------------------------- #
# Independent restatements of the definitions
# --------------------------------------------------------------------------- #


def _days_in(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def _act_act_isda(start: date, end: date) -> Fraction:
    """ISDA 4.16(b): the days in each year over its length, summed."""

    if start.year == end.year:
        return Fraction((end - start).days, _days_in(start.year))
    total = Fraction((date(start.year + 1, 1, 1) - start).days, _days_in(start.year))
    total += end.year - start.year - 1
    total += Fraction((end - date(end.year, 1, 1)).days, _days_in(end.year))
    return total


def _thirty_e(start: date, end: date) -> int:
    """ISDA 4.16(g): both 31s become 30."""

    d1, d2 = min(start.day, 30), min(end.day, 30)
    return 360 * (end.year - start.year) + 30 * (end.month - start.month) + d2 - d1


def _month_end(day: date) -> bool:
    return (day + timedelta(days=1)).month != day.month


def _thirty_e_isda(start: date, end: date, termination: date) -> int:
    """ISDA 4.16(h): month ends become 30, but not a February termination date."""

    d1 = 30 if _month_end(start) else start.day
    d2 = 30 if _month_end(end) and not (end == termination and end.month == 2) else end.day
    return 360 * (end.year - start.year) + 30 * (end.month - start.month) + d2 - d1


def _no_leap(start: date, end: date) -> int:
    day, count = start, 0
    while day < end:
        day += timedelta(days=1)
        if not (day.month == 2 and day.day == 29):
            count += 1
    return count


def _random_dates(seed: int, count: int) -> list[tuple[date, date]]:
    rng = random.Random(seed)
    pairs = []
    for _ in range(count):
        start = date(1999, 1, 1) + timedelta(days=rng.randint(0, 12000))
        pairs.append((start, start + timedelta(days=rng.randint(0, 2000))))
    return pairs


# --------------------------------------------------------------------------- #
# Each basis against its definition
# --------------------------------------------------------------------------- #


def test_act_act_isda_splits_a_period_at_each_new_year() -> None:
    # 2003-11-01 to 2004-05-01: 61 days of 2003 over 365, 121 days of 2004 over 366.
    start, end = date(2003, 11, 1), date(2004, 5, 1)

    assert year_fraction(start, end, DayCount.ACT_ACT_ISDA) == float(
        Fraction(61, 365) + Fraction(121, 366)
    )
    assert year_fraction(date(2024, 1, 1), date(2025, 1, 1), DayCount.ACT_ACT_ISDA) == 1.0
    # Five anniversaries are not five years: 184 days of 2019 over 365, four whole
    # years, then 182 days of 2024 over 366.
    assert year_fraction(date(2019, 7, 1), date(2024, 7, 1), DayCount.ACT_ACT_ISDA) == float(
        Fraction(184, 365) + 4 + Fraction(182, 366)
    )


@pytest.mark.parametrize(("start", "end"), _random_dates(1, 300))
def test_act_act_isda_is_its_definition_exactly(start: date, end: date) -> None:
    assert year_fraction(start, end, DayCount.ACT_ACT_ISDA) == float(_act_act_isda(start, end))


@pytest.mark.parametrize(
    ("start", "end", "us", "eurobond"),
    [
        (date(2025, 1, 29), date(2025, 3, 31), 62, 61),
        (date(2025, 1, 31), date(2025, 3, 31), 60, 60),
        (date(2025, 1, 31), date(2025, 2, 28), 28, 28),
        (date(2024, 2, 29), date(2024, 8, 31), 182, 181),
    ],
)
def test_the_eurobond_basis_turns_every_31_into_30(
    start: date, end: date, us: int, eurobond: int
) -> None:
    assert day_count_days(start, end, DayCount.THIRTY_360_US) == us
    assert day_count_days(start, end, DayCount.THIRTY_E_360) == eurobond


@pytest.mark.parametrize(("start", "end"), _random_dates(2, 300))
def test_the_eurobond_basis_is_its_definition(start: date, end: date) -> None:
    assert day_count_days(start, end, DayCount.THIRTY_E_360) == _thirty_e(start, end)
    assert year_fraction(start, end, DayCount.THIRTY_E_360) == _thirty_e(start, end) / 360


def test_thirty_e_isda_spares_a_february_termination_date() -> None:
    terms = DayCountTerms(termination=date(2024, 2, 29))
    later = DayCountTerms(termination=date(2030, 1, 1))

    # A February month end that is the termination date stays 29 ...
    assert (
        day_count_days(
            date(2023, 8, 31), date(2024, 2, 29), DayCount.THIRTY_E_360_ISDA, terms=terms
        )
        == 179
    )
    # ... and any other month end, February's included, becomes 30.
    assert (
        day_count_days(
            date(2023, 8, 31), date(2024, 2, 29), DayCount.THIRTY_E_360_ISDA, terms=later
        )
        == 180
    )
    assert (
        day_count_days(
            date(2024, 2, 29), date(2024, 8, 31), DayCount.THIRTY_E_360_ISDA, terms=later
        )
        == 180
    )


@pytest.mark.parametrize(("start", "end"), _random_dates(3, 300))
def test_thirty_e_isda_is_its_definition(start: date, end: date) -> None:
    terms = DayCountTerms(termination=end)

    assert day_count_days(start, end, DayCount.THIRTY_E_360_ISDA, terms=terms) == _thirty_e_isda(
        start, end, end
    )


def test_act_act_icma_divides_by_the_frequency_times_the_period() -> None:
    period = DayCountTerms(period_start=date(2003, 11, 1), period_end=date(2004, 5, 1), frequency=2)

    assert (
        year_fraction(date(2003, 11, 1), date(2004, 5, 1), DayCount.ACT_ACT_ICMA, terms=period)
        == 0.5
    )
    assert year_fraction(
        date(2003, 11, 1), date(2004, 2, 1), DayCount.ACT_ACT_ICMA, terms=period
    ) == float(Fraction(92, 2 * 182))


def test_act_act_icma_refuses_an_accrual_outside_its_period() -> None:
    period = DayCountTerms(
        period_start=date(2024, 1, 15), period_end=date(2024, 7, 15), frequency=2
    )

    with pytest.raises(ConventionInputError, match="not within"):
        year_fraction(date(2024, 1, 10), date(2024, 3, 1), DayCount.ACT_ACT_ICMA, terms=period)


@pytest.mark.parametrize(
    ("period_start", "period_end", "frequency", "denominator"),
    [
        (date(2023, 3, 1), date(2024, 3, 1), 1, 366),  # annual, 29 Feb 2024 inside
        (date(2024, 3, 1), date(2025, 3, 1), 1, 365),  # annual, no 29 February
        (date(2023, 9, 1), date(2024, 2, 28), 2, 366),  # semi-annual, ends in a leap year
        (date(2024, 9, 1), date(2025, 3, 1), 2, 365),  # semi-annual, ends in a common year
    ],
)
def test_act_365l_counts_366_when_a_leap_day_bears_on_the_period(
    period_start: date, period_end: date, frequency: int, denominator: int
) -> None:
    terms = DayCountTerms(period_start=period_start, period_end=period_end, frequency=frequency)

    assert year_fraction(period_start, period_end, DayCount.ACT_365_LEAP, terms=terms) == float(
        Fraction((period_end - period_start).days, denominator)
    )


@pytest.mark.parametrize(("start", "end"), _random_dates(4, 300))
def test_no_leap_365_is_its_definition(start: date, end: date) -> None:
    assert day_count_days(start, end, DayCount.NO_LEAP_365) == _no_leap(start, end)
    assert day_count_days(end, start, DayCount.NO_LEAP_365) == -_no_leap(start, end)


def test_no_leap_365_gives_a_leap_year_one_whole_year() -> None:
    assert year_fraction(date(2024, 1, 1), date(2025, 1, 1), DayCount.NO_LEAP_365) == 1.0


def test_bus_252_counts_business_days_from_start_to_before_end() -> None:
    week = DayCountTerms(holidays=frozenset({date(2025, 4, 21)}), weekend=frozenset({5, 6}))
    # Monday 14 April to Monday 28 April 2025: ten weekdays, one of them a holiday.
    assert (
        day_count_days(date(2025, 4, 14), date(2025, 4, 28), DayCount.BUSINESS_252, terms=week) == 9
    )
    assert (
        year_fraction(date(2025, 4, 14), date(2025, 4, 28), DayCount.BUSINESS_252, terms=week)
        == 9 / 252
    )
    # An empty holiday set is a statement that there are none.
    none = DayCountTerms(holidays=frozenset(), weekend=frozenset({5, 6}))
    assert (
        day_count_days(date(2025, 4, 14), date(2025, 4, 28), DayCount.BUSINESS_252, terms=none)
        == 10
    )


def test_a_weekend_is_whatever_the_calendar_says_it_is() -> None:
    friday_saturday = DayCountTerms(holidays=frozenset(), weekend=frozenset({4, 5}))

    assert (
        day_count_days(
            date(2025, 4, 13), date(2025, 4, 20), DayCount.BUSINESS_252, terms=friday_saturday
        )
        == 5
    )


def test_the_bases_disagree_on_the_same_dates() -> None:
    start, end = date(2024, 1, 31), date(2024, 7, 31)
    fractions = {
        basis: year_fraction(start, end, basis)
        for basis in (
            DayCount.ACT_365_FIXED,
            DayCount.ACT_360,
            DayCount.THIRTY_360_US,
            DayCount.ACT_ACT_ISDA,
            DayCount.NO_LEAP_365,
        )
    }

    assert len(set(fractions.values())) == len(fractions)  # five conventions, five answers
    assert fractions[DayCount.ACT_ACT_ISDA] == 182 / 366


# --------------------------------------------------------------------------- #
# The terms rule
# --------------------------------------------------------------------------- #


def test_every_basis_states_its_terms() -> None:
    assert {basis: required_terms(basis) for basis in DayCount} == {
        DayCount.ACT_365_FIXED: frozenset(),
        DayCount.ACT_360: frozenset(),
        DayCount.THIRTY_360_US: frozenset(),
        DayCount.THIRTY_E_360: frozenset(),
        DayCount.THIRTY_E_360_ISDA: frozenset({"termination"}),
        DayCount.ACT_ACT_ISDA: frozenset(),
        DayCount.ACT_ACT_ICMA: frozenset({"period_start", "period_end", "frequency"}),
        DayCount.ACT_365_LEAP: frozenset({"period_start", "period_end", "frequency"}),
        DayCount.NO_LEAP_365: frozenset(),
        DayCount.BUSINESS_252: frozenset({"holidays", "weekend"}),
    }


@pytest.mark.parametrize(
    "basis",
    [
        DayCount.THIRTY_E_360_ISDA,
        DayCount.ACT_ACT_ICMA,
        DayCount.ACT_365_LEAP,
        DayCount.BUSINESS_252,
    ],
)
def test_a_basis_that_needs_terms_refuses_without_them(basis: DayCount) -> None:
    with pytest.raises(ConventionInputError, match="never infers"):
        year_fraction(date(2024, 1, 15), date(2024, 3, 15), basis)


def test_a_term_the_basis_does_not_use_is_refused_not_ignored() -> None:
    with pytest.raises(ConventionInputError, match="refused rather than ignored"):
        year_fraction(
            date(2024, 1, 15),
            date(2024, 3, 15),
            DayCount.ACT_360,
            terms=DayCountTerms(holidays=frozenset()),
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"frequency": 0},
        {"frequency": True},
        {"weekend": frozenset({7})},
        {"termination": "2024-01-01"},
    ],
)
def test_malformed_terms_are_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ConventionInputError):
        DayCountTerms(**fields)  # type: ignore[arg-type]


def test_a_daily_accrual_takes_only_a_basis_that_weights_a_day_alone() -> None:
    for basis in (
        DayCount.ACT_365_FIXED,
        DayCount.ACT_360,
        DayCount.THIRTY_360_US,
        DayCount.THIRTY_E_360,
        DayCount.NO_LEAP_365,
    ):
        require_accrual_basis(basis)
    for basis in (DayCount.ACT_ACT_ISDA, DayCount.ACT_ACT_ICMA, DayCount.ACT_365_LEAP):
        with pytest.raises(ConventionInputError):
            require_accrual_basis(basis)
    for basis in (DayCount.THIRTY_E_360_ISDA, DayCount.BUSINESS_252):
        with pytest.raises(ConventionInputError, match="daily accrual"):
            require_accrual_basis(basis)


# --------------------------------------------------------------------------- #
# The consumers
# --------------------------------------------------------------------------- #


def test_a_bond_accrues_under_act_act_icma_by_its_coupon_period() -> None:
    from alphalab.conventions.rates import Compounding
    from alphalab.macro.bond import Bond, accrued_interest

    bond = Bond(
        face=Decimal("1000"),
        coupon_rate=0.05,
        frequency=Compounding.SEMI_ANNUAL,
        issue_date=date(2020, 1, 15),
        maturity=date(2030, 1, 15),
        day_count=DayCount.ACT_ACT_ICMA,
        currency="USD",
    )
    # 15 Jan to 1 Apr 2024 is 77 of the 182 days to 15 Jul: 25 * 77 / 182.
    accrued = accrued_interest(bond, date(2024, 4, 1))

    assert accrued == (Decimal("25") * Decimal(str(77 / 182))).quantize(
        Decimal("0.00000001"), rounding=ROUND_HALF_EVEN
    )


def test_a_bond_has_no_calendar_for_bus_252() -> None:
    from alphalab.conventions.rates import Compounding
    from alphalab.macro.bond import Bond
    from alphalab.macro.exceptions import MacroInputError

    with pytest.raises(MacroInputError, match="business calendar"):
        Bond(
            face=Decimal("1000"),
            coupon_rate=0.1,
            frequency=Compounding.SEMI_ANNUAL,
            issue_date=date(2020, 1, 1),
            maturity=date(2027, 1, 1),
            day_count=DayCount.BUSINESS_252,
            currency="BRL",
        )


def test_borrow_terms_refuse_a_basis_that_cannot_weight_a_day() -> None:
    from alphalab.instrument.borrow import BorrowAvailability, BorrowTerms
    from alphalab.instrument.exceptions import InstrumentInputError

    with pytest.raises(InstrumentInputError, match="accrues daily"):
        BorrowTerms(
            entries=(
                BorrowAvailability(
                    effective_at=0.0, available=Decimal("100"), annual_rate=Decimal("0.01")
                ),
            ),
            day_count=DayCount.ACT_ACT_ISDA,
        )


def test_an_fx_forward_refuses_a_basis_that_needs_an_instruments_terms() -> None:
    from alphalab.portfolio.exceptions import PortfolioError
    from alphalab.portfolio.fx_research import ForwardTerms

    with pytest.raises(PortfolioError, match="spot to value date"):
        ForwardTerms(date(2025, 4, 30), date(2025, 1, 30), 0.03, 0.04, DayCount.ACT_ACT_ICMA)
    terms = ForwardTerms(date(2025, 4, 30), date(2025, 1, 30), 0.03, 0.04, DayCount.ACT_ACT_ISDA)
    assert terms.years == 90 / 365


# --------------------------------------------------------------------------- #
# Published worked examples (plan GR-2)
# --------------------------------------------------------------------------- #
#
# ISDA's November 1998 memorandum on EMU and market conventions (mktc1198)
# works ACT/ACT through regular, short and long periods, and ISDA's 2008 note
# on 30/360 day count conventions works 30E/360 and 30E/360 (ISDA) through
# three schedules. QuantLib's day-counter tests reproduce both
# (test-suite/daycounters.cpp); the values below are theirs. They are held to
# 5e-11: one, 1.001377348600, is good to ten decimals and padded with zeros --
# the definition's exact 184/365 + 182/366 is 1.00137734860394 -- so ten
# decimals is the precision the published figures carry.
PUBLISHED = 5e-11

#: ACT/ACT (ISDA): (start, end, published year fraction).
ISDA_1998_ACT_ACT_ISDA = [
    (date(2003, 11, 1), date(2004, 5, 1), 0.497724380567),
    (date(1999, 2, 1), date(1999, 7, 1), 0.410958904110),
    (date(1999, 7, 1), date(2000, 7, 1), 1.001377348600),
    (date(2002, 8, 15), date(2003, 7, 15), 0.915068493151),
    (date(2003, 7, 15), date(2004, 1, 15), 0.504004790778),
    (date(1999, 7, 30), date(2000, 1, 30), 0.503892506924),
    (date(2000, 1, 30), date(2000, 6, 30), 0.415300546448),
]

#: ACT/ACT (ICMA): (start, end, determination period start and end, frequency, published).
ISDA_1998_ACT_ACT_ICMA = [
    (date(2003, 11, 1), date(2004, 5, 1), date(2003, 11, 1), date(2004, 5, 1), 2, 0.5),
    (date(1999, 2, 1), date(1999, 7, 1), date(1998, 7, 1), date(1999, 7, 1), 1, 0.410958904110),
    (date(1999, 7, 1), date(2000, 7, 1), date(1999, 7, 1), date(2000, 7, 1), 1, 1.0),
    (date(2003, 7, 15), date(2004, 1, 15), date(2003, 7, 15), date(2004, 1, 15), 2, 0.5),
    (date(1999, 7, 30), date(2000, 1, 30), date(1999, 7, 30), date(2000, 1, 30), 2, 0.5),
    (date(2000, 1, 30), date(2000, 6, 30), date(2000, 1, 30), date(2000, 7, 30), 2, 0.417582417582),
]


@pytest.mark.parametrize(("start", "end", "published"), ISDA_1998_ACT_ACT_ISDA)
def test_act_act_isda_matches_isdas_worked_examples(
    start: date, end: date, published: float
) -> None:
    assert abs(year_fraction(start, end, DayCount.ACT_ACT_ISDA) - published) <= PUBLISHED


@pytest.mark.parametrize(
    ("start", "end", "period_start", "period_end", "frequency", "published"), ISDA_1998_ACT_ACT_ICMA
)
def test_act_act_icma_matches_isdas_worked_examples(
    start: date, end: date, period_start: date, period_end: date, frequency: int, published: float
) -> None:
    terms = DayCountTerms(period_start=period_start, period_end=period_end, frequency=frequency)

    assert (
        abs(year_fraction(start, end, DayCount.ACT_ACT_ICMA, terms=terms) - published) <= PUBLISHED
    )


def test_a_long_icma_period_is_the_sum_of_its_determination_periods() -> None:
    """ISDA 4.16(c): 15 Aug 2002 to 15 Jul 2003, semi-annual, a long first coupon.

    The calculation period spans two determination periods, so its fraction is
    the sum of its part in each, each over its own period: the caller, who holds
    the schedule, splits it. ISDA's published result is 0.915760869565.
    """

    first = DayCountTerms(period_start=date(2002, 7, 15), period_end=date(2003, 1, 15), frequency=2)
    second = DayCountTerms(
        period_start=date(2003, 1, 15), period_end=date(2003, 7, 15), frequency=2
    )
    with pytest.raises(ConventionInputError, match="not within"):
        year_fraction(date(2002, 8, 15), date(2003, 7, 15), DayCount.ACT_ACT_ICMA, terms=second)

    total = year_fraction(
        date(2002, 8, 15), date(2003, 1, 15), DayCount.ACT_ACT_ICMA, terms=first
    ) + year_fraction(date(2003, 1, 15), date(2003, 7, 15), DayCount.ACT_ACT_ICMA, terms=second)

    assert abs(total - 0.915760869565) <= PUBLISHED


#: ISDA 2008, 30/360: (start, end, 30E/360 days, 30E/360 (ISDA) days, termination).
ISDA_2008_THIRTY_360 = [
    # Example 1: a regular semi-annual schedule, terminating 20 Aug 2009.
    (date(2006, 8, 20), date(2007, 2, 20), 180, 180, date(2009, 8, 20)),
    (date(2007, 2, 20), date(2007, 8, 20), 180, 180, date(2009, 8, 20)),
    (date(2008, 2, 20), date(2008, 8, 20), 180, 180, date(2009, 8, 20)),
    # Example 2: month-end dates, terminating 29 Feb 2012.
    (date(2006, 2, 28), date(2006, 8, 31), 182, 180, date(2012, 2, 29)),
    (date(2006, 8, 31), date(2007, 2, 28), 178, 180, date(2012, 2, 29)),
    (date(2007, 8, 31), date(2008, 2, 29), 179, 180, date(2012, 2, 29)),
    (date(2008, 2, 29), date(2008, 8, 31), 181, 180, date(2012, 2, 29)),
    (date(2011, 8, 31), date(2012, 2, 29), 179, 179, date(2012, 2, 29)),
    # Example 3: assorted dates, terminating 29 Feb 2008.
    (date(2006, 1, 31), date(2006, 2, 28), 28, 30, date(2008, 2, 29)),
    (date(2006, 1, 30), date(2006, 2, 28), 28, 30, date(2008, 2, 29)),
    (date(2006, 2, 28), date(2006, 3, 3), 5, 3, date(2008, 2, 29)),
    (date(2006, 2, 14), date(2006, 2, 28), 14, 16, date(2008, 2, 29)),
    (date(2006, 9, 30), date(2006, 10, 31), 30, 30, date(2008, 2, 29)),
    (date(2006, 10, 31), date(2006, 11, 28), 28, 28, date(2008, 2, 29)),
    (date(2007, 8, 31), date(2008, 2, 28), 178, 178, date(2008, 2, 29)),
    (date(2008, 2, 28), date(2008, 8, 28), 180, 180, date(2008, 2, 29)),
    (date(2008, 2, 28), date(2008, 8, 30), 182, 182, date(2008, 2, 29)),
    (date(2008, 2, 28), date(2008, 8, 31), 182, 182, date(2008, 2, 29)),
    (date(2008, 2, 29), date(2009, 2, 28), 359, 360, date(2008, 2, 29)),
    (date(2008, 2, 29), date(2008, 3, 30), 31, 30, date(2008, 2, 29)),
    (date(2008, 2, 29), date(2008, 3, 31), 31, 30, date(2008, 2, 29)),
]


@pytest.mark.parametrize(("start", "end", "eurobond", "isda", "termination"), ISDA_2008_THIRTY_360)
def test_thirty_e_360_and_its_isda_form_match_isdas_worked_examples(
    start: date, end: date, eurobond: int, isda: int, termination: date
) -> None:
    terms = DayCountTerms(termination=termination)

    assert day_count_days(start, end, DayCount.THIRTY_E_360) == eurobond
    assert day_count_days(start, end, DayCount.THIRTY_E_360_ISDA, terms=terms) == isda


#: Brazilian business days, the holidays in each period stated: QuantLib's
#: Business/252 reference values under its Brazil calendar.
BRAZIL_2006 = frozenset(
    {
        date(2006, 2, 27),  # Carnival
        date(2006, 2, 28),  # Carnival
        date(2006, 4, 14),  # Good Friday
        date(2006, 4, 21),  # Tiradentes
        date(2006, 5, 1),  # Labour Day
    }
)


@pytest.mark.parametrize(
    ("start", "end", "published"),
    [
        (date(2002, 2, 1), date(2002, 2, 4), 0.0039682539683),
        (date(2005, 12, 19), date(2006, 1, 2), 0.0396825396825),
        (date(2006, 1, 2), date(2006, 3, 13), 0.1904761904762),
        (date(2006, 3, 13), date(2006, 5, 15), 0.1666666666667),
        (date(2006, 3, 17), date(2006, 5, 15), 0.1507936507937),
    ],
)
def test_bus_252_matches_a_reference_implementation(
    start: date, end: date, published: float
) -> None:
    terms = DayCountTerms(holidays=BRAZIL_2006, weekend=frozenset({5, 6}))

    assert (
        abs(year_fraction(start, end, DayCount.BUSINESS_252, terms=terms) - published) <= PUBLISHED
    )
    # Backwards the count is signed, as QuantLib's -0.1507936507937 is; a year
    # fraction refuses a reversed period instead.
    assert day_count_days(end, start, DayCount.BUSINESS_252, terms=terms) == -round(published * 252)
