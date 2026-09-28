"""A backtest's cost grows with its records, not with its universe (ledger PRF-001, TST-006).

Until v3.10 every market event re-marked and logged every held position, and the
valuation, the net asset value and the risk exposure each summed every position
again -- so a record cost more the more assets the book held, and a backtest's
cost grew with the square of its universe. The audit measured 160 assets for 50
bars at 55 CPU-seconds and 337 MB, 1.27 million logged marks; extrapolated, 500
stocks over ten years of daily bars was hours. Every "linear" claim the suite
made until then was measured with **one** asset, which is why nothing caught it.

Now the book keeps its totals as it changes (:mod:`alphalab.portfolio.book`), an
event re-marks only the asset it priced and the positions a fill priced since
(:meth:`~alphalab.portfolio.engine.PortfolioEngine.mark_changed`), and the risk
exposure reads the book instead of walking it.

The structural tests are the guard: they are deterministic and state the
property the fix rests on. The timing test is the backstop, read with the one
stabilized method every complexity guard shares (``tests/regression/_timing.py``).
"""

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from alphalab.backtesting import BacktestEngine, MarketDataset
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.events import MarketValueUpdated
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    backtest_config,
    context_factory,
    running_strategy_state,
    sized_quote,
)
from tests.regression._timing import CLOCK, timings

STRATEGY_ID = str(UUID(int=0x5CA1E))
#: A 4x universe. Linear predicts ~4x the time; the quadratic this removed
#: predicted ~16x, and measured more. The bound sits between them.
SMALL_UNIVERSE, LARGE_UNIVERSE = 16, 64
MAX_GROWTH = 8.0
INSTANTS = 12


def _assets(count: int) -> list[str]:
    return [str(UUID(int=index + 1, version=4)) for index in range(count)]


class _BuyEachThenHold(BaseStrategy):
    """Buys one unit of every asset at its first quote, then holds."""

    def __init__(self, strategy_id: str, first: float) -> None:
        self._strategy_id = strategy_id
        self._first = first

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quote = event.quote
        if quote.timestamp != self._first:
            return ()
        return (Intent(self._strategy_id, quote.asset_id, Decimal("1"), timestamp=quote.timestamp),)


def _run(universe: int, instants: int = INSTANTS) -> Any:
    assets = _assets(universe)
    records = [
        sized_quote(
            asset,
            2.0 + step,
            Decimal(100 + (step * (index + 3)) % 11 + index % 5),
            Decimal("1000"),
        )
        for step in range(instants)
        for index, asset in enumerate(assets)
    ]
    return BacktestEngine.run(
        backtest_config(STRATEGY_ID, compile_analytics=False),
        MarketDataset.of("UNIVERSE", records),
        running_strategy_state(STRATEGY_ID, _BuyEachThenHold(STRATEGY_ID, 2.0)),
        context_factory,
    )


# --------------------------------------------------------------------------- #
# Structural: the property the fix rests on
# --------------------------------------------------------------------------- #


def test_an_event_logs_the_marks_it_changed_not_the_whole_book() -> None:
    result = _run(24, instants=4)
    marks = [e for e in result.state.portfolio.events if isinstance(e, MarketValueUpdated)]

    assert len(result.state.portfolio.positions) == 24
    # An event re-marks the asset it priced and the positions a fill priced
    # since the last mark -- here, the one bought at the previous event. Every
    # event used to log all 24 held positions.
    assert marks
    assert max(len(event.prices) for event in marks) <= 2
    assert sum(len(event.prices) for event in marks) <= 2 * len(marks)


def test_a_position_keeps_the_instant_its_own_price_was_observed() -> None:
    result = _run(6, instants=3)
    positions = result.state.portfolio.positions

    # Every asset was last quoted at t=4.0, the third instant, so every mark
    # says 4.0 -- and it says so because its own quote said so, not because
    # the last asset in the dataset ticked then.
    assert {position.last_updated for position in positions.values()} == {4.0}


def test_marking_one_change_equals_marking_every_price() -> None:
    """``mark_changed`` is ``update_market_prices`` whenever one price moved."""

    account = Account("ACC", "USD", "Book", 0.0)
    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=account), Decimal("1e7"), "USD", 0.0
    )
    assets = _assets(8)
    prices: dict[str, Decimal] = {}
    incremental = full = state
    for step in range(60):
        asset = assets[(step * 5) % len(assets)]
        prices[asset] = Decimal(90 + (step * 7) % 23) + Decimal(step % 4) / 4
        incremental = PortfolioEngine.mark_changed(incremental, prices, float(step), asset)
        full = PortfolioEngine.update_market_prices(full, prices, float(step))
        if step % 3 == 0:
            quantity = Decimal(1 + step % 5) * (1 if step % 2 else -1)
            incremental = PortfolioEngine.apply_fill(
                incremental, asset, quantity, prices[asset] + 1, Decimal("0"), float(step), "USD"
            )
            full = PortfolioEngine.apply_fill(
                full, asset, quantity, prices[asset] + 1, Decimal("0"), float(step), "USD"
            )
        _assert_same_book(incremental, full)


def _assert_same_book(left: PortfolioState, right: PortfolioState) -> None:
    def marks(state: PortfolioState) -> Mapping[str, tuple[Decimal | None, ...]]:
        return {
            asset: (p.quantity, p.market_price, p.market_value, p.unrealized_pnl, p.cost_basis)
            for asset, p in state.positions.items()
        }

    assert marks(left) == marks(right)
    assert left.book.totals("USD") == right.book.totals("USD")
    assert left.cash == right.cash


def test_valuing_a_book_reads_its_totals_and_not_its_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every per-event aggregate costs the same whatever the book holds.

    Counted rather than timed: each position's market value is read once when
    it is booked, and valuing, marking or measuring the exposure of a 200-asset
    book afterwards reads it for nothing but the asset that changed.
    """

    from alphalab.portfolio.nav import NAVCalculator
    from alphalab.portfolio.position import Position
    from alphalab.portfolio.valuation import PortfolioValuation
    from alphalab.runtime.execution_pipeline import _risk_exposure

    account = Account("ACC", "USD", "Book", 0.0)
    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=account), Decimal("1e9"), "USD", 0.0
    )
    assets = _assets(200)
    prices = {asset: Decimal("100") for asset in assets}
    for asset in assets:
        state = PortfolioEngine.apply_fill(
            state, asset, Decimal("3"), Decimal("100"), Decimal("0"), 1.0, "USD"
        )
    state = PortfolioEngine.update_market_prices(state, prices, 1.0)

    reads: list[str] = []
    market_value = Position.market_value
    unrealized = Position.unrealized_pnl

    def counted(original: Any) -> property:
        def read(position: Position) -> Decimal:
            reads.append(position.asset_id)
            return original.__get__(position)  # type: ignore[no-any-return]

        return property(read)

    monkeypatch.setattr(Position, "market_value", counted(market_value))
    monkeypatch.setattr(Position, "unrealized_pnl", counted(unrealized))

    PortfolioValuation.snapshot(state, 2.0, "USD")
    NAVCalculator.calculate(state.cash, state.positions, "USD")
    _risk_exposure(state, None, as_of=2.0)
    assert reads == []

    prices[assets[7]] = Decimal("101")
    PortfolioEngine.mark_changed(state, prices, 2.0, assets[7])
    assert set(reads) == {assets[7]}


def test_valuing_a_mixed_book_converts_totals_not_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A book in two currencies converts each currency's totals, not each position."""

    from alphalab.portfolio.fx import FxRate, FxRates
    from alphalab.portfolio.nav import NAVCalculator
    from alphalab.portfolio.position import Position
    from alphalab.portfolio.valuation import PortfolioValuation
    from alphalab.runtime.execution_pipeline import _risk_exposure

    rates = FxRates.of([FxRate("EUR", "USD", Decimal("1.08"), 0.0, "TEST")])
    account = Account("ACC", "USD", "Book", 0.0)
    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=account), Decimal("1e9"), "USD", 0.0
    )
    state = PortfolioEngine.apply_deposit(state, Decimal("1e9"), "EUR", 0.0)
    for index, asset in enumerate(_assets(200)):
        state = PortfolioEngine.apply_fill(
            state, asset, Decimal("2"), Decimal("40"), Decimal("0"), 1.0, ("USD", "EUR")[index % 2]
        )

    reads: list[str] = []
    market_value = Position.market_value

    def read(position: Position) -> Decimal:
        reads.append(position.asset_id)
        return market_value.__get__(position)  # type: ignore[no-any-return]

    monkeypatch.setattr(Position, "market_value", property(read))

    valuation = PortfolioValuation.snapshot(state, 2.0, "USD", rates)
    NAVCalculator.calculate(state.cash, state.positions, "USD", rates, 2.0)
    exposure = _risk_exposure(state, None, rates, as_of=2.0)
    assert reads == []
    # Cash in EUR, then EUR longs and EUR unrealized P&L: three conversions for
    # a hundred EUR positions.
    assert len(valuation.conversions) == 3
    assert exposure.asset_exposure[_assets(200)[1]] == Decimal("86.40")  # 2 x 40 EUR at 1.08


def test_an_event_copies_neither_the_price_map_nor_the_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting one price, and reading the exposure, walk no whole map.

    Counted: a copy of a persistent map iterates it, so an event that copied
    the price map, or an exposure that copied the book's market values, would
    show here as a walk -- each is an O(universe) term per event too cheap for
    the timing backstop to see at the sizes it runs.
    """

    from alphalab.common.persistent_map import PersistentMap
    from alphalab.runtime.execution_pipeline import _market_prices_with_event, _risk_exposure

    account = Account("ACC", "USD", "Book", 0.0)
    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=account), Decimal("1e9"), "USD", 0.0
    )
    for asset in _assets(300):
        state = PortfolioEngine.apply_fill(
            state, asset, Decimal("1"), Decimal("50"), Decimal("0"), 1.0, "USD"
        )
    prices = PersistentMap({asset: Decimal("50") for asset in _assets(300)})

    walks: list[str] = []
    iterate = PersistentMap._iter_items
    keys = PersistentMap.__iter__

    def counted_items(self: PersistentMap[Any, Any]) -> Any:
        walks.append("items")
        return iterate(self)

    def counted_keys(self: PersistentMap[Any, Any]) -> Any:
        walks.append("keys")
        return keys(self)

    monkeypatch.setattr(PersistentMap, "_iter_items", counted_items)
    monkeypatch.setattr(PersistentMap, "__iter__", counted_keys)

    updated = _market_prices_with_event(prices, (_assets(300)[3], Decimal("51")))
    exposure = _risk_exposure(state, None, as_of=2.0)
    assert exposure.asset_exposure[_assets(300)[3]] == Decimal("50.00")
    assert walks == []
    assert updated[_assets(300)[3]] == Decimal("51")
    assert prices[_assets(300)[3]] == Decimal("50")


# --------------------------------------------------------------------------- #
# Timing backstop
# --------------------------------------------------------------------------- #


def _measure(universe: int) -> float:
    start = CLOCK()
    _run(universe)
    return CLOCK() - start


def test_a_backtest_costs_in_proportion_to_its_records_whatever_the_universe() -> None:
    _run(4, instants=2)  # warm up imports and first-call costs

    small, large = timings(_measure, SMALL_UNIVERSE, LARGE_UNIVERSE, rounds=3)
    growth = large / max(small, 1e-6)

    assert growth < MAX_GROWTH, (
        f"{LARGE_UNIVERSE // SMALL_UNIVERSE}x the universe, and so the records, cost "
        f"{growth:.1f}x the time ({small:.3f}s -> {large:.3f}s); a record's cost is "
        "growing with the size of the book again"
    )
