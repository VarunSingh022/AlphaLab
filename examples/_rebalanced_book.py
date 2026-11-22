"""Example 83's experiment, defined where its identity is stable (ADR-0063, ADR-0072).

An experiment's identity names its configuration type by module, and a script's
module is ``__main__``: defined there, the experiment would be another
experiment when imported by its test, or loaded by a spawned worker. So the
experiment that ``83_rebalance_a_factor_neutral_book.py`` runs lives here.
Everything it turns on is stated: the universe and its sectors, the value
scores, the prices, the model and each of its choices, the strategy, capital,
limits, costs and fills, the capture, what each run keeps, and the seed.

The prices are **synthetic**: ten years of weekday quotes for eight equities,
random walks drawn with ``random()`` and arithmetic alone, so every host draws
the same bits. Nothing here says whether momentum pays.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from itertools import pairwise
from types import MappingProxyType

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.analytics.risk_model import Classification, CovarianceMatrix, FactorLoadings
from alphalab.backtesting import ExecutionMode, RunConfig
from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.schedule import EMPTY_SCHEDULE
from alphalab.common.order_terms import MARKET
from alphalab.common.registry import RunnerRegistry
from alphalab.core.enums import AssetType
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.policy import FillTiming, ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.instrument.snapshot import registry_digest
from alphalab.lifecycle.experiments import (
    ExperimentRecord,
    ExperimentSpec,
    FullSample,
    SequentialExecutor,
    TrialInputs,
    TrialLedger,
    TrialLimits,
    TrialSpec,
    dataset_identity,
    run_experiment,
)
from alphalab.market.quote import Quote
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.portfolio.account import Account
from alphalab.portfolio_optimizer import (
    ConstraintSet,
    ConstructionProblem,
    CVaRLimit,
    ExpectedReturns,
    ExposureRange,
    FactorBound,
    GroupBound,
    LinearCosts,
    MaximumReturn,
    ScenarioSet,
    SolverSettings,
    WeightBounds,
)
from alphalab.rebalancing import (
    AccountEquity,
    CalendarTrigger,
    Period,
    RebalanceExecution,
    RebalanceView,
    RebalancingStrategy,
)
from alphalab.research import ParameterSpace
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.runtime.retention import RetentionPolicy
from alphalab.strategy import (
    DiscardingLogger,
    FixedClock,
    context_factory,
    create_runtime,
    start_strategy,
)

STRATEGY_ID = "EX83-S01-CONSTRUCTION"
START_CASH = Decimal("1000000.00")
CENT = Decimal("0.01")
ZONE = "America/New_York"

# --------------------------------------------------------------------------- #
# The universe: eight equities, their sectors declared, and a value score each
# --------------------------------------------------------------------------- #

#: Symbol -> (sector, the value score a factor model states for it, its beta).
DECLARED: dict[str, tuple[str, float, float]] = {
    "OILX": ("ENERGY", 1.2, 1.1),
    "GASX": ("ENERGY", -0.4, 0.9),
    "BNKX": ("FINANCIALS", 0.8, 1.2),
    "INSX": ("FINANCIALS", -1.0, 0.8),
    "CHPX": ("TECHNOLOGY", -1.3, 1.3),
    "SFTX": ("TECHNOLOGY", 0.3, 1.0),
    "PWRX": ("UTILITIES", 0.9, 0.6),
    "WTRX": ("UTILITIES", -0.5, 0.7),
}
RECORDS = tuple(
    InstrumentRecord(symbol, AssetType.EQUITY, "XNYS", "USD", sector=sector)
    for symbol, (sector, _, _) in DECLARED.items()
)
INSTRUMENTS: InstrumentRegistry = register_instruments(InstrumentRegistry(), RECORDS)
SYMBOL = {record.asset_id: record.symbol for record in RECORDS}
ASSETS = tuple(sorted(SYMBOL))
VALUE = {asset: DECLARED[SYMBOL[asset]][1] for asset in ASSETS}
BETA = {asset: DECLARED[SYMBOL[asset]][2] for asset in ASSETS}
LOADINGS = FactorLoadings(
    ("value",),
    ASSETS,
    tuple((VALUE[asset],) for asset in ASSETS),
    source="example 83's declared value scores",
    lineage=MappingProxyType({"value": "book-to-price scores, standardized, as declared"}),
    as_of=None,
)

# --------------------------------------------------------------------------- #
# Ten years of weekday quotes, drawn with random() and arithmetic alone
# --------------------------------------------------------------------------- #


def _weekdays() -> tuple[float, ...]:
    """Each weekday from 2015-01-02 to 2024-12-31, at 21:00 UTC: after New York's close."""

    day, last = datetime(2015, 1, 2, 21, tzinfo=UTC), datetime(2024, 12, 31, 21, tzinfo=UTC)
    instants = []
    while day <= last:
        if day.weekday() < 5:
            instants.append(day.timestamp())
        day += timedelta(days=1)
    return tuple(instants)


INSTANTS = _weekdays()


def _mids() -> dict[str, list[Decimal]]:
    """A market move, a value move and each asset's own, each uniform, compounded to cents.

    Only ``random()`` and ``+ - x /`` are used, so every host draws the same
    prices to the bit.
    """

    rng = random.Random(83)
    level = {asset: 40.0 + 10.0 * index for index, asset in enumerate(ASSETS)}
    mids: dict[str, list[Decimal]] = {asset: [] for asset in ASSETS}
    for _ in INSTANTS:
        market = 0.024 * (rng.random() - 0.5)
        value = 0.012 * (rng.random() - 0.5)
        for asset in ASSETS:
            own = 0.03 * (rng.random() - 0.5)
            level[asset] *= 1.0 + 0.0003 + BETA[asset] * market + VALUE[asset] * value + own
            mids[asset].append(Decimal(repr(level[asset])).quantize(CENT, rounding=ROUND_HALF_EVEN))
    return mids


MIDS = _mids()


def _quote(asset: str, instant: float, mid: Decimal) -> Quote:
    """A quote a cent either side of its mid, a thousand shares deep."""

    return Quote(
        asset_id=asset,
        timestamp=instant,
        bid=mid - CENT,
        ask=mid + CENT,
        bid_size=Decimal("1000"),
        ask_size=Decimal("1000"),
        venue="XNYS",
        currency="USD",
    )


DATASET = MarketDataset.of(
    "EX83-SYNTHETIC",
    [
        _quote(asset, instant, MIDS[asset][day])
        for day, instant in enumerate(INSTANTS)
        for asset in ASSETS
    ],
)

# --------------------------------------------------------------------------- #
# The model: what the strategy has observed, as a construction problem
# --------------------------------------------------------------------------- #

#: The construction's tolerances and iteration budget, stated.
SETTINGS = SolverSettings(
    feasibility_tolerance=1e-9, convergence_tolerance=1e-9, max_iterations=500
)


@dataclass
class ValueNeutralMomentum:
    """The most expected return a value-neutral, sector-capped book holds within a CVaR limit.

    It reads only what the slice's context exposes -- the history, bounded at
    the instant, and the registry's sectors -- and states every choice. It
    declines, stating no problem, until it holds ``momentum`` returns.

    ``seen`` keeps, for each problem it stated, the strategy's book and the
    account's position at that slice, for the example and its test to
    reconcile.
    """

    cvar_limit: float
    momentum: int = 126
    scenarios: int = 63
    cvar_level: float = 0.9
    sector_cap: float = 0.35
    weight_cap: float = 0.25
    invested: float = 0.95
    cost_rate: float = 0.0005
    seen: list[tuple[RebalanceView, ConstructionProblem, dict[str, Decimal]]] = field(
        default_factory=list
    )

    def problem(self, view: RebalanceView) -> ConstructionProblem | None:
        history = view.context.history
        mids = {
            asset: [(q.bid + q.ask) / 2 for q in history.quotes(asset, limit=self.momentum + 1)]
            for asset in view.assets
        }
        if any(len(path) < self.momentum + 1 for path in mids.values()):
            return None
        returns = {
            asset: [float(b / a - 1) for a, b in pairwise(path)] for asset, path in mids.items()
        }
        assets = view.assets
        expected = ExpectedReturns(
            {asset: math.fsum(returns[asset]) / self.momentum for asset in assets},
            currency="USD",
            period="1D",
            source=f"mean daily return over the last {self.momentum} days",
        )
        recent = {asset: returns[asset][-self.scenarios :] for asset in assets}
        scenarios = ScenarioSet(
            assets,
            tuple(tuple(recent[asset][day] for asset in assets) for day in range(self.scenarios)),
            currency="USD",
            period="1D",
            source=f"the last {self.scenarios} daily returns, as they happened",
        )
        means = {asset: math.fsum(recent[asset]) / self.scenarios for asset in assets}
        covariance = CovarianceMatrix.from_rows(
            assets,
            [
                [
                    math.fsum(
                        (x - means[a]) * (y - means[b])
                        for x, y in zip(recent[a], recent[b], strict=True)
                    )
                    / (self.scenarios - 1)
                    for b in assets
                ]
                for a in assets
            ],
            currency="USD",
            period="1D",
            source="sample",
            observations=self.scenarios,
        )
        sectors = Classification(
            "sector",
            {asset: view.context.universe.sector(asset) or "UNCLASSIFIED" for asset in assets},
            source="the instrument registry",
            as_of=None,
        )
        constraints = ConstraintSet(
            # Five percent is kept in cash: targets sized at one close fill at
            # later quotes, and the account cannot spend what it does not hold.
            ExposureRange.exactly(self.invested),
            WeightBounds(0.0, self.weight_cap),
            groups=tuple(
                GroupBound(sectors, sector, None, self.sector_cap)
                for sector in sorted(set(sectors.labels.values()))
            ),
            factors=(FactorBound.neutral(LOADINGS, "value"),),
            limits=(CVaRLimit(scenarios, self.cvar_level, self.cvar_limit),),
        )
        objective = MaximumReturn(
            expected,
            # Costs start from the strategy's book, exactly.
            LinearCosts(dict(view.weights), dict.fromkeys(assets, self.cost_rate)),
        )
        problem = ConstructionProblem(covariance, objective, constraints, SETTINGS)
        account = {asset: view.context.portfolio.quantity(asset) for asset in assets}
        self.seen.append((view, problem, {a: q for a, q in account.items() if q}))
        return problem


# --------------------------------------------------------------------------- #
# The run, and the experiment: two CVaR limits, each a counted trial
# --------------------------------------------------------------------------- #


def run_config() -> RunConfig:
    """Capital, limits, costs and fills -- each stated, none assumed."""

    ample = Decimal("1000000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX83", "USD", "Example 83", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=False, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("1")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(ample, ZONE),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.005"))),
            instruments=INSTRUMENTS,
            # Decided at one close, an order fills at its asset's next quote.
            fill_timing=FillTiming.NEXT_EVENT,
        ),
        fill_policy=ImmediateFill(),
        seed=83,
        start_timestamp=INSTANTS[0] - 1.0,
    )


@dataclass(frozen=True)
class Limit:
    """A candidate: the CVaR limit, a fraction of the book's value lost in a day."""

    cvar_limit: float


#: Each trial's model and strategy, by trial id, kept in this process for the
#: example and its test to read: the ledger keeps a trial's digests and series.
BUILT: dict[str, tuple[ValueNeutralMomentum, RebalancingStrategy]] = {}


def build(trial: TrialSpec) -> TrialInputs:
    """The runner: one trial's inputs from its typed candidate."""

    model = ValueNeutralMomentum(cvar_limit=trial.configuration.cvar_limit)
    strategy = RebalancingStrategy(
        STRATEGY_ID,
        assets=ASSETS,
        model=model,
        triggers=(CalendarTrigger(Period.MONTH, zone=ZONE),),
        capital=AccountEquity("USD"),
        quantum=Decimal("1"),
        terms=MARKET,
        execution=RebalanceExecution.SELLS_FIRST,
    )
    BUILT[trial.trial_id] = (model, strategy)
    return TrialInputs(
        config=run_config(),
        dataset=DATASET,
        strategy_state=start_strategy(
            create_runtime(),
            STRATEGY_ID,
            strategy,
            config={},
            subscriptions={"slices", "fills"},
            at=INSTANTS[0] - 1.0,
        ),
        context_factory=context_factory(FixedClock(INSTANTS[0]), DiscardingLogger()),
        schedule=EMPTY_SCHEDULE,
        observations=(),
    )


REGISTRY = RunnerRegistry().register("s01-construction", build)
_CONFIG = run_config()
SPEC = ExperimentSpec(
    name="s01-construction-synthetic",
    hypothesis=(
        "a value-neutral, sector-capped momentum book rebalanced monthly keeps its daily CVaR "
        "within the stated limit at every rebalance"
    ),
    strategy="s01-construction@" + "83" * 32,
    configuration_type=Limit,
    space=ParameterSpace.grid({"cvar_limit": [0.012, 0.02]}),
    dataset=dataset_identity(DATASET),
    schedule=EMPTY_SCHEDULE.binding,
    instruments=registry_digest(INSTRUMENTS),
    execution=execution_assumptions(_CONFIG.pipeline, _CONFIG.fill_policy),
    capture=CaptureSpec(
        ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.TRUNCATE),
        trace=True,
    ),
    # The model reads 127 quotes of each of eight assets: its window must hold them.
    retention=RetentionPolicy(market_history=2048, steps=64, audit_events=256, results=4096),
    validation=FullSample(None),
    seed=83,
    parent_id=None,
    limits=TrialLimits(None),
)


def ledger() -> TrialLedger:
    """A trial ledger in memory."""

    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


def run(where: TrialLedger) -> ExperimentRecord:
    """Every trial of the experiment, in this process."""

    return run_experiment(SPEC, REGISTRY, "s01-construction", where, SequentialExecutor())


def growth(where: TrialLedger, trial: TrialSpec) -> float:
    """What a trial's captured daily returns compound to."""

    record = where.find(trial.trial_id)
    assert record is not None and record.series_id is not None
    _, returns = where.series(record.series_id)
    return math.prod(1.0 + value for value in returns) - 1.0
