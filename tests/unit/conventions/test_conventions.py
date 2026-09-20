"""Market conventions: settlement, ticks, lots, multipliers, day counts and rates."""

from datetime import date, time
from decimal import Decimal

import pytest

from alphalab.conventions import (
    Compounding,
    ContractNotional,
    ConventionInputError,
    ConventionViolationError,
    DayCount,
    LotSpecification,
    MarketConvention,
    RoundingDirection,
    SettlementBasis,
    SettlementRule,
    TickBand,
    TickSchedule,
    compound_factor,
    contract_notional,
    day_count_days,
    discount_factor,
    is_on_tick,
    lots_in,
    round_down_to_lot,
    round_to_tick,
    settlement_date,
    tick_value,
    year_fraction,
)
from alphalab.data.calendar import MarketCalendar, SessionWindow

# --------------------------------------------------------------------------- #
# Settlement
# --------------------------------------------------------------------------- #

_WEEKDAYS = dict.fromkeys(range(5), (SessionWindow(time(9, 15), time(15, 30)),))


def _calendar(holidays: frozenset[date] = frozenset()) -> MarketCalendar:
    return MarketCalendar(
        calendar_id="XNSE",
        timezone_name="Asia/Kolkata",
        weekly_sessions=_WEEKDAYS,
        holidays=holidays,
    )


def test_trade_date_settlement_is_the_trade_date() -> None:
    rule = SettlementRule(SettlementBasis.TRADE_DATE, 0)
    assert settlement_date(rule, date(2026, 3, 10), None) == date(2026, 3, 10)
    assert rule.label == "T+0"


def test_a_trade_date_rule_refuses_a_non_zero_offset() -> None:
    with pytest.raises(ConventionInputError, match="offset_days must be 0"):
        SettlementRule(SettlementBasis.TRADE_DATE, 2)


def test_settlement_cannot_precede_the_trade() -> None:
    with pytest.raises(ConventionInputError, match="cannot precede"):
        SettlementRule(SettlementBasis.TRADING_DAYS, -1)


def test_calendar_days_count_the_weekend_and_trading_days_do_not() -> None:
    """The distinction the basis exists for, on one Friday."""

    friday = date(2026, 3, 13)
    assert friday.weekday() == 4

    calendar_rule = SettlementRule(SettlementBasis.CALENDAR_DAYS, 2)
    trading_rule = SettlementRule(SettlementBasis.TRADING_DAYS, 2)

    assert settlement_date(calendar_rule, friday, None) == date(2026, 3, 15)  # a Sunday
    assert settlement_date(trading_rule, friday, _calendar()) == date(2026, 3, 17)


def test_a_holiday_pushes_a_trading_day_settlement_out() -> None:
    holiday = date(2026, 3, 16)
    with_holiday = _calendar(frozenset({holiday}))
    rule = SettlementRule(SettlementBasis.TRADING_DAYS, 2)
    assert settlement_date(rule, date(2026, 3, 13), with_holiday) == date(2026, 3, 18)


def test_a_trading_day_rule_without_a_calendar_is_refused() -> None:
    rule = SettlementRule(SettlementBasis.TRADING_DAYS, 2)
    with pytest.raises(ConventionInputError, match="needs the market's calendar"):
        settlement_date(rule, date(2026, 3, 13), None)


def test_a_calendar_where_it_has_no_effect_is_refused() -> None:
    """Supplying one would suggest holidays were skipped when they were not."""

    rule = SettlementRule(SettlementBasis.CALENDAR_DAYS, 2)
    with pytest.raises(ConventionInputError, match="does not read a calendar"):
        settlement_date(rule, date(2026, 3, 13), _calendar())


def test_a_calendar_with_no_trading_day_refuses_rather_than_looping() -> None:
    never = MarketCalendar(calendar_id="SHUT", timezone_name="UTC", weekly_sessions={})
    rule = SettlementRule(SettlementBasis.TRADING_DAYS, 1)
    with pytest.raises(ConventionViolationError, match="mis-declared calendar"):
        settlement_date(rule, date(2026, 3, 13), never)


def test_the_market_calendar_satisfies_the_settlement_protocol_structurally() -> None:
    """One calendar authority, reached without an import edge.

    ``alphalab.conventions`` imports only ``alphalab.common``; if it named
    ``MarketCalendar`` it would close a package cycle through
    ``data -> options -> portfolio``.
    """

    import ast
    import inspect
    import pathlib

    from alphalab.conventions import settlement as module

    source = pathlib.Path(inspect.getfile(module)).read_text()
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not [name for name in imported if name.startswith("alphalab.data")]
    # And it works anyway, because the protocol is structural.
    assert settlement_date(
        SettlementRule(SettlementBasis.TRADING_DAYS, 1), date(2026, 3, 13), _calendar()
    ) == date(2026, 3, 16)


# --------------------------------------------------------------------------- #
# Ticks
# --------------------------------------------------------------------------- #


def test_a_flat_schedule_answers_one_increment_everywhere() -> None:
    schedule = TickSchedule.flat(Decimal("0.25"))
    assert schedule.is_flat
    assert schedule.tick_size_at(Decimal("1")) == Decimal("0.25")
    assert schedule.tick_size_at(Decimal("100000")) == Decimal("0.25")


def test_a_tiered_schedule_reads_the_band_the_price_falls_in() -> None:
    schedule = TickSchedule(
        (
            TickBand(Decimal("100"), Decimal("0.01")),
            TickBand(Decimal("1000"), Decimal("0.05")),
            TickBand(None, Decimal("0.10")),
        )
    )
    assert schedule.tick_size_at(Decimal("99.99")) == Decimal("0.01")
    assert schedule.tick_size_at(Decimal("100")) == Decimal("0.05")
    assert schedule.tick_size_at(Decimal("5000")) == Decimal("0.10")


def test_a_schedule_whose_last_band_is_bounded_is_refused() -> None:
    with pytest.raises(ConventionInputError, match="highest band is unbounded"):
        TickSchedule((TickBand(Decimal("100"), Decimal("0.01")),))


def test_a_schedule_with_an_unreachable_band_is_refused() -> None:
    with pytest.raises(ConventionInputError, match="unreachable"):
        TickSchedule((TickBand(None, Decimal("0.01")), TickBand(Decimal("100"), Decimal("0.05"))))


def test_bands_must_ascend() -> None:
    with pytest.raises(ConventionInputError, match="does not exceed"):
        TickSchedule(
            (
                TickBand(Decimal("1000"), Decimal("0.01")),
                TickBand(Decimal("100"), Decimal("0.05")),
                TickBand(None, Decimal("0.10")),
            )
        )


def test_a_negative_price_has_no_band_and_is_refused() -> None:
    with pytest.raises(ConventionViolationError, match="negative price"):
        TickSchedule.flat(Decimal("0.01")).tick_size_at(Decimal("-1"))


def test_tick_value_is_money_and_tick_size_is_a_price() -> None:
    value = tick_value(Decimal("0.01"), Decimal("1000"), "USD")
    assert value.amount == Decimal("10.00")
    assert value.currency == "USD"
    # The inputs are kept so the figure can be recomputed by a reader.
    assert value.tick_size * value.multiplier == value.amount


def test_tick_value_refuses_an_unnamed_currency() -> None:
    with pytest.raises(ConventionInputError, match="names its currency"):
        tick_value(Decimal("0.01"), Decimal("1000"), "  ")


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (RoundingDirection.DOWN, Decimal("100.25")),
        (RoundingDirection.UP, Decimal("100.50")),
        (RoundingDirection.NEAREST, Decimal("100.50")),
    ],
)
def test_rounding_direction_changes_the_answer(
    direction: RoundingDirection, expected: Decimal
) -> None:
    assert round_to_tick(Decimal("100.40"), Decimal("0.25"), direction) == expected


def test_a_price_already_on_the_grid_is_unmoved_in_every_direction() -> None:
    for direction in RoundingDirection:
        assert round_to_tick(Decimal("100.50"), Decimal("0.25"), direction) == Decimal("100.50")
    assert is_on_tick(Decimal("100.50"), Decimal("0.25"))
    assert not is_on_tick(Decimal("100.40"), Decimal("0.25"))


# --------------------------------------------------------------------------- #
# Lots
# --------------------------------------------------------------------------- #


def test_a_minimum_that_is_not_a_whole_lot_is_refused() -> None:
    with pytest.raises(ConventionInputError, match="not a whole number"):
        LotSpecification(lot_size=Decimal("50"), minimum_quantity=Decimal("75"))


def test_a_short_is_as_tradable_as_a_long_of_the_same_size() -> None:
    lot = LotSpecification(Decimal("50"), Decimal("50"))
    assert lot.admits(Decimal("100"))
    assert lot.admits(Decimal("-100"))
    assert not lot.admits(Decimal("75"))


def test_require_refuses_a_partial_lot_rather_than_rounding_it() -> None:
    lot = LotSpecification(Decimal("100"), Decimal("100"))
    with pytest.raises(ConventionViolationError, match="two different orders"):
        lot.require(Decimal("150"))
    with pytest.raises(ConventionViolationError, match="below the minimum"):
        lot.require(Decimal("50"))


def test_lots_in_is_signed_and_refuses_a_fraction() -> None:
    lot = LotSpecification(Decimal("50"), Decimal("50"))
    assert lots_in(Decimal("150"), lot) == Decimal("3")
    assert lots_in(Decimal("-150"), lot) == Decimal("-3")
    with pytest.raises(ConventionViolationError):
        lots_in(Decimal("75"), lot)


def test_rounding_to_a_lot_always_moves_toward_zero() -> None:
    lot = LotSpecification(Decimal("50"), Decimal("50"))
    assert round_down_to_lot(Decimal("149"), lot) == Decimal("100")
    assert round_down_to_lot(Decimal("-149"), lot) == Decimal("-100")
    # Below the minimum there is no tradable quantity, and zero says so.
    assert round_down_to_lot(Decimal("30"), lot) == Decimal("0")


# --------------------------------------------------------------------------- #
# The convention bundle, and the multiplier
# --------------------------------------------------------------------------- #


def _convention(**overrides: object) -> MarketConvention:
    defaults: dict[str, object] = {
        "venue": "XCME",
        "calendar_id": "XCME",
        "quote_currency": "USD",
        "settlement_currency": "USD",
        "multiplier": Decimal("1000"),
        "tick": TickSchedule.flat(Decimal("0.01")),
        "lot": LotSpecification.single_units(),
        "settlement": SettlementRule(SettlementBasis.TRADE_DATE, 0),
    }
    defaults.update(overrides)
    return MarketConvention(**defaults)  # type: ignore[arg-type]


def test_every_field_of_a_convention_is_required() -> None:
    """No default anywhere: an instrument nobody declared cannot be built."""

    import inspect

    for parameter in inspect.signature(MarketConvention).parameters.values():
        assert parameter.default is inspect.Parameter.empty, (
            f"MarketConvention.{parameter.name} is defaulted"
        )


def test_a_blank_name_is_refused() -> None:
    with pytest.raises(ConventionInputError, match="is blank"):
        _convention(quote_currency="   ")


def test_a_non_positive_multiplier_is_refused() -> None:
    with pytest.raises(ConventionInputError, match="controlling nothing"):
        _convention(multiplier=Decimal("0"))


def test_contract_notional_applies_the_multiplier_exactly_once() -> None:
    result = contract_notional(_convention(), Decimal("10"), Decimal("75.50"))
    assert isinstance(result, ContractNotional)
    assert result.amount == Decimal("755000.00")
    assert result.underlying_units == Decimal("10000")
    assert result.multiplier == Decimal("1000")
    # The recomputation a reader would do to check it was applied once.
    assert result.amount == result.quantity * result.price * result.multiplier


def test_a_short_position_has_a_negative_notional_and_negative_units() -> None:
    result = contract_notional(_convention(), Decimal("-10"), Decimal("75.50"))
    assert result.amount == Decimal("-755000.00")
    assert result.underlying_units == Decimal("-10000")


def test_a_negative_price_is_refused_rather_than_flipping_the_sign() -> None:
    with pytest.raises(ConventionInputError, match="flip the sign"):
        contract_notional(_convention(), Decimal("10"), Decimal("-1"))


def test_a_zero_price_is_allowed_because_a_worthless_option_is_real() -> None:
    assert contract_notional(_convention(), Decimal("10"), Decimal("0")).amount == Decimal("0")


def test_a_quanto_convention_knows_it_needs_a_rate() -> None:
    same = _convention()
    quanto = _convention(quote_currency="USD", settlement_currency="EUR")
    assert same.settles_in_quote_currency
    assert not quanto.settles_in_quote_currency
    # And the notional is denominated in the quote currency, not the settlement one.
    assert contract_notional(quanto, Decimal("1"), Decimal("100")).currency == "USD"


def test_tick_value_at_reads_the_band_and_names_the_quote_currency() -> None:
    tiered = _convention(
        tick=TickSchedule(
            (TickBand(Decimal("100"), Decimal("0.01")), TickBand(None, Decimal("0.05")))
        ),
        quote_currency="JPY",
        settlement_currency="JPY",
    )
    assert tiered.tick_value_at(Decimal("50")).amount == Decimal("10.00")
    assert tiered.tick_value_at(Decimal("500")).amount == Decimal("50.00")
    assert tiered.tick_value_at(Decimal("50")).currency == "JPY"


# --------------------------------------------------------------------------- #
# Day counts and compounding
# --------------------------------------------------------------------------- #


def test_the_three_bases_disagree_on_the_same_dates() -> None:
    """The reason the basis is named at every call."""

    start, end = date(2025, 1, 31), date(2025, 7, 31)
    fractions = {basis: year_fraction(start, end, basis) for basis in DayCount}
    assert len(set(fractions.values())) == 3
    assert fractions[DayCount.THIRTY_360_US] == pytest.approx(0.5)
    assert fractions[DayCount.ACT_365_FIXED] == pytest.approx(181 / 365)
    assert fractions[DayCount.ACT_360] == pytest.approx(181 / 360)


def test_thirty_360_applies_the_end_of_month_rule() -> None:
    assert day_count_days(date(2025, 1, 31), date(2025, 2, 28), DayCount.THIRTY_360_US) == 28
    assert day_count_days(date(2025, 1, 30), date(2025, 3, 31), DayCount.THIRTY_360_US) == 60


def test_a_reversed_period_is_refused_rather_than_absolute() -> None:
    with pytest.raises(ConventionInputError, match="inverts every discount factor"):
        year_fraction(date(2025, 7, 31), date(2025, 1, 31), DayCount.ACT_365_FIXED)
    # The signed day count still reports the direction.
    assert day_count_days(date(2025, 7, 31), date(2025, 1, 31), DayCount.ACT_365_FIXED) < 0


def test_compounding_changes_a_discount_factor_materially() -> None:
    factors = {c: discount_factor(0.05, 10.0, c) for c in Compounding}
    assert len(set(factors.values())) == len(Compounding)
    assert factors[Compounding.ANNUAL] == pytest.approx(1.05**-10)
    assert factors[Compounding.CONTINUOUS] == pytest.approx(2.718281828459045**-0.5, rel=1e-9)


def test_continuous_compounding_has_no_period_count() -> None:
    assert Compounding.CONTINUOUS.periods_per_year is None
    assert not Compounding.CONTINUOUS.is_periodic
    assert Compounding.SEMI_ANNUAL.periods_per_year == 2


def test_compounding_and_discounting_are_reciprocal() -> None:
    for compounding in Compounding:
        grown = compound_factor(0.043, 7.5, compounding)
        assert grown * discount_factor(0.043, 7.5, compounding) == pytest.approx(1.0)


def test_a_rate_that_destroys_the_growth_factor_is_refused() -> None:
    with pytest.raises(ConventionInputError, match="no real power"):
        compound_factor(-3.0, 1.0, Compounding.SEMI_ANNUAL)
