"""Margin calls and forced liquidation (v4.2.0, ledger FUT-017).

An instrument declaring margin terms requires collateral against its position:
an initial requirement a liquidation restores the account to, and a maintenance
requirement below which the account is called. A call is checked after every
market step; a liquidation is placed as each holding strategy's own target
through allocation, risk and the OMS, rests to its asset's next event, and
fills there by the run's fill policy -- so it trades at a price the market
showed, in part or not at all as the market allows, and is booked like every
other fill.

The base case is a short sale squeezed by a rise: 100 shares sold at 100 from
10,000 of cash, margined at half their value initially and three tenths at
maintenance. At 170 the account's equity is 3,000 against 5,100 maintenance; it
restores to the 8,500 initial requirement by buying 65 back (5,500 / 85,
rounded up to a whole share).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import OrderTerms
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.core.enums import AssetType
from alphalab.execution.policy import FillPolicy, LiquidityCappedFill
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.margin import (
    MarginBasis,
    MarginTerms,
    margin_from_primitives,
    margin_to_primitives,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.fx import NO_RATES, FxRate
from alphalab.portfolio.types import TransactionType
from alphalab.risk.events import MarginCall
from alphalab.risk.limits import MarginLimit
from alphalab.runtime.execution_pipeline import margin_position
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    backtest_config,
    context_factory,
    permissive_risk_limits,
    quote,
    registry_of,
    running_strategy_state,
    sized_quote,
)

D = Decimal
CASH = D("10000")
SHORT_MARGIN = MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.3"))
ACME = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD", margin=SHORT_MARGIN)
PLAIN = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")
OTHER = InstrumentRecord("OTHR", AssetType.EQUITY, "XNAS", "USD")


class Trader(BaseStrategy):
    """Asks for a scripted delta at chosen quotes, and keeps every rejection's reason."""

    def __init__(self, plan: dict[tuple[float, str], Decimal]) -> None:
        self._plan = plan
        self.rejections: list[str] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        wanted = self._plan.get((event.quote.timestamp, event.quote.asset_id))
        if wanted is None:
            return ()
        return (Intent("S", event.quote.asset_id, wanted, timestamp=event.quote.timestamp),)

    def on_order(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.reason:
            self.rejections.append(event.reason)
        return ()


def short(record: InstrumentRecord = ACME) -> dict[tuple[float, str], Decimal]:
    return {(2.0, record.asset_id): D("-100")}


#: Risk's pre-trade margin-utilization gate, loose enough for a leveraged book:
#: what this file tests is the call once the market has moved.
WIDE = D("100000000")
LEVERED = replace(permissive_risk_limits(), margin=MarginLimit(D("10")))


def config(*records: InstrumentRecord, fill_policy: FillPolicy | None = None) -> Any:
    base = backtest_config("S", starting_cash=CASH, fill_policy=fill_policy, risk_limits=LEVERED)
    # A budget allocation never binds: the account's cash and its margin do.
    budget = replace(
        base.pipeline.budget,
        global_capital=WIDE,
        maximum_exposure=WIDE,
        strategy_budgets={"S": WIDE},
    )
    pipeline = replace(base.pipeline, instruments=registry_of(*records), budget=budget)
    return replace(base, pipeline=pipeline)


def prices(mids: dict[str, dict[float, str]], size: str | None = None) -> MarketDataset:
    quotes = [
        quote(asset, at, D(mid)) if size is None else sized_quote(asset, at, D(mid), D(size))
        for asset, series in mids.items()
        for at, mid in series.items()
    ]
    return MarketDataset.of("MARGIN", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))


def series(*values: str, start: float = 2.0) -> dict[float, str]:
    return {start + n: v for n, v in enumerate(values)}


#: A squeeze: sold at 100, called at 170, liquidated at the next quote.
SQUEEZE = series("100", "120", "170", "170", "170")
#: Squeezed twice: the second rise after the first liquidation filled.
REPEATED = series("100", "170", "170", "220", "220", "220")


def run(
    mids: dict[str, dict[float, str]] | None = None,
    plan: dict[tuple[float, str], Decimal] | None = None,
    *records: InstrumentRecord,
    strategy: Trader | None = None,
    fill_policy: FillPolicy | None = None,
    size: str | None = None,
    schedule: RunSchedule | None = None,
) -> BacktestResult:
    chosen = records or (ACME,)
    trader = strategy if strategy is not None else Trader(short() if plan is None else plan)
    return BacktestEngine.run(
        config(*chosen, fill_policy=fill_policy),
        prices({ACME.asset_id: SQUEEZE} if mids is None else mids, size),
        running_strategy_state("S", trader),
        context_factory,
        schedule=RunSchedule.of() if schedule is None else schedule,
    )


def calls(result: BacktestResult) -> list[MarginCall]:
    return [e for e in result.state.risk.events if isinstance(e, MarginCall)]


TRADES = (TransactionType.BUY, TransactionType.SELL)


def fills(result: BacktestResult, record: InstrumentRecord = ACME) -> list[tuple[float, Decimal]]:
    return [
        (t.timestamp, t.quantity)
        for t in result.state.portfolio.ledger.transactions
        if t.asset_id == record.asset_id and t.type in TRADES
    ]


def held(result: BacktestResult, record: InstrumentRecord = ACME) -> Decimal:
    position = result.state.portfolio.positions.get(record.asset_id)
    return D("0") if position is None else position.quantity


# --------------------------------------------------------------------------- #
# A breach by a price move, and its liquidation
# --------------------------------------------------------------------------- #


def test_a_price_move_below_maintenance_calls_and_liquidates_at_the_next_quote() -> None:
    result = run()
    (call,) = calls(result)
    assert call.timestamp == 4.0
    assert (call.equity, call.maintenance_requirement) == (D("3000.00"), D("5100"))
    assert (call.initial_requirement, call.deficit) == (D("8500"), D("5500"))
    (order_id,) = call.orders
    # Bought back at the next quote's price, through the ledger like any fill.
    assert fills(result) == [(2.0, D("-100")), (5.0, D("65"))]
    (buy,) = [t for t in result.state.portfolio.ledger.transactions if t.timestamp == 5.0]
    assert buy.price == D("170.00")
    (order,) = [o for o in result.state.oms.orders.orders() if str(o.order_id.value) == order_id]
    assert order.filled_quantity == D("65")
    # The account is restored to the initial requirement, not merely maintenance.
    margin = margin_position(result.state, NO_RATES, 6.0)
    assert margin is not None and not margin.in_breach
    assert margin.equity >= margin.initial


def test_an_account_above_maintenance_is_not_called() -> None:
    result = run({ACME.asset_id: series("100", "120", "130", "130")})
    assert calls(result) == []
    assert fills(result) == [(2.0, D("-100"))]


def test_a_breach_repeated_after_a_liquidation_is_called_again() -> None:
    result = run({ACME.asset_id: REPEATED})
    first, second = calls(result)
    assert (first.timestamp, second.timestamp) == (3.0, 5.0)
    # 35 short at 220, 8,950 of cash: equity 1,250 against 2,310; restoring to
    # the 3,850 initial requirement buys 2,600 / 110 = 24 back, rounded up.
    assert (second.equity, second.deficit) == (D("1250.00"), D("2600"))
    assert fills(result) == [(2.0, D("-100")), (4.0, D("65")), (6.0, D("24"))]
    assert held(result) == D("-11")


def test_a_run_declaring_no_margin_is_never_called_and_its_identity_is_unchanged() -> None:
    plain = run(None, short(PLAIN), PLAIN)
    assert calls(plain) == []
    # Margin terms that are never breached change nothing a run records.
    calm = {ACME.asset_id: series("100", "110", "120")}
    declared = run(calm)
    undeclared = run(calm, short(PLAIN), PLAIN)
    assert calls(declared) == []
    assert digest_run(declared).result_id == digest_run(undeclared).result_id


# --------------------------------------------------------------------------- #
# The market decides the liquidation: liquidity, missing quotes, partial fills
# --------------------------------------------------------------------------- #


#: Deep enough to open the short and be called; then 100 shown each quote, a
#: quarter of it ours: 25 a quote.
THIN = {2.0: "1000", 3.0: "1000", 4.0: "100", 5.0: "100", 6.0: "100", 7.0: "100"}


def test_a_liquidation_in_thin_liquidity_fills_in_part_and_is_called_again_from_where_it_is() -> (
    None
):
    data = MarketDataset.of(
        "MARGIN",
        [
            sized_quote(ACME.asset_id, t, D("170" if t > 2 else "100"), D(n))
            for t, n in THIN.items()
        ],
    )
    result = BacktestEngine.run(
        config(ACME, fill_policy=LiquidityCappedFill(D("0.25"))),
        data,
        running_strategy_state("S", Trader(short())),
        context_factory,
        schedule=RunSchedule.of(),
    )
    # 25 of the 65 fill at 4.0, and the market order's remainder is cancelled,
    # as any market order's is. Still below maintenance -- 3,000 against 3,825 --
    # the next step calls again, sized from the book as it now is: 3,375 / 85,
    # rounded up. After 25 more the account is above maintenance and no further
    # call is made: never two liquidations at once, never a stale size.
    first, second = calls(result)
    assert (first.timestamp, first.deficit) == (3.0, D("5500"))
    assert (second.timestamp, second.deficit) == (4.0, D("3375"))
    assert fills(result) == [(2.0, D("-100")), (4.0, D("25")), (5.0, D("25"))]
    ordered = {
        str(o.order_id.value): (o.quantity, o.filled_quantity)
        for o in result.state.oms.orders.orders()
    }
    assert [ordered[c.orders[0]] for c in (first, second)] == [
        (D("65"), D("25")),
        (D("40"), D("25")),
    ]
    margin = margin_position(result.state, NO_RATES, 8.0)
    assert margin is not None and not margin.in_breach


def test_a_liquidation_with_no_further_quote_rests_and_is_never_placed_twice() -> None:
    # ACME's last quote is the squeeze; OTHER keeps quoting.
    mids = {
        ACME.asset_id: series("100", "170"),
        OTHER.asset_id: series("50", "50", "50", "50", "50"),
    }
    result = run(mids, short(), ACME, OTHER)
    (call,) = calls(result)
    assert call.timestamp == 3.0 and len(call.orders) == 1
    # No price was shown for ACME again: nothing is assumed filled.
    assert fills(result) == [(2.0, D("-100"))]
    working = result.state.oms.working_orders_for(ACME.asset_id)
    assert len(working) == 1


class Asker(Trader):
    """Asks for a delta in ACME at OTHER's quote, while ACME's liquidation still rests."""

    def __init__(self, delta: Decimal) -> None:
        super().__init__(short())
        self._delta = delta

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.asset_id == OTHER.asset_id:
            return (Intent("S", ACME.asset_id, self._delta, timestamp=event.quote.timestamp),)
        return super().on_quote(context, event)


@pytest.mark.parametrize(
    ("delta", "refused"),
    [
        (D("-10"), True),
        (D("5"), False),
        # Buying 150 against a 100 short reverses it: a new long, refused.
        (D("150"), True),
    ],
)
def test_only_an_order_reducing_a_position_trades_while_the_account_is_in_breach(
    delta: Decimal, refused: bool
) -> None:
    # Called at 3.0; OTHER quotes at 3.5, before ACME's next quote fills the
    # liquidation at 4.0.
    mids = {ACME.asset_id: series("100", "170", "170"), OTHER.asset_id: {3.5: "50"}}
    asker = Asker(delta)
    result = run(mids, None, ACME, OTHER, strategy=asker)
    reasons = [r for r in asker.rejections if "below its maintenance margin" in r]
    assert bool(reasons) is refused
    asked = [(t, q) for t, q in fills(result) if t == 3.5]
    assert asked == ([] if refused else [(3.5, D("5"))])
    # The liquidation fills either way, at ACME's next quote.
    assert any(t == 4.0 and q > 0 for t, q in fills(result))


# --------------------------------------------------------------------------- #
# Breaches by an exchange rate, a delivery, and a per-contract requirement
# --------------------------------------------------------------------------- #


def test_an_exchange_rate_move_calls_an_account_whose_position_is_in_another_currency() -> None:
    tight = MarginTerms(MarginBasis.VALUE_RATE, D("0.9"), D("0.8"))
    euro = InstrumentRecord("SAPX", AssetType.EQUITY, "XETR", "EUR", margin=tight)
    base = config(euro)
    pipeline = replace(
        base.pipeline,
        currency="USD",
        also_settles=frozenset({"EUR"}),
        budget=base.pipeline.budget.in_currency("USD"),
    )
    rates = [
        FxRate("EUR", "USD", D("1.10"), 1.0, "test"),
        FxRate("USD", "EUR", D("0.90"), 1.0, "test"),
        FxRate("EUR", "USD", D("1.60"), 3.5, "test"),
    ]
    quotes = [quote(euro.asset_id, t, D("100")) for t in (2.0, 3.0, 4.0, 5.0)]
    data = MarketDataset.of("MARGIN", [replace(q, currency="EUR") for q in quotes])
    result = BacktestEngine.run(
        replace(base, pipeline=pipeline),
        data,
        running_strategy_state("S", Trader({(2.0, euro.asset_id): D("-80")})),
        context_factory,
        schedule=RunSchedule.of(rates=rates),
    )
    # The share never moved; the euro did. At 1.60 the 8,000 EUR short is
    # 12,800 USD: maintenance 10,240 against equity 10,000. Restoring to the
    # 11,520 initial requirement buys 11 back (1,520 / 144, rounded up).
    (call,) = calls(result)
    assert call.timestamp == 4.0
    assert (call.equity, call.maintenance_requirement) == (D("10000.00"), D("10240"))
    assert fills(result, euro) == [(2.0, D("-80")), (5.0, D("11"))]


def test_a_delivery_from_an_exercise_that_leaves_the_account_short_of_margin_is_called() -> None:
    from tests.integration.test_option_lifecycle import (
        AM_CALL,
        SHARE,
        SHARE_MIDS,
        expiry,
        runtime,
    )
    from tests.integration.test_option_lifecycle import config as lifecycle_config
    from tests.integration.test_option_lifecycle import data as lifecycle_data
    from tests.integration.test_option_lifecycle import (
        series as option_series,
    )

    # Shares that count for a fifth of their value as collateral.
    haircut = MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.3"), haircut=D("0.8"))
    share = replace(SHARE, margin=haircut)
    plans = {"S": {(2.0, AM_CALL.asset_id): D("2")}}
    result = BacktestEngine.run(
        lifecycle_config((AM_CALL, share), plans, cash=D("21000")),
        lifecycle_data({share.asset_id: SHARE_MIDS, AM_CALL.asset_id: option_series("5", "6")}),
        runtime(plans),
        context_factory,
        schedule=expiry(AM_CALL, "112"),
    )
    # Delivered 200 shares for the strike's 20,000 at expiry: equity is the
    # fifth of their value the haircut leaves -- under the three tenths
    # maintenance requires -- at the share's next quote.
    # At 112: 22,400 of shares, no cash; equity 4,480 against 6,720. Each share
    # sold frees its 56 of initial requirement and adds the 89.60 its haircut
    # held back: 6,720 / 145.60, rounded up, is 47.
    (call,) = calls(result)
    assert call.timestamp == 7.0
    assert (call.equity, call.maintenance_requirement) == (D("4480.00"), D("6720"))
    assert [(t, q) for t, q in fills(result, share) if q < 0] == [(8.0, D("-47"))]
    margin = margin_position(result.state, NO_RATES, 9.5)
    assert margin is not None and not margin.in_breach


FUTURE = InstrumentRecord(
    "ESZ6",
    AssetType.FUTURE,
    "XCME",
    "USD",
    economics=InstrumentEconomics(
        D("10"), SettlementModel.FUTURES_VARIATION, lot=LotSpecification(D("1"), D("1"))
    ),
    margin=MarginTerms(MarginBasis.PER_CONTRACT, D("2500"), D("2250")),
)


@pytest.mark.parametrize(
    ("low", "sold"),
    [
        # Four contracts lose 70 points x 10 each: 2,800 of 10,000. Equity 7,200
        # against 9,000 maintenance; restoring to 10,000 sells 2,800 / 2,500 = 2.
        ("30", D("-2")),
        # A loss of exactly 1,000 leaves equity at maintenance: not a breach.
        ("75", None),
    ],
)
def test_a_per_contract_requirement_calls_a_future_after_a_variation_loss(
    low: str, sold: Decimal | None
) -> None:
    result = run(
        {FUTURE.asset_id: series("100", "100", low, low, low)},
        {(2.0, FUTURE.asset_id): D("4")},
        FUTURE,
    )
    if sold is None:
        assert calls(result) == []
        assert fills(result, FUTURE) == [(2.0, D("4"))]
        return
    (call,) = calls(result)
    assert call.timestamp == 4.0
    assert (call.equity, call.maintenance_requirement) == (D("7200.00"), D("9000"))
    assert (call.initial_requirement, call.deficit) == (D("10000"), D("2800"))
    assert fills(result, FUTURE) == [(2.0, D("4")), (5.0, sold)]


# --------------------------------------------------------------------------- #
# Replay, pause and restore
# --------------------------------------------------------------------------- #


def test_the_replay_equals_the_backtest_and_a_pause_anywhere_restores_the_call() -> None:
    data = prices({ACME.asset_id: REPEATED})
    cfg = config(ACME)
    backtest = BacktestEngine.run(
        cfg, data, running_strategy_state("S", Trader(short())), context_factory
    )
    whole = ReplayBacktest.run(
        cfg, data, running_strategy_state("S", Trader(short())), context_factory
    )
    assert whole.backtest.fills == backtest.fills
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg,
            data,
            running_strategy_state("S", Trader(short())),
            context_factory,
            pause_when=_at(boundary),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": Trader(short())},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, data)
        assert calls_of(restored.run.pipeline) == calls_of(paused.run.pipeline)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference
        assert len(calls(resumed.backtest)) == 2


def calls_of(state: Any) -> list[MarginCall]:
    return [e for e in state.risk.events if isinstance(e, MarginCall)]


def _at(boundary: int) -> Any:
    def pause(progress: Any) -> bool:
        return bool(progress.records_replayed == boundary)

    return pause


# --------------------------------------------------------------------------- #
# The terms, and their persistence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("initial", "maintenance", "haircut"),
    [
        ("0.3", "0.5", "0"),  # maintenance above initial
        ("0.5", "0", "0"),  # no maintenance requirement
        ("0.5", "0.3", "1"),  # a holding counting for nothing
        ("0.5", "0.3", "-0.1"),
    ],
)
def test_terms_that_cannot_hold_are_refused(initial: str, maintenance: str, haircut: str) -> None:
    with pytest.raises(InstrumentInputError):
        MarginTerms(MarginBasis.VALUE_RATE, D(initial), D(maintenance), haircut=D(haircut))


def test_a_maintenance_requirement_may_equal_the_initial_one() -> None:
    terms = MarginTerms(MarginBasis.PER_CONTRACT, D("3000"), D("3000"))
    assert terms.requirement(D("-2"), D("0"), initial=False) == D("6000")


def test_a_float_or_a_non_integer_priority_is_refused() -> None:
    with pytest.raises(InstrumentInputError):
        MarginTerms(MarginBasis.VALUE_RATE, 0.5, D("0.3"))  # type: ignore[arg-type]
    with pytest.raises(InstrumentInputError):
        MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.3"), priority=True)


def test_terms_round_trip_through_primitives_and_the_registry_snapshot() -> None:
    terms = MarginTerms(MarginBasis.PER_CONTRACT, D("6000"), D("4500"), D("0.1"), priority=2)
    assert margin_from_primitives(margin_to_primitives(terms)) == terms
    assert margin_to_primitives(None) is None and margin_from_primitives(None) is None
    registry = registry_of(replace(ACME, margin=terms), OTHER)
    payload = deserialize(serialize(capture_registry(registry)))
    restored = restore_registry(registry_from_primitives(payload))
    assert restored.record_for(ACME.asset_id).margin == terms  # type: ignore[union-attr]
    assert restored.record_for(OTHER.asset_id).margin is None  # type: ignore[union-attr]


CLOSE = MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.45"))


@pytest.mark.parametrize(
    ("acme_priority", "other_priority", "reduced", "bought"),
    [
        # The lower priority first: either way round.
        (1, 0, OTHER, D("45")),  # 2,250 / 50
        (0, 1, ACME, D("40")),  # 2,250 / 57.50, rounded up
        # A tie: the largest requirement first -- ACME's, at 115.
        (0, 0, ACME, D("40")),
    ],
)
def test_a_liquidation_reduces_positions_in_their_declared_order(
    acme_priority: int, other_priority: int, reduced: InstrumentRecord, bought: Decimal
) -> None:
    acme = replace(ACME, margin=replace(CLOSE, priority=acme_priority))
    other = replace(OTHER, margin=replace(CLOSE, priority=other_priority))
    plan = {(2.0, acme.asset_id): D("-100"), (2.0, other.asset_id): D("-100")}
    # ACME rises to 115: equity 8,500 against 9,675 maintenance, 2,250 short of
    # the 10,750 initial requirement -- what either position alone restores.
    result = run(
        {acme.asset_id: series("100", "115", "115"), other.asset_id: series("100", "100", "100")},
        plan,
        acme,
        other,
    )
    (call,) = calls(result)
    assert call.deficit == D("2250")
    untouched = other if reduced.asset_id == acme.asset_id else acme
    assert [q for _, q in fills(result, reduced) if q > 0] == [bought]
    assert [q for _, q in fills(result, untouched) if q > 0] == []


def test_buying_back_a_short_the_cash_cannot_cover_is_booked_as_a_debit() -> None:
    # Short 100 ACME for 10,000, then 15,000 of OTHER: 5,000 of cash left. At
    # 120 the short costs 12,000 to buy back -- more than the cash, though the
    # account is worth 8,000. Until v4.2.0 the fill stopped the run; buying back
    # what the account owes is an obligation, and is debited as one.
    plan = {
        (2.0, PLAIN.asset_id): D("-100"),
        (2.5, OTHER.asset_id): D("300"),
        (3.0, PLAIN.asset_id): D("100"),
    }
    result = run(
        {PLAIN.asset_id: {2.0: "100", 3.0: "120"}, OTHER.asset_id: {2.5: "50", 3.5: "50"}},
        plan,
        PLAIN,
        OTHER,
    )
    assert fills(result, PLAIN) == [(2.0, D("-100")), (3.0, D("100"))]
    assert result.state.portfolio.cash.balance("USD") == D("-7000.00")
    assert result.valuation.equity == D("8000.00")


def test_a_haircut_is_taken_on_a_long_holding_and_not_on_a_short() -> None:
    cut = replace(ACME, margin=replace(SHORT_MARGIN, haircut=D("0.5")))
    assert [(c.equity, c.deficit) for c in calls(run(None, None, cut))] == [
        (c.equity, c.deficit) for c in calls(run())
    ]


def test_an_account_whose_short_has_outrun_its_cash_is_liquidated_in_full() -> None:
    # Analytics refuses a book worth less than nothing, so this run compiles none.
    base = config(ACME)
    cfg = replace(base, compile_analytics=False)
    result = BacktestEngine.run(
        cfg,
        prices({ACME.asset_id: series("100", "300", "300")}),
        running_strategy_state("S", Trader(short())),
        context_factory,
    )
    # At 300 the account is worth 20,000 - 30,000: no sale restores it, so the
    # whole short is bought back, and its cost is debited past the cash.
    (call,) = calls(result)
    assert call.equity == D("-10000.00")
    assert fills(result) == [(2.0, D("-100")), (4.0, D("100"))]
    assert result.state.portfolio.cash.balance("USD") == D("-10000.00")


def test_a_buy_past_a_short_is_spent_from_cash_the_account_holds() -> None:
    from alphalab.portfolio import Account, InsufficientFundsError, PortfolioEngine, PortfolioState

    state = PortfolioState(account=Account("A", "USD", "margin", 1.0))
    state = PortfolioEngine.apply_deposit(state, D("1000"), "USD", 1.0)
    state = PortfolioEngine.apply_fill(state, "X", D("-10"), D("100"), D("0"), 2.0, "USD")
    covered = PortfolioEngine.apply_fill(state, "X", D("10"), D("250"), D("0"), 3.0, "USD")
    assert covered.cash.balance("USD") == D("-500.00")
    # Buying 20 is a cover and a new long: the long's cost is not an obligation.
    with pytest.raises(InsufficientFundsError):
        PortfolioEngine.apply_fill(state, "X", D("20"), D("250"), D("0"), 3.0, "USD")


# --------------------------------------------------------------------------- #
# Before an order is placed: the initial requirement and buying power
# --------------------------------------------------------------------------- #

#: A short margined at 150% of its value, as Reg-T-style rules margin one.
REG_T_SHORT = MarginTerms(MarginBasis.VALUE_RATE, D("1.5"), D("1.2"))


@pytest.mark.parametrize(("quantity", "placed"), [(D("-100"), False), (D("-60"), True)])
def test_an_order_beyond_the_initial_requirement_is_refused_before_it_is_placed(
    quantity: Decimal, placed: bool
) -> None:
    record = replace(ACME, margin=REG_T_SHORT)
    plan = {(2.0, record.asset_id): quantity}
    trader = Trader(plan)
    # Selling 100 at 100 requires 15,000 of the account's 10,000; 60, 9,000.
    result = run({record.asset_id: series("100", "100")}, plan, record, strategy=trader)
    assert fills(result, record) == ([(2.0, quantity)] if placed else [])
    refused = [r for r in trader.rejections if "below its initial margin" in r]
    assert bool(refused) is not placed


class Poster(Trader):
    """Rests a limit buy at 2.0, then asks for a market buy at 3.0."""

    def __init__(self, resting: Decimal, then: Decimal) -> None:
        super().__init__({})
        self._resting, self._then = resting, then

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        at = event.quote.timestamp
        asset = event.quote.asset_id
        if at == 2.0 and self._resting:
            terms = OrderTerms.limit(D("90"))
            return (Intent("S", asset, self._resting, timestamp=at, terms=terms),)
        if at == 3.0:
            return (Intent("S", asset, self._then, timestamp=at),)
        return ()


@pytest.mark.parametrize(("resting", "placed"), [(D("3"), False), (D("0"), True)])
def test_a_working_order_counts_against_the_initial_requirement(
    resting: Decimal, placed: bool
) -> None:
    # A resting bid for 3 futures commits 7,500 of initial margin; 2 more at
    # market would need 5,000 beside it, of 10,000.
    poster = Poster(resting, D("2"))
    result = run({FUTURE.asset_id: series("100", "100")}, None, FUTURE, strategy=poster)
    assert ((3.0, D("2")) in fills(result, FUTURE)) is placed
    assert any("below its initial margin" in r for r in poster.rejections) is not placed


def test_a_margined_future_is_refused_for_its_initial_margin_though_its_notional_fits() -> None:
    # Six contracts at 100 x 10 are 6,000 of notional and 15,000 of initial
    # margin: refused for the margin, though the notional is within the cash.
    plan = {(2.0, FUTURE.asset_id): D("6")}
    trader = Trader(plan)
    result = run({FUTURE.asset_id: series("100", "100")}, plan, FUTURE, strategy=trader)
    assert fills(result, FUTURE) == []
    assert any("below its initial margin" in r for r in trader.rejections)


def test_a_fully_paid_margined_purchase_is_still_charged_its_notional() -> None:
    # 150 at 100 needs 7,500 of initial margin, within the 10,000 of equity; it
    # costs 15,000 of cash, which a purchase is never financed past: only a
    # delivery is (FUT-017), and buying on margin is not modelled.
    plan = {(2.0, ACME.asset_id): D("150")}
    trader = Trader(plan)
    result = run({ACME.asset_id: series("100", "100")}, plan, ACME, strategy=trader)
    assert fills(result) == []
    rejected = [d for d in result.state.risk.history if not d.approved]
    assert any(v.rule == "BuyingPowerLimit" for d in rejected for v in d.violations)


def test_a_margined_option_purchase_is_charged_its_premium_not_its_margin() -> None:
    from tests.integration.test_option_lifecycle import AM_CALL

    call = replace(AM_CALL, margin=SHORT_MARGIN)
    plan = {(2.0, call.asset_id): D("3")}
    trader = Trader(plan)
    # Three contracts at 50 x 100 are 15,000 of premium -- paid in full -- of
    # 10,000 of cash; the 7,500 of initial margin is no part of what it costs.
    result = run({call.asset_id: series("50", "50")}, plan, call, PLAIN, strategy=trader)
    assert fills(result, call) == []
    rejected = [d for d in result.state.risk.history if not d.approved]
    assert any(v.rule == "BuyingPowerLimit" for d in rejected for v in d.violations)
