"""A strategy's context returns the bars the market holds (v4.1.1).

:class:`~alphalab.market.engine.MarketEngine` keeps the latest bar of an asset
per *interval*, keyed ``"<asset_id>_<interval code>"``.
:meth:`MarketView.bar <alphalab.runtime.context_views.MarketView.bar>` --
``context.market.bar(asset_id)``, on the documented
:class:`~alphalab.strategy.context.MarketViewProtocol` -- looked the bare
``asset_id`` up in that index from v2.10 until v4.1.1, which no key equals, so
it answered ``None`` for every asset in every run and no test read one. Found
writing the v4.1.1 acceptance scenario, whose hedge sizes a futures position
from the future's last settlement and so never hedged.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from alphalab.backtesting import BacktestEngine
from alphalab.backtesting.dataset import MarketDataset
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.engine import MarketEngine
from alphalab.market.state import MarketState
from alphalab.runtime.context_views import MarketView
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import backtest_config, context_factory, running_strategy_state

ASSET = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
OTHER = "0b7c3a52-3e43-4d0e-8f6c-1a9e5d2c7b10"


def _bar(asset: str, at: float, close: str, timeframe: TimeFrame = TimeFrame.D1) -> Bar:
    price = Decimal(close)
    return Bar(asset, at, price, price, price, price, Decimal("1000"), None, None, timeframe)


def _market(*bars: Bar) -> MarketState:
    state = MarketEngine.reset()
    for bar in bars:
        state = MarketEngine.publish_bar(state, bar)
    return state


def test_the_view_returns_the_bar_the_market_engine_published() -> None:
    bar = _bar(ASSET, 10.0, "101")
    view = MarketView(_market(bar))

    assert view.bar(ASSET) == bar
    assert view.bar(OTHER) is None


def test_an_asset_never_barred_has_no_bar_even_when_its_id_prefixes_anothers() -> None:
    view = MarketView(_market(_bar(ASSET, 10.0, "101")))

    assert view.bar(ASSET[:8]) is None
    assert view.bar("") is None


def test_of_several_intervals_the_latest_bar_is_returned() -> None:
    hourly = _bar(ASSET, 20.0, "102", TimeFrame.H1)
    daily = _bar(ASSET, 10.0, "101", TimeFrame.D1)

    assert MarketView(_market(daily, hourly)).bar(ASSET) == hourly
    assert MarketView(_market(hourly, daily)).bar(ASSET) == hourly, "not publication order"


def test_two_bars_closing_at_one_instant_give_the_longer_interval() -> None:
    hourly = _bar(ASSET, 30.0, "103", TimeFrame.H1)
    daily = _bar(ASSET, 30.0, "104", TimeFrame.D1)
    monthly = _bar(ASSET, 30.0, "105", TimeFrame.MN1)

    assert MarketView(_market(hourly, daily)).bar(ASSET) == daily
    assert MarketView(_market(monthly, daily, hourly)).bar(ASSET) == monthly


class _Reader(BaseStrategy):
    def __init__(self) -> None:
        self.read: list[Any] = []

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.read.append(context.market.bar(event.bar.asset_id))
        return ()


def test_a_strategy_reads_the_bar_it_is_dispatched_on_through_its_context() -> None:
    bars = [_bar(ASSET, 10.0 + day, str(100 + day)) for day in range(3)]
    strategy = _Reader()

    BacktestEngine.run(
        backtest_config("A"),
        MarketDataset.of("BARS", bars),
        running_strategy_state("A", strategy, frozenset({"bars"})),
        context_factory,
    )

    assert strategy.read == bars
