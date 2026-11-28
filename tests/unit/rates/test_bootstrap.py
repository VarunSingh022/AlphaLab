"""A curve bootstrapped from quotes, each pillar solved to a stated tolerance (v4.3, ADR-0065 s. 3).

**GR-6, the published worked example.** John C. Hull, *Options, Futures, and
Other Derivatives* (8th edition, Pearson, 2012), chapter 4, "the bootstrap
method", Tables 4.3 and 4.4. Treasury zero rates are bootstrapped from five
bonds of principal 100, coupons paid every six months:

=========  ==============  ======  ===============================
Maturity   Annual coupon   Price   Zero rate, continuous (Table 4.4)
=========  ==============  ======  ===============================
0.25       0               97.5    10.127%
0.50       0               94.9    10.469%
1.00       0               90.0    10.536%
1.50       8               96.0    10.681%
2.00       12              101.6   10.808%
=========  ==============  ======  ===============================

The times are stated in years, so the curve's axis here is 30/360 from 15
January 2025, on which each maturity's date is exactly its time and each
six-month coupon period accrues exactly a half. The bootstrap reproduces each
published rate to the three decimals published, under both interpolations:
every coupon falls on a pillar, so the example's figures need none, as its text
says.

The same book's swap chapter ("determining LIBOR/swap zero rates") extends
zero rates of 4%, 4.5% and 4.8% at six, twelve and eighteen months with a
two-year swap at 5%, paid every six months, to a two-year zero rate of 4.953%.
That is held too.

Beyond the published figures:

* **Every quote type reprices** -- deposits, FRAs, futures with their convexity
  adjustment, par swaps and bonds -- to its tolerance, under each
  interpolation, and a quote with dates between pillars is solved under the
  interpolation the curve is read by.
* **Multi-curve.** A projection curve bootstrapped against a stated OIS
  discount curve reprices its swaps at that curve, and is another curve than
  one bootstrapped on its own.
* **Refused, with the reason**: a quote no discount factor in range reprices,
  two quotes at one pillar, a date before the anchor, a bond for a projection
  curve, a discount curve from another date, and a pillar not solved within its
  iterations.
* **Identity** is the problem's: the curve's derivation, never its solved bits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import pytest

from alphalab.conventions import BusinessDayConvention, Compounding, DayCount, Tenor
from alphalab.rates import (
    BondQuote,
    BootstrapError,
    CurveBootstrap,
    DepositQuote,
    DiscountCurve,
    FraQuote,
    FutureQuote,
    Interpolation,
    Pillar,
    RateQuote,
    RatesInputError,
    SwapQuote,
    bootstrap_curve,
)

THIRTY = DayCount.THIRTY_360_US
HULL = date(2025, 1, 15)
MODIFIED = BusinessDayConvention.MODIFIED_FOLLOWING


@dataclass(frozen=True)
class Weekdays:
    """Open Monday to Friday, except the holidays it names."""

    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays


PLAIN = (Weekdays(),)


def bootstrap(
    quotes: list[RateQuote],
    interpolation: Interpolation = Interpolation.LOG_LINEAR_DISCOUNT,
    *,
    anchor: date = HULL,
    basis: DayCount = THIRTY,
    discount: DiscountCurve | None = None,
    tolerance: float = 1e-14,
    max_iterations: int = 100,
    name: str = "USD-TEST",
) -> CurveBootstrap:
    return bootstrap_curve(
        name,
        "USD",
        anchor,
        basis,
        interpolation,
        quotes,
        tolerance=tolerance,
        max_iterations=max_iterations,
        discount=discount,
    )


def swap(
    start: date, length: str, rate: float, fixed: str = "6M", floating: str = "6M"
) -> SwapQuote:
    return SwapQuote(
        start,
        Tenor.parse(length),
        rate,
        Tenor.parse(fixed),
        THIRTY,
        Tenor.parse(floating),
        MODIFIED,
        PLAIN,
        end_of_month=False,
    )


# --------------------------------------------------------------------------- #
# GR-6: the published worked examples
# --------------------------------------------------------------------------- #

TABLE_4_3 = [
    (date(2025, 4, 15), 0.0, 97.5),
    (date(2025, 7, 15), 0.0, 94.9),
    (date(2026, 1, 15), 0.0, 90.0),
    (date(2026, 7, 15), 0.08, 96.0),
    (date(2027, 1, 15), 0.12, 101.6),
]
TABLE_4_4 = [10.127, 10.469, 10.536, 10.681, 10.808]


@pytest.mark.parametrize("interpolation", list(Interpolation))
def test_gr6_hulls_treasury_bootstrap_is_reproduced(interpolation: Interpolation) -> None:
    quotes: list[RateQuote] = [
        BondQuote(HULL, maturity, coupon, 2, THIRTY, price) for maturity, coupon, price in TABLE_4_3
    ]
    result = bootstrap(quotes, interpolation, name="USD-TREASURY")
    curve = result.curve
    assert [curve.time(pillar.day) for pillar in curve.pillars] == [0.25, 0.5, 1.0, 1.5, 2.0]
    for pillar, published in zip(curve.pillars, TABLE_4_4, strict=True):
        zero = 100 * curve.zero_rate(pillar.day, Compounding.CONTINUOUS)
        assert abs(zero - published) <= 0.0005, (pillar.day, zero, published)
    # The 1.5-year bond's coupons: $4 at six months and a year, $104 at maturity.
    assert quotes[3].flows == (  # type: ignore[union-attr]
        (date(2025, 7, 15), 4.0),
        (date(2026, 1, 15), 4.0),
        (date(2026, 7, 15), 104.0),
    )
    assert result.largest_error < 1e-12  # every bond repriced, in price per 100


def test_gr6_hulls_two_year_swap_extends_his_zero_rates() -> None:
    zeros = [
        (date(2025, 7, 15), 0.04, 0.5),
        (date(2026, 1, 15), 0.045, 1.0),
        (date(2026, 7, 15), 0.048, 1.5),
    ]
    quotes: list[RateQuote] = [
        BondQuote(HULL, day, 0.0, 2, THIRTY, 100 * math.exp(-rate * years))
        for day, rate, years in zeros
    ]
    quotes.append(swap(HULL, "2Y", 0.05))
    curve = bootstrap(quotes, name="USD-SWAP").curve
    two_year = 100 * curve.zero_rate(date(2027, 1, 15), Compounding.CONTINUOUS)
    assert abs(two_year - 4.953) <= 0.0005


# --------------------------------------------------------------------------- #
# Every quote type, under each interpolation
# --------------------------------------------------------------------------- #

SPOT = date(2025, 1, 17)  # two business days after the curve's date, 15 January


def money_market() -> list[RateQuote]:
    quotes: list[RateQuote] = [
        DepositQuote(SPOT, SPOT + timedelta(days=days), rate, DayCount.ACT_360)
        for days, rate in [(7, 0.0431), (30, 0.0433), (91, 0.0436)]
    ]
    quotes.append(FraQuote(date(2025, 4, 17), date(2025, 7, 17), 0.0428, DayCount.ACT_360))
    quotes.append(FraQuote(date(2025, 7, 17), date(2025, 10, 17), 0.0419, DayCount.ACT_360))
    quotes.append(
        FutureQuote(date(2025, 10, 17), date(2026, 1, 20), 95.92, DayCount.ACT_360, 0.0002)
    )
    quotes += [
        swap(SPOT, f"{years}Y", 0.0395 + 0.0004 * years, "1Y", "3M") for years in (2, 3, 5, 7, 10)
    ]
    return quotes


@pytest.mark.parametrize("interpolation", list(Interpolation))
def test_every_quote_type_reprices_under_each_interpolation(interpolation: Interpolation) -> None:
    result = bootstrap(money_market(), interpolation, basis=DayCount.ACT_365_FIXED, tolerance=1e-15)
    for repricing in result.repricing:
        assert abs(repricing.error) < 1e-11, repricing
    future = next(r for r in result.repricing if isinstance(r.quote, FutureQuote))
    assert future.implied == pytest.approx(95.92, abs=1e-11)
    # The future's forward is its rate less the adjustment stated.
    curve = result.curve
    forward = curve.forward_rate(date(2025, 10, 17), date(2026, 1, 20), DayCount.ACT_360)
    assert forward == pytest.approx(1 - 0.9592 - 0.0002, abs=1e-12)
    assert len(result.convergence) == len(curve.pillars) == 11


def test_the_interpolation_is_solved_under_and_shows_between_pillars() -> None:
    log_linear = bootstrap(
        money_market(), Interpolation.LOG_LINEAR_DISCOUNT, basis=DayCount.ACT_365_FIXED
    )
    linear = bootstrap(money_market(), Interpolation.LINEAR_ZERO, basis=DayCount.ACT_365_FIXED)
    # The 7-year swap's annual coupons fall between the 5- and 7-year pillars: each
    # bootstrap solved its pillar under its own interpolation, and both reprice.
    between = date(2031, 1, 17)
    assert log_linear.curve.discount(between) != pytest.approx(
        linear.curve.discount(between), rel=1e-7
    )
    assert log_linear.largest_error < 1e-11 and linear.largest_error < 1e-11
    assert log_linear.bootstrap_id != linear.bootstrap_id


def test_a_projection_curve_reprices_its_swaps_at_the_stated_discount_curve() -> None:
    ois_quotes: list[RateQuote] = [
        DepositQuote(SPOT, SPOT + timedelta(days=1), 0.0430, DayCount.ACT_360)
    ]
    ois_quotes += [
        swap(SPOT, f"{years}Y", 0.0380 + 0.0002 * years, "1Y", "1Y")
        for years in (1, 2, 3, 5, 7, 10)
    ]
    ois = bootstrap(ois_quotes, basis=DayCount.ACT_365_FIXED, name="USD-OIS").curve
    quotes = money_market()
    projected = bootstrap(quotes, basis=DayCount.ACT_365_FIXED, discount=ois, name="USD-3M")
    alone = bootstrap(quotes, basis=DayCount.ACT_365_FIXED, name="USD-3M")
    assert projected.largest_error < 1e-11
    # By hand: each swap's fixed leg at the OIS curve equals its floating leg, whose
    # coupons are projected off the 3M curve and discounted at the OIS curve.
    curve = projected.curve
    for quote in quotes:
        if not isinstance(quote, SwapQuote):
            continue
        fixed = sum(
            tau * ois.discount(day)
            for tau, day in zip(quote.fixed_accruals, quote.fixed_dates[1:], strict=True)
        )
        floating = sum(
            (curve.discount(start) / curve.discount(end) - 1) * ois.discount(end)
            for start, end in zip(quote.float_dates, quote.float_dates[1:], strict=False)
        )
        assert quote.rate * fixed == pytest.approx(floating, abs=1e-12)
    # Discounting at another curve moves the projection curve.
    ten = date(2035, 1, 17)
    assert curve.discount(ten) != pytest.approx(alone.curve.discount(ten), rel=1e-6)
    assert f"discount={ois.curve_id}" in projected.canonical_key.splitlines()


def test_a_bond_settled_after_the_curves_date_is_worth_its_price_at_settlement() -> None:
    # Settled two business days after the curve's date: its price is paid on the 17th.
    settle = date(2025, 1, 17)
    quotes: list[RateQuote] = [DepositQuote(HULL, settle, 0.043, DayCount.ACT_360)]
    quotes += [
        BondQuote(settle, date(2026, 1, 17), 0.0, 2, THIRTY, 95.9),
        BondQuote(settle, date(2027, 7, 15), 0.045, 2, THIRTY, 99.2),
    ]
    result = bootstrap(quotes)
    assert result.largest_error < 1e-11
    curve, bond = result.curve, quotes[2]
    assert isinstance(bond, BondQuote)
    worth = sum(amount * curve.discount(day) for day, amount in bond.flows)
    assert worth == pytest.approx(99.2 * curve.discount(settle), rel=1e-13)
    assert curve.discount(settle) < 1.0  # two days of the deposit's interest


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_a_quote_no_discount_factor_reprices_is_refused_with_the_reason() -> None:
    # A one-year deposit at -150%: P(end) would have to be negative.
    impossible = DepositQuote(HULL, date(2026, 1, 15), -1.5, THIRTY)
    with pytest.raises(BootstrapError, match="never reaches zero"):
        bootstrap([impossible])


@pytest.mark.parametrize(
    ("quotes", "message"),
    [
        (
            [
                DepositQuote(HULL, date(2025, 7, 15), 0.04, DayCount.ACT_360),
                FraQuote(date(2025, 4, 15), date(2025, 7, 15), 0.04, DayCount.ACT_360),
            ],
            "each pillar is solved from one quote",
        ),
        (
            [DepositQuote(date(2025, 1, 10), date(2025, 7, 15), 0.04, DayCount.ACT_360)],
            "before the curve's anchor",
        ),
        ([], "at least one quote"),
        (["a quote"], "not a quote"),
    ],
)
def test_a_malformed_set_of_quotes_is_refused(quotes: list[RateQuote], message: str) -> None:
    with pytest.raises(RatesInputError, match=message):
        bootstrap(quotes)


def test_a_bond_is_no_projection_curves_quote_and_the_curves_must_agree() -> None:
    ois = bootstrap([DepositQuote(HULL, date(2026, 1, 15), 0.04, DayCount.ACT_360)]).curve
    bond = BondQuote(HULL, date(2026, 1, 15), 0.0, 2, THIRTY, 96.0)
    with pytest.raises(RatesInputError, match="not an index's forwards"):
        bootstrap([bond], discount=ois)
    with pytest.raises(RatesInputError, match="both must agree"):
        bootstrap([bond], discount=ois, anchor=date(2025, 1, 16))


def test_a_pillar_not_solved_within_its_iterations_is_refused() -> None:
    quotes: list[RateQuote] = [
        BondQuote(HULL, maturity, c, 2, THIRTY, p) for maturity, c, p in TABLE_4_3
    ]
    with pytest.raises(BootstrapError, match="within 1 iterations"):
        bootstrap(quotes, max_iterations=1)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"tolerance": 0.0}, "must be positive"),
        ({"tolerance": math.nan}, "finite number"),
        ({"max_iterations": 0}, "a positive integer"),
        ({"interpolation": None}, "which it names"),
        ({"basis": DayCount.ACT_ACT_ICMA}, "cannot be a curve's time axis"),
    ],
)
def test_a_malformed_bootstrap_is_refused(change: dict[str, object], message: str) -> None:
    arguments: dict[str, object] = {
        "quotes": [DepositQuote(HULL, date(2026, 1, 15), 0.04, THIRTY)],
        **change,
    }
    with pytest.raises(RatesInputError, match=message):
        bootstrap(**arguments)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_the_identity_is_the_problems() -> None:
    quotes: list[RateQuote] = [
        BondQuote(HULL, maturity, c, 2, THIRTY, p) for maturity, c, p in TABLE_4_3
    ]
    first, again = bootstrap(quotes), bootstrap(list(reversed(quotes)))
    assert first.bootstrap_id == again.bootstrap_id  # quotes are taken in pillar order
    assert first.curve.derivation_id == first.bootstrap_id
    assert first.curve.curve_id == again.curve.curve_id
    moved = [*quotes[:4], BondQuote(HULL, date(2027, 1, 15), 0.12, 2, THIRTY, 101.7)]
    others = [
        bootstrap(quotes, tolerance=1e-13),
        bootstrap(moved),
        bootstrap(quotes, Interpolation.LINEAR_ZERO),
    ]
    assert len({first.bootstrap_id, *(other.bootstrap_id for other in others)}) == 4
    # The solved discount factors are recorded beside the identity, never in it.
    key = first.canonical_key
    assert "quote=bond|2025-01-15|2027-01-15|0.12|2|THIRTY_360_US|101.6" in key.splitlines()
    assert not any(repr(pillar.discount) in key for pillar in first.curve.pillars[3:])
    bits = tuple(Pillar(p.day, math.nextafter(p.discount, 2.0)) for p in first.curve.pillars)
    other_host = DiscountCurve(
        "USD-TEST",
        "USD",
        HULL,
        THIRTY,
        bits,
        Interpolation.LOG_LINEAR_DISCOUNT,
        None,
        first.bootstrap_id,
    )
    assert other_host.curve_id == first.curve.curve_id


def test_each_pillars_convergence_is_reported() -> None:
    quotes: list[RateQuote] = [
        BondQuote(HULL, maturity, c, 2, THIRTY, p) for maturity, c, p in TABLE_4_3
    ]
    result = bootstrap(quotes, tolerance=1e-12, max_iterations=50)
    for report in result.convergence:
        assert report.method == "Brent's root finder"
        assert report.tolerance == 1e-12 and report.limit == 50
        assert 0 <= report.iterations <= 50
