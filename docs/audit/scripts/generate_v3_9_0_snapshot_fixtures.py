"""Write the v3.9.0 golden snapshot payloads that v3.10's upgrade tests read.

This script targets the **v3.9.0 API** and is kept for provenance, not run by the
suite: the payloads it writes are frozen in ``tests/fixtures/snapshots/v3.9.0``
and every later build must keep reading them (or refuse them for the documented
reason). Re-running it requires a checkout of the v3.9.0 tag::

    git archive v3.9.0 | tar -x -C /tmp/alphalab-v3.9.0
    PYTHONPATH=/tmp/alphalab-v3.9.0 python3.12 \
        docs/audit/scripts/generate_v3_9_0_snapshot_fixtures.py tests/fixtures/snapshots/v3.9.0

Every payload is the exact text ``alphalab.persistence.serialize`` wrote at
v3.9.0, so a fixture is evidence of what the release put on disk rather than a
hand-written approximation of it.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.common.ids import id_scope
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.persistence import FileRunStateStore, serialize
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.fx import FxRate, FxRates


def _write(root: Path, name: str, payload: str) -> None:
    (root / name).write_text(payload + "\n", encoding="utf-8")
    print("wrote", name, len(payload), "bytes")


def _portfolio(fractional_yen: bool) -> PortfolioState:
    account = Account("ACC-GOLDEN", "USD", "Golden v3.9.0 account", 1.0)
    state = PortfolioState(account=account)
    state = PortfolioEngine.apply_deposit(state, Decimal("1000000.00"), "USD", 1.0)
    state = PortfolioEngine.apply_deposit(state, Decimal("5000000"), "JPY", 1.0)
    state = PortfolioEngine.apply_deposit(state, Decimal("50000.00"), "USDT", 1.0)
    rates = FxRates.of([FxRate("USD", "EUR", Decimal("0.925926"), 1.0, "ECB")])
    state, _ = PortfolioEngine.convert_cash(state, Decimal("110000.00"), "USD", "EUR", rates, 1.5)
    state = PortfolioEngine.apply_withdrawal(state, Decimal("250.00"), "USD", 1.6)
    # USD long, increased, partially reduced; the mark moves once.
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("10"), Decimal("190.1234"), Decimal("1.00"), 2.0, "USD"
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("5"), Decimal("191.50"), Decimal("0.50"), 3.0, "USD"
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("-6"), Decimal("195.25"), Decimal("0.60"), 4.0, "USD"
    )
    # EUR short, partially covered.
    state = PortfolioEngine.apply_fill(
        state, "SAP", Decimal("-8"), Decimal("120.55"), Decimal("0.80"), 5.0, "EUR"
    )
    state = PortfolioEngine.apply_fill(
        state, "SAP", Decimal("3"), Decimal("118.10"), Decimal("0.30"), 6.0, "EUR"
    )
    # A settlement asset outside ISO 4217, booked at 0.01 by v3.9.0.
    state = PortfolioEngine.apply_fill(
        state, "BTCUSDT", Decimal("0.5"), Decimal("60000.12"), Decimal("3.00"), 7.0, "USDT"
    )
    # JPY: whole yen throughout, unless the fractional variant is asked for.
    commission = Decimal("1.50") if fractional_yen else Decimal("150")
    state = PortfolioEngine.apply_fill(
        state, "7203", Decimal("100"), Decimal("2500"), commission, 8.0, "JPY"
    )
    # A round trip that closes, so a PositionClosed event is on the log.
    state = PortfolioEngine.apply_fill(
        state, "MSFT", Decimal("4"), Decimal("410.00"), Decimal("0"), 9.0, "USD"
    )
    state = PortfolioEngine.apply_fill(
        state, "MSFT", Decimal("-4"), Decimal("412.00"), Decimal("0"), 10.0, "USD"
    )
    return PortfolioEngine.update_market_prices(
        state,
        {
            "AAPL": Decimal("196.00"),
            "SAP": Decimal("119.00"),
            "BTCUSDT": Decimal("61000.00"),
            "7203": Decimal("2510"),
        },
        11.0,
    )


def _run_state() -> Any:
    from alphalab.backtesting.engine import BacktestEngine
    from tests.integration.harness import scripted_run

    config, dataset, strategies = scripted_run(
        {2.0: Decimal("10"), 4.0: Decimal("-4"), 6.0: Decimal("-10")},
        [Decimal(str(value)) for value in ("100", "101.5", "99.25", "102", "103.75", "101")],
        "GOLDEN-STRAT",
        str(uuid.UUID(int=0x3900_0001)),
    )
    result = BacktestEngine.run(config, dataset, strategies, _context_factory)
    return result.run


def _context_factory(strategy_id: str) -> Any:
    from tests.integration.harness import context_factory

    return context_factory(strategy_id)


def main(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)

    from alphalab.portfolio.snapshot import capture as capture_portfolio

    with id_scope(39001):
        _write(root, "portfolio.json", serialize(capture_portfolio(_portfolio(False))))
    with id_scope(39002):
        _write(
            root,
            "portfolio_fractional_yen.json",
            serialize(capture_portfolio(_portfolio(True))),
        )

    from alphalab.oms.snapshot import capture as capture_oms
    from tests.unit.oms.test_oms_snapshot import _populated

    with id_scope(39003):
        oms_state, _, _ = _populated()
        _write(root, "oms.json", serialize(capture_oms(oms_state)))

    from alphalab.allocation.snapshot import capture as capture_allocation
    from tests.regression.test_allocation_snapshot import _netted_state

    with id_scope(39004):
        _write(root, "allocation.json", serialize(capture_allocation(_netted_state())))

    from alphalab.instrument.snapshot import capture as capture_instruments

    registry = register_instruments(
        InstrumentRegistry(),
        [
            InstrumentRecord(
                "ACME", AssetType.EQUITY, "XNAS", "USD", sector="Technology", aliases={"iex": "A.N"}
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
                FxRate("EUR", "USD", Decimal("1.02"), 1.5, "ECB"),
                FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB"),
            ],
        ),
    )
    _write(root, "fx_feed.json", serialize(capture_feed(feed)))

    from alphalab.runtime.run_snapshot import capture as capture_run

    with id_scope(39005):
        run_state = _run_state()
    run_payload = serialize(capture_run(run_state))
    _write(root, "run_backtest.json", run_payload)

    # The on-disk envelope FileRunStateStore wrote at v3.9.0, digest included.
    store_root = root / "run_store"
    store_root.mkdir(exist_ok=True)
    store = FileRunStateStore(store_root)
    store.put("golden-run", 1, run_payload)

    from alphalab.broker.snapshot import capture as capture_broker
    from alphalab.runtime.live import LiveSession
    from alphalab.runtime.live_snapshot import capture as capture_live
    from tests.integration.harness import dataset_of_quotes
    from tests.integration.test_live_session import _SYMBOL, _broker, _credentials, _started
    from tests.integration.venue_server import run_venue

    with id_scope(39006), run_venue(_credentials()) as (base_url, _book, _script):
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
    _ = replace


if __name__ == "__main__":
    main(Path(sys.argv[1]))
