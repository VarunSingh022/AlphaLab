"""Contract chains, roll policies, continuous construction and futures margin."""

from datetime import UTC, datetime, time
from decimal import Decimal

import pytest

from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.futures import (
    AdjustmentMethod,
    ContractChain,
    ContractMarginSpec,
    CurveShape,
    FutureContract,
    FuturesCurve,
    FuturesCurvePoint,
    FuturesInputError,
    RollPolicy,
    RollSegment,
    RollTrigger,
    active_contract_at,
    build_continuous_series,
    continuous_segments,
    contract_tick_value,
    curve_shape,
    curve_slope,
    futures_symbol,
    position_margin,
    roll_schedule,
    roll_yield,
)
from alphalab.market.bar import Bar, TimeFrame

DAY = 86400.0


def _expiry(year: int, month: int, day: int) -> float:
    return datetime(year, month, day, tzinfo=UTC).timestamp()


def _contract(month: int, *, multiplier: int = 1000, currency: str = "USD") -> FutureContract:
    return FutureContract(
        underlying_asset_id="CL",
        contract_month=_expiry(2026, month, 1),
        expiry=_expiry(2026, month, 20),
        multiplier=multiplier,
        tick_size=Decimal("0.01"),
        currency=currency,
    )


_MARCH, _JUNE, _SEPTEMBER = _contract(3), _contract(6), _contract(9)
_CHAIN = ContractChain("CL", (_MARCH, _JUNE, _SEPTEMBER))


def _bar(asset_id: str, timestamp: float, close: str, volume: str = "100") -> Bar:
    price = Decimal(close)
    return Bar(
        asset_id=asset_id,
        timestamp=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(volume),
        vwap=price,
        trade_count=1,
        timeframe=TimeFrame.D1,
    )


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #


def test_a_chain_refuses_a_contract_on_another_root() -> None:
    other = FutureContract(
        "NG", _expiry(2026, 3, 1), _expiry(2026, 3, 20), 1000, Decimal("0.01"), "USD"
    )
    with pytest.raises(FuturesInputError, match="A chain splices one root"):
        ContractChain("CL", (_MARCH, other))


def test_a_chain_refuses_two_multipliers() -> None:
    """The halves of the spliced series would be in different units."""

    with pytest.raises(FuturesInputError, match="different units"):
        ContractChain("CL", (_MARCH, _contract(6, multiplier=500)))


def test_a_chain_refuses_two_currencies() -> None:
    with pytest.raises(FuturesInputError, match="sum two currencies"):
        ContractChain("CL", (_MARCH, _contract(6, currency="EUR")))


def test_a_chain_refuses_out_of_order_or_repeated_expiries() -> None:
    with pytest.raises(FuturesInputError, match="ordered by expiry"):
        ContractChain("CL", (_JUNE, _MARCH))
    with pytest.raises(FuturesInputError, match="holds each month once"):
        ContractChain("CL", (_MARCH, _contract(3)))


def test_an_empty_chain_is_refused() -> None:
    with pytest.raises(FuturesInputError, match="at least one contract"):
        ContractChain("CL", ())


def test_a_chain_exposes_the_terms_its_contracts_share() -> None:
    assert _CHAIN.multiplier == 1000
    assert _CHAIN.currency == "USD"
    assert _CHAIN.symbols == ("CL_202603", "CL_202606", "CL_202609")


# --------------------------------------------------------------------------- #
# The policy
# --------------------------------------------------------------------------- #


def test_a_date_trigger_without_an_offset_is_refused() -> None:
    with pytest.raises(FuturesInputError, match="no default number"):
        RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY)


def test_a_volume_trigger_with_an_offset_is_refused() -> None:
    with pytest.raises(FuturesInputError, match="no effect"):
        RollPolicy(RollTrigger.VOLUME_CROSSOVER, offset_days=5)


def test_a_negative_offset_is_refused() -> None:
    with pytest.raises(FuturesInputError, match="before expiry, not after"):
        RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, -1)


# --------------------------------------------------------------------------- #
# The schedule
# --------------------------------------------------------------------------- #


def test_a_chain_of_n_contracts_has_n_minus_one_rolls() -> None:
    rolls = roll_schedule(_CHAIN, RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5))
    assert len(rolls) == 2
    assert rolls[0].outgoing is _MARCH
    assert rolls[0].incoming is _JUNE
    assert rolls[-1].incoming is _SEPTEMBER


def test_the_calendar_day_roll_lands_exactly_n_days_before_expiry() -> None:
    rolls = roll_schedule(_CHAIN, RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5))
    assert rolls[0].timestamp == _MARCH.expiry - 5 * DAY


def test_trading_days_and_calendar_days_are_different_rules() -> None:
    """Five trading days back crosses a weekend; five calendar days does not."""

    weekday_calendar = MarketCalendar(
        calendar_id="XCME",
        timezone_name="UTC",
        weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9), time(17)),)),
    )
    by_calendar = roll_schedule(_CHAIN, RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5))
    by_trading = roll_schedule(
        _CHAIN, RollPolicy(RollTrigger.TRADING_DAYS_BEFORE_EXPIRY, 5), weekday_calendar
    )
    assert by_trading[0].timestamp < by_calendar[0].timestamp


def test_a_trading_day_policy_without_a_calendar_is_refused() -> None:
    with pytest.raises(FuturesInputError, match="needs the venue's calendar"):
        roll_schedule(_CHAIN, RollPolicy(RollTrigger.TRADING_DAYS_BEFORE_EXPIRY, 5))


def test_a_calendar_where_it_has_no_effect_is_refused() -> None:
    weekday_calendar = MarketCalendar(
        calendar_id="X",
        timezone_name="UTC",
        weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9), time(17)),)),
    )
    with pytest.raises(FuturesInputError, match="reads no calendar"):
        roll_schedule(
            _CHAIN, RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5), weekday_calendar
        )


def test_a_volume_policy_without_observations_is_refused() -> None:
    """The missing dependency is exposed rather than approximated from dates."""

    with pytest.raises(FuturesInputError, match="approximating a crossover"):
        roll_schedule(_CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER))


def _volume_observations() -> dict[str, tuple[Bar, ...]]:
    """March leads until day 3, then June; June leads until day 7, then September."""

    days = [float(index) * DAY for index in range(10)]
    march = tuple(
        _bar("CL_202603", day, "70.00", volume="100" if index < 3 else "10")
        for index, day in enumerate(days)
    )
    june = tuple(
        _bar("CL_202606", day, "72.00", volume="50" if index < 3 else ("200" if index < 7 else "5"))
        for index, day in enumerate(days)
    )
    september = tuple(
        _bar("CL_202609", day, "74.00", volume="10" if index < 7 else "300")
        for index, day in enumerate(days)
    )
    return {"CL_202603": march, "CL_202606": june, "CL_202609": september}


def test_a_volume_crossover_rolls_at_the_first_instant_the_next_month_leads() -> None:
    rolls = roll_schedule(
        _CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=_volume_observations()
    )
    assert [event.timestamp for event in rolls] == [3 * DAY, 7 * DAY]
    assert "first traded more volume than" in rolls[0].reason


def test_a_crossover_that_never_happens_is_refused_rather_than_dated() -> None:
    observations = _volume_observations()
    observations["CL_202606"] = tuple(
        _bar("CL_202606", bar.timestamp, "72.00", volume="1") for bar in observations["CL_202606"]
    )
    with pytest.raises(FuturesInputError, match="never out-trades"):
        roll_schedule(_CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations)


def test_a_crossover_reads_only_timestamps_both_contracts_reported() -> None:
    """Pairing two different days would compare two different markets."""

    observations = _volume_observations()
    observations["CL_202606"] = tuple(
        bar for bar in observations["CL_202606"] if bar.timestamp != 3 * DAY
    )
    rolls = roll_schedule(
        _CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
    )
    assert rolls[0].timestamp == 4 * DAY


def test_the_schedule_is_deterministic() -> None:
    policy = RollPolicy(RollTrigger.VOLUME_CROSSOVER)
    observations = _volume_observations()
    first = roll_schedule(_CHAIN, policy, observations=observations)
    second = roll_schedule(_CHAIN, policy, observations=observations)
    assert first == second


def test_two_policies_produce_two_different_series_which_is_the_point() -> None:
    five = roll_schedule(_CHAIN, RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5))
    ten = roll_schedule(_CHAIN, RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 10))
    assert [e.timestamp for e in five] != [e.timestamp for e in ten]


# --------------------------------------------------------------------------- #
# Active contract
# --------------------------------------------------------------------------- #


def test_the_active_contract_changes_exactly_at_the_roll_instant() -> None:
    rolls = roll_schedule(
        _CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=_volume_observations()
    )
    assert active_contract_at(_CHAIN, rolls, 0.0) is _MARCH
    assert active_contract_at(_CHAIN, rolls, 3 * DAY - 1) is _MARCH
    assert active_contract_at(_CHAIN, rolls, 3 * DAY) is _JUNE
    assert active_contract_at(_CHAIN, rolls, 7 * DAY) is _SEPTEMBER
    assert active_contract_at(_CHAIN, rolls, 99 * DAY) is _SEPTEMBER


def test_a_chain_of_one_is_that_contract_at_every_instant() -> None:
    single = ContractChain("CL", (_MARCH,))
    assert roll_schedule(single, RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5)) == ()
    assert active_contract_at(single, (), 12345.0) is _MARCH


# --------------------------------------------------------------------------- #
# Continuous construction
# --------------------------------------------------------------------------- #


def _segments() -> tuple[RollSegment, ...]:
    observations = _volume_observations()
    rolls = roll_schedule(
        _CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
    )
    return continuous_segments(_CHAIN, rolls, observations)


def test_segments_partition_the_observations_without_overlap() -> None:
    segments = _segments()
    assert len(segments) == 3
    spans = [(seg.bars[0].timestamp, seg.bars[-1].timestamp) for seg in segments]
    assert spans == [(0.0, 2 * DAY), (3 * DAY, 6 * DAY), (7 * DAY, 9 * DAY)]


def test_every_non_final_segment_carries_both_roll_prices_and_the_last_carries_none() -> None:
    segments = _segments()
    for segment in segments[:-1]:
        assert segment.outgoing_roll_price is not None
        assert segment.incoming_roll_price is not None
    assert segments[-1].outgoing_roll_price is None


def test_the_roll_prices_are_the_prints_at_the_roll_instant() -> None:
    segments = _segments()
    assert segments[0].outgoing_roll_price == Decimal("70.00")
    assert segments[0].incoming_roll_price == Decimal("72.00")


def test_a_missing_bar_at_the_roll_instant_is_refused_not_interpolated() -> None:
    observations = _volume_observations()
    rolls = roll_schedule(
        _CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
    )
    observations["CL_202606"] = tuple(
        bar for bar in observations["CL_202606"] if bar.timestamp != 3 * DAY
    )
    with pytest.raises(FuturesInputError, match="interpolating one would invent"):
        continuous_segments(_CHAIN, rolls, observations)


def test_a_front_contract_with_no_bars_is_refused_not_skipped() -> None:
    """March keeps only the bar at its own roll instant, which falls outside the
    window it was front for. The roll price exists; the segment is empty."""

    observations = _volume_observations()
    rolls = roll_schedule(
        _CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
    )
    observations["CL_202603"] = tuple(
        bar for bar in observations["CL_202603"] if bar.timestamp == 3 * DAY
    )
    with pytest.raises(FuturesInputError, match="silently shorten the history"):
        continuous_segments(_CHAIN, rolls, observations)


def test_a_continuous_series_is_reproducible_from_chain_policy_bars_and_method() -> None:
    """Four stated things and nothing else."""

    def build(method: AdjustmentMethod) -> tuple[Decimal, ...]:
        observations = _volume_observations()
        rolls = roll_schedule(
            _CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
        )
        segments = continuous_segments(_CHAIN, rolls, observations)
        return tuple(bar.close for bar in build_continuous_series(segments, method))

    assert build(AdjustmentMethod.BACK_ADJUSTED) == build(AdjustmentMethod.BACK_ADJUSTED)
    # And the method is a real choice: the three disagree.
    assert len({build(method) for method in AdjustmentMethod}) == 3


def test_the_unadjusted_series_shows_the_roll_jump_the_others_remove() -> None:
    observations = _volume_observations()
    rolls = roll_schedule(
        _CHAIN, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
    )
    segments = continuous_segments(_CHAIN, rolls, observations)
    raw = build_continuous_series(segments, AdjustmentMethod.UNADJUSTED)
    adjusted = build_continuous_series(segments, AdjustmentMethod.BACK_ADJUSTED)

    assert raw[2].close == Decimal("70.00") and raw[3].close == Decimal("72.00")
    # Back-adjusted: the anchor is the current contract, so the last segment is
    # untouched and the earlier ones are shifted onto it.
    assert adjusted[-1].close == Decimal("74.00")
    assert adjusted[2].close == adjusted[3].close


def test_every_front_window_bar_survives_every_adjustment_method() -> None:
    """The segments hold 3 + 4 + 3 of the 30 observations -- one contract's bars
    per instant, which is what a continuous series is. No method drops one."""

    segments = _segments()
    expected = sum(len(segment.bars) for segment in segments)
    assert expected == 10
    for method in AdjustmentMethod:
        assert len(build_continuous_series(segments, method)) == expected


# --------------------------------------------------------------------------- #
# Curve shape and roll yield
# --------------------------------------------------------------------------- #


def _curve(*prices: str) -> FuturesCurve:
    return FuturesCurve(
        underlying_asset_id="CL",
        timestamp=0.0,
        points=tuple(
            FuturesCurvePoint(contract_month=float(index) * 30 * DAY, price=Decimal(price))
            for index, price in enumerate(prices)
        ),
    )


def test_a_humped_curve_is_mixed_and_its_endpoint_slope_says_contango() -> None:
    """The reason ``curve_shape`` exists beside ``curve_slope``."""

    humped = _curve("70.00", "68.00", "71.00")
    assert curve_slope(humped) > Decimal("0")
    assert curve_shape(humped) is CurveShape.MIXED


@pytest.mark.parametrize(
    ("prices", "shape"),
    [
        (("70.00", "71.00", "72.00"), CurveShape.CONTANGO),
        (("72.00", "71.00", "70.00"), CurveShape.BACKWARDATION),
        (("70.00", "70.00", "70.00"), CurveShape.FLAT),
    ],
)
def test_monotone_curves_classify_as_their_shape(
    prices: tuple[str, ...], shape: CurveShape
) -> None:
    assert curve_shape(_curve(*prices)) is shape


def test_one_month_is_not_a_term_structure() -> None:
    with pytest.raises(FuturesInputError, match="at least two"):
        curve_shape(_curve("70.00"))


def test_roll_yield_is_positive_in_backwardation_and_annualized() -> None:
    curve = _curve("72.00", "70.00")
    months = 30 * DAY
    yielded = roll_yield(curve, 0.0, months)
    assert yielded > Decimal("0")
    # Half the horizon, so twice the annualized figure.
    half = roll_yield(_curve("72.00", "70.00"), 0.0, months)
    assert half == yielded


def test_roll_yield_refuses_a_month_the_curve_does_not_quote() -> None:
    with pytest.raises(FuturesInputError, match="would invent the price"):
        roll_yield(_curve("70.00", "71.00"), 0.0, 999.0)


def test_roll_yield_refuses_reversed_legs_rather_than_flipping_the_sign() -> None:
    curve = _curve("72.00", "70.00")
    with pytest.raises(FuturesInputError, match="reverses its sign"):
        roll_yield(curve, 30 * DAY, 0.0)


# --------------------------------------------------------------------------- #
# Tick value and margin
# --------------------------------------------------------------------------- #


def test_a_contracts_tick_value_reconciles_with_its_tick_size_and_multiplier() -> None:
    value = contract_tick_value(_MARCH)
    assert value.amount == value.tick_size * value.multiplier
    assert value.amount == Decimal("10.00")
    assert value.currency == "USD"


def test_margin_is_carried_not_computed_and_a_missing_one_is_named() -> None:
    with pytest.raises(FuturesInputError, match="AlphaLab holds none and derives none"):
        position_margin(_MARCH, Decimal("2"), {}, as_of=100.0)


def test_margin_scales_by_the_absolute_contract_count() -> None:
    spec = ContractMarginSpec(
        contract_symbol=futures_symbol(_MARCH),
        initial=Decimal("6000"),
        maintenance=Decimal("5000"),
        currency="USD",
        as_of=50.0,
    )
    specs = {spec.contract_symbol: spec}
    long = position_margin(_MARCH, Decimal("3"), specs, as_of=100.0)
    short = position_margin(_MARCH, Decimal("-3"), specs, as_of=100.0)
    assert long.initial == short.initial == Decimal("18000")
    assert long.maintenance == Decimal("15000")
    assert long.currency == "USD"


def test_a_margin_figure_published_after_the_research_instant_is_refused() -> None:
    """Applying a requirement that did not exist yet is a look-ahead."""

    spec = ContractMarginSpec(
        futures_symbol(_MARCH), Decimal("6000"), Decimal("5000"), "USD", 200.0
    )
    with pytest.raises(FuturesInputError, match="did not exist"):
        position_margin(_MARCH, Decimal("1"), {spec.contract_symbol: spec}, as_of=100.0)


def test_maintenance_above_initial_is_refused() -> None:
    with pytest.raises(FuturesInputError, match="deficient the moment it opened"):
        ContractMarginSpec("CL_202603", Decimal("100"), Decimal("200"), "USD", 0.0)


def test_a_margin_spec_names_its_own_currency() -> None:
    """Not assumed to be the contract's: a clearing house may call margin in another."""

    spec = ContractMarginSpec(futures_symbol(_MARCH), Decimal("6000"), Decimal("5000"), "EUR", 0.0)
    posture = position_margin(_MARCH, Decimal("1"), {spec.contract_symbol: spec}, as_of=1.0)
    assert posture.currency == "EUR" != _MARCH.currency
