"""v3.3 end to end: a real backtest, then costs, attribution, risk and stress.

Driven through the canonical execution path -- the same ``RunEngine``,
``ExecutionPipeline``, OMS and ``PortfolioEngine`` every other integration test
uses -- so the figures the institutional surfaces report are computed from fills
that actually happened rather than from records assembled for the occasion::

    market data
      -> strategy / backtest
      -> orders
      -> execution simulation   (itemized costs, liquidity-capped fills)
      -> fills
      -> portfolio              (cash, positions, realized P&L)
      -> attribution            (reconciled against the portfolio)
      -> risk decomposition     (contributions reconciled against volatility)
      -> scenario / stress      (applied to the book the run produced)

What is checked at each hop is the property the next hop depends on, and the
reconciliations are checked against the *portfolio's own* numbers, never against
a total recomputed from the same records being attributed.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.analytics.attribution import (
    AttributionDimension,
    Availability,
    TradeFacts,
    attribute,
)
from alphalab.analytics.decomposition import (
    PositionRisk,
    VaRMethod,
    VaRPolicy,
    leverage,
    portfolio_volatility,
    risk_contributions,
)
from alphalab.analytics.engine import PortfolioSnapshot
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.state import BacktestResult
from alphalab.core.enums import OrderStatus, Side
from alphalab.execution.capacity import AssetLiquidity, CapacityModel
from alphalab.execution.commission import PercentageCommission
from alphalab.execution.costs import (
    CostContext,
    ExecutionCostModel,
    NoSpread,
    PerTradeFee,
    ProportionalTax,
    SquareRootImpact,
    itemized,
)
from alphalab.execution.policy import LiquidityCappedFill
from alphalab.execution.report import ExecutionReport
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import PercentageSlippage
from alphalab.instrument.record import InstrumentRecord
from alphalab.scenario import (
    ScenarioExposure,
    ScenarioState,
    flash_crash,
    of_sector,
    scenario,
    sector_shock,
)
from tests.integration.harness import context_factory, equity, registry_of, scripted_run

STRATEGY = "MOM"
SECTOR = "Technology"


def institutional_simulator() -> ExecutionSimulator:
    """A simulator paying all six cost roles, each one named."""

    return ExecutionSimulator(
        cost_model=ExecutionCostModel(
            spread_model=NoSpread(),
            slippage_model=PercentageSlippage(Decimal("0.0005")),
            impact_model=SquareRootImpact(Decimal("0.02")),
            commission_model=PercentageCommission(Decimal("0.0005")),
            fee_model=PerTradeFee(Decimal("0.75")),
            tax_model=ProportionalTax(Decimal("0.001"), frozenset({Side.BUY})),
        )
    )


def run_backtest(
    *, simulator: ExecutionSimulator | None = None, participation: str | None = None
) -> tuple[InstrumentRecord, BacktestResult]:
    """One scripted backtest through the canonical path, with a classified name."""

    instrument = equity("AAPL", sector=SECTOR)
    plan = {
        2.0: Decimal("100"),
        3.0: Decimal("50"),
        4.0: Decimal("-80"),
        5.0: Decimal("-20"),
    }
    mids = [Decimal("100"), Decimal("104"), Decimal("101"), Decimal("108"), Decimal("106")]
    config, dataset, state = scripted_run(
        plan,
        mids,
        STRATEGY,
        instrument.asset_id,
        simulator=simulator if simulator is not None else institutional_simulator(),
        fill_policy=(
            None if participation is None else LiquidityCappedFill(Decimal(participation))
        ),
    )
    # The registry is what freezes a sector onto each fill's TradeRecord, which
    # is what makes sector attribution available below rather than absent.
    config = replace(config, pipeline=replace(config.pipeline, instruments=registry_of(instrument)))
    result = BacktestEngine.run(config, dataset, state, context_factory)
    return instrument, result


# --------------------------------------------------------------------------- #
# The run itself
# --------------------------------------------------------------------------- #


def test_the_backtest_produces_fills_through_the_canonical_path() -> None:
    _, result = run_backtest()
    pipeline = result.state

    assert len(pipeline.execution.history) > 0
    assert len(tuple(pipeline.trade_records)) == len(pipeline.execution.history)
    assert pipeline.portfolio.events


def test_every_fill_carries_the_sector_frozen_at_execution_time() -> None:
    _, result = run_backtest()

    assert all(record.sector_id == SECTOR for record in tuple(result.state.trade_records))


# --------------------------------------------------------------------------- #
# Execution costs reconcile with the portfolio's own accounting
# --------------------------------------------------------------------------- #


def test_the_commission_the_portfolio_paid_is_the_reports_cash_costs_rounded_once() -> None:
    """No execution cost is invented, dropped or double-counted on the way in.

    Not *equal* to the reports' figures, but equal to them rounded at the one
    rounding site. ``PercentageCommission`` quantizes to four places, the
    portfolio books money at two, and :func:`~alphalab.portfolio.money.to_money`
    is where that happens -- once, per fill, which is invariant 3 of
    ``nowandfuture.md`` section 14. Asserting exact equality across that
    boundary would be asserting that the rounding does not happen; asserting
    this is what shows nothing else does.
    """

    from alphalab.portfolio.money import to_money

    _, result = run_backtest()
    pipeline = result.state

    charged = sum(
        (to_money(report.commission) for report in pipeline.execution.history), Decimal("0")
    )
    booked = sum(pipeline.portfolio.commission_paid.values(), Decimal("0"))

    assert charged == booked
    # And the residue really is sub-cent per fill, not a lost component.
    raw = sum((report.commission for report in pipeline.execution.history), Decimal("0"))
    assert abs(raw - booked) < Decimal("0.01") * len(pipeline.execution.history)


def test_the_itemization_recomputes_exactly_from_the_run_s_own_configuration() -> None:
    """The breakdown is derived, so it is recoverable and cannot drift."""

    _, result = run_backtest()
    pipeline = result.state
    simulator = pipeline.config.simulator

    sides = sides_by_order(result)

    for report in pipeline.execution.history:
        side = sides[report.order_id]
        # The concession was added for a buy and subtracted for a sell, so the
        # reference price is recovered the same way round.
        reference = (
            report.fill_price - report.slippage
            if side is Side.BUY
            else report.fill_price + report.slippage
        )
        costs = simulator.costs.quote(_context(report, reference, side))

        assert costs.price_concession == report.slippage
        assert costs.cash_charged == report.commission


#: Both sides of every quote the harness builds show this many units, and the
#: pipeline forwards it to the cost model. A recomputation has to use the same
#: figure or it is pricing a different fill.
QUOTE_SIZE = Decimal("100")


def sides_by_order(result: BacktestResult) -> dict[str, Side]:
    """Which way each of the run's orders went, keyed by the report's order id."""

    return {str(order.order_id.value): order.side for order in result.orders}


def _context(report: ExecutionReport, reference: Decimal, side: Side) -> CostContext:
    """The context the pipeline priced ``report`` with, rebuilt exactly."""

    return CostContext(
        asset_id=report.asset_id,
        side=side,
        quantity=report.fill_quantity,
        reference_price=reference,
        currency=report.currency,
        venue=report.venue,
        timestamp=report.timestamp,
        available_liquidity=QUOTE_SIZE,
    )


def _facts_for(result: BacktestResult) -> dict[str, TradeFacts]:
    """Project each of the run's fills into the facts attribution needs.

    This is the projection an application writes: the execution path knows the
    currency and the venue of every report, and recomputes the cost itemization
    from the configuration the run was driven with.
    """

    pipeline = result.state
    sides = sides_by_order(result)
    facts: dict[str, TradeFacts] = {}
    for report in pipeline.execution.history:
        side = sides[report.order_id]
        reference = (
            report.fill_price - report.slippage
            if side is Side.BUY
            else report.fill_price + report.slippage
        )
        facts[report.execution_id] = TradeFacts(
            report.execution_id,
            currency=report.currency,
            venue=report.venue,
            execution_costs=itemized(
                pipeline.config.simulator.costs.quote(_context(report, reference, side)),
                report.fill_quantity,
            ),
        )
    return facts


def test_a_price_embedded_cost_never_reaches_the_cash_ledger() -> None:
    """Slippage moves the fill price; only the cash roles are debited."""

    _, priced = run_backtest()
    _, free = run_backtest(simulator=ExecutionSimulator())

    priced_cash = sum(priced.state.portfolio.commission_paid.values(), Decimal("0"))
    free_cash = sum(free.state.portfolio.commission_paid.values(), Decimal("0"))

    assert priced_cash > free_cash
    # And the concession shows up in the prices, not in the cash figure.
    assert all(report.slippage > 0 for report in priced.state.execution.history)
    assert all(report.slippage == 0 for report in free.state.execution.history)


def test_costing_a_run_makes_it_worse_off_than_the_frictionless_one() -> None:
    _, priced = run_backtest()
    _, free = run_backtest(simulator=ExecutionSimulator())

    def realized(run: BacktestResult) -> Decimal:
        return sum(run.state.portfolio.realized_pnl.values(), Decimal("0"))

    assert realized(priced) < realized(free)


# --------------------------------------------------------------------------- #
# Partial fills and the liquidity cap, on the real path
# --------------------------------------------------------------------------- #


def test_a_liquidity_cap_holds_for_every_fill_the_pipeline_produced() -> None:
    """The quotes show 100 units; at 10% participation no fill exceeds 10."""

    _, result = run_backtest(participation="0.10")
    history = result.state.execution.history

    assert history
    assert all(report.fill_quantity <= Decimal("10") for report in history)


def test_capping_liquidity_fills_less_than_the_uncapped_run() -> None:
    _, capped = run_backtest(participation="0.10")
    _, uncapped = run_backtest()

    def filled(run: BacktestResult) -> Decimal:
        return sum(
            (report.fill_quantity for report in run.state.execution.history),
            Decimal("0"),
        )

    assert filled(capped) < filled(uncapped)


def test_a_capped_run_leaves_no_order_working_forever() -> None:
    _, result = run_backtest(participation="0.10")
    statuses = {order.status for order in result.orders}

    assert OrderStatus.PARTIALLY_FILLED not in statuses


# --------------------------------------------------------------------------- #
# Attribution reconciles against the portfolio, not against itself
# --------------------------------------------------------------------------- #


def test_attribution_reconciles_to_the_portfolio_s_realized_pnl() -> None:
    _, result = run_backtest()
    pipeline = result.state
    records = tuple(pipeline.trade_records)

    facts = _facts_for(result)
    report = attribute(records, facts)
    booked = sum(pipeline.portfolio.realized_pnl.values(), Decimal("0"))

    assert report.realized_pnl == booked
    for dimension in (
        AttributionDimension.STRATEGY,
        AttributionDimension.ASSET,
        AttributionDimension.SECTOR,
        AttributionDimension.VENUE,
    ):
        built = report.dimensions[dimension]
        assert built.availability is Availability.AVAILABLE
        assert built.reconciles
        assert sum(built.buckets.values()) == booked


def test_the_strategy_bucket_is_the_real_strategy_and_not_a_netting_placeholder() -> None:
    _, result = run_backtest()
    records = tuple(result.state.trade_records)
    built = attribute(records).dimensions[AttributionDimension.STRATEGY]

    assert set(built.buckets) == {STRATEGY}
    assert "ALLOC-NETTED" not in built.buckets


def test_country_and_broker_are_unavailable_because_the_run_holds_neither() -> None:
    """The honest answer for metadata AlphaLab does not have."""

    _, result = run_backtest()
    records = tuple(result.state.trade_records)
    report = attribute(records)

    for dimension in (AttributionDimension.COUNTRY, AttributionDimension.BROKER):
        built = report.dimensions[dimension]
        assert built.availability is Availability.NO_METADATA
        assert built.buckets == {}


def test_execution_attribution_sums_to_what_the_run_actually_paid() -> None:
    _, result = run_backtest()
    pipeline = result.state
    records = tuple(pipeline.trade_records)

    facts = _facts_for(result)
    built = attribute(records, facts).dimensions[AttributionDimension.EXECUTION]
    charged = sum((r.commission for r in pipeline.execution.history), Decimal("0"))
    concessions = sum(
        (r.slippage * r.fill_quantity for r in pipeline.execution.history), Decimal("0")
    )

    assert float(-sum(built.buckets.values())) == pytest.approx(
        float(charged + concessions), rel=1e-9
    )


# --------------------------------------------------------------------------- #
# Risk, measured on the book the run produced
# --------------------------------------------------------------------------- #


def test_risk_decomposition_reconciles_on_the_run_s_own_positions() -> None:
    instrument, result = run_backtest()
    pipeline = result.state
    snapshots = tuple(pipeline.portfolio_snapshots)
    returns = _returns_from(snapshots)

    assert pipeline.portfolio.positions, "the scripted plan leaves a position open"

    book = tuple(
        PositionRisk(position.asset_id, position.market_value, position.currency, returns)
        for position in pipeline.portfolio.positions.values()
    )

    assert sum(risk_contributions(book).values()) == pytest.approx(
        portfolio_volatility(book), rel=1e-12
    )
    assert instrument.asset_id in {item.asset_id for item in book}


def test_leverage_is_measured_against_the_run_s_own_equity() -> None:
    _, result = run_backtest()
    pipeline = result.state
    snapshots = tuple(pipeline.portfolio_snapshots)
    equity_value = snapshots[-1].total_equity
    returns = _returns_from(snapshots)

    book = tuple(
        PositionRisk(position.asset_id, position.market_value, position.currency, returns)
        for position in pipeline.portfolio.positions.values()
    )
    metrics = leverage(book, equity_value)

    assert metrics.gross_exposure == sum(
        (abs(position.market_value) for position in pipeline.portfolio.positions.values()),
        Decimal("0"),
    )
    assert metrics.gross_leverage > 0


def test_var_on_the_run_s_equity_curve_states_its_method() -> None:
    _, result = run_backtest()
    returns = _returns_from(tuple(result.state.portfolio_snapshots))
    policy = VaRPolicy(VaRMethod.HISTORICAL, 0.95)

    assert policy.var(returns) == policy.var(returns)
    assert policy.identity() == "HISTORICAL@0.95"


def _returns_from(snapshots: tuple[PortfolioSnapshot, ...]) -> tuple[float, ...]:
    values = [float(snapshot.total_equity) for snapshot in snapshots]
    return tuple(
        (values[index] - values[index - 1]) / values[index - 1]
        for index in range(1, len(values))
        if values[index - 1] != 0.0
    )


# --------------------------------------------------------------------------- #
# Stress, applied to the book the run produced
# --------------------------------------------------------------------------- #


def scenario_state_from(result: BacktestResult) -> ScenarioState:
    pipeline = result.state
    return ScenarioState(
        exposures=tuple(
            ScenarioExposure(
                asset_id=position.asset_id,
                quantity=position.quantity,
                price=position.market_price,
                currency=position.currency,
                volatility=0.2,
                available_liquidity=Decimal("100"),
                sector=SECTOR,
            )
            for position in pipeline.portfolio.positions.values()
        ),
        base_currency=pipeline.config.currency,
        rates={},
    )


def test_a_scenario_applies_to_the_book_a_real_run_produced() -> None:
    _, result = run_backtest()
    state = scenario_state_from(result)
    stressed = flash_crash(Decimal("-0.20")).apply(state)

    assert stressed.relative_change == Decimal("-0.20")
    assert sum(stressed.change_by_asset.values()) == stressed.change


def test_stressing_the_run_s_book_does_not_disturb_the_run() -> None:
    """Scenario leakage into the base state, checked on a real portfolio."""

    _, result = run_backtest()
    pipeline = result.state
    before = {
        asset_id: (position.quantity, position.market_price)
        for asset_id, position in pipeline.portfolio.positions.items()
    }

    state = scenario_state_from(result)
    for magnitude in ("-0.10", "-0.35", "-0.90"):
        flash_crash(Decimal(magnitude)).apply(state)
        scenario("vol", volatility_shock=Decimal(magnitude)).apply(state)

    after = {
        asset_id: (position.quantity, position.market_price)
        for asset_id, position in pipeline.portfolio.positions.items()
    }

    assert before == after


def test_a_sector_scenario_reaches_the_sector_the_registry_classified() -> None:
    _, result = run_backtest()
    state = scenario_state_from(result)
    stressed = sector_shock(Decimal("-0.30"), SECTOR).apply(state)

    assert stressed.reached == (len(state.exposures),)


def test_a_composed_scenario_is_reproducible_on_a_real_book() -> None:
    _, result = run_backtest()
    state = scenario_state_from(result)
    composed = flash_crash(Decimal("-0.15")).then(
        scenario("dry", liquidity_shock=Decimal("-0.80"), scope=of_sector(SECTOR))
    )

    assert composed.apply(state) == composed.apply(state)
    assert composed.identity() == composed.identity()


# --------------------------------------------------------------------------- #
# Capacity, measured with the impact model the run was priced with
# --------------------------------------------------------------------------- #


def test_capacity_uses_the_same_impact_model_the_run_paid() -> None:
    instrument, result = run_backtest()
    simulator = result.state.config.simulator
    impact = simulator.costs.impact_model

    model = CapacityModel(
        participation_limit=Decimal("0.10"),
        turnover=Decimal("0.25"),
        impact_model=impact,
        impact_budget=Decimal("0.005"),
        search_ceiling=Decimal("1e12"),
    )
    universe = (
        AssetLiquidity(
            instrument.asset_id, Decimal("106"), Decimal("500000"), Decimal("1"), "USD", "SIM"
        ),
    )
    capacity = model.capacity(universe, 0.0)

    assert capacity.capacity > 0
    assert capacity.assumptions["impact_model"] == "SquareRootImpact"
    assert capacity.binding_asset_id == instrument.asset_id
