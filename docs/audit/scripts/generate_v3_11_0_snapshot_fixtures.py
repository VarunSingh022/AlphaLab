"""Write the v3.11.0 golden snapshot payloads that v3.12's upgrade tests read.

This script targets the **v3.11.0 API** and is kept for provenance, not run by
the suite: the payloads it writes are frozen in
``tests/fixtures/snapshots/v3.11.0`` and every later build must keep reading
them (or refuse them for the documented reason). Re-running it requires a
checkout of the v3.11.0 tag::

    git archive v3.11.0 | tar -x -C /tmp/alphalab-v3.11.0
    cd /tmp/alphalab-v3.11.0 && PYTHONPATH=. python3.12 \\
        <repo>/docs/audit/scripts/generate_v3_11_0_snapshot_fixtures.py \\
        <repo>/tests/fixtures/snapshots/v3.11.0

Every payload is the exact text ``alphalab.persistence.serialize`` wrote at
v3.11.0, so a fixture is evidence of what the release put on disk rather than a
hand-written approximation of it. v3.12 moved four of these subsystems --
pipeline 5 -> 6, run 3 -> 4, allocation 2 -> 3, instrument 2 -> 3 -- and left
portfolio 5, OMS 2, lifecycle 2, FX feed 1, broker 2 and live 2 where they were.
"""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.common.ids import id_scope
from alphalab.core.enums import AssetType
from alphalab.instrument.record import DatedAlias, InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.persistence import FileRunStateStore, serialize
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.fx import FxRate, FxRates

ASSET = str(uuid.UUID(int=0x3110_0001))


def _write(root: Path, name: str, payload: str) -> None:
    (root / name).write_text(payload + "\n", encoding="utf-8")
    print("wrote", name, len(payload), "bytes")


def _portfolio() -> PortfolioState:
    account = Account("ACC-GOLDEN", "USD", "Golden v3.11.0 account", 1.0)
    state = PortfolioState(account=account)
    state = PortfolioEngine.apply_deposit(state, Decimal("1000000.00"), "USD", 1.0)
    state = PortfolioEngine.apply_deposit(state, Decimal("5000000"), "JPY", 1.0)
    rates = FxRates.of([FxRate("USD", "EUR", Decimal("0.925926"), 1.0, "ECB")])
    state, _ = PortfolioEngine.convert_cash(state, Decimal("110000.00"), "USD", "EUR", rates, 1.5)
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("10"), Decimal("190.1234"), Decimal("1.00"), 2.0, "USD"
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("-6"), Decimal("195.25"), Decimal("0.60"), 4.0, "USD"
    )
    state = PortfolioEngine.apply_fill(
        state, "SAP", Decimal("-8"), Decimal("120.55"), Decimal("0.80"), 5.0, "EUR"
    )
    state = PortfolioEngine.apply_fill(
        state, "7203", Decimal("100"), Decimal("2500"), Decimal("150"), 8.0, "JPY"
    )
    return PortfolioEngine.update_market_prices(
        state, {"AAPL": Decimal("196.00"), "SAP": Decimal("119.00"), "7203": Decimal("2510")}, 11.0
    )


def _context_factory(strategy_id: str) -> Any:
    from tests.integration.harness import context_factory

    return context_factory(strategy_id)


def _quote_run() -> Any:
    from alphalab.backtesting.engine import BacktestEngine
    from tests.integration.harness import scripted_run

    config, dataset, strategies = scripted_run(
        {2.0: Decimal("10"), 4.0: Decimal("-4"), 6.0: Decimal("-10")},
        [Decimal(str(value)) for value in ("100", "101.5", "99.25", "102", "103.75", "101")],
        "GOLDEN-STRAT",
        ASSET,
    )
    return BacktestEngine.run(config, dataset, strategies, _context_factory).run


def _tick_run() -> Any:
    """A run whose market history holds trade prints, which v3.11 recorded with no aggressor."""

    from alphalab.backtesting.dataset import MarketDataset
    from alphalab.backtesting.engine import BacktestEngine
    from alphalab.market.tick import Tick
    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        running_strategy_state,
        sized_quote,
    )

    inputs: list[Any] = []
    for index in range(4):
        timestamp = 2.0 + index
        inputs.append(sized_quote(ASSET, timestamp, Decimal("100") + index, Decimal("100")))
        inputs.append(
            Tick(ASSET, timestamp, Decimal("100.5") + index, Decimal("7"), f"P-{index}", "X", "USD")
        )
    dataset = MarketDataset.of("TICKS", inputs)
    strategy = ScriptedStrategy("TICK-STRAT", ASSET, {3.0: Decimal("5")})
    return BacktestEngine.run(
        backtest_config("TICK-STRAT"),
        dataset,
        running_strategy_state("TICK-STRAT", strategy),
        _context_factory,
    ).run


def _allocation_in_euros() -> Any:
    """A v3.11 allocation whose budget names its currency -- which v3.11 then never read."""

    from alphalab.allocation.budget import CapitalBudget
    from alphalab.allocation.constraints import AllocationConstraints
    from alphalab.allocation.engine import AllocationEngine
    from alphalab.allocation.sizing import FixedQuantitySizing
    from alphalab.strategy.events import Intent

    state = AllocationEngine.initialize(
        CapitalBudget(
            global_capital=Decimal("1000000"),
            maximum_exposure=Decimal("2000000"),
            cash_buffer=Decimal("500"),
            strategy_budgets={"S-EUR": Decimal("400000")},
            currency="EUR",
        )
    )
    state, _ = AllocationEngine.allocate(
        state,
        (Intent(strategy_id="S-EUR", instrument=ASSET, target=Decimal("25"), timestamp=2.0),),
        {ASSET: Decimal("100.00")},
        FixedQuantitySizing(),
        AllocationConstraints(allow_shorting=True, enforce_integer_quantities=False),
        2.0,
    )
    return state


def main(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)

    from alphalab.portfolio.snapshot import capture as capture_portfolio

    with id_scope(31101):
        _write(root, "portfolio.json", serialize(capture_portfolio(_portfolio())))

    from alphalab.oms.snapshot import capture as capture_oms
    from tests.unit.oms.test_oms_snapshot import _populated

    with id_scope(31102):
        oms_state, _, _ = _populated()
        _write(root, "oms.json", serialize(capture_oms(oms_state)))

    from alphalab.allocation.snapshot import capture as capture_allocation
    from tests.regression.test_allocation_snapshot import _netted_state

    with id_scope(31103):
        _write(root, "allocation.json", serialize(capture_allocation(_netted_state())))
    with id_scope(31104):
        _write(root, "allocation_eur.json", serialize(capture_allocation(_allocation_in_euros())))

    from alphalab.instrument.snapshot import capture as capture_instruments

    registry = register_instruments(
        InstrumentRegistry(),
        [
            InstrumentRecord(
                "ACME",
                AssetType.EQUITY,
                "XNAS",
                "USD",
                sector="Technology",
                aliases={"iex": "A.N"},
                dated_aliases=(DatedAlias("vendor", "ACME.OLD", None, 1_600_000_000.0),),
            ),
            InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR", sector="Technology"),
            InstrumentRecord("7203", AssetType.EQUITY, "XTKS", "JPY"),
        ],
    )
    _write(root, "instrument.json", serialize(capture_instruments(registry)))

    from alphalab.lifecycle.snapshot import capture as capture_lifecycle
    from tests.unit.lifecycle.test_lifecycle_snapshot import _state as lifecycle_state

    _write(root, "lifecycle.json", serialize(capture_lifecycle(lifecycle_state())))

    from alphalab.portfolio.fx_feed import FxFeed, SequenceFxSource
    from alphalab.portfolio.fx_feed import capture as capture_feed

    feed, _ = FxFeed.drain(
        FxFeed.initialize("ECB-FEED", max_age_seconds=3600.0),
        SequenceFxSource.of(
            "ECB",
            [
                FxRate("EUR", "USD", Decimal("1.08"), 1.0, "ECB"),
                FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB"),
            ],
        ),
    )
    _write(root, "fx_feed.json", serialize(capture_feed(feed)))

    from alphalab.runtime.run_snapshot import capture as capture_run

    with id_scope(31105):
        run_payload = serialize(capture_run(_quote_run()))
    _write(root, "run_backtest.json", run_payload)
    with id_scope(31106):
        _write(root, "run_ticks.json", serialize(capture_run(_tick_run())))

    store_root = root / "run_store"
    store_root.mkdir(exist_ok=True)
    FileRunStateStore(store_root).put("golden-run", 1, run_payload)

    from alphalab.broker.snapshot import capture as capture_broker
    from alphalab.runtime.live import LiveSession
    from alphalab.runtime.live_snapshot import capture as capture_live
    from tests.integration.harness import dataset_of_quotes
    from tests.integration.test_live_session import _SYMBOL, _broker, _credentials, _started
    from tests.integration.venue_server import run_venue

    with id_scope(31107), run_venue(_credentials()) as (base_url, _book, _script):
        live = _started(base_url)
        broker = _broker(base_url)
        for record in dataset_of_quotes(_SYMBOL, [Decimal("100"), Decimal("100.5")]).records:
            live, _ = LiveSession.advance(live, record, _context_factory, broker)
        _write(root, "live.json", serialize(capture_live(live)))
        _write(
            root,
            "broker.json",
            serialize(capture_broker(live.broker, live.mapping, live.reconciliation)),
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
