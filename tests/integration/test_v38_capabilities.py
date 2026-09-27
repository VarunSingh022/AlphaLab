"""The five v3.8 capabilities, joined through the real packages beneath them.

Nothing is mocked. Three independent strategies run through the canonical
execution path -- two in a dollar account, one in a euro account -- first on
research capital, then on the capital a plan allocates them. Their own
portfolio states become the sleeves of one book; the book is valued through
the FX authority; its risk is budgeted along five dimensions with sectors read
from the instrument registry; the strategies are compared with loadings from
the factor library's panels; and every identity along the way is rebuilt from
scratch and must come out the same.

.. code-block:: text

    registry (sectors, currencies) ---------------------------------------+
    quotes --> research runs --> strategy returns --> risk parity         |
                                                         |                |
    reservation ledger --> capital plan (FX) <-----------+                |
                               |                                           |
                               v                                           |
            execution budgets --> production runs --> sleeves --> book    |
                                                                  |        |
    asset returns (in USD) --> covariance --+                     v        |
    wire bars --> factor panels --> loadings +--> risk budget <-- valuation|
                                             +--> cross-strategy risk <---+
                                             +--> factor-neutral / BL construction
                                                          |
                                             fingerprint <-+--> rebuilt: same identities

What is looked for is the class of defect no unit test can see: an identity
that changes between hops, a figure in one currency read as another, a
strategy's holding that loses its owner on the way into an aggregate, capital
allocated twice, and risk that does not add back up.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import ROUND_DOWN, Decimal
from itertools import pairwise
from typing import Any

import pytest

from alphalab.allocation import (
    AllocationConstraints,
    AllocationState,
    CapitalAccount,
    CapitalAllocationPlan,
    CapitalAllocationResult,
    CapitalBudget,
    CapitalDimension,
    CapitalLimit,
    CapitalPlacement,
    OversubscriptionRule,
    PlacementWeights,
    allocate_capital,
    capital_budget,
    reserved_capital,
)
from alphalab.analytics import (
    BudgetBasis,
    BudgetLimit,
    BudgetStatus,
    Classification,
    CommonDimension,
    CovarianceMatrix,
    RiskBudget,
    RiskDimension,
    StrategyReturns,
    capital_concentration,
    capital_overlap,
    common_exposures,
    evaluate_risk_budget,
    factor_crowding,
    strategy_overlap,
    strategy_return_correlation,
)
from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_model import FactorLoadings
from alphalab.api import currency_classification, sector_classification
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.state import BacktestResult
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import AssetType
from alphalab.data.feed import Bar as WireBar
from alphalab.execution.policy import ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    compute_panel,
    loadings_from_panels,
    observations_from_records,
    percentile_rank_panel,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import (
    InstrumentRegistry,
    classify_instrument,
    register_instruments,
)
from alphalab.lifecycle import research_configuration_with_portfolio, verify_fingerprint
from alphalab.market.quote import Quote
from alphalab.portfolio import (
    Account,
    CurrencyAmounts,
    FxRate,
    FxRates,
    MultiStrategyBook,
    StrategySleeve,
    value_book,
)
from alphalab.portfolio.valuation import PortfolioValuation
from alphalab.portfolio_optimizer import (
    BlackLittermanModel,
    ConstraintSet,
    ConstructionProblem,
    ConstructionResult,
    ConstructionStatus,
    EquilibriumPrior,
    ExposureRange,
    FactorBound,
    GroupBound,
    InvestorView,
    MeanVariance,
    MinimumVariance,
    RiskParity,
    SolverSettings,
    TurnoverLimit,
    WeightBounds,
    black_litterman,
    construct,
)
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.runtime.run import ExecutionMode, RunConfig
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    context_factory,
    permissive_risk_limits,
    running_strategy_state,
)
from tests.unit.lifecycle.evidence_harness import fingerprint

STEPS = 30
SETTINGS = SolverSettings(1e-10, 1e-10, 2_000)

APPLE = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD")
MICROSOFT = InstrumentRecord("MSFT", AssetType.EQUITY, "XNAS", "USD")
EXXON = InstrumentRecord("XOM", AssetType.EQUITY, "XNYS", "USD")
SAP = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR")
INSTRUMENTS = (APPLE, MICROSOFT, EXXON, SAP)
IDS = {record.symbol: record.asset_id for record in INSTRUMENTS}


def _registry() -> InstrumentRegistry:
    registry = register_instruments(InstrumentRegistry(), INSTRUMENTS)
    for record, sector, source in (
        (APPLE, "Technology", "vendor-gics-2026"),
        (MICROSOFT, "Technology", "vendor-gics-2026"),
        (EXXON, "Energy", "vendor-gics-2026"),
        (SAP, "Technology", "analyst"),
    ):
        registry = classify_instrument(registry, record.asset_id, sector, source=source, as_of=0.0)
    return registry


REGISTRY = _registry()
COUNTRIES = Classification(
    "country",
    {APPLE.asset_id: "US", MICROSOFT.asset_id: "US", EXXON.asset_id: "US", SAP.asset_id: "DE"},
    "caller-declared country of risk",
    None,
)


def _path(start: str, a: int, b: int) -> list[Decimal]:
    """A deterministic price path: no random source, no float."""

    prices = []
    price = Decimal(start)
    for step in range(STEPS + 1):
        drift = Decimal(((a * step + b) % 11) - 5) / Decimal(400)
        price = (price * (Decimal(1) + drift)).quantize(Decimal("0.01"))
        prices.append(price)
    return prices


PRICES = {
    "AAPL": _path("200", 7, 3),
    "MSFT": _path("400", 5, 1),
    "XOM": _path("100", 3, 8),
    "SAP": _path("120", 9, 4),
}
#: EUR/USD at each step, declared here: AlphaLab ships no rate.
EURUSD = [
    Decimal("1.08") + Decimal(((4 * step + 2) % 7) - 3) / Decimal(1000) for step in range(STEPS + 1)
]


def _timestamp(step: int, asset: int) -> float:
    return 10.0 + step + 0.1 * asset


def _quote(symbol: str, step: int, asset: int, currency: str) -> Quote:
    mid = PRICES[symbol][step]
    return Quote(
        asset_id=IDS[symbol],
        timestamp=_timestamp(step, asset),
        bid=mid,
        ask=mid,
        bid_size=Decimal("100000"),
        ask_size=Decimal("100000"),
        venue="SIM",
        currency=currency,
    )


class Basket(BaseStrategy):
    """Buys (or sells) a stated quantity of each of its instruments at the first step."""

    def __init__(self, strategy_id: str, targets: Mapping[str, Decimal]) -> None:
        self._strategy_id = strategy_id
        self._targets = dict(targets)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quote = event.quote
        symbol = next(name for name, asset in IDS.items() if asset == quote.asset_id)
        if quote.timestamp >= 11.0 or symbol not in self._targets:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=quote.asset_id,
                target=self._targets[symbol],
                timestamp=quote.timestamp,
            ),
        )


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    currency: str
    targets: Mapping[str, Decimal]


SPECS = (
    StrategySpec("MOM", "USD", {"AAPL": Decimal("1500"), "MSFT": Decimal("600")}),
    StrategySpec("MR", "USD", {"AAPL": Decimal("-800"), "XOM": Decimal("2500")}),
    StrategySpec("CARRY", "EUR", {"SAP": Decimal("3000")}),
)


def _run(spec: StrategySpec, cash: Decimal, budget: CapitalBudget) -> BacktestResult:
    symbols = sorted(spec.targets)
    records = [
        _quote(symbol, step, index, spec.currency)
        for step in range(STEPS + 1)
        for index, symbol in enumerate(symbols)
    ]
    config = RunConfig(
        pipeline=ExecutionPipelineConfig(
            account=Account(f"acct-{spec.strategy_id}", spec.currency, spec.strategy_id, 1.0),
            starting_cash=cash,
            budget=budget,
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=False
            ),
            risk_limits=permissive_risk_limits(),
            simulator=ExecutionSimulator(),
            currency=spec.currency,
        ),
        mode=ExecutionMode.BACKTEST,
        fill_policy=ImmediateFill(),
        seed=380_000,
        start_timestamp=1.0,
        compile_analytics=False,
    )
    state = running_strategy_state(spec.strategy_id, Basket(spec.strategy_id, spec.targets))
    return BacktestEngine.run(
        config, MarketDataset.of(f"V38-{spec.strategy_id}", records), state, context_factory
    )


def _step_returns(result: BacktestResult, per_step: int) -> tuple[float, ...]:
    curve = result.equity_curve
    equities = [curve[per_step * step].total_equity for step in range(1, STEPS + 2)]
    return tuple(float(later / earlier - 1) for earlier, later in pairwise(equities))


def _in_dollars(returns: tuple[float, ...]) -> tuple[float, ...]:
    """Euro returns restated in dollars: ``(1 + r_eur)(1 + r_fx) - 1``, the caller's step."""

    fx = [float(EURUSD[step + 1] / EURUSD[step] - 1) for step in range(len(returns))]
    return tuple((1 + r) * (1 + f) - 1 for r, f in zip(returns, fx, strict=True))


# --------------------------------------------------------------------------- #
# The whole workflow, built from scratch -- so it can be built twice
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Workflow:
    research_returns: dict[str, tuple[float, ...]]
    parity: ConstructionResult
    allocation: CapitalAllocationResult
    runs: dict[str, BacktestResult]
    book: MultiStrategyBook
    valuation: Any
    covariance: CovarianceMatrix
    budget_report: Any
    rates: FxRates


def _build() -> Workflow:
    # 1. Research runs on a notional million each, in each strategy's own currency.
    research = {
        spec.strategy_id: _run(
            spec,
            Decimal("1000000"),
            CapitalBudget(Decimal("1000000"), Decimal("10000000"), currency=spec.currency),
        )
        for spec in SPECS
    }
    research_returns = {
        spec.strategy_id: _step_returns(research[spec.strategy_id], len(spec.targets))
        for spec in SPECS
    }

    # 2. Risk parity across strategies, on dollar returns.
    dollar_returns = [
        StrategyReturns(
            spec.strategy_id,
            research_returns[spec.strategy_id]
            if spec.currency == "USD"
            else _in_dollars(research_returns[spec.strategy_id]),
            "USD",
            "1step",
        )
        for spec in SPECS
    ]
    strategies = strategy_return_correlation(dollar_returns, source="v38 research runs")
    parity = construct(
        ConstructionProblem(
            strategies.covariance,
            RiskParity.equal(),
            ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(None)),
            SETTINGS,
        )
    )

    # 3. A capital plan funded from two accounts, one already partly committed.
    ledger = AllocationState(
        budget=CapitalBudget(Decimal("3500000"), Decimal("7000000"), currency="USD"),
        notional_allocated=Decimal("50000"),
        reservations=PersistentMap({"working-order": Decimal("50000")}),
    )
    accounts = (
        CapitalAccount(
            "ACC-US",
            "BROKER-A",
            "USD",
            Decimal("3500000"),
            reserved_capital(ledger, currency="USD"),
        ),
        CapitalAccount("ACC-EU", "BROKER-B", "EUR", Decimal("1000000"), Decimal("0")),
    )
    placements = {
        "MOM": CapitalPlacement("MOM", "US_EQUITIES", "ACC-US"),
        "MR": CapitalPlacement("MR", "US_EQUITIES", "ACC-US"),
        "CARRY": CapitalPlacement("CARRY", "EU_EQUITIES", "ACC-EU"),
    }
    weights = parity.require_weights()
    rates = FxRates.of(
        [
            FxRate("EUR", "USD", EURUSD[0], 0.0, "declared EUR/USD"),
            FxRate(
                "USD",
                "EUR",
                (Decimal(1) / EURUSD[0]).quantize(Decimal("0.000001")),
                0.0,
                "declared USD/EUR",
            ),
        ]
    )
    plan = CapitalAllocationPlan(
        "v38-integration",
        "USD",
        5.0,
        accounts,
        tuple(placements.values()),
        PlacementWeights(
            {
                placements[name]: Decimal(repr(0.9 * weight)).quantize(
                    Decimal("0.000001"), rounding=ROUND_DOWN
                )
                for name, weight in weights.items()
            },
            f"risk parity {parity.result_id}",
        ),
        (CapitalLimit(CapitalDimension.BROKER, "BROKER-A", Decimal("0.8"), None),),
        OversubscriptionRule.REFUSE,
        Decimal("1000"),
    )
    allocation = allocate_capital(plan, rates)
    assert allocation.succeeded, allocation.reasons

    # 4. Production runs on the allocated capital, through the execution budget it becomes.
    runs: dict[str, BacktestResult] = {}
    for spec in SPECS:
        account = "ACC-US" if spec.currency == "USD" else "ACC-EU"
        budget = capital_budget(
            allocation,
            account_id=account,
            maximum_exposure=Decimal("100000000"),
            cash_buffer=Decimal("0"),
        )
        cash = budget.available_strategy_capital(spec.strategy_id)
        runs[spec.strategy_id] = _run(spec, cash, replace(budget, global_capital=cash))

    # 5. One book of three sleeves; the unallocated capital is the portfolio's own.
    unassigned = CurrencyAmounts()
    for entry in allocation.accounts:
        unassigned = unassigned.add(entry.unallocated, entry.account.currency)
    book = MultiStrategyBook(
        "V38-PORTFOLIO",
        tuple(
            StrategySleeve.from_portfolio_state(name, run.state.portfolio)
            for name, run in runs.items()
        ),
        unassigned,
    )
    final = FxRates.of([FxRate("EUR", "USD", EURUSD[-1], 40.0, "declared EUR/USD")])
    valuation = value_book(book, reporting_currency="USD", rates=final, as_of=41.0)

    # 6. Asset covariance of dollar returns: SAP's includes the euro's move.
    asset_returns: dict[str, list[float]] = {}
    for symbol, path in PRICES.items():
        local = [float(later / earlier - 1) for earlier, later in pairwise(path)]
        asset_returns[IDS[symbol]] = list(_in_dollars(tuple(local))) if symbol == "SAP" else local
    covariance = CovarianceMatrix.sample(
        asset_returns, currency="USD", period="1step", source="v38 quotes"
    )

    # 7. The risk budget, along all five dimensions.
    sectors = sector_classification(REGISTRY, tuple(IDS.values()), None)
    budget_report = evaluate_risk_budget(
        valuation.lines,
        capital=valuation.nav,
        reporting_currency="USD",
        covariance=covariance,
        budget=RiskBudget(
            "v38 limits",
            (
                BudgetLimit(RiskDimension.STRATEGY, "MR", BudgetBasis.RELATIVE, 0.6, None, None),
                BudgetLimit(RiskDimension.SECTOR, "Energy", BudgetBasis.RELATIVE, 0.05, None, None),
                BudgetLimit(RiskDimension.COUNTRY, "DE", BudgetBasis.RELATIVE, 0.9, 0.0, None),
                BudgetLimit(RiskDimension.CURRENCY, "EUR", BudgetBasis.ABSOLUTE, 1.0, None, None),
                BudgetLimit(
                    RiskDimension.ASSET, IDS["AAPL"], BudgetBasis.RELATIVE, 1.0, None, None
                ),
            ),
            1e-12,
        ),
        classifications=(sectors, COUNTRIES),
    )
    return Workflow(
        research_returns,
        parity,
        allocation,
        runs,
        book,
        valuation,
        covariance,
        budget_report,
        rates,
    )


@pytest.fixture(scope="module")
def workflow() -> Workflow:
    return _build()


# --------------------------------------------------------------------------- #
# Strategy allocation and capital
# --------------------------------------------------------------------------- #


def test_returns_in_two_currencies_are_refused_until_the_caller_restates_them(
    workflow: Workflow,
) -> None:
    with pytest.raises(AnalyticsValidationError, match="different quantities"):
        strategy_return_correlation(
            [
                StrategyReturns("MOM", workflow.research_returns["MOM"], "USD", "1step"),
                StrategyReturns("CARRY", workflow.research_returns["CARRY"], "EUR", "1step"),
            ],
            source="mixed",
        )


def test_risk_parity_across_strategies_equalizes_their_risk(workflow: Workflow) -> None:
    diagnostics = workflow.parity.diagnostics

    assert workflow.parity.status is ConstructionStatus.OPTIMAL
    assert diagnostics.risk is not None
    assert all(abs(share - 1 / 3) <= 1e-10 for share in diagnostics.risk.relative.values())


def test_every_account_reconciles_and_the_plan_names_its_provenance(workflow: Workflow) -> None:
    allocation = workflow.allocation

    for entry in allocation.accounts:
        assert (
            entry.account.available == entry.account.reserved + entry.allocated + entry.unallocated
        )
    us = next(entry for entry in allocation.accounts if entry.account.account_id == "ACC-US")
    assert us.account.reserved == Decimal("50000")
    assert all(check.satisfied for check in allocation.checks)
    assert any(c.source == "declared EUR/USD" for c in allocation.conversions)
    strategy = allocation.dimension(CapitalDimension.STRATEGY)
    assert set(strategy.amounts) == {"CARRY", "MOM", "MR"}


def test_each_run_was_funded_with_exactly_its_allocation(workflow: Workflow) -> None:
    assert workflow.allocation.placements is not None
    for entry in workflow.allocation.placements:
        run = workflow.runs[entry.placement.strategy_id]
        assert run.config.pipeline.starting_cash == entry.allocated
        assert run.config.pipeline.currency == entry.currency


# --------------------------------------------------------------------------- #
# Multi-strategy book and attribution
# --------------------------------------------------------------------------- #


def test_the_shared_instrument_keeps_both_owners_and_their_opposition(workflow: Workflow) -> None:
    holding = workflow.book.holding(IDS["AAPL"])

    assert [(c.strategy_id, c.quantity) for c in holding.contributions] == [
        ("MOM", Decimal("1500")),
        ("MR", Decimal("-800")),
    ]
    assert holding.net_quantity == Decimal("700")
    assert holding.crossed_quantity == Decimal("800")


def test_each_sleeve_is_its_run_and_the_book_adds_them_up_exactly(workflow: Workflow) -> None:
    valuation = workflow.valuation
    for name, run in workflow.runs.items():
        sleeve = workflow.book.sleeve(name)
        assert dict(sleeve.positions) == dict(run.state.portfolio.positions)
        assert sleeve.realized_pnl == run.state.portfolio.realized_pnl
    assert sum((s.net_exposure for s in valuation.strategies), Decimal(0)) == valuation.net_exposure
    assert (
        sum((i.reporting_value for i in valuation.instruments), Decimal(0))
        == valuation.net_exposure
    )
    assert (
        valuation.nav
        == sum((s.nav for s in valuation.strategies), Decimal(0)) + valuation.unassigned_cash
    )


def test_a_euro_sleeve_is_valued_through_a_recorded_conversion(workflow: Workflow) -> None:
    carry = workflow.valuation.strategy("CARRY")
    run = workflow.runs["CARRY"]
    euro_equity = PortfolioValuation.snapshot(run.state.portfolio, 41.0, "EUR").equity

    # The sleeve converts its cash and its position separately, each rounded to
    # the cent once (money rule 2), so it may differ from one conversion of the
    # euro equity by at most the two half-cents.
    assert abs(carry.nav - euro_equity * EURUSD[-1]) <= Decimal("0.01")
    assert any(conversion.rate.base == "EUR" for conversion in workflow.valuation.conversions)
    assert workflow.valuation.exposure_by_currency.of("EUR") == sum(
        (p.market_value for p in run.state.portfolio.positions.values()), Decimal(0)
    )


def test_strategy_pnl_attribution_is_each_run_s_own_and_sums_to_the_book(
    workflow: Workflow,
) -> None:
    for name, run in workflow.runs.items():
        own = PortfolioValuation.snapshot(run.state.portfolio, 41.0, run.config.pipeline.currency)
        entry = workflow.valuation.strategy(name)
        assert entry.realized_pnl.of(run.config.pipeline.currency) == own.realized_pnl
        if run.config.pipeline.currency == "USD":
            assert entry.unrealized_pnl == own.unrealized_pnl
    total_unrealized = sum(
        (line.reporting_unrealized_pnl for line in workflow.valuation.lines), Decimal(0)
    )
    assert total_unrealized == sum(
        (s.unrealized_pnl for s in workflow.valuation.strategies), Decimal(0)
    )


# --------------------------------------------------------------------------- #
# Risk budgeting
# --------------------------------------------------------------------------- #


def test_the_book_s_risk_reconciles_along_all_five_dimensions(workflow: Workflow) -> None:
    report = workflow.budget_report

    assert [d.dimension for d in report.dimensions] == list(RiskDimension)
    for dimension in report.dimensions:
        assert abs(dimension.residual) <= 1e-15
    assert report.dimension(RiskDimension.SECTOR).source.startswith("instrument registry")
    assert report.dimension(RiskDimension.COUNTRY).bucket("DE") is not None


def test_risk_is_attributed_to_the_strategies_that_hold_it(workflow: Workflow) -> None:
    report = workflow.budget_report
    strategies = report.dimension(RiskDimension.STRATEGY)
    line_totals: dict[str, float] = {}
    for line in report.lines:
        line_totals[line.strategy_id] = line_totals.get(line.strategy_id, 0.0) + line.contribution

    for bucket in strategies.buckets:
        assert bucket.contribution == pytest.approx(line_totals[bucket.bucket], rel=1e-12)
    assert strategies.bucket("MR") is not None


def test_the_budget_judges_every_limit_and_names_the_breach(workflow: Workflow) -> None:
    checks = {check.limit.key: check for check in workflow.budget_report.checks}

    assert checks[("SECTOR", "Energy", "RELATIVE")].status is BudgetStatus.BREACHED
    assert checks[("CURRENCY", "EUR", "ABSOLUTE")].status is BudgetStatus.WITHIN
    assert workflow.budget_report.breaches == (checks[("SECTOR", "Energy", "RELATIVE")],)


def test_the_currency_dimension_holds_euro_risk_apart_from_its_translation(
    workflow: Workflow,
) -> None:
    euro = workflow.budget_report.dimension(RiskDimension.CURRENCY).bucket("EUR")

    assert euro is not None
    assert euro.native_exposure == workflow.valuation.exposure_by_currency.of("EUR")
    assert euro.net_exposure == workflow.valuation.reporting_exposure_by_currency["EUR"]


# --------------------------------------------------------------------------- #
# Cross-strategy risk
# --------------------------------------------------------------------------- #


def _loadings() -> FactorLoadings:
    bars = [
        WireBar(
            symbol,
            100.0 + step * 86400.0,
            float(price),
            float(price),
            float(price),
            float(price),
            1000.0,
        )
        for symbol, path in PRICES.items()
        for step, price in enumerate(path)
    ]
    frame = observations_from_records(bars, FeatureField.CLOSE, "UTC", "v38-bars")
    instant = 100.0 + STEPS * 86400.0
    momentum = percentile_rank_panel(
        compute_panel(
            FeatureDefinition("momentum", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=10),
            frame,
        )
    ).panel
    volatility = percentile_rank_panel(
        compute_panel(
            FeatureDefinition(
                "volatility", FeatureKind.REALIZED_VOLATILITY, FeatureField.CLOSE, window=10
            ),
            frame,
        )
    ).panel
    return loadings_from_panels(
        {"momentum": momentum, "volatility": volatility},
        instant=instant,
        source="v38 characteristic model",
        asset_ids=IDS,
    )


def test_overlap_sees_the_shared_and_opposed_instrument(workflow: Workflow) -> None:
    pairs = {(pair.first, pair.second): pair for pair in strategy_overlap(workflow.valuation.lines)}

    assert pairs[("MOM", "MR")].shared_instruments == (IDS["AAPL"],)
    assert pairs[("MOM", "MR")].opposing_overlap > 0
    assert pairs[("CARRY", "MOM")].shared_instruments == ()


def test_factor_crowding_reads_the_factor_library_s_loadings(workflow: Workflow) -> None:
    loadings = _loadings()
    report = factor_crowding(workflow.valuation.lines, loadings, capital=workflow.valuation.nav)

    assert report.loadings_id == loadings.loadings_id
    assert {entry.factor for entry in report.factors} == {"momentum", "volatility"}
    assert "percentile" in loadings.lineage["momentum"]  # the transform chain is the lineage
    for entry in report.factors:
        assert entry.aggregate_exposure == pytest.approx(
            math.fsum(entry.by_strategy.values()), abs=1e-15
        )


def test_common_exposure_by_sector_comes_from_the_registry(workflow: Workflow) -> None:
    sectors = sector_classification(REGISTRY, tuple(IDS.values()), None)
    report = common_exposures(
        workflow.valuation.lines, by=CommonDimension.CLASSIFICATION, classification=sectors
    )
    technology = next(bucket for bucket in report.buckets if bucket.bucket == "Technology")

    assert set(technology.by_strategy) == {"CARRY", "MOM", "MR"}
    assert technology.shared


def test_capital_concentration_and_overlap_read_the_allocation(workflow: Workflow) -> None:
    strategy = workflow.allocation.dimension(CapitalDimension.STRATEGY)
    concentration = capital_concentration(
        dict(strategy.amounts), currency="USD", dimension="strategy"
    )
    assert workflow.allocation.placements is not None
    pools = capital_overlap(
        {
            entry.placement.strategy_id: {entry.placement.account_id: entry.base_amount}
            for entry in workflow.allocation.placements
        },
        currency="USD",
    )

    assert 1.0 < concentration.effective_count <= 3.0
    assert [pool.pool for pool in pools if pool.shared] == ["ACC-US"]


# --------------------------------------------------------------------------- #
# Asset-level construction on the book
# --------------------------------------------------------------------------- #


def test_a_factor_neutral_sector_capped_rebalance_of_the_book(workflow: Workflow) -> None:
    loadings = _loadings()
    sectors = sector_classification(REGISTRY, tuple(IDS.values()), None)
    currencies = currency_classification(REGISTRY, tuple(IDS.values()))
    current = dict.fromkeys(IDS.values(), 0.0)
    for instrument in workflow.valuation.instruments:
        current[instrument.holding.asset_id] = float(
            instrument.reporting_value / workflow.valuation.nav
        )
    problem = ConstructionProblem(
        workflow.covariance,
        MinimumVariance(),
        ConstraintSet(
            ExposureRange.exactly(math.fsum(current.values())),
            WeightBounds.unbounded(),
            groups=(
                GroupBound(sectors, "Technology", None, 0.5),
                GroupBound(currencies, "EUR", None, 0.2),
            ),
            factors=(FactorBound.target(loadings, "momentum", 0.3, 0.05),),
            turnover=TurnoverLimit(current, 1.5),
        ),
        SETTINGS,
    )
    result = construct(problem)

    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    assert result.diagnostics.group_exposures["sector[Technology]"] <= 0.5 + 1e-10
    assert result.diagnostics.group_exposures["currency[EUR]"] <= 0.2 + 1e-10
    assert 0.25 - 1e-10 <= result.diagnostics.factor_exposures["momentum"] <= 0.35 + 1e-10
    assert result.diagnostics.turnover is not None and result.diagnostics.turnover <= 1.5 + 1e-10


def test_black_litterman_on_the_universe_with_a_view(workflow: Workflow) -> None:
    posterior = black_litterman(
        BlackLittermanModel(
            workflow.covariance,
            EquilibriumPrior(
                {
                    IDS[s]: Decimal(v)
                    for s, v in (("AAPL", "3000"), ("MSFT", "3100"), ("XOM", "450"), ("SAP", "250"))
                },
                "USD",
                2.5,
            ),
            0.05,
            (
                InvestorView(
                    "energy outperforms", {IDS["XOM"]: 1.0, IDS["AAPL"]: -1.0}, 0.002, 1e-6
                ),
            ),
        )
    )
    result = construct(
        ConstructionProblem(
            workflow.covariance,
            MeanVariance(posterior.posterior_returns, 2.5),
            ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(0.6)),
            SETTINGS,
        )
    )

    assert result.succeeded
    view = posterior.views[0]
    assert (
        min(view.prior_implied, view.stated)
        < view.posterior_implied
        < max(view.prior_implied, view.stated)
    )


# --------------------------------------------------------------------------- #
# Reproducibility
# --------------------------------------------------------------------------- #


def test_every_identity_is_rebuilt_identically_from_scratch(workflow: Workflow) -> None:
    again = _build()

    assert again.parity.result_id == workflow.parity.result_id
    assert again.allocation.result_id == workflow.allocation.result_id
    assert again.book.book_id == workflow.book.book_id
    assert again.valuation.valuation_id == workflow.valuation.valuation_id
    assert again.covariance.covariance_id == workflow.covariance.covariance_id
    assert again.budget_report.report_id == workflow.budget_report.report_id


def test_the_fingerprint_names_the_construction_and_the_capital_behind_it(
    workflow: Workflow,
) -> None:
    research = research_configuration_with_portfolio(
        {"validation": "research runs, 30 steps"},
        constructions={"strategies": workflow.parity},
        capital={"production": workflow.allocation},
        study=None,
    )
    stamped = fingerprint(research=research)

    assert verify_fingerprint(stamped)
    assert stamped.research.settings["portfolio.strategies.result"] == workflow.parity.result_id
    assert stamped.research.settings["capital.production.plan"] == workflow.allocation.plan_id
