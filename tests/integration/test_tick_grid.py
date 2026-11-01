"""A venue's price grid on the execution path (v4.2.0, the phase 2 global gap audit).

An instrument declares the grid its venue quotes on (``InstrumentRecord.tick``,
a :class:`~alphalab.conventions.tick.TickSchedule`, tiered or flat). An order
whose limit or stop lies between two ticks is refused at placement, naming the
step, as the venue would refuse it; a market order is priced by the market and
is never refused for the grid. A band that caps a limit lands it on the grid
inside the band. A run declaring no grid accepts any price, as before.

ACME quotes from 2.0, one quote a second; the strategy's orders are scripted
(the helpers are tests/integration/test_trading_controls.py's).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import PausedReplay, ReplayBacktest
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.conventions.exceptions import ConventionInputError
from alphalab.conventions.tick import (
    TickBand,
    TickSchedule,
    tick_from_primitives,
    tick_to_primitives,
)
from alphalab.instrument.controls import BandAction, PriceBand, TradingControls
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.events import Intent
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_trading_controls as controls
from tests.integration.harness import context_factory, registry_of, running_strategy_state

D = Decimal
#: Below 10 in steps of 0.005, from 10 in steps of 0.05: a tiered cash-market grid.
TIERED = TickSchedule((TickBand(D("10"), D("0.005")), TickBand(None, D("0.05"))))
CENTS = TickSchedule.flat(D("0.01"))
ACME = replace(controls.ACME, tick=CENTS)
LOW = InstrumentRecord("LOW", controls.ACME.asset_type, "XSHG", "CNY", tick=TIERED)


def limit(price: str, tif: TimeInForce = TimeInForce.GTC) -> OrderTerms:
    return OrderTerms.limit(D(price), tif)


def placed(result: Any) -> list[Decimal]:
    return [order.limit_price for order in result.state.oms.orders.orders() if order.limit_price]


@pytest.mark.parametrize(
    ("price", "accepted"),
    [("100.01", True), ("100", True), ("100.015", False), ("99.999", False)],
)
def test_a_limit_off_a_flat_grid_is_refused_naming_the_step(price: str, accepted: bool) -> None:
    result, strategy = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0, terms=limit(price)),)},
        {ACME.asset_id: controls.flat(2.0, 3.0)},
        None,
        ACME,
    )
    assert (placed(result) == [D(price)]) is accepted
    if accepted:
        assert not strategy.rejections
    else:
        (reason,) = strategy.rejections
        assert f"The limit {price} is not on ACME's price grid" in reason
        assert "steps of 0.01" in reason


@pytest.mark.parametrize(
    ("price", "step", "accepted"),
    [
        ("9.995", "0.005", True),
        ("9.997", "0.005", False),
        ("10.05", "0.05", True),
        ("10.01", "0.05", False),  # on the finer band's grid, not on this one's
    ],
)
def test_a_tiered_grid_judges_each_price_by_its_own_band(
    price: str, step: str, accepted: bool
) -> None:
    result, strategy = controls.run(
        {(2.0, LOW.asset_id): (controls.buy(2.0, terms=limit(price), asset=LOW),)},
        {LOW.asset_id: controls.flat(2.0, 3.0, mid="10")},
        None,
        LOW,
    )
    assert (placed(result) == [D(price)]) is accepted
    assert any(f"steps of {step}" in r for r in strategy.rejections) is not accepted


def test_a_stop_off_the_grid_is_refused_and_one_on_it_triggers() -> None:
    off = OrderTerms.stop(D("101.005"), TimeInForce.GTC)
    on = OrderTerms.stop(D("101.00"), TimeInForce.GTC)
    mids = {ACME.asset_id: {2.0: "100", 3.0: "102", 4.0: "102"}}
    result, strategy = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0, terms=off),)}, mids, None, ACME
    )
    assert controls.trades(result) == []
    (reason,) = strategy.rejections
    assert "The stop 101.005 is not on ACME's price grid" in reason
    result, strategy = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0, terms=on),)}, mids, None, ACME
    )
    assert [t[0] for t in controls.trades(result)] == [3.0]
    assert not strategy.rejections


def test_a_market_order_is_never_refused_for_the_grid() -> None:
    # The market prints off the declared grid; a market order takes it.
    mids = {ACME.asset_id: {2.0: "100.003", 3.0: "100.003"}}
    result, strategy = controls.run({(2.0, ACME.asset_id): (controls.buy(2.0),)}, mids, None, ACME)
    assert [t[0] for t in controls.trades(result)] == [2.0]
    assert not strategy.rejections


def test_a_negative_limit_is_refused_for_a_grid_that_covers_none() -> None:
    result, strategy = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("-1.00")),)},
        {ACME.asset_id: controls.flat(2.0, 3.0)},
        None,
        ACME,
    )
    assert placed(result) == []
    (reason,) = strategy.rejections
    assert "covers no negative price" in reason


def test_a_band_capping_a_limit_lands_it_on_the_grid_inside_the_band() -> None:
    # A band whose edges lie between the ticks: limit up 110.037, limit down 89.983 --
    # each nearer the tick outside the band than the one inside it.
    banded = TradingControls(
        bands=(PriceBand(ACME.asset_id, 1.0, None, D("89.983"), D("110.037"), BandAction.CAP),)
    )
    sell = Intent("S", ACME.asset_id, D("-10"), timestamp=2.0, terms=limit("80"))
    result, strategy = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("115")), sell)},
        {ACME.asset_id: controls.flat(2.0, 3.0)},
        banded,
        ACME,
    )
    # The buy rounds down to 110.03 and the sell up to 89.99: inside the band,
    # on the grid -- neither crosses further than the venue allows.
    assert sorted(placed(result)) == [D("89.99"), D("110.03")]
    assert not strategy.rejections


def test_a_capped_limit_with_no_grid_is_the_bands_edge_exactly() -> None:
    banded = TradingControls(
        bands=(PriceBand(ACME.asset_id, 1.0, None, D("89.987"), D("110.033"), BandAction.CAP),)
    )
    result, _ = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("115")),)},
        {ACME.asset_id: controls.flat(2.0, 3.0)},
        banded,
        controls.ACME,
    )
    assert placed(result) == [D("110.033")]


def test_a_run_declaring_no_grid_accepts_any_price_and_an_honoured_grid_moves_nothing() -> None:
    script = {(2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("100.005")),)}
    mids = {ACME.asset_id: controls.flat(2.0, 3.0)}
    free, strategy = controls.run(script, mids, None, controls.ACME)
    assert placed(free) == [D("100.005")] and not strategy.rejections
    # On the grid, the same orders as a run declaring none: the same result.
    on_grid = {(2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("100.01")),)}
    declared, _ = controls.run(on_grid, mids, None, ACME)
    undeclared, _ = controls.run(on_grid, mids, None, controls.ACME)
    assert digest_run(declared).result_id == digest_run(undeclared).result_id


def test_a_refusal_for_the_grid_replays_and_resumes_identically() -> None:
    script = {
        (2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("100.015")),),
        (3.0, ACME.asset_id): (controls.buy(3.0, terms=limit("100.02")),),
    }
    mids = {ACME.asset_id: controls.flat(2.0, 3.0, 4.0)}
    result, _ = controls.run(script, mids, None, ACME)
    cfg, dataset = controls.config(ACME), controls.data(mids)
    whole = ReplayBacktest.run(
        cfg, dataset, running_strategy_state("S", controls.Script(script)), context_factory
    )
    assert placed(whole.backtest) == placed(result) == [D("100.02")]
    assert whole.backtest.fills == result.fills
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg,
            dataset,
            running_strategy_state("S", controls.Script(script)),
            context_factory,
            pause_when=margin._at(boundary),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": controls.Script(script)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, dataset)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary


def test_a_grid_round_trips_and_the_registry_snapshot_keeps_it() -> None:
    assert tick_from_primitives(tick_to_primitives(TIERED)) == TIERED
    assert tick_to_primitives(None) is None and tick_from_primitives(None) is None
    registry = registry_of(ACME, LOW, controls.PEER)
    payload = deserialize(serialize(capture_registry(registry)))
    restored = restore_registry(registry_from_primitives(payload))
    for record in (ACME, LOW, controls.PEER):
        assert restored.record_for(record.asset_id) == record
    by_symbol = {entry["symbol"]: entry for entry in payload["instruments"]}
    assert by_symbol["PEER"]["tick"] is None
    assert by_symbol["ACME"]["tick"] == {"bands": [{"upper_bound": None, "tick_size": "0.01"}]}


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"bands": "0.01"},
        {"bands": [], "extra": 1},
        {"bands": [{"tick_size": "0.01"}]},
        {"bands": [{"upper_bound": None, "tick_size": "a cent"}]},
        {"bands": [{"upper_bound": None, "tick_size": "0"}]},
        {"bands": [{"upper_bound": "10", "tick_size": "0.01"}]},
    ],
)
def test_a_grid_that_is_not_one_is_refused(value: Any) -> None:
    with pytest.raises(ConventionInputError):
        tick_from_primitives(value)


def test_a_snapshot_holding_a_malformed_grid_is_refused_as_undecodable() -> None:
    payload = deserialize(serialize(capture_registry(registry_of(ACME))))
    payload["instruments"][0]["tick"] = {"bands": [{"upper_bound": None, "tick_size": "-1"}]}
    with pytest.raises(StateDecodeError, match="tick"):
        restore_registry(registry_from_primitives(payload))


def test_a_record_refuses_a_grid_that_is_not_a_tick_schedule() -> None:
    with pytest.raises(InstrumentInputError, match="TickSchedule"):
        replace(controls.ACME, tick=D("0.01"))  # type: ignore[arg-type]
