"""Execution quality: shortfall, slippage, fill quality, latency, rejections, venues, currency."""

from __future__ import annotations

import dataclasses
import decimal
from decimal import Decimal
from typing import Any

import pytest

from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import OrderStatus, Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.fill import FillStatus
from alphalab.execution.quality import (
    ClockSource,
    ExecutionBenchmarks,
    LifecycleMark,
    OrderExecution,
    OrderOutcome,
    OrderTimeline,
    ReferencePrice,
    TimelineMark,
    execution_quality_report,
    fill_quality,
    implementation_shortfall,
    measure_latency,
    measure_slippage,
    rejection_rate,
    venue_quality,
)
from alphalab.execution.report import ExecutionReport
from alphalab.portfolio.fx import FxRate, FxRates, MissingRateError

D = Decimal
CONTRIBUTIONS = (StrategyContribution("ALPHA", D("70")), StrategyContribution("BETA", D("30")))


def _order(
    order_id: str = "ORD-1",
    side: Side = Side.BUY,
    quantity: str = "100",
    price: str = "50",
    contributions: tuple[StrategyContribution, ...] = CONTRIBUTIONS,
    asset: str = "ASSET",
) -> OrderRequest:
    return OrderRequest(order_id, "", asset, side, D(quantity), D(price), 10.0, contributions)


def _fill(
    execution_id: str,
    quantity: str,
    price: str,
    order_id: str = "ORD-1",
    venue: str = "VEN-A",
    commission: str = "1",
    currency: str = "USD",
    at: float = 20.0,
) -> ExecutionReport:
    return ExecutionReport(
        execution_id=execution_id,
        order_id=order_id,
        asset_id="ASSET",
        strategy_id="",
        timestamp=at,
        fill_price=D(price),
        fill_quantity=D(quantity),
        commission=D(commission),
        slippage=D("0"),
        liquidity_flag="",
        venue=venue,
        currency=currency,
        status=FillStatus.PARTIAL_FILL,
    )


def _benchmarks(**overrides: Any) -> ExecutionBenchmarks:
    base = ExecutionBenchmarks(
        decision_price=None,
        arrival_price=D("50.10"),
        interval_vwap=D("50.20"),
        end_price=D("51"),
        source="desk-marks",
    )
    return dataclasses.replace(base, **overrides)


def _execution(
    fills: tuple[ExecutionReport, ...] = (_fill("X-1", "60", "50.25"), _fill("X-2", "40", "50.30")),
    order: OrderRequest | None = None,
    **overrides: Any,
) -> OrderExecution:
    fields: dict[str, Any] = {
        "order": order or _order(),
        "fills": fills,
        "currency": "USD",
        "outcome": OrderStatus.FILLED,
        "benchmarks": _benchmarks(),
        "limit_price": D("50.50"),
        "timeline": None,
        "fill_midpoints": None,
        "account_id": "ACC-1",
    }
    fields.update(overrides)
    return OrderExecution(**fields)


# --------------------------------------------------------------------------- #
# Implementation shortfall
# --------------------------------------------------------------------------- #


def test_a_fully_filled_buy_decomposes_exactly() -> None:
    shortfall = implementation_shortfall(_execution())
    # 60 @ 50.25 and 40 @ 50.30 against a decision at 50.
    assert shortfall.execution_cost == D("60") * D("0.25") + D("40") * D("0.30")
    assert shortfall.explicit_costs == D("2")
    assert shortfall.opportunity_cost == 0
    assert shortfall.total == D("29")
    assert shortfall.delay_cost == D("100") * D("0.10")
    assert shortfall.trading_cost == D("60") * D("0.15") + D("40") * D("0.20")
    assert shortfall.delay_cost + shortfall.trading_cost == shortfall.execution_cost
    assert shortfall.paper_notional == D("5000")
    assert shortfall.total_bps == D("58")
    assert shortfall.average_fill_price == D("50.27")
    assert shortfall.execution_ids == ("X-1", "X-2")
    assert shortfall.currency == "USD"
    assert "reference price" in shortfall.assumptions[0]


def test_a_sale_below_its_decision_is_a_cost_and_above_it_a_gain() -> None:
    sold_low = implementation_shortfall(
        _execution(fills=(_fill("X-1", "100", "49.80"),), order=_order(side=Side.SELL))
    )
    sold_high = implementation_shortfall(
        _execution(fills=(_fill("X-1", "100", "50.20"),), order=_order(side=Side.SELL))
    )
    assert sold_low.execution_cost == D("20")
    assert sold_high.execution_cost == D("-20")


def test_an_unfilled_remainder_is_costed_at_the_end_price_or_left_unknown() -> None:
    partial = _execution(fills=(_fill("X-1", "60", "50.25"),), outcome=OrderStatus.CANCELLED)
    known = implementation_shortfall(partial)
    assert known.unfilled_quantity == D("40")
    assert known.opportunity_cost == D("40")
    assert known.total == D("15") + D("1") + D("40")

    unknown = implementation_shortfall(
        dataclasses.replace(partial, benchmarks=_benchmarks(end_price=None))
    )
    assert unknown.opportunity_cost is None and unknown.total is None
    assert unknown.total_bps is None and unknown.strategy_shares == ()
    assert "no end price" in unknown.unavailable["opportunity_cost"]


def test_a_supplied_decision_price_replaces_the_orders_and_says_so() -> None:
    shortfall = implementation_shortfall(
        _execution(benchmarks=_benchmarks(decision_price=D("50.05")))
    )
    assert shortfall.decision_price == D("50.05")
    assert "desk-marks" in shortfall.assumptions[0]


def test_the_strategy_shares_sum_exactly_to_the_total() -> None:
    shortfall = implementation_shortfall(
        _execution(
            order=_order(
                contributions=(
                    StrategyContribution("A", D("1")),
                    StrategyContribution("B", D("1")),
                    StrategyContribution("C", D("1")),
                )
            )
        )
    )
    assert shortfall.total is not None
    assert sum((share for _, share in shortfall.strategy_shares), D(0)) == shortfall.total
    assert [name for name, _ in shortfall.strategy_shares] == ["A", "B", "C"]


def test_a_rejected_order_has_no_fills_and_only_opportunity_cost() -> None:
    rejected = implementation_shortfall(_execution(fills=(), outcome=OrderStatus.REJECTED))
    assert rejected.execution_cost == 0 and rejected.explicit_costs == 0
    assert rejected.opportunity_cost == D("100")
    assert rejected.average_fill_price is None


def test_without_an_arrival_price_the_split_is_unavailable_not_zero() -> None:
    shortfall = implementation_shortfall(_execution(benchmarks=_benchmarks(arrival_price=None)))
    assert shortfall.delay_cost is None and shortfall.trading_cost is None
    assert "arrival" in shortfall.unavailable["delay_cost"]
    assert shortfall.total == D("29")


# --------------------------------------------------------------------------- #
# Slippage
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("reference", "per_unit"),
    [
        (ReferencePrice.DECISION, D("0.27")),
        (ReferencePrice.ARRIVAL, D("0.17")),
        (ReferencePrice.INTERVAL_VWAP, D("0.07")),
    ],
)
def test_slippage_is_measured_against_the_named_reference(
    reference: ReferencePrice, per_unit: Decimal
) -> None:
    measured = measure_slippage(_execution(), reference)
    assert measured.available
    assert measured.per_unit == per_unit
    assert measured.amount == per_unit * 100
    assert measured.reference is reference


def test_slippage_without_its_reference_or_a_fill_is_unavailable() -> None:
    no_vwap = measure_slippage(
        _execution(benchmarks=_benchmarks(interval_vwap=None)), ReferencePrice.INTERVAL_VWAP
    )
    assert not no_vwap.available and "interval_vwap" in no_vwap.reason
    no_fill = measure_slippage(_execution(fills=()), ReferencePrice.DECISION)
    assert not no_fill.available and "no fill" in no_fill.reason


# --------------------------------------------------------------------------- #
# Fill quality
# --------------------------------------------------------------------------- #


def test_fill_quality_states_completeness_price_quality_and_fragmentation() -> None:
    execution = _execution(fill_midpoints={"X-1": D("50.20"), "X-2": D("50.25")})
    quality = fill_quality(execution)
    assert quality.fill_ratio == 1 and quality.fill_count == 2
    assert quality.limit_improvement_per_unit == D("0.23")
    expected = (
        (D(60) * (D(2) * D("0.05") / D("50.20")) + D(40) * (D(2) * D("0.05") / D("50.25")))
        / D(100)
        * D(10000)
    )
    assert quality.effective_spread_bps is not None
    assert abs(quality.effective_spread_bps - expected) < D("1e-20")


def test_fill_quality_says_what_it_could_not_measure() -> None:
    partial = fill_quality(
        _execution(
            fills=(_fill("X-1", "25", "50.25"),), limit_price=None, outcome=OrderStatus.EXPIRED
        )
    )
    assert partial.fill_ratio == D("0.25")
    assert partial.limit_improvement_per_unit is None
    assert set(partial.unavailable) == {"limit_improvement_per_unit", "effective_spread_bps"}
    incomplete_midpoints = fill_quality(_execution(fill_midpoints={"X-1": D("50.2")}))
    assert incomplete_midpoints.effective_spread_bps is None


# --------------------------------------------------------------------------- #
# Latency
# --------------------------------------------------------------------------- #


def _timeline(*marks: tuple[LifecycleMark, float, ClockSource]) -> OrderTimeline:
    return OrderTimeline("ORD-1", "VEN-A", tuple(TimelineMark(m, at, c) for m, at, c in marks))


def test_latency_names_its_ends_units_and_clocks() -> None:
    L, V = ClockSource.LOCAL, ClockSource.VENUE
    timeline = _timeline(
        (LifecycleMark.SUBMISSION, 100.0, L),
        (LifecycleMark.ACKNOWLEDGEMENT, 100.25, V),
        (LifecycleMark.FIRST_FILL, 101.5, V),
    )
    ack = measure_latency(timeline, LifecycleMark.SUBMISSION, LifecycleMark.ACKNOWLEDGEMENT)
    assert ack.seconds == D("0.25") and ack.mixed_clocks
    fill = measure_latency(timeline, LifecycleMark.ACKNOWLEDGEMENT, LifecycleMark.FIRST_FILL)
    assert fill.seconds == D("1.25") and not fill.mixed_clocks
    missing = measure_latency(timeline, LifecycleMark.SUBMISSION, LifecycleMark.TERMINAL)
    assert missing.seconds is None and "terminal" in missing.reason


def test_an_end_before_its_start_is_inconsistent_never_negative() -> None:
    timeline = _timeline(
        (LifecycleMark.SUBMISSION, 100.0, ClockSource.LOCAL),
        (LifecycleMark.ACKNOWLEDGEMENT, 99.9, ClockSource.VENUE),
    )
    measured = measure_latency(timeline, LifecycleMark.SUBMISSION, LifecycleMark.ACKNOWLEDGEMENT)
    assert measured.seconds is None
    assert "Inconsistent" in measured.reason and "two clocks" in measured.reason


def test_a_timeline_records_each_mark_once() -> None:
    with pytest.raises(ExecutionValidationError, match="more than once"):
        _timeline(
            (LifecycleMark.SUBMISSION, 1.0, ClockSource.LOCAL),
            (LifecycleMark.SUBMISSION, 2.0, ClockSource.LOCAL),
        )


# --------------------------------------------------------------------------- #
# Rejection rate
# --------------------------------------------------------------------------- #


OUTCOMES = (
    OrderOutcome("O-1", "VEN-A", 10.0, OrderStatus.FILLED),
    OrderOutcome("O-2", "VEN-A", 20.0, OrderStatus.REJECTED),
    OrderOutcome("O-3", "VEN-B", 30.0, OrderStatus.CANCELLED),
    OrderOutcome("O-4", "VEN-B", 40.0, OrderStatus.PENDING),
    OrderOutcome("O-5", "VEN-A", 50.0, OrderStatus.REJECTED),
    OrderOutcome("O-6", "VEN-A", 60.0, OrderStatus.ACCEPTED),
)


def test_the_rejection_rate_counts_rejections_over_resolved_submissions() -> None:
    rate = rejection_rate(OUTCOMES, window_start=0.0, window_end=100.0, venue=None)
    assert (rate.submitted, rate.resolved, rate.rejected, rate.unresolved) == (6, 5, 2, 1)
    assert rate.rate == D("0.4")


def test_the_window_is_half_open_and_the_venue_scopes_it() -> None:
    scoped = rejection_rate(OUTCOMES, window_start=20.0, window_end=50.0, venue=None)
    assert [scoped.submitted, scoped.rejected] == [3, 1]
    venue = rejection_rate(OUTCOMES, window_start=0.0, window_end=100.0, venue="VEN-A")
    assert (venue.submitted, venue.rejected, venue.rate) == (4, 2, D("0.5"))


def test_nothing_resolved_is_no_rate_not_a_zero_rate() -> None:
    rate = rejection_rate(OUTCOMES, window_start=35.0, window_end=45.0, venue=None)
    assert (rate.submitted, rate.resolved, rate.rate) == (1, 0, None)
    with pytest.raises(ExecutionValidationError, match="empty"):
        rejection_rate(OUTCOMES, window_start=5.0, window_end=5.0, venue=None)
    with pytest.raises(ExecutionValidationError, match="counted twice"):
        rejection_rate((*OUTCOMES, OUTCOMES[0]), window_start=0.0, window_end=99.0, venue=None)


# --------------------------------------------------------------------------- #
# Venue quality
# --------------------------------------------------------------------------- #


def test_venue_quality_groups_fills_by_where_they_executed() -> None:
    split = _execution(
        fills=(
            _fill("X-1", "60", "50.25", venue="VEN-A"),
            _fill("X-2", "40", "50.30", venue="VEN-B"),
        ),
        timeline=OrderTimeline(
            "ORD-1",
            "VEN-A",
            (
                TimelineMark(LifecycleMark.SUBMISSION, 11.0, ClockSource.LOCAL),
                TimelineMark(LifecycleMark.ACKNOWLEDGEMENT, 11.5, ClockSource.VENUE),
            ),
        ),
    )
    rejected_only = OrderOutcome("O-9", "VEN-C", 12.0, OrderStatus.REJECTED)
    venues = venue_quality(
        [split],
        [OrderOutcome("ORD-1", "VEN-A", 11.0, OrderStatus.FILLED), rejected_only],
        reference=ReferencePrice.DECISION,
        window_start=0.0,
        window_end=100.0,
    )
    by_venue = {v.venue: v for v in venues}
    assert set(by_venue) == {"VEN-A", "VEN-B", "VEN-C"}
    assert by_venue["VEN-A"].filled_quantity == D("60")
    assert by_venue["VEN-A"].slippage_bps == D("50")
    assert by_venue["VEN-B"].slippage_bps == D("60")
    assert by_venue["VEN-A"].acknowledgement_seconds_median == D("0.5")
    assert by_venue["VEN-C"].currency is None and by_venue["VEN-C"].fills == 0
    assert by_venue["VEN-C"].rejection_rate.rate == D("1")


def test_a_venue_filling_in_two_currencies_is_reported_per_currency() -> None:
    dollars = _execution()
    euros = _execution(
        order=_order("ORD-2"),
        fills=(_fill("X-3", "100", "50.25", order_id="ORD-2", currency="EUR"),),
        currency="EUR",
    )
    venues = venue_quality(
        [dollars, euros], [], reference=ReferencePrice.DECISION, window_start=0.0, window_end=1.0
    )
    assert [(v.venue, v.currency) for v in venues] == [("VEN-A", "EUR"), ("VEN-A", "USD")]


# --------------------------------------------------------------------------- #
# Evidence validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("fills", "message"),
    [
        ((_fill("X-1", "10", "50", order_id="OTHER"),), "not ORD-1"),
        ((_fill("X-1", "10", "50", currency="EUR"),), "one order is measured in one currency"),
        ((_fill("X-1", "10", "50"), _fill("X-1", "10", "50")), "appear twice"),
        ((_fill("X-1", "101", "50"),), "overfill"),
    ],
)
def test_evidence_that_cannot_be_one_orders_is_refused(
    fills: tuple[ExecutionReport, ...], message: str
) -> None:
    with pytest.raises(ExecutionValidationError, match=message):
        _execution(fills=fills)


def test_benchmarks_must_be_prices_with_a_source() -> None:
    with pytest.raises(ExecutionValidationError, match="no source"):
        _benchmarks(source=" ")
    with pytest.raises(ExecutionValidationError, match="not a price"):
        _benchmarks(arrival_price=D("0"))


# --------------------------------------------------------------------------- #
# The report: per currency, and once through a converter
# --------------------------------------------------------------------------- #


def _two_currencies() -> list[OrderExecution]:
    euro_order = _order("ORD-EUR", contributions=(StrategyContribution("BETA", D("5")),))
    return [
        _execution(),
        _execution(
            order=euro_order,
            fills=(_fill("E-1", "100", "50.10", order_id="ORD-EUR", currency="EUR"),),
            currency="EUR",
        ),
    ]


RATES = FxRates.of([FxRate("EUR", "USD", D("1.10"), 5.0, "desk-fix")])


def test_totals_are_kept_per_currency_and_converted_only_through_rates() -> None:
    report = execution_quality_report(
        _two_currencies(), reporting_currency="USD", converter=RATES, conversion_at=100.0
    )
    assert dict(report.total_by_currency) == {"EUR": D("11.00"), "USD": D("29")}
    assert report.reporting_total == D("29") + D("12.10")
    assert [c.rate.source for c in report.conversions] == ["identity", "desk-fix"]
    assert report.conversion_at == 100.0
    assert dict(report.strategy_totals["EUR"]) == {"BETA": D("11.00")}
    assert sum(report.strategy_totals["USD"].values(), D(0)) == D("29")


def test_no_rate_is_invented() -> None:
    with pytest.raises(MissingRateError):
        execution_quality_report(
            _two_currencies(), reporting_currency="JPY", converter=RATES, conversion_at=100.0
        )
    with pytest.raises(ExecutionValidationError, match="converter"):
        execution_quality_report(
            _two_currencies(), reporting_currency="USD", converter=None, conversion_at=None
        )
    unconverted = execution_quality_report(
        _two_currencies(), reporting_currency=None, converter=None, conversion_at=None
    )
    assert unconverted.reporting_total is None and unconverted.conversions == ()


def test_an_order_with_no_total_withholds_the_reporting_total() -> None:
    unknown = _execution(
        order=_order("ORD-3"),
        fills=(_fill("X-9", "10", "50.25", order_id="ORD-3"),),
        benchmarks=_benchmarks(end_price=None),
    )
    report = execution_quality_report(
        [*_two_currencies(), unknown],
        reporting_currency="USD",
        converter=RATES,
        conversion_at=100.0,
    )
    assert report.incomplete_orders == ("ORD-3",)
    assert report.reporting_total is None
    assert "ORD-3" in report.assumptions[0]


def test_the_report_is_deterministic_and_refuses_an_order_twice() -> None:
    first = execution_quality_report(
        _two_currencies(), reporting_currency="USD", converter=RATES, conversion_at=100.0
    )
    second = execution_quality_report(
        _two_currencies(), reporting_currency="USD", converter=RATES, conversion_at=100.0
    )
    assert first.report_id == second.report_id
    with pytest.raises(ExecutionValidationError, match="measured twice"):
        execution_quality_report(
            [_execution(), _execution()],
            reporting_currency=None,
            converter=None,
            conversion_at=None,
        )


def test_two_strategies_trading_one_instrument_stay_distinct() -> None:
    alpha = _execution(
        order=_order("O-A", contributions=(StrategyContribution("ALPHA", D("100")),)),
        fills=(_fill("A-1", "100", "50.25", order_id="O-A"),),
    )
    beta = _execution(
        order=_order("O-B", contributions=(StrategyContribution("BETA", D("100")),)),
        fills=(_fill("B-1", "100", "50.50", order_id="O-B"),),
    )
    report = execution_quality_report(
        [alpha, beta], reporting_currency=None, converter=None, conversion_at=None
    )
    assert dict(report.strategy_totals["USD"]) == {"ALPHA": D("26"), "BETA": D("51")}


def test_measurements_do_not_read_the_callers_decimal_context() -> None:
    expected = implementation_shortfall(_execution())
    with decimal.localcontext() as context:
        context.prec = 2
        assert implementation_shortfall(_execution()) == expected
