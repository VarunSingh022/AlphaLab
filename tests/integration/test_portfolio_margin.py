"""A declared portfolio margin model, and the offsets it gives (v4.2.0, ledger FUT-019).

The account's margin is computed by the model the registry declares: a
:class:`ScenarioMargin` revalues every position it covers under each declared
scenario at once, so a hedge offsets exactly as far as the scenarios say and no
further, never below a declared floor; a position it does not cover is margined
by its own terms. The margin check, a liquidation's sizing, the pre-trade check
and the risk gate's margin figure all read the model.

The book: two futures on 1x, AAAF and BBBF, margined on their own at half their
value. The scenarios move both 20% up and down together, and their spread 5% and
15% apart either way: a long AAAF and short BBBF pair loses only on the spread.
"""

from __future__ import annotations

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
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.core.enums import AssetType
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.margin import (
    MarginBasis,
    MarginScenario,
    MarginTerms,
    PerPositionMargin,
    ScenarioMargin,
    margin_model_from_primitives,
    margin_model_to_primitives,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import declare_margin_model
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.fx import NO_RATES, FxRate
from alphalab.risk.limits import MarginLimit
from alphalab.runtime.execution_pipeline import margin_position
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import context_factory, quote, registry_of, running_strategy_state

D = Decimal
HALF = MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.4"))


def future(symbol: str, currency: str = "USD", **terms: Any) -> InstrumentRecord:
    return InstrumentRecord(
        symbol,
        AssetType.FUTURE,
        "XCME",
        currency,
        economics=InstrumentEconomics(
            D("1"), SettlementModel.FUTURES_VARIATION, lot=LotSpecification(D("1"), D("1"))
        ),
        margin=replace(HALF, **terms),
    )


#: The pair is liquidated after CCCF: priority 1 against CCCF's 0.
A, B, C = future("AAAF", priority=1), future("BBBF", priority=1), future("CCCF")


def scenarios(a: InstrumentRecord = A, b: InstrumentRecord = B) -> ScenarioMargin:
    def moves(x: str, y: str) -> dict[str, Decimal]:
        return {a.asset_id: D(x), b.asset_id: D(y)}

    return ScenarioMargin(
        (
            MarginScenario("up", moves("0.20", "0.20")),
            MarginScenario("down", moves("-0.20", "-0.20")),
            MarginScenario("spread out", moves("0.05", "0.15")),
            MarginScenario("spread in", moves("-0.05", "-0.15")),
        ),
        maintenance_ratio=D("0.8"),
        floor_rate=D("0.05"),
    )


def config(*records: InstrumentRecord, model: Any = None, utilization: str = "10") -> Any:
    cfg = margin.config(*records)
    registry = cfg.pipeline.instruments
    if model is not None:
        registry = declare_margin_model(registry, model)
    limits = replace(cfg.pipeline.risk_limits, margin=MarginLimit(D(utilization)))
    return replace(cfg, pipeline=replace(cfg.pipeline, instruments=registry, risk_limits=limits))


def run(
    plan: dict[tuple[float, str], Decimal],
    mids: dict[str, dict[float, str]],
    *records: InstrumentRecord,
    model: Any = None,
    utilization: str = "10",
    strategy: Any = None,
) -> BacktestResult:
    trader = strategy if strategy is not None else margin.Trader(plan)
    return BacktestEngine.run(
        config(*records, model=model, utilization=utilization),
        margin.prices(mids),
        running_strategy_state("S", trader),
        context_factory,
        schedule=RunSchedule.of(),
    )


def flat(*assets: InstrumentRecord, n: int = 3) -> dict[str, dict[float, str]]:
    return {a.asset_id: margin.series(*(["100"] * n)) for a in assets}


PAIR = {(2.0, A.asset_id): D("150"), (2.0, B.asset_id): D("-150")}


# --------------------------------------------------------------------------- #
# The model's requirement, and what it admits
# --------------------------------------------------------------------------- #


def test_a_hedged_pair_requires_its_scenario_loss_not_the_sum_of_its_legs() -> None:
    result = run(PAIR, flat(A, B), A, B, model=scenarios())
    assert margin.fills(result, A) == [(2.0, D("150"))]
    assert margin.fills(result, B) == [(2.0, D("-150"))]
    figures = margin_position(result.state, NO_RATES, 4.0)
    assert figures is not None
    # 15,000 each way: the spread's 10% is 1,500, the floor 5% of 30,000 is
    # 1,500; on their own the legs would require 15,000.
    assert (figures.initial, figures.maintenance) == (D("1500.00"), D("1200.000"))
    assert sum(h.initial for h in figures.holdings) == D("15000.00")


def test_without_the_model_the_pair_is_held_to_each_legs_own_requirement() -> None:
    trader = margin.Trader(PAIR)
    result = run(PAIR, flat(A, B), A, B, strategy=trader)
    # The first leg's 7,500 fits 10,000 of equity; the second's does not.
    assert len(margin.fills(result, A) + margin.fills(result, B)) == 1
    assert any("below its initial margin" in r for r in trader.rejections)


def test_the_risk_gate_reads_the_model_and_not_gross_exposure() -> None:
    # A margin limit of 1: gross exposure is three times net asset value, the
    # model's requirement 15% of margin equity.
    with_model = run(PAIR, flat(A, B), A, B, model=scenarios(), utilization="1")
    assert len(margin.fills(with_model, A) + margin.fills(with_model, B)) == 2
    rejected = [d for d in with_model.state.risk.history if not d.approved]
    assert not rejected
    gross = run(PAIR, flat(A, B), A, B, model=None, utilization="1", strategy=margin.Trader(PAIR))
    refused = [v.rule for d in gross.state.risk.history if not d.approved for v in d.violations]
    assert "MarginLimit" in refused


def test_a_position_the_scenarios_do_not_cover_is_margined_on_its_own() -> None:
    plan = {**PAIR, (2.0, C.asset_id): D("20")}
    result = run(plan, flat(A, B, C), A, B, C, model=scenarios())
    figures = margin_position(result.state, NO_RATES, 4.0)
    assert figures is not None
    # The pair's 1,500 and CCCF's own half of 2,000.
    assert figures.initial == D("1500.00") + D("1000")


# --------------------------------------------------------------------------- #
# Calls and liquidation under the model
# --------------------------------------------------------------------------- #


#: A pair built in two tranches: each order is judged against the book as it
#: stands, so a first leg alone must fit before its hedge offsets it.
BIG = {
    (2.0, A.asset_id): D("300"),
    (2.0, B.asset_id): D("-300"),
    (3.0, A.asset_id): D("300"),
    (3.0, B.asset_id): D("-300"),
}
SPREAD = {
    A.asset_id: margin.series("100", "100", "95", "95", "95"),
    B.asset_id: margin.series("100", "100", "105", "105", "105"),
}


def test_a_large_pair_placed_at_once_is_judged_leg_by_leg() -> None:
    at_once = {(2.0, A.asset_id): D("600"), (2.0, B.asset_id): D("-600")}
    trader = margin.Trader(at_once)
    result = run(at_once, flat(A, B), A, B, model=scenarios(), strategy=trader)
    # AAAF alone, 60,000 long, loses 12,000 in the down scenario: more than the
    # 10,000 of equity. Its hedge is not yet held, so it offsets nothing.
    assert margin.fills(result, A) == []
    assert any("margin model" in r for r in trader.rejections)


def test_a_spread_move_calls_the_pair_and_the_liquidation_restores_it() -> None:
    result = run(BIG, SPREAD, A, B, model=scenarios())
    calls = margin.calls(result)
    assert calls, "the spread's move put the pair below maintenance"
    # 3,000 lost on each leg: equity 4,000 against 5,280 maintenance.
    assert (calls[0].equity, calls[0].maintenance_requirement) == (D("4000.00"), D("5280.000"))
    after = margin_position(result.state, NO_RATES, 9.0)
    assert after is not None and not after.in_breach and after.equity >= after.initial


def test_a_liquidation_leaves_a_hedge_alone_and_sells_what_is_losing() -> None:
    plan = {
        (2.0, A.asset_id): D("400"),
        (2.0, B.asset_id): D("-400"),
        (2.0, C.asset_id): D("100"),
    }
    mids = {
        A.asset_id: margin.series("100", "100", "100"),
        B.asset_id: margin.series("100", "100", "100"),
        C.asset_id: margin.series("100", "50", "50"),
    }
    result = run(plan, mids, A, B, C, model=scenarios())
    # CCCF halves: equity 5,000 against 5,200 maintenance. CCCF comes first in
    # the declared order, and its 2,500 of initial at 50 is 25 a contract: 1,500
    # short sells 60 of it, which restores the account, and the pair is not
    # touched.
    (call,) = margin.calls(result)
    assert call.deficit == D("1500.00")
    assert margin.fills(result, A) == [(2.0, D("400"))]
    assert margin.fills(result, B) == [(2.0, D("-400"))]
    assert margin.fills(result, C) == [(2.0, D("100")), (4.0, D("-60"))]


def test_covering_one_leg_removes_the_offset_and_the_other_is_called() -> None:
    plan = {
        (2.0, A.asset_id): D("450"),
        (2.0, B.asset_id): D("-450"),
        (3.0, A.asset_id): D("450"),
        (3.0, B.asset_id): D("-450"),
        (4.0, B.asset_id): D("900"),
    }
    result = run(plan, flat(A, B, n=5), A, B, model=scenarios())
    # Hedged, the pair requires 9,000. Covered, AAAF alone loses 20% of 90,000
    # in the down scenario: 18,000 against 10,000 of equity. The cover -- a
    # reduction -- is not refused; the call that follows sells AAAF down to
    # 50,000 of value, 400 contracts, at its next quote.
    assert margin.fills(result, B)[-1] == (4.0, D("900"))
    (call,) = margin.calls(result)
    assert call.timestamp == 4.0
    assert margin.fills(result, A)[-1] == (5.0, D("-400"))


def test_a_pair_in_another_currency_is_valued_at_the_rate() -> None:
    a, b = future("AAAE", "EUR"), future("BBBE", "EUR")
    model = scenarios(a, b)
    cfg = config(a, b, model=model)
    pipeline = replace(
        cfg.pipeline,
        currency="USD",
        also_settles=frozenset({"EUR"}),
        budget=cfg.pipeline.budget.in_currency("USD"),
    )
    from alphalab.backtesting import Funding

    rates = [FxRate("EUR", "USD", D("1.25"), 1.0, "t"), FxRate("USD", "EUR", D("0.8"), 1.0, "t")]
    from alphalab.backtesting.dataset import MarketDataset

    data = MarketDataset.of(
        "MARGIN",
        [
            replace(quote(asset.asset_id, t, D("100")), currency="EUR")
            for t in (2.0, 3.0)
            for asset in (a, b)
        ],
    )
    plan = {(2.0, a.asset_id): D("100"), (2.0, b.asset_id): D("-100")}
    result = BacktestEngine.run(
        replace(cfg, pipeline=pipeline),
        data,
        running_strategy_state("S", margin.Trader(plan)),
        context_factory,
        schedule=RunSchedule.of(rates=rates, fundings=[Funding(D("100"), "EUR", 1.5)]),
    )
    from alphalab.portfolio.fx import FxRates

    table = FxRates.of(rates)
    figures = margin_position(result.state, table, 4.0)
    assert figures is not None
    # 10,000 EUR each way is 12,500 USD: the spread's 1,250, the floor's 1,250.
    assert figures.initial == D("1250.00")


# --------------------------------------------------------------------------- #
# Declaration, validation and persistence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: ScenarioMargin((), D("0.8")),
        lambda: ScenarioMargin(
            (
                MarginScenario("one", {A.asset_id: D("0.1"), B.asset_id: D("0.1")}),
                MarginScenario("two", {A.asset_id: D("0.1")}),  # forgets BBBF
            ),
            D("0.8"),
        ),
        lambda: ScenarioMargin(
            (
                MarginScenario("x", {A.asset_id: D("0.1")}),
                MarginScenario("x", {A.asset_id: D("0.2")}),
            ),
            D("0.8"),
        ),
        lambda: ScenarioMargin((MarginScenario("x", {A.asset_id: D("0.1")}),), D("1.2")),
        lambda: ScenarioMargin((MarginScenario("x", {A.asset_id: D("0.1")}),), D("0.8"), D("-0.1")),
        lambda: MarginScenario("crash", {A.asset_id: D("-1")}),
        lambda: MarginScenario("empty", {}),
    ],
)
def test_a_scenario_set_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_a_model_covering_what_the_registry_does_not_margin_is_refused() -> None:
    plain = InstrumentRecord("PLNF", AssetType.FUTURE, "XCME", "USD", economics=A.economics)
    with pytest.raises(InstrumentRegistrationError, match="does not hold"):
        declare_margin_model(registry_of(A), scenarios())
    with pytest.raises(InstrumentRegistrationError, match="declares no margin terms"):
        declare_margin_model(registry_of(A, plain), scenarios(A, plain))


def test_a_declared_model_round_trips_through_the_registry_snapshot() -> None:
    model = scenarios()
    assert margin_model_from_primitives(margin_model_to_primitives(model)) == model
    assert margin_model_from_primitives(margin_model_to_primitives(PerPositionMargin())) == (
        PerPositionMargin()
    )
    registry = declare_margin_model(registry_of(A, B), model)
    payload = deserialize(serialize(capture_registry(registry)))
    assert restore_registry(registry_from_primitives(payload)).margin_model == model
    bare = deserialize(serialize(capture_registry(registry_of(A, B))))
    assert restore_registry(registry_from_primitives(bare)).margin_model is None
    with pytest.raises(InstrumentInputError, match="cannot be written"):

        class House:
            def requirement(self, exposures: Any) -> Any:
                raise NotImplementedError

        margin_model_to_primitives(House())


def test_a_run_under_the_model_replays_and_restores_from_every_pause() -> None:
    big = BIG
    cfg, data = config(A, B, model=scenarios()), margin.prices(SPREAD)

    def strategies() -> Any:
        return running_strategy_state("S", margin.Trader(big))

    whole = ReplayBacktest.run(cfg, data, strategies(), context_factory)
    backtest = BacktestEngine.run(cfg, data, strategies(), context_factory)
    assert whole.backtest.fills == backtest.fills
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg, data, strategies(), context_factory, pause_when=margin._at(boundary)
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": margin.Trader(big)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, data)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference


# --------------------------------------------------------------------------- #
# The model's edges: its floor, a one-sided set, working orders, a haircut
# --------------------------------------------------------------------------- #


def one_sided(*assets: InstrumentRecord, move: str = "-0.20", floor: str = "0") -> ScenarioMargin:
    return ScenarioMargin(
        (MarginScenario("down", {a.asset_id: D(move) for a in assets}),),
        maintenance_ratio=D("0.8"),
        floor_rate=D(floor),
    )


def test_a_perfect_hedge_still_requires_the_floor() -> None:
    # Moved together only, the pair loses nothing in any scenario; the floor of
    # 5% of 30,000 is what it requires.
    parallel = ScenarioMargin(
        (
            MarginScenario("up", {A.asset_id: D("0.2"), B.asset_id: D("0.2")}),
            MarginScenario("down", {A.asset_id: D("-0.2"), B.asset_id: D("-0.2")}),
        ),
        maintenance_ratio=D("0.8"),
        floor_rate=D("0.05"),
    )
    result = run(PAIR, flat(A, B), A, B, model=parallel)
    figures = margin_position(result.state, NO_RATES, 4.0)
    assert figures is not None and figures.initial == D("1500.00")


def test_a_one_sided_scenario_set_charges_only_the_side_it_hurts() -> None:
    long_only = {(2.0, A.asset_id): D("100")}
    short_only = {(2.0, A.asset_id): D("-100")}
    long_book = run(long_only, flat(A), A, model=one_sided(A))
    short_book = run(short_only, flat(A), A, model=one_sided(A))
    # A fall of 20% costs a long 2,000 and earns a short as much.
    longs, shorts = (margin_position(r.state, NO_RATES, 4.0) for r in (long_book, short_book))
    assert longs is not None and longs.initial == D("2000.00")
    assert shorts is not None and shorts.initial == D("0")


def test_a_working_order_counts_in_the_models_projection() -> None:
    poster = margin.Poster(D("300"), D("300"))  # a resting bid for 300, then 300 at market
    result = run({}, flat(A), A, model=one_sided(A, move="-0.30"), strategy=poster)
    # The bid alone loses 9,000 in a 30% fall, within 10,000, and rests below
    # the market. The market order beside it is judged on the 600 the two would
    # make -- 18,000 -- and refused; on its own it would have fitted.
    assert margin.fills(result, A) == []
    (order,) = result.state.oms.orders.orders()
    assert order.limit_price == D("90")
    assert sum("margin model" in r for r in poster.rejections) == 1


def test_a_long_holdings_haircut_counts_against_equity_in_the_projection() -> None:
    share = InstrumentRecord(
        "HCUT",
        AssetType.EQUITY,
        "XNAS",
        "USD",
        margin=MarginTerms(
            MarginBasis.VALUE_RATE, D("0.5"), D("0.4"), haircut=D("0.5"), purchase_on_margin=True
        ),
    )
    trader = margin.Trader({(2.0, share.asset_id): D("150")})
    result = run({}, flat(share), share, model=one_sided(share), strategy=trader)
    # 15,000 of it on margin leaves 10,000 of net asset value, 7,500 of it
    # haircut away: 2,500 of equity against the 3,000 a fall of 20% costs.
    assert margin.fills(result, share) == []
    assert any("margin model" in r for r in trader.rejections)


def test_a_deep_shortfall_is_restored_over_more_than_one_pass() -> None:
    severe = {
        A.asset_id: margin.series("100", "100", "92", "92", "92"),
        B.asset_id: margin.series("100", "100", "108", "108", "108"),
    }
    result = run(BIG, severe, A, B, model=scenarios())
    assert margin.calls(result)
    after = margin_position(result.state, NO_RATES, 9.0)
    assert after is not None and not after.in_breach
