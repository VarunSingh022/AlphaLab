"""Example 85's experiment, defined where its identity is stable (ADR-0065, ADR-0072).

Plan scenario S-06, rates relative value: a curve book that holds a two-year
Treasury note and pays fixed on a ten-year swap through 2025. Everything it
turns on is stated: the markets, the curves and their interpolation, the two
instruments' terms, the fixings as they were published, the strategy, capital,
limits, costs and fills, the capture, and the seed.

The markets are **synthetic**. Every weekday a level, a slope and a swap spread
move by uniform steps drawn with ``random()`` and arithmetic alone, so every
host draws the same bits:

* a swap rate at ``T`` years is ``level + slope * (T - 2) / (T + 2)``, the
  three-month deposit read at ``T = 0.25``;
* a Treasury par yield is the swap rate less the spread.

Each Wednesday, two curves are bootstrapped under a named interpolation,
log-linear on discount factors:

* the swap curve, from a three-month deposit and par swaps of 1, 2, 3, 5, 7
  and 10 years, semi-annual 30/360 against quarterly ACT/360;
* the Treasury curve, from par notes of the same maturities, semi-annual
  ACT/ACT (ICMA).

The note is marked at its flows on the Treasury curve and the swap at its
value on the swap curve, each rounded to a published price's precision. The
swap's fixings are the three-month rate on each fixing date, published at
11:00 New York.

The experiment's trials differ only in how the swap is sized against the note:

* ``duration``: the notional that makes the book's value unmoved by a
  parallel basis point on both curves at inception;
* ``notional``: the note's face.

Nothing here says whether the curve steepens: the markets are random walks.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
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
from alphalab.backtesting.rates_payments import PaymentTime, PublishedFixing, rates_payments
from alphalab.backtesting.schedule import RunSchedule, ScheduledDistribution
from alphalab.common.order_terms import MARKET
from alphalab.common.registry import RunnerRegistry
from alphalab.conventions import (
    BusinessDayConvention,
    DayCount,
    Tenor,
    TenorUnit,
    add_business_days,
    add_tenor,
)
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
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
from alphalab.portfolio.corporate_actions import CashFlowKind
from alphalab.rates import (
    BondQuote,
    CurveBootstrap,
    DepositQuote,
    DiscountCurve,
    FixedLeg,
    FloatingLeg,
    Interpolation,
    LegSchedule,
    RateQuote,
    SwapQuote,
    SwapTerms,
    bootstrap_curve,
    fixed_bond_flows,
    leg_periods,
    value_flows,
    value_swap,
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
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy

STRATEGY_ID = "EX85-S06-CURVE-BOOK"
START_CASH = Decimal("2000000.00")
CENT = Decimal("0.01")
ZONE = "America/New_York"
NY = ZoneInfo(ZONE)
INTERPOLATION = Interpolation.LOG_LINEAR_DISCOUNT
MODIFIED = BusinessDayConvention.MODIFIED_FOLLOWING
FIRST, LAST = date(2025, 1, 15), date(2025, 12, 31)  # both Wednesdays
FACE = Decimal("1000000")


@dataclass(frozen=True)
class Weekdays:
    """New York's business days, as this example states them: Monday to Friday."""

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5


WEEKDAYS = Weekdays()
CALENDARS = {"USNY": WEEKDAYS}


def at(day: date, hour: int) -> float:
    """``day`` at ``hour`` on New York's clock."""

    return datetime.combine(day, time(hour), tzinfo=NY).timestamp()


# --------------------------------------------------------------------------- #
# The markets: a level, a slope and a spread, every weekday
# --------------------------------------------------------------------------- #


def _market() -> dict[date, tuple[float, float, float]]:
    draws = random.Random(85)
    level, slope, spread = 0.0400, 0.0080, 0.0030
    out: dict[date, tuple[float, float, float]] = {}
    day = FIRST
    while day <= LAST:
        if day.weekday() < 5:
            out[day] = (level, slope, spread)
            level += 0.00003 + (draws.random() - 0.5) * 0.0010
            slope += (draws.random() - 0.5) * 0.0005
            spread += (draws.random() - 0.5) * 0.0002
        day += timedelta(days=1)
    return out


STATES = _market()
OBSERVED = tuple(day for day in STATES if day.weekday() == 2)
TENORS = (1, 2, 3, 5, 7, 10)


def swap_rate(day: date, years: float) -> float:
    level, slope, _ = STATES[day]
    return level + slope * (years - 2.0) / (years + 2.0)


def treasury_yield(day: date, years: float) -> float:
    return swap_rate(day, years) - STATES[day][2]


def _years_on(day: date, years: int) -> date:
    return day.replace(year=day.year + years)


def swap_curve(day: date) -> CurveBootstrap:
    """The swap curve on ``day``: a deposit and six par swaps from spot."""

    spot = add_business_days(day, 2, (WEEKDAYS,))
    three_months = add_tenor(
        spot, Tenor(3, TenorUnit.MONTHS), MODIFIED, (WEEKDAYS,), end_of_month=False
    )
    quotes: list[RateQuote] = [
        DepositQuote(spot, three_months, swap_rate(day, 0.25), DayCount.ACT_360)
    ]
    quotes += [
        SwapQuote(
            spot,
            Tenor(years, TenorUnit.YEARS),
            swap_rate(day, years),
            Tenor(6, TenorUnit.MONTHS),
            DayCount.THIRTY_360_US,
            Tenor(3, TenorUnit.MONTHS),
            MODIFIED,
            (WEEKDAYS,),
            end_of_month=False,
        )
        for years in TENORS
    ]
    return bootstrap_curve(
        "USD-SWAP",
        "USD",
        day,
        DayCount.ACT_365_FIXED,
        INTERPOLATION,
        quotes,
        tolerance=1e-14,
        max_iterations=100,
    )


def treasury_curve(day: date) -> CurveBootstrap:
    """The Treasury curve on ``day``: six par notes, settled the next business day."""

    settles = add_business_days(day, 1, (WEEKDAYS,))
    quotes: list[RateQuote] = [
        BondQuote(
            settles,
            _years_on(day, years),
            treasury_yield(day, years),
            2,
            DayCount.ACT_ACT_ICMA,
            100.0,
        )
        for years in TENORS
    ]
    return bootstrap_curve(
        "USD-TREASURY",
        "USD",
        day,
        DayCount.ACT_365_FIXED,
        INTERPOLATION,
        quotes,
        tolerance=1e-14,
        max_iterations=100,
    )


# --------------------------------------------------------------------------- #
# The instruments: a two-year note and a ten-year swap, struck at inception
# --------------------------------------------------------------------------- #

#: The note's coupon: its inception par yield, to the nearest eighth of a percent.
COUPON = round(treasury_yield(FIRST, 2) * 800) / 800
NOTE_MATURITY = _years_on(FIRST, 2)
NOTE = InstrumentRecord(
    "UST-2Y-2027",
    AssetType.BOND,
    "XOTC",
    "USD",
    # Quoted per 100 of face; one unit is one dollar of face.
    economics=InstrumentEconomics(Decimal("0.01"), SettlementModel.CASH_EQUITY),
    last_trade_at=at(NOTE_MATURITY, 17),
)
#: The swap's fixed rate: its inception par rate, to a tenth of a basis point.
SWAP_RATE = Decimal(f"{swap_rate(FIRST, 10):.5f}")
SWAP_START = add_business_days(FIRST, 2, (WEEKDAYS,))
SEMI = LegSchedule(Tenor(6, TenorUnit.MONTHS), MODIFIED, ("USNY",), False)
QUARTERLY = LegSchedule(Tenor(3, TenorUnit.MONTHS), MODIFIED, ("USNY",), False)
INDEX = "USD-TERM-3M"
SWAP_TERMS = SwapTerms(
    "USD",
    SWAP_START,
    Tenor(10, TenorUnit.YEARS),
    FixedLeg(Decimal("1"), SWAP_RATE, DayCount.THIRTY_360_US, SEMI),
    FloatingLeg(Decimal("1"), INDEX, Decimal("0"), DayCount.ACT_360, QUARTERLY, 2, "SYNTHETIC"),
)
SWAP = InstrumentRecord(
    "USD-10Y-SWAP",
    AssetType.SWAP,
    "XOTC",
    "USD",
    economics=InstrumentEconomics(
        Decimal("1"), SettlementModel.CASH_EQUITY, allows_negative_prices=True
    ),
    last_trade_at=at(
        leg_periods(SWAP_TERMS.fixed, SWAP_START, SWAP_TERMS.length, CALENDARS)[-1].end, 17
    ),
    terms=SWAP_TERMS,
)
INSTRUMENTS: InstrumentRegistry = register_instruments(InstrumentRegistry(), (NOTE, SWAP))


def _published_fixings() -> tuple[PublishedFixing, ...]:
    """Each floating period's fixing within the year, as it was published."""

    out = []
    for period in leg_periods(SWAP_TERMS.floating, SWAP_START, SWAP_TERMS.length, CALENDARS):
        day = period.fixing
        if day is None or day > LAST:
            break
        out.append(PublishedFixing(INDEX, day, Decimal(f"{swap_rate(day, 0.25):.5f}"), at(day, 11)))
    return tuple(out)


FIXINGS = _published_fixings()
KNOWN = {(fixing.index, fixing.fixed_on): fixing.value for fixing in FIXINGS}


def note_price(day: date, curve: DiscountCurve) -> float:
    """The note's value per 100 of face on ``day``: its flows on the Treasury curve."""

    flows = fixed_bond_flows(day, NOTE_MATURITY, COUPON, 2, DayCount.ACT_ACT_ICMA)
    return value_flows(flows, discount=curve).value


def swap_value(day: date, curve: DiscountCurve) -> float:
    """The swap's value per unit of notional, to the receiver of fixed, on ``day``."""

    fixed = {key: value for key, value in KNOWN.items() if key[1] < day}
    return value_swap(
        SWAP_TERMS, discount=curve, projection=curve, calendars=CALENDARS, fixings=fixed
    ).value


def _mark(value: float, places: str) -> Decimal:
    return Decimal(repr(value)).quantize(Decimal(places), rounding=ROUND_HALF_EVEN)


def _quotes() -> list[Quote]:
    quotes = []
    for day in OBSERVED:
        treasury, swaps = treasury_curve(day).curve, swap_curve(day).curve
        for record, mark in (
            (NOTE, _mark(note_price(day, treasury), "0.000001")),
            (SWAP, _mark(swap_value(day, swaps), "0.00000001")),
        ):
            quotes.append(
                Quote(
                    record.asset_id,
                    at(day, 16),
                    mark,
                    mark,
                    Decimal("1E9"),
                    Decimal("1E9"),
                    "SIM",
                    "USD",
                )
            )
    return quotes


DATASET = MarketDataset.of("EX85-RATES", sorted(_quotes(), key=lambda q: (q.timestamp, q.asset_id)))


def dv01s() -> tuple[float, float]:
    """At inception, what a parallel basis point on both curves moves each unit by.

    The note's per dollar of face, the swap's per unit of notional to the
    receiver of fixed: both lose as rates rise.
    """

    treasury, swaps = treasury_curve(FIRST).curve, swap_curve(FIRST).curve
    note = (note_price(FIRST, treasury.shifted(0.0001)) - note_price(FIRST, treasury)) / 100.0
    swap = swap_value(FIRST, swaps.shifted(0.0001)) - swap_value(FIRST, swaps)
    return note, swap


NOTE_DV01, SWAP_DV01 = dv01s()


# --------------------------------------------------------------------------- #
# The payments, as the schedule takes them
# --------------------------------------------------------------------------- #

PAID = PaymentTime(ZONE, time(17))
_SWAP_PAYMENTS = rates_payments(
    SWAP, CALENDARS, PAID, start=at(FIRST, 0), end=at(LAST, 23), fixings=FIXINGS
)
#: The note's coupons in the year: each pays the coupon over two, a dollar of face.
NOTE_COUPONS = tuple(
    ScheduledDistribution(
        NOTE.asset_id,
        Decimal(repr(amount / 100.0)).quantize(Decimal("1E-12")),
        "USD",
        at(day, 17),
        CashFlowKind.INTEREST,
        f"{NOTE.symbol}:{day.isoformat()}:coupon",
    )
    for day, amount in fixed_bond_flows(FIRST, NOTE_MATURITY, COUPON, 2, DayCount.ACT_ACT_ICMA)
    if day <= LAST
)
SCHEDULE = RunSchedule.of(distributions=(*_SWAP_PAYMENTS.distributions, *NOTE_COUPONS))


# --------------------------------------------------------------------------- #
# The strategy, the run, and the experiment
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Hedge:
    """A candidate: how the swap is sized against the note."""

    rule: str


def swap_notional(rule: str) -> Decimal:
    """The swap notional the book pays fixed on, by the trial's rule."""

    if rule == "duration":
        return Decimal(round(float(FACE) * NOTE_DV01 / SWAP_DV01))
    if rule == "notional":
        return FACE
    raise ValueError(f"A hedge is 'duration' or 'notional', not {rule!r}.")


class CurveBook(BaseStrategy):
    """Buys the note's face and pays fixed on its swap at the first quotes, then holds."""

    def __init__(self, notional: Decimal) -> None:
        self._plan = {NOTE.asset_id: FACE, SWAP.asset_id: -notional}

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quantity = self._plan.pop(event.quote.asset_id, None)
        if quantity is None:
            return ()
        return (
            Intent(
                STRATEGY_ID,
                event.quote.asset_id,
                quantity,
                terms=MARKET,
                timestamp=event.quote.timestamp,
            ),
        )


def run_config() -> RunConfig:
    """Capital, limits, costs and fills -- each stated, none assumed."""

    ample = Decimal("1000000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX85", "USD", "Example 85", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=ample,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            # Paying fixed on the swap is a negative position in its receiver.
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("10")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(ample, ZONE),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            # Dealers' spreads are in the marks: no commission is charged.
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0"))),
            instruments=INSTRUMENTS,
            fill_timing=FillTiming.SAME_EVENT,
        ),
        fill_policy=ImmediateFill(),
        seed=85,
        start_timestamp=at(FIRST, 0),
    )


#: Each trial's strategy, by trial id, kept in this process for the example and its test.
BUILT: dict[str, CurveBook] = {}


def build(trial: TrialSpec) -> TrialInputs:
    """The runner: one trial's inputs from its typed candidate."""

    strategy = CurveBook(swap_notional(trial.configuration.rule))
    BUILT[trial.trial_id] = strategy
    return TrialInputs(
        config=run_config(),
        dataset=DATASET,
        strategy_state=start_strategy(
            create_runtime(),
            STRATEGY_ID,
            strategy,
            config={},
            subscriptions={"quotes"},
            at=at(FIRST, 0),
        ),
        context_factory=context_factory(FixedClock(at(FIRST, 0)), DiscardingLogger()),
        schedule=SCHEDULE,
        observations=(),
    )


REGISTRY = RunnerRegistry().register("s06-curve-book", build)
_CONFIG = run_config()
SPEC = ExperimentSpec(
    name="s06-curve-book-synthetic",
    hypothesis=(
        "a two-year note against a ten-year payer swap, sized to a zero parallel DV01 at "
        "inception, moves with the curve's shape and the swap spread rather than its level"
    ),
    strategy="s06-curve-book@" + "85" * 32,
    configuration_type=Hedge,
    space=ParameterSpace.grid({"rule": ["duration", "notional"]}),
    dataset=dataset_identity(DATASET),
    schedule=SCHEDULE.binding,
    instruments=registry_digest(INSTRUMENTS),
    execution=execution_assumptions(_CONFIG.pipeline, _CONFIG.fill_policy),
    capture=CaptureSpec(
        ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.TRUNCATE),
        trace=True,
    ),
    retention=RetentionPolicy(market_history=64, steps=64, audit_events=256, results=4096),
    validation=FullSample(None),
    seed=85,
    parent_id=None,
    limits=TrialLimits(None),
)


def ledger() -> TrialLedger:
    """A trial ledger in memory."""

    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


def run(where: TrialLedger) -> ExperimentRecord:
    """Every trial of the experiment, in this process."""

    return run_experiment(SPEC, REGISTRY, "s06-curve-book", where, SequentialExecutor())


def growth(where: TrialLedger, trial: TrialSpec) -> float:
    """What a trial's captured returns compound to over the year."""

    record = where.find(trial.trial_id)
    assert record is not None and record.series_id is not None
    _, returns = where.series(record.series_id)
    return math.prod(1.0 + value for value in returns) - 1.0
