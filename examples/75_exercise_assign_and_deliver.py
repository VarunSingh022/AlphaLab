"""
AlphaLab Examples
=================

Example 75 : Exercise, Assign and Deliver -- an Option's Whole Life in a Backtest

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 70 (build a strategy)
✓ Example 35 (option pricing)
✓ Example 72 (a run's schedule)

Topics
------

• A market declared, not assumed: its time zone, sessions and holidays, and every
  instant of the contracts' lives -- expiry at its close, delivery T+1 by its
  calendar -- read from it
• An American, physically settled call: exercised early, before a dividend, by a
  policy the researcher states and that reads only prices already published
• A European, cash-settled put on an index: settled at expiry against the
  index's published value
• The deliverable moving against the strike through the portfolio's one fill
  authority; the dividend then paid on the delivered shares
• Contract terms AlphaLab cannot honour refused at declaration or at the event
• The same run replayed, paused before the exercise, written to JSON and resumed

What this shows
---------------

v4.1.1's evidence for ledger FUT-013: a derivative's lifecycle is part of the
run, not the caller's arithmetic. The contract declares what it does
(:class:`~alphalab.instrument.terms.OptionTerms`); the run's schedule carries the
exercise and the expiry; the engine moves the cash, the contracts and the shares,
and attributes them. The prices are **synthetic** and the market's calendar is a
stated one -- nothing here describes a real exchange.
``tests/integration/test_lifecycle_example.py`` holds it to its figures.

Run

    python examples/75_exercise_assign_and_deliver.py
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    ExecutionMode,
    MarketDataset,
    PausedReplay,
    ReplayBacktest,
    RunConfig,
    RunSchedule,
    ScheduledDistribution,
    ScheduledExercise,
    ScheduledExpiry,
)
from alphalab.backtesting.replay_snapshot import capture, from_primitives, restore
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.conventions.settlement import SettlementBasis, SettlementRule
from alphalab.core.enums import AssetType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.execution.policy import FillTiming
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.instrument.terms import AutomaticExercise, Deliverable, OptionTerms
from alphalab.lifecycle import digest_run
from alphalab.market.bar import Bar, TimeFrame
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.expiration import SettlementStyle
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.account import Account
from alphalab.portfolio.corporate_actions import Exercise, OptionExpiry
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
from alphalab.runtime.calendars import session_close, settles_at
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import DiscardingLogger, FixedClock, context_factory
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

# --------------------------------------------------------------------------- #
# 1. A market, declared: its zone, sessions and a holiday
# --------------------------------------------------------------------------- #

MARKET = MarketCalendar(
    "EX75-MARKET",
    "Europe/London",
    {day: (SessionWindow(time(8, 0), time(16, 30)),) for day in range(5)},
    frozenset({date(2024, 5, 27)}),  # a Monday the market is shut
)
CURRENCY = "GBP"
T_PLUS_ONE = SettlementRule(SettlementBasis.TRADING_DAYS, 1)
SAME_DAY = SettlementRule(SettlementBasis.TRADE_DATE, 0)
SESSIONS = MARKET.sessions_between(date(2024, 5, 13), date(2024, 6, 14))
EXPIRY_DAY = date(2024, 6, 14)
EXPIRES = session_close(MARKET, EXPIRY_DAY)
EX_DIVIDEND = date(2024, 6, 4)
DIVIDEND = Decimal("3.00")

ONE = LotSpecification(Decimal("1"), Decimal("1"))
STOCK = InstrumentRecord("WIDGET", AssetType.EQUITY, "XEXM", CURRENCY)
INDEX = InstrumentRecord("WIDX", AssetType.INDEX, "XEXM", CURRENCY)
CALL = InstrumentRecord(
    "WIDGETC100",
    AssetType.OPTION,
    "XEXM",
    CURRENCY,
    economics=InstrumentEconomics(Decimal("100"), SettlementModel.OPTION_PREMIUM, lot=ONE),
    last_trade_at=EXPIRES,
    terms=OptionTerms(
        option_type=OptionType.CALL,
        strike=Decimal("100"),
        exercise_style=ExerciseStyle.AMERICAN,
        settlement=SettlementStyle.PHYSICAL,
        deliverable=Deliverable(STOCK.asset_id, Decimal("100")),
        expires_at=EXPIRES,
        automatic_exercise=AutomaticExercise(Decimal("0.01")),
    ),
)
PUT = InstrumentRecord(
    "WIDXP2000",
    AssetType.OPTION,
    "XEXM",
    CURRENCY,
    economics=InstrumentEconomics(Decimal("10"), SettlementModel.OPTION_PREMIUM, lot=ONE),
    last_trade_at=EXPIRES,
    terms=OptionTerms(
        option_type=OptionType.PUT,
        strike=Decimal("2000"),
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement=SettlementStyle.CASH,
        deliverable=Deliverable(INDEX.asset_id, Decimal("10")),
        expires_at=EXPIRES,
        automatic_exercise=AutomaticExercise(Decimal("0")),
    ),
)
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (STOCK, INDEX, CALL, PUT))


# --------------------------------------------------------------------------- #
# 2. Synthetic prices, one bar per session at its close
# --------------------------------------------------------------------------- #


def stock_close(n: int) -> Decimal:
    return Decimal("98") + Decimal(n) * Decimal("0.75")


def index_close(n: int) -> Decimal:
    return Decimal("2050") - Decimal(n) * Decimal("4")


def call_close(n: int) -> Decimal:
    return max(stock_close(n) - Decimal("100"), Decimal("0")) + Decimal("2")


def put_close(n: int) -> Decimal:
    return max(Decimal("2000") - index_close(n), Decimal("0")) + Decimal("15")


def _bar(record: InstrumentRecord, n: int, price: Decimal) -> Bar:
    at = session_close(MARKET, SESSIONS[n])
    return Bar(
        record.asset_id, at, price, price, price, price, Decimal("10000"), None, None, TimeFrame.D1
    )


def dataset() -> MarketDataset:
    bars: list[Bar] = []
    for n, day in enumerate(SESSIONS):
        bars.append(_bar(STOCK, n, stock_close(n)))
        bars.append(_bar(INDEX, n, index_close(n)))
        if day < EXPIRY_DAY:
            bars.append(_bar(CALL, n, call_close(n)))
            bars.append(_bar(PUT, n, put_close(n)))
    # One stock bar after expiry, so the expiry falls inside the data.
    after = session_close(MARKET, MARKET.add_trading_days(EXPIRY_DAY, 1))
    bars.append(
        Bar(
            STOCK.asset_id,
            after,
            stock_close(len(SESSIONS)),
            stock_close(len(SESSIONS)),
            stock_close(len(SESSIONS)),
            stock_close(len(SESSIONS)),
            Decimal("10000"),
            None,
            None,
            TimeFrame.D1,
        )
    )
    return MarketDataset.of("EX75-BARS", sorted(bars, key=lambda b: (b.timestamp, b.asset_id)))


# --------------------------------------------------------------------------- #
# 3. A researcher's strategy, and a stated exercise policy
# --------------------------------------------------------------------------- #

STRATEGY_ID = "EX75-HOLDER"


class Holder(BaseStrategy):
    """Buys two calls when three-session momentum first turns up, and one index put.

    Decides from the history its context shows and nothing it remembers, so a
    rebuilt instance continues a stored run exactly.
    """

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        if bar.asset_id != STOCK.asset_id:
            return ()
        closes = [seen.close for seen in context.history.bars(STOCK.asset_id, 5)]
        if len(closes) != 4 or closes[-1] <= closes[0]:
            return ()
        return (
            Intent(
                STRATEGY_ID,
                CALL.asset_id,
                Decimal("2"),
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
            Intent(
                STRATEGY_ID,
                PUT.asset_id,
                Decimal("1"),
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


def early_exercise() -> ScheduledExercise:
    """Exercise one call at the close before the ex-dividend date, when the dividend is worth more.

    The rule of thumb, stated: a call deep enough in the money that its holder
    would rather own the shares on the ex-date. It reads the stock's close on
    the decision day only -- published at the instant the exercise takes effect --
    and the dividend, declared in advance. Whether it is wise is the
    researcher's question; the engine checks only that the contract allows it.
    """

    decision_day = SESSIONS[SESSIONS.index(EX_DIVIDEND) - 1]
    close = stock_close(SESSIONS.index(decision_day))
    assert close - Decimal("100") > DIVIDEND, "the stated policy would not exercise"
    return ScheduledExercise(
        Exercise(CALL.asset_id, Decimal("1"), reference="pre-dividend"),
        session_close(MARKET, decision_day),
    )


def schedule() -> RunSchedule:
    return RunSchedule.of(
        exercises=[early_exercise()],
        # Paid to whoever holds the shares at the ex-date's open.
        distributions=[
            ScheduledDistribution(
                STOCK.asset_id, DIVIDEND, CURRENCY, settles_at(MARKET, SAME_DAY, EX_DIVIDEND)
            )
        ],
        expiries=[
            ScheduledExpiry(OptionExpiry(CALL.asset_id, stock_close(len(SESSIONS) - 1)), EXPIRES),
            ScheduledExpiry(OptionExpiry(PUT.asset_id, index_close(len(SESSIONS) - 1)), EXPIRES),
        ],
    )


def running() -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, Holder())
    entry = state.strategies[STRATEGY_ID]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"bars"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(state, strategies={STRATEGY_ID: entry})


START_CASH = Decimal("100000")
CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())


def run_config() -> RunConfig:
    huge = Decimal("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX75", CURRENCY, "Example 75", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(START_CASH, START_CASH, Decimal("0"), {STRATEGY_ID: START_CASH}),
            allocation_constraints=AllocationConstraints(
                allow_shorting=False, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(huge, huge),
                position=PositionLimit(huge, huge),
                exposure=ExposureLimit(huge, huge),
                leverage=LeverageLimit(Decimal("10")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(huge, "Europe/London"),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            instruments=REGISTRY,
            fill_timing=FillTiming.NEXT_EVENT,
        ),
        seed=75,
        start_timestamp=1.0,
    )


def run() -> BacktestResult:
    return BacktestEngine.run(run_config(), dataset(), running(), CONTEXTS, schedule=schedule())


def replayed(pause: int | None = None) -> BacktestResult:
    if pause is None:
        return ReplayBacktest.run(
            run_config(), dataset(), running(), CONTEXTS, schedule=schedule()
        ).backtest
    paused = ReplayBacktest.run(
        run_config(),
        dataset(),
        running(),
        CONTEXTS,
        schedule=schedule(),
        pause_when=lambda progress: progress.records_replayed == pause,
    )
    assert isinstance(paused, PausedReplay)
    config = run_config()
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY_ID: Holder()},
            instruments=REGISTRY,
        ),
        fill_policy=config.fill_policy,
    )
    restored = restore(
        from_primitives(deserialize(serialize(capture(paused)))), objects, dataset(), schedule()
    )
    return ReplayBacktest.resume(restored, CONTEXTS).backtest


# --------------------------------------------------------------------------- #
# 4. What is refused
# --------------------------------------------------------------------------- #


def refusals() -> list[str]:
    found = []
    try:
        terms = CALL.terms
        assert isinstance(terms, OptionTerms)
        replace(terms, exercise_style="BERMUDAN")  # type: ignore[arg-type]
    except InstrumentInputError as exc:
        found.append(f"a Bermudan call: {exc}")
    try:
        replace(CALL, last_trade_at=EXPIRES + 3600)
    except InstrumentInputError as exc:
        found.append(f"exercise ending before trading: {exc}")
    early_put = RunSchedule.of(
        exercises=[
            ScheduledExercise(
                Exercise(PUT.asset_id, Decimal("1"), Decimal("1990")),
                session_close(MARKET, date(2024, 6, 3)),
            )
        ],
    )
    try:
        BacktestEngine.run(run_config(), dataset(), running(), CONTEXTS, schedule=early_put)
    except RuntimeValidationError as exc:
        found.append(f"a European put exercised early: {exc}")
    return found


def money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,}"


def main() -> None:
    print("Example 75: an option's whole life in a backtest")
    print(
        f"  market {MARKET.calendar_id} ({MARKET.timezone_name}); expiry at its close on "
        f"{EXPIRY_DAY.isoformat()}, a holiday on 2024-05-27"
    )
    result = run()
    portfolio = result.state.portfolio
    print()
    print("  lifecycle records:")
    for record in result.state.trade_records:
        if record.trade_id.startswith(("EXERCISE", "DELIVERY", "EXPIRY")):
            print(
                f"    {record.trade_id.split('-')[0]:<9} "
                f"{REGISTRY.instruments[record.asset_id].symbol:<11} "
                f"realized {money(record.realized_pnl):>10}"
            )
    stock = portfolio.positions[STOCK.asset_id]
    print()
    print(
        f"  WIDGET held: {stock.quantity} (exercised early and at expiry), "
        f"cost {money(stock.average_cost)}"
    )
    print(
        f"  cash: {money(portfolio.cash.balance(CURRENCY))} {CURRENCY}; "
        f"options held: {[a for a in (CALL.asset_id, PUT.asset_id) if a in portfolio.positions]}"
    )
    valuation = result.valuation
    print(f"  equity {money(valuation.equity)}; realized {money(valuation.realized_pnl)}")
    print()
    for line in refusals():
        print(f"  refused -- {line[:110]}")
    print()
    replay = replayed()
    assert replay.fills == result.fills and replay.valuation == result.valuation
    resumed = replayed(pause=40)
    assert digest_run(resumed).result_id == digest_run(replay).result_id
    assert digest_run(run()).result_id == digest_run(result).result_id
    print("  replay: the same fills and valuation; paused before the exercise and resumed")
    identity = digest_run(result).result_id
    print(f"  from JSON: the same replay; rerun: the same result {identity[:16]}...")


if __name__ == "__main__":
    main()
