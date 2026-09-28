"""The pre-trade gate judges the book an order would leave (ledger RSK-001..006, KD-001..003).

Every defect here was measured at v3.9.0 and is pinned by name:

* **RSK-001** -- buying power was charged the full notional of every order, so
  a fully invested account could not sell.
* **RSK-002** -- gross exposure, leverage and margin added the order's notional
  whatever its side, so a book at its limit could not reduce.
* **RSK-003** -- a breached drawdown refused every order, liquidation included.
* **RSK-004** -- working orders were invisible, so several could jointly breach
  a limit none breached alone.
* **RSK-005** -- no test pinned a limit boundary: ``quantity > max`` mutated to
  ``>=`` survived the whole suite.
* **RSK-006** -- severity was a free string; zero margin base passed every order.
* **KD-001** -- the position limit added market value to quantity.
* **KD-002** -- the daily loss limit was never enforced.
* **KD-003** -- ``max_net_exposure`` was read by nothing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any

import pytest

from alphalab.core.enums import Side
from alphalab.risk import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    ExposureStatus,
    LeverageLimit,
    MarginLimit,
    OrderRequest,
    OrderSizeLimit,
    PositionLimit,
    RiskEngine,
    RiskLimits,
    RiskSeverity,
    RiskState,
    RiskValidationError,
    WorkingExposure,
)

HUGE = Decimal("1000000000")


def _limits(**overrides: Any) -> RiskLimits:
    limits = RiskLimits(
        order_size=OrderSizeLimit(HUGE, HUGE),
        position=PositionLimit(HUGE, HUGE),
        exposure=ExposureLimit(HUGE, HUGE),
        leverage=LeverageLimit(HUGE),
        margin=MarginLimit(HUGE),
        daily_loss=None,
        drawdown=DrawdownLimit(Decimal("1")),
    )
    return replace(limits, **overrides)


def _book(
    limits: RiskLimits,
    *,
    cash: str = "0",
    nav: str = "100000",
    holdings: dict[str, str] | None = None,
) -> RiskState:
    """A gate that has seen a book: signed market values per asset, cash and NAV."""

    values = {asset: Decimal(value) for asset, value in (holdings or {}).items()}
    long_value = sum((value for value in values.values() if value > 0), Decimal("0"))
    short_value = sum((value for value in values.values() if value < 0), Decimal("0"))
    exposure = ExposureStatus(
        gross_exposure=long_value - short_value,
        net_exposure=long_value + short_value,
        long_exposure=long_value,
        short_exposure=short_value,
        asset_exposure=values,
    )
    state = RiskEngine.update_exposure(RiskEngine.reset(limits), exposure, 0.0)
    return RiskEngine.mark(state, nav=Decimal(nav), cash=Decimal(cash), timestamp=0.0)


def _order(side: Side, quantity: str, price: str = "100", asset: str = "A") -> OrderRequest:
    return OrderRequest("O", "S", asset, side, Decimal(quantity), Decimal(price))


def _rules(decision: object) -> set[str]:
    return {violation.rule for violation in decision.violations}  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# RSK-001: buying power is charged only for what grows a position
# --------------------------------------------------------------------------- #


def test_a_fully_invested_account_can_sell_what_it_holds() -> None:
    state = _book(_limits(), cash="0", holdings={"A": "100000"})

    _, decision = RiskEngine.evaluate(
        state, _order(Side.SELL, "400"), 1.0, position=Decimal("1000")
    )

    assert decision.approved, decision.violations
    assert decision.required_margin == 0


def test_buying_still_needs_buying_power() -> None:
    state = _book(_limits(), cash="0", holdings={"A": "100000"})

    _, decision = RiskEngine.evaluate(state, _order(Side.BUY, "1"), 1.0, position=Decimal("1000"))

    assert "BuyingPowerLimit" in _rules(decision)


def test_a_reversal_is_charged_only_for_the_new_opposite_leg() -> None:
    state = _book(_limits(), cash="30000", holdings={"A": "100000"})

    # Long 1000 at 100; sell 1300 -> short 300, which is 30,000 of new exposure.
    _, decision = RiskEngine.evaluate(
        state, _order(Side.SELL, "1300"), 1.0, position=Decimal("1000")
    )

    assert decision.approved, decision.violations
    assert decision.required_margin == Decimal("30000")


# --------------------------------------------------------------------------- #
# RSK-002: a book at its limit can always reduce
# --------------------------------------------------------------------------- #


def test_a_book_over_its_gross_limit_may_still_reduce() -> None:
    limits = _limits(exposure=ExposureLimit(Decimal("50000"), HUGE))
    state = _book(limits, cash="100000", holdings={"A": "100000"})

    _, reduce = RiskEngine.evaluate(state, _order(Side.SELL, "100"), 1.0, position=Decimal("1000"))
    _, grow = RiskEngine.evaluate(state, _order(Side.BUY, "1"), 1.0, position=Decimal("1000"))

    assert reduce.approved, reduce.violations
    assert "GrossExposureLimit" in _rules(grow)


def test_leverage_and_margin_refuse_growth_not_reduction() -> None:
    limits = _limits(leverage=LeverageLimit(Decimal("1")), margin=MarginLimit(Decimal("1")))
    state = _book(limits, cash="0", nav="50000", holdings={"A": "100000"})

    _, reduce = RiskEngine.evaluate(state, _order(Side.SELL, "10"), 1.0, position=Decimal("1000"))
    _, grow = RiskEngine.evaluate(
        replace(state, buying_power=HUGE), _order(Side.BUY, "10"), 1.0, position=Decimal("1000")
    )

    assert reduce.approved, reduce.violations
    assert {"LeverageLimit", "MarginLimit"} <= _rules(grow)


# --------------------------------------------------------------------------- #
# RSK-003: a breach is closed by the orders that reduce, and reported on all
# --------------------------------------------------------------------------- #


def test_a_drawdown_breach_lets_the_book_liquidate_and_says_so() -> None:
    limits = _limits(drawdown=DrawdownLimit(Decimal("0.10")))
    state = replace(
        _book(limits, cash="0", nav="85000", holdings={"A": "85000"}),
        peak_nav=Decimal("100000"),
    )

    _, liquidation = RiskEngine.evaluate(
        state, _order(Side.SELL, "850"), 1.0, position=Decimal("850")
    )
    _, growth = RiskEngine.evaluate(
        replace(state, buying_power=HUGE), _order(Side.BUY, "1"), 1.0, position=Decimal("850")
    )

    assert liquidation.approved, liquidation.violations
    assert [breach.rule for breach in liquidation.breaches] == ["DrawdownLimit"]
    assert "DrawdownLimit" in _rules(growth)


# --------------------------------------------------------------------------- #
# RSK-004: working orders count
# --------------------------------------------------------------------------- #


def test_working_orders_count_toward_the_position_limit() -> None:
    limits = _limits(position=PositionLimit(Decimal("100"), HUGE))
    state = _book(limits, cash=str(HUGE))
    working = {"A": WorkingExposure(Decimal("80"), Decimal("100"), Decimal("0"))}

    _, alone = RiskEngine.evaluate(state, _order(Side.BUY, "30"), 1.0)
    _, jointly = RiskEngine.evaluate(state, _order(Side.BUY, "30"), 1.0, working=working)

    assert alone.approved
    assert "PositionLimit" in _rules(jointly)


def test_working_buys_spend_buying_power_before_they_fill() -> None:
    state = _book(_limits(), cash="10000")
    working = {"B": WorkingExposure(Decimal("80"), Decimal("100"), Decimal("0"))}

    _, decision = RiskEngine.evaluate(state, _order(Side.BUY, "30"), 1.0, working=working)

    # 8,000 already committed by the working order; 3,000 more is beyond 10,000.
    assert "BuyingPowerLimit" in _rules(decision)


def test_a_working_sale_of_a_holding_spends_no_buying_power() -> None:
    state = _book(_limits(), cash="3000", holdings={"B": "8000"})
    working = {"B": WorkingExposure(Decimal("-80"), Decimal("100"), Decimal("80"))}

    _, decision = RiskEngine.evaluate(state, _order(Side.BUY, "30"), 1.0, working=working)

    assert decision.approved, decision.violations


# --------------------------------------------------------------------------- #
# RSK-005: every limit is inclusive, and one quantum past it is refused
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("limits", "order", "rule"),
    [
        (_limits(order_size=OrderSizeLimit(Decimal("10"), HUGE)), "10", "OrderSizeQuantity"),
        (_limits(order_size=OrderSizeLimit(HUGE, Decimal("1000"))), "10", "OrderSizeNotional"),
        (_limits(position=PositionLimit(Decimal("10"), HUGE)), "10", "PositionLimit"),
        (_limits(position=PositionLimit(HUGE, Decimal("1000"))), "10", "PositionNotionalLimit"),
        (_limits(exposure=ExposureLimit(Decimal("1000"), HUGE)), "10", "GrossExposureLimit"),
        (_limits(exposure=ExposureLimit(HUGE, Decimal("1000"))), "10", "NetExposureLimit"),
    ],
)
def test_a_limit_allows_its_own_value_and_refuses_one_quantum_more(
    limits: RiskLimits, order: str, rule: str
) -> None:
    state = _book(limits, cash=str(HUGE), nav=str(HUGE))

    _, at = RiskEngine.evaluate(state, _order(Side.BUY, order), 1.0)
    _, past = RiskEngine.evaluate(state, _order(Side.BUY, "10.000001"), 1.0)

    assert at.approved, at.violations
    assert rule in _rules(past)


def test_buying_power_is_inclusive() -> None:
    state = _book(_limits(), cash="1000")

    _, at = RiskEngine.evaluate(state, _order(Side.BUY, "10"), 1.0)
    _, past = RiskEngine.evaluate(state, _order(Side.BUY, "10.01"), 1.0)

    assert at.approved
    assert "BuyingPowerLimit" in _rules(past)


# --------------------------------------------------------------------------- #
# RSK-006: typed severity; undefined ratios refuse growth
# --------------------------------------------------------------------------- #


def test_severity_is_typed() -> None:
    state = _book(_limits(order_size=OrderSizeLimit(Decimal("1"), HUGE)), cash=str(HUGE))

    _, decision = RiskEngine.evaluate(state, _order(Side.BUY, "2"), 1.0)

    assert decision.violations[0].severity is RiskSeverity.HIGH
    assert RiskSeverity("CRITICAL") is RiskSeverity.CRITICAL


def test_a_severity_is_a_member_and_a_misspelled_one_is_refused() -> None:
    """The string a pre-v3.10 record carries reads as its member; a typo does not."""

    from alphalab.risk.models import RiskViolation

    written = RiskViolation("r", "d", "CRITICAL", Decimal("1"), Decimal("0"))  # type: ignore[arg-type]
    assert written.severity is RiskSeverity.CRITICAL

    with pytest.raises(RiskValidationError, match="Critical"):
        RiskViolation("r", "d", "Critical", Decimal("1"), Decimal("0"))  # type: ignore[arg-type]


def test_with_no_positive_nav_growth_is_refused_and_reduction_is_not() -> None:
    state = _book(_limits(), cash=str(HUGE), nav="0", holdings={"A": "1000"})

    _, grow = RiskEngine.evaluate(state, _order(Side.BUY, "1"), 1.0, position=Decimal("10"))
    _, reduce = RiskEngine.evaluate(state, _order(Side.SELL, "1"), 1.0, position=Decimal("10"))

    assert {"MarginLimit", "LeverageLimit"} <= _rules(grow)
    assert reduce.approved, reduce.violations


# --------------------------------------------------------------------------- #
# KD-001 / KD-003: position in units, net exposure enforced
# --------------------------------------------------------------------------- #


def test_the_position_limit_is_in_units_not_units_plus_value() -> None:
    limits = _limits(position=PositionLimit(Decimal("150"), HUGE))
    # Long 100 units worth 10,000: v3.9 compared 10,000 + 60 with 150 and refused.
    state = _book(limits, cash=str(HUGE), holdings={"A": "10000"})

    _, decision = RiskEngine.evaluate(state, _order(Side.BUY, "50"), 1.0, position=Decimal("100"))

    assert decision.approved, decision.violations


def test_net_exposure_is_enforced_on_its_magnitude() -> None:
    limits = _limits(exposure=ExposureLimit(HUGE, Decimal("5000")))
    state = _book(limits, cash=str(HUGE), holdings={"A": "4000", "B": "-1000"})

    _, short_more = RiskEngine.evaluate(state, _order(Side.SELL, "30", asset="B"), 1.0)
    _, long_more = RiskEngine.evaluate(state, _order(Side.BUY, "30"), 1.0, position=Decimal("40"))

    # Net is 3,000: selling 3,000 more of B reduces its magnitude to zero.
    assert short_more.approved, short_more.violations
    assert "NetExposureLimit" in _rules(long_more)


# --------------------------------------------------------------------------- #
# KD-002: daily loss, maintained against a declared trading day
# --------------------------------------------------------------------------- #


def _instant(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def test_a_daily_loss_limit_declares_its_zone() -> None:
    with pytest.raises(RiskValidationError, match="time zone"):
        DailyLossLimit(Decimal("100"), "")
    with pytest.raises(RiskValidationError, match="IANA"):
        DailyLossLimit(Decimal("100"), "Mars/Olympus_Mons")


def test_the_daily_loss_is_measured_from_the_trading_days_start() -> None:
    limit = DailyLossLimit(Decimal("1000"), "America/New_York")
    state = RiskEngine.reset(_limits(daily_loss=limit))
    # 15:00 New York on the 2nd, then 23:00 UTC (19:00 New York) the same day.
    state = RiskEngine.mark(
        state, nav=Decimal("100000"), cash=Decimal("0"), timestamp=_instant("2026-03-02T20:00")
    )
    state = RiskEngine.mark(
        state, nav=Decimal("98500"), cash=Decimal("0"), timestamp=_instant("2026-03-02T23:00")
    )

    assert state.trading_day == "2026-03-02"
    assert state.daily_loss == Decimal("1500")
    _, grow = RiskEngine.evaluate(replace(state, buying_power=HUGE), _order(Side.BUY, "1"), 1.0)
    assert "DailyLossLimit" in _rules(grow)

    # 06:00 UTC on the 3rd is 01:00 in New York: a new day, starting at the close.
    state = RiskEngine.mark(
        state, nav=Decimal("98400"), cash=Decimal("0"), timestamp=_instant("2026-03-03T06:00")
    )
    assert state.trading_day == "2026-03-03"
    assert state.day_start_nav == Decimal("98500")
    assert state.daily_loss == Decimal("100")


def test_a_trading_day_can_start_at_a_declared_local_time() -> None:
    limit = DailyLossLimit(Decimal("1"), "America/New_York", time(17))

    # 16:59 and 17:01 New York (21:59 and 22:01 UTC in March, before DST).
    assert limit.trading_day(_instant("2026-03-02T21:59")).isoformat() == "2026-03-01"
    assert limit.trading_day(_instant("2026-03-02T22:01")).isoformat() == "2026-03-02"


def test_with_no_daily_loss_limit_there_is_no_day_to_measure() -> None:
    state = RiskEngine.mark(
        RiskEngine.reset(_limits()), nav=Decimal("5"), cash=Decimal("0"), timestamp=1.0
    )

    assert state.daily_loss == 0
    assert state.trading_day is None


def test_a_reversal_is_not_a_reduction_during_a_breach() -> None:
    """Reduce-only means to zero and no further: a flip opens a new position."""

    limits = _limits(drawdown=DrawdownLimit(Decimal("0.10")))
    state = replace(
        _book(limits, cash=str(HUGE), nav="85000", holdings={"A": "85000"}),
        peak_nav=Decimal("100000"),
    )

    _, to_zero = RiskEngine.evaluate(state, _order(Side.SELL, "850"), 1.0, position=Decimal("850"))
    _, flip = RiskEngine.evaluate(state, _order(Side.SELL, "900"), 1.0, position=Decimal("850"))

    assert to_zero.approved, to_zero.violations
    assert "DrawdownLimit" in _rules(flip)
