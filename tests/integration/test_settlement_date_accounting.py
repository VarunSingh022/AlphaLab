"""Settlement-date accounting: trade-date books, settled cash, buying power (v4.2.0, FUT-015).

An instrument declaring a settlement rule -- T+n by its listing venue's
declared calendar -- has each of its fills booked with its settlement instant;
the proceeds of a sale are not cash a run may spend until then. The calendar
here is New York's with Good Friday 2024 a holiday, so T+2 from Thursday
28 March is Tuesday 2 April, and every instant is the venue's own local time,
in UTC.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from alphalab.backtesting import BacktestEngine, BacktestResult, PausedReplay, ReplayBacktest
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.conventions.settlement import SettlementBasis, SettlementRule
from alphalab.core.enums import AssetType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.instrument.record import InstrumentRecord
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.fx import FxRate
from alphalab.portfolio.settlement import SETTLES_AT, pending_from
from alphalab.runtime.calendars import VenueCalendars
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
)

D = Decimal
NY = ZoneInfo("America/New_York")
NEW_YORK = MarketCalendar(
    "SYN-NEWYORK",
    "America/New_York",
    dict.fromkeys((0, 1, 2, 3, 4), (SessionWindow(time(9, 30), time(16, 0)),)),
    frozenset({date(2024, 3, 29)}),  # Good Friday
)
T_PLUS_2 = SettlementRule(SettlementBasis.TRADING_DAYS, 2)
ACME = InstrumentRecord("ACME", AssetType.EQUITY, "XNYS", "USD", settlement=T_PLUS_2)
PLAIN = InstrumentRecord("PLAIN", AssetType.EQUITY, "XNYS", "USD")
CASH = D("10000")


def ny(day: int, month: int = 3, hour: int = 15, minute: int = 0) -> float:
    return datetime(2024, month, day, hour, minute, tzinfo=NY).timestamp()


WED, THU, MON, TUE = ny(27), ny(28), ny(1, 4), ny(2, 4)
TUE_OPEN = ny(2, 4, 9, 30)


class Trader(BaseStrategy):
    """Asks for a scripted delta at chosen quotes; stateless."""

    def __init__(self, plan: dict[tuple[float, str], Decimal]) -> None:
        self._plan = plan

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        wanted = self._plan.get((event.quote.timestamp, event.quote.asset_id))
        if wanted is None:
            return ()
        return (Intent("S", event.quote.asset_id, wanted, timestamp=event.quote.timestamp),)


#: Buy all the cash's worth on Wednesday, sell it on Thursday, then try to buy
#: half again on Monday -- before the sale has settled -- and on Tuesday, after.
PLAN = {
    (WED, ACME.asset_id): D("100"),
    (THU, ACME.asset_id): D("-100"),
    (MON, ACME.asset_id): D("50"),
    (TUE, ACME.asset_id): D("50"),
}


def config(*records: InstrumentRecord, calendars: VenueCalendars | None = None) -> Any:
    base = backtest_config("S", starting_cash=CASH)
    pipeline = replace(
        base.pipeline,
        instruments=registry_of(*records),
        calendars=VenueCalendars({"XNYS": NEW_YORK}) if calendars is None else calendars,
    )
    return replace(base, pipeline=pipeline)


def prices(asset_id: str, at: Iterable[float], mid: str = "100") -> MarketDataset:
    return MarketDataset.of("SETTLE", [quote(asset_id, t, D(mid)) for t in at])


def run(
    record: InstrumentRecord = ACME,
    plan: dict[tuple[float, str], Decimal] | None = None,
    **kw: Any,
) -> BacktestResult:
    chosen = PLAN if plan is None else plan
    keyed = {(t, record.asset_id): q for (t, _), q in chosen.items()}
    return BacktestEngine.run(
        config(record, **kw),
        prices(record.asset_id, sorted({t for t, _ in keyed})),
        running_strategy_state("S", Trader(keyed)),
        context_factory,
    )


def refusals(result: BacktestResult) -> list[Any]:
    return [d for d in result.state.risk.history if not d.approved]


# --------------------------------------------------------------------------- #
# Settlement instants, by the venue's calendar
# --------------------------------------------------------------------------- #


def test_each_fill_is_booked_with_its_settlement_instant_by_the_venues_calendar() -> None:
    result = run()
    fills = [t for t in result.state.portfolio.ledger.transactions if t.asset_id == ACME.asset_id]
    by_trade = {t.timestamp: t.metadata[SETTLES_AT] for t in fills}
    # Wednesday T+2 skips Good Friday: Monday's open. Thursday T+2: Tuesday's.
    assert by_trade[WED] == ny(1, 4, 9, 30)
    assert by_trade[THU] == TUE_OPEN
    assert by_trade[TUE] == ny(4, 4, 9, 30)


def test_a_sales_proceeds_are_not_spendable_until_they_settle() -> None:
    result = run()
    # Monday's purchase needed Thursday's unsettled proceeds: refused for
    # buying power; Tuesday's, after they settled at the open, filled.
    (refused,) = refusals(result)
    assert refused.timestamp == MON
    assert any(v.rule == "BuyingPowerLimit" for v in refused.violations)
    traded = [
        (t.timestamp, t.quantity)
        for t in result.state.portfolio.ledger.transactions
        if t.asset_id == ACME.asset_id
    ]
    assert traded == [(WED, D("100")), (THU, D("-100")), (TUE, D("50"))]


def test_cash_is_reported_trade_date_settled_and_available() -> None:
    result = run(plan={(WED, ACME.asset_id): D("100"), (THU, ACME.asset_id): D("-100")})
    portfolio = result.state.portfolio
    # At Thursday's close: bought 10,000 Wednesday (unpaid until Monday), sold
    # for 10,000 Thursday (unreceived until Tuesday).
    at_thursday = PortfolioEngine.cash_position(portfolio, "USD", THU)
    assert at_thursday.trade_date == CASH
    assert at_thursday.unsettled_credits == D("10000")
    assert at_thursday.unsettled_debits == D("10000")
    assert at_thursday.settled == CASH
    assert at_thursday.available == 0
    # Monday after the open: the purchase has settled, the sale has not.
    monday = PortfolioEngine.cash_position(portfolio, "USD", ny(1, 4, 10))
    assert (monday.settled, monday.available) == (D("0"), D("0"))
    # Tuesday after the open: everything has.
    tuesday = PortfolioEngine.cash_position(portfolio, "USD", ny(2, 4, 10))
    assert tuesday.settled == tuesday.available == tuesday.trade_date == CASH


def test_settlement_is_a_fact_of_the_clock_and_is_never_applied_twice() -> None:
    result = run()
    portfolio = result.state.portfolio
    first = PortfolioEngine.cash_position(portfolio, "USD", ny(5, 4))
    again = PortfolioEngine.cash_position(portfolio, "USD", ny(5, 4))
    assert first == again and first.unsettled_credits == first.unsettled_debits == 0
    assert first.trade_date == portfolio.cash.balance("USD")


# --------------------------------------------------------------------------- #
# What declares nothing, and what cannot settle
# --------------------------------------------------------------------------- #


def test_an_instrument_declaring_no_rule_settles_at_the_trade_and_records_nothing_new() -> None:
    result = run(PLAIN)
    fills = [t for t in result.state.portfolio.ledger.transactions if t.asset_id == PLAIN.asset_id]
    assert fills and all(t.metadata == {} for t in fills)
    assert result.state.portfolio.unsettled == ()
    assert not refusals(result), "Monday's purchase spends Thursday's proceeds at once"


class Listener(Trader):
    """Trades as scripted, and keeps every rejection's reason."""

    def __init__(self, plan: dict[tuple[float, str], Decimal]) -> None:
        super().__init__(plan)
        self.rejections: list[str] = []

    def on_order(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.reason:
            self.rejections.append(event.reason)
        return ()


def test_an_order_in_an_instrument_its_venue_has_no_calendar_for_is_refused() -> None:
    keyed = {(WED, ACME.asset_id): D("100")}
    listener = Listener(keyed)
    result = BacktestEngine.run(
        config(ACME, calendars=VenueCalendars()),
        prices(ACME.asset_id, (WED,)),
        running_strategy_state("S", listener),
        context_factory,
    )
    assert not [
        t for t in result.state.portfolio.ledger.transactions if t.asset_id == ACME.asset_id
    ]
    (reason,) = listener.rejections
    assert "settles T+n by its venue's calendar" in reason
    assert "ExecutionPipelineConfig.calendars" in reason


# --------------------------------------------------------------------------- #
# Two currencies
# --------------------------------------------------------------------------- #

FRANKFURT = MarketCalendar(
    "SYN-FRANKFURT",
    "Europe/Berlin",
    dict.fromkeys((0, 1, 2, 3, 4), (SessionWindow(time(9, 0), time(17, 30)),)),
    frozenset({date(2024, 3, 29), date(2024, 4, 1)}),  # Good Friday, Easter Monday
)
SAP = InstrumentRecord("SAPX", AssetType.EQUITY, "XETR", "EUR", settlement=T_PLUS_2)
EURUSD = (FxRate("EUR", "USD", D("1.10"), 1.0, "test"), FxRate("USD", "EUR", D("0.90"), 1.0, "t"))


def test_unsettled_proceeds_in_another_currency_are_kept_out_of_base_buying_power() -> None:
    from alphalab.backtesting import Funding, RunSchedule

    berlin = ZoneInfo("Europe/Berlin")
    wed = datetime(2024, 3, 27, 15, 0, tzinfo=berlin).timestamp()
    thu = datetime(2024, 3, 28, 15, 0, tzinfo=berlin).timestamp()
    base = config(SAP, calendars=VenueCalendars({"XETR": FRANKFURT}))
    pipeline = replace(
        base.pipeline,
        currency="USD",
        also_settles=frozenset({"EUR"}),
        budget=base.pipeline.budget.in_currency("USD"),
    )
    plan = {(wed, SAP.asset_id): D("10"), (thu, SAP.asset_id): D("-10")}
    result = BacktestEngine.run(
        replace(base, pipeline=pipeline),
        prices(SAP.asset_id, (wed, thu)),
        running_strategy_state("S", Trader(plan)),
        context_factory,
        schedule=RunSchedule.of(rates=list(EURUSD), fundings=[Funding(D("5000"), "EUR", 1.5)]),
    )
    # Bought and sold 1,000 EUR of shares; the sale's proceeds settle T+2 over
    # Easter: Wednesday 3 April.
    sale = next(t for t in result.state.portfolio.ledger.transactions if t.quantity < 0)
    assert sale.metadata[SETTLES_AT] == datetime(2024, 4, 3, 9, 0, tzinfo=berlin).timestamp()
    euro = PortfolioEngine.cash_position(result.state.portfolio, "EUR", thu)
    assert euro.unsettled_credits == D("1000")
    risk = result.state.risk
    assert risk.buying_power == max(D("0.00"), risk.cash - D("1000") * D("1.10"))


# --------------------------------------------------------------------------- #
# Replay, pause and restore
# --------------------------------------------------------------------------- #


def test_the_replay_equals_the_backtest_and_a_pause_anywhere_restores_the_obligations() -> None:
    keyed = {(t, ACME.asset_id): q for (t, _), q in PLAN.items()}
    data = prices(ACME.asset_id, sorted({t for t, _ in keyed}))
    cfg = config(ACME)
    backtest = BacktestEngine.run(
        cfg, data, running_strategy_state("S", Trader(keyed)), context_factory
    )
    whole = ReplayBacktest.run(
        cfg, data, running_strategy_state("S", Trader(keyed)), context_factory
    )
    assert whole.backtest.fills == backtest.fills
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg,
            data,
            running_strategy_state("S", Trader(keyed)),
            context_factory,
            pause_when=_at(boundary),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": Trader(keyed)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, data)
        assert restored.run.pipeline.portfolio.unsettled == paused.run.pipeline.portfolio.unsettled
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference


def _at(boundary: int) -> Any:
    def pause(progress: Any) -> bool:
        return bool(progress.records_replayed == boundary)

    return pause


def test_the_index_is_what_the_ledger_implies() -> None:
    portfolio = run().state.portfolio
    assert pending_from(portfolio.ledger.transactions) == portfolio.unsettled


@pytest.mark.parametrize("rule", [SettlementRule(SettlementBasis.TRADE_DATE, 0)])
def test_a_trade_date_rule_settles_at_the_trade_days_open_and_holds_nothing_back(
    rule: SettlementRule,
) -> None:
    same_day = replace(ACME, settlement=rule)
    result = run(same_day)
    assert not refusals(result)


def test_a_delivery_from_an_exercise_settles_by_the_deliverables_rule() -> None:
    from alphalab.backtesting import RunSchedule, ScheduledExercise
    from alphalab.options.enums import ExerciseStyle, OptionType
    from alphalab.options.expiration import SettlementStyle
    from alphalab.portfolio.corporate_actions import Exercise
    from tests.integration.test_option_lifecycle import SHARE_MIDS, data, option, runtime, series
    from tests.integration.test_option_lifecycle import config as lifecycle_config

    share = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD", settlement=T_PLUS_2)
    call = option(
        "ACMEC100S",
        OptionType.CALL,
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        deliverable=share,
    )
    plans = {"S": {(2.0, call.asset_id): D("1")}}
    base = lifecycle_config((call, share), plans)
    cfg = replace(
        base, pipeline=replace(base.pipeline, calendars=VenueCalendars({"XNAS": NEW_YORK}))
    )
    result = BacktestEngine.run(
        cfg,
        data({share.asset_id: SHARE_MIDS, call.asset_id: series("5", "6", "8", "9")}),
        runtime(plans),
        context_factory,
        schedule=RunSchedule.of(
            exercises=[ScheduledExercise(Exercise(call.asset_id, D("1")), 4.0)]
        ),
    )
    (delivery,) = [
        t for t in result.state.portfolio.ledger.transactions if t.asset_id == share.asset_id
    ]
    # Delivered at 4.0 UTC -- 19:00 on Wednesday 31 December 1969 in New York,
    # the venue's own date -- so T+2 trading days is Friday 2 January's open.
    assert delivery.metadata[SETTLES_AT] == datetime(1970, 1, 2, 9, 30, tzinfo=NY).timestamp()
    (pending,) = result.state.portfolio.unsettled
    assert pending.cash == D("-10000") and pending.quantity == D("100")
    option_fills = [
        t for t in result.state.portfolio.ledger.transactions if t.asset_id == call.asset_id
    ]
    assert all(t.metadata == {} for t in option_fills), "the option declares no rule"
