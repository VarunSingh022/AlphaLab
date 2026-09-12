"""The two context surfaces ADR-0026 deferred, and the guarantees they rest on.

ADR-0026 populated `portfolio`, `orders`, `risk_view` and `market`, and deferred
`history` and `universe` with reasons it recorded precisely:

    `history` | **DEFER** | Requires a clock-bounded accessor whose bound is
    enforced at construction, plus a look-ahead regression suite.
    `universe` | **DEFER** | Requires deciding whether membership is
    configuration, instrument-registry state, or a risk control.

This is that look-ahead regression suite, and the tests that pin the membership
decision. `test_strategy_context_visibility.py` continues to own everything
v2.10 settled and is unchanged apart from the two assertions that pinned the
deferral itself.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.common.ids import id_scope
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import (
    InstrumentRegistry,
    classify_instrument,
    register_instruments,
)
from alphalab.market.record import MarketRecord
from alphalab.runtime.context_views import HistoryView, MarketView, UniverseView
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from alphalab.strategy.context import NoHistory, NoUniverse, StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import context_factory, pipeline_config, sized_quote

MOM = "MOMENTUM"
SEED = 2_015

_ACME = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")
_BETA = InstrumentRecord("BETA", AssetType.EQUITY, "XNAS", "USD")
ASSET = _ACME.asset_id


def _registry() -> InstrumentRegistry:
    registry = register_instruments(InstrumentRegistry(), (_ACME, _BETA))
    return classify_instrument(registry, ASSET, "Technology", "GICS-VENDOR", 1.0)


class Observer(BaseStrategy):
    """Records the context it is handed at every event, and trades on demand."""

    def __init__(self, strategy_id: str, quantity: Decimal | None = None) -> None:
        self._strategy_id = strategy_id
        self._quantity = quantity
        self.seen: list[StrategyContext] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Any:
        self.seen.append(context)
        if self._quantity is None:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=ASSET,
                target=self._quantity,
                timestamp=event.quote.timestamp,
            ),
        )

    @property
    def last(self) -> StrategyContext:
        assert self.seen, "the strategy was never dispatched"
        return self.seen[-1]


def _record(index: int, mid: Decimal, asset: str = ASSET) -> MarketRecord:
    return MarketRecord(
        event_id=f"REC-{index}",
        timestamp=2.0 + index,
        payload=sized_quote(asset, 2.0 + index, mid, Decimal("1000")),
    )


def _records(count: int) -> list[MarketRecord]:
    return [_record(index, Decimal(100 + index)) for index in range(count)]


def _running(*strategies: tuple[str, BaseStrategy]) -> StrategyRuntimeState:
    state = create_runtime()
    for strategy_id, instance in strategies:
        state = register_strategy(state, strategy_id, instance)
        entry = state.strategies[strategy_id]
        entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
        entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"quotes"}), 1.2)
        entry, _ = RuntimeSupervisor.start(entry, 1.3)
        state = replace(state, strategies={**state.strategies, strategy_id: entry})
    return state


#: Distinguishes "the caller did not choose a registry" from "the caller chose
#: to have none", which `None` alone cannot say.
_DEFAULT_REGISTRY: Any = object()


def _drive(
    observer: Observer,
    records: list[MarketRecord] | None = None,
    registry: InstrumentRegistry | None = _DEFAULT_REGISTRY,
) -> None:
    chosen = _registry() if registry is _DEFAULT_REGISTRY else registry
    config = replace(pipeline_config(MOM), instruments=chosen)
    with id_scope(SEED):
        state = ExecutionPipeline.initialize(config, _running((MOM, observer)), 1.0)
        for record in records if records is not None else _records(3):
            state = ExecutionPipeline.process_record(state, record, context_factory).state


# ---------------------------------------------------------------------------
# The deferral is closed
# ---------------------------------------------------------------------------


def test_the_pipeline_supplies_a_real_history_and_universe() -> None:
    observer = Observer(MOM)
    _drive(observer)

    assert isinstance(observer.last.history, HistoryView)
    assert isinstance(observer.last.universe, UniverseView)


def test_a_context_built_outside_a_run_declares_that_it_supplies_neither() -> None:
    """A stated absence, not an empty view pretending to be a real one."""

    context = context_factory(MOM)

    assert isinstance(context.history, NoHistory)
    assert isinstance(context.universe, NoUniverse)
    assert context.history.available is False
    assert context.universe.configured is False
    assert context.history.bars(ASSET) == ()
    assert context.universe.sector(ASSET) is None


# ---------------------------------------------------------------------------
# History: look-ahead safety
# ---------------------------------------------------------------------------


def test_history_stops_at_the_event_being_dispatched() -> None:
    """The bound. A strategy at event N sees N and nothing after it."""

    observer = Observer(MOM)
    _drive(observer, _records(5))

    for index, context in enumerate(observer.seen):
        assert context.history.as_of == 2.0 + index
        quotes = context.history.quotes(ASSET)
        assert all(quote.timestamp <= context.history.as_of for quote in quotes)


def test_history_includes_the_event_being_dispatched() -> None:
    """Inclusive at the bound: a strategy may act on the quote it was handed."""

    observer = Observer(MOM)
    _drive(observer, _records(3))

    first = observer.seen[0]
    assert [q.timestamp for q in first.history.quotes(ASSET)] == [2.0]
    assert first.history.quotes(ASSET)[-1].bid == Decimal("100")


def test_history_grows_by_exactly_one_event_per_dispatch() -> None:
    """No future event is reachable, and no past one is dropped."""

    observer = Observer(MOM)
    _drive(observer, _records(5))

    assert [len(context.history.quotes(ASSET)) for context in observer.seen] == [1, 2, 3, 4, 5]


def test_no_future_price_is_reachable_from_any_context() -> None:
    """The look-ahead property, stated over the whole run at once.

    The prices are strictly increasing, so a context that could see ahead would
    show a price above the one it is being marked at.
    """

    observer = Observer(MOM)
    _drive(observer, _records(6))

    for context in observer.seen:
        market = context.market
        assert isinstance(market, MarketView)
        highest = max(q.bid for q in context.history.quotes(ASSET))
        current = market.price(ASSET)
        assert current is not None
        assert highest <= current, "a later quote leaked into an earlier context"


def test_the_history_bound_is_the_event_time_not_a_wall_clock() -> None:
    """What makes a backtest and a live session see the same window."""

    observer = Observer(MOM)
    _drive(observer, _records(3))

    assert [context.history.as_of for context in observer.seen] == [2.0, 3.0, 4.0]


# ---------------------------------------------------------------------------
# History: what it returns
# ---------------------------------------------------------------------------


def test_history_is_returned_oldest_first() -> None:
    """The order an indicator consumes."""

    observer = Observer(MOM)
    _drive(observer, _records(4))

    timestamps = [quote.timestamp for quote in observer.last.history.quotes(ASSET)]
    assert timestamps == sorted(timestamps)
    assert timestamps == [2.0, 3.0, 4.0, 5.0]


def test_a_limit_keeps_the_most_recent_entries() -> None:
    observer = Observer(MOM)
    _drive(observer, _records(6))

    recent = observer.last.history.quotes(ASSET, limit=2)
    assert [quote.timestamp for quote in recent] == [6.0, 7.0]


def test_a_limit_larger_than_the_history_returns_all_of_it() -> None:
    observer = Observer(MOM)
    _drive(observer, _records(3))

    assert len(observer.last.history.quotes(ASSET, limit=99)) == 3


def test_history_is_scoped_to_the_asset_asked_for() -> None:
    observer = Observer(MOM)
    other = _BETA.asset_id
    _drive(
        observer,
        [
            _record(0, Decimal("100"), ASSET),
            _record(1, Decimal("200"), other),
            _record(2, Decimal("101"), ASSET),
        ],
    )

    assert len(observer.last.history.quotes(ASSET)) == 2
    assert len(observer.last.history.quotes(other)) == 1


def test_history_of_an_asset_never_seen_is_empty_not_an_error() -> None:
    observer = Observer(MOM)
    _drive(observer)

    assert observer.last.history.bars("no-such-asset") == ()
    assert observer.last.history.ticks("no-such-asset") == ()


def test_an_empty_history_is_falsy_and_a_populated_one_is_truthy() -> None:
    observer = Observer(MOM)
    _drive(observer, _records(2))

    assert bool(observer.last.history) is True
    assert len(observer.last.history) == 2


def test_the_market_view_still_exposes_no_history() -> None:
    """ADR-0026 kept these apart, and closing the deferral does not merge them.

    `MarketView` answers "what is the price now"; history answers "what has
    happened". Putting a lookback on the market view is what the v2.10 test
    refused, and it stays refused.
    """

    observer = Observer(MOM)
    _drive(observer)

    assert isinstance(observer.last.market, MarketView)
    assert not hasattr(observer.last.market, "history")
    assert not hasattr(observer.last.market, "quotes")


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------


def test_the_universe_is_the_instrument_registry() -> None:
    """ADR-0026's deferred semantic decision, made and pinned."""

    observer = Observer(MOM)
    _drive(observer)

    universe = observer.last.universe
    assert universe.configured is True
    assert set(universe.assets) == {_ACME.asset_id, _BETA.asset_id}
    assert ASSET in universe
    assert len(universe) == 2


def test_the_universe_answers_what_an_instrument_is() -> None:
    observer = Observer(MOM)
    _drive(observer)

    record = observer.last.universe.instrument(ASSET)
    assert record is not None
    assert record.symbol == "ACME"
    assert record.exchange == "XNAS"
    assert record.currency == "USD"


def test_the_security_master_reaches_the_strategy_boundary() -> None:
    """Cross-capability: a classification made on the registry is readable here."""

    observer = Observer(MOM)
    _drive(observer)

    assert observer.last.universe.sector(ASSET) == "Technology"
    assert observer.last.universe.sector(_BETA.asset_id) is None, "unclassified, honestly"


def test_an_unregistered_asset_is_absent_rather_than_an_error() -> None:
    observer = Observer(MOM)
    _drive(observer)

    universe = observer.last.universe
    assert "not-an-asset" not in universe
    assert universe.instrument("not-an-asset") is None
    assert universe.sector("not-an-asset") is None


def test_a_run_without_a_registry_has_an_unconfigured_universe() -> None:
    """Empty because none was declared -- not an empty one that was."""

    observer = Observer(MOM)
    _drive(observer, registry=None)

    universe = observer.last.universe
    assert universe.configured is False
    assert universe.assets == ()
    assert len(universe) == 0
    assert bool(universe) is False


def test_an_unconfigured_universe_is_not_the_priced_assets() -> None:
    """Two different questions must not share one word.

    The run has priced this asset -- `market.assets` says so -- and still has no
    universe, because no registry was declared. Substituting one for the other
    would make "what may I trade" and "what have I seen a price for" the same
    concept.
    """

    observer = Observer(MOM)
    _drive(observer, registry=None)

    market = observer.last.market
    assert isinstance(market, MarketView)
    assert ASSET in market.assets
    assert ASSET not in observer.last.universe


# ---------------------------------------------------------------------------
# Ownership and immutability
# ---------------------------------------------------------------------------


def test_the_pipeline_overwrites_whatever_the_caller_supplied() -> None:
    """No caller-installable second source of truth, for these two as well."""

    class Fabricated:
        available = True
        configured = True
        as_of = 9_999.0

        def quotes(self, asset_id: str, limit: int | None = None) -> Any:
            return ("a lie",)

        def bars(self, asset_id: str, limit: int | None = None) -> Any:
            return ()

        def ticks(self, asset_id: str, limit: int | None = None) -> Any:
            return ()

        @property
        def assets(self) -> Any:
            return ("fabricated",)

        def __contains__(self, asset_id: object) -> bool:
            return True

        def __iter__(self) -> Any:
            return iter(("fabricated",))

        def __len__(self) -> int:
            return 1

        def instrument(self, asset_id: str) -> Any:
            return None

        def sector(self, asset_id: str) -> str | None:
            return "Fabricated"

    def lying_factory(strategy_id: str) -> StrategyContext:
        return replace(context_factory(strategy_id), history=Fabricated(), universe=Fabricated())

    observer = Observer(MOM)
    config = replace(pipeline_config(MOM), instruments=_registry())
    with id_scope(SEED):
        state = ExecutionPipeline.initialize(config, _running((MOM, observer)), 1.0)
        for record in _records(2):
            state = ExecutionPipeline.process_record(state, record, lying_factory).state

    assert isinstance(observer.last.history, HistoryView)
    assert isinstance(observer.last.universe, UniverseView)
    assert observer.last.history.as_of == 3.0, "not the fabricated 9,999"
    assert observer.last.universe.sector(ASSET) == "Technology", "not 'Fabricated'"


def test_the_views_are_frozen() -> None:
    observer = Observer(MOM)
    _drive(observer)

    with pytest.raises(AttributeError):
        observer.last.history.as_of = 1.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        observer.last.universe._registry = None  # type: ignore[attr-defined]


def test_a_strategy_cannot_reach_authority_through_the_universe() -> None:
    """Read-only: no registration, no classification, no write of any kind."""

    observer = Observer(MOM)
    _drive(observer)

    universe = observer.last.universe
    for forbidden in ("register", "classify", "add", "remove", "set", "update"):
        assert not any(
            name.startswith(forbidden) for name in dir(universe) if not name.startswith("_")
        ), f"the universe exposes a {forbidden}* method"


def test_each_context_is_bounded_at_its_own_event_not_a_shared_one() -> None:
    """No stale hidden state: two contexts from one run do not share a bound."""

    observer = Observer(MOM)
    _drive(observer, _records(4))

    bounds = [context.history.as_of for context in observer.seen]
    assert len(set(bounds)) == 4, "every dispatch got its own bound"


# ---------------------------------------------------------------------------
# Cross-engine parity and continuation
# ---------------------------------------------------------------------------


def _run_config() -> RunConfig:
    return RunConfig(
        pipeline=replace(pipeline_config(MOM), instruments=_registry()),
        mode=ExecutionMode.BACKTEST,
        seed=SEED,
        compile_analytics=False,
    )


def _observed(engine: str, records: list[MarketRecord]) -> list[tuple[float, int, str | None]]:
    """What the strategy saw of history and universe, per event, under ``engine``."""

    observer = Observer(MOM)
    dataset = MarketDataset.of("ctx-parity", [record.payload for record in records])
    state = _running((MOM, observer))

    if engine == "backtest":
        BacktestEngine.run(_run_config(), dataset, state, context_factory)
    else:
        ReplayBacktest.run(_run_config(), dataset, state, context_factory)

    return [
        (
            context.history.as_of,
            len(context.history.quotes(ASSET)),
            context.universe.sector(ASSET),
        )
        for context in observer.seen
    ]


def test_history_and_universe_are_identical_across_backtest_and_replay() -> None:
    """Cross-engine parity for the new surfaces, as ADR-0010 requires of the rest."""

    records = _records(5)

    assert _observed("backtest", records) == _observed("replay", records)


def test_a_fresh_instance_continuing_a_run_sees_history_from_where_it_resumed() -> None:
    """Continuation: the new strategy object gets a context, and an honest one.

    The restored run's market state carries what it processed before the stop,
    so the continuing instance sees that history -- it is the *run's* history,
    not the object's, which is exactly the ownership ADR-0025 and ADR-0026
    establish. What it must never see is anything after its own event.
    """

    first = Observer(MOM)
    state = RunEngine.initialize(_run_config(), _running((MOM, first)))
    for record in _records(3):
        state, _ = RunEngine.advance(state, record, context_factory)

    # A brand-new strategy object, with no memory of its own, continues the run.
    fresh = Observer(MOM)
    continued = replace(state, pipeline=replace(state.pipeline, strategy=_running((MOM, fresh))))
    with RunEngine.resume(continued):
        for index in range(3, 6):
            continued, _ = RunEngine.advance(
                continued, _record(index, Decimal(100 + index)), context_factory
            )

    assert len(fresh.seen) == 3, "the fresh instance was dispatched three times"
    assert [context.history.as_of for context in fresh.seen] == [5.0, 6.0, 7.0]
    # It inherits the run's history and keeps growing it, and never sees ahead.
    assert [len(c.history.quotes(ASSET)) for c in fresh.seen] == [4, 5, 6]
    for context in fresh.seen:
        assert all(q.timestamp <= context.history.as_of for q in context.history.quotes(ASSET))


def test_a_fresh_instance_receives_no_stale_view_from_the_previous_one() -> None:
    """The continuing instance's contexts are its own, built at its own events."""

    first = Observer(MOM)
    state = RunEngine.initialize(_run_config(), _running((MOM, first)))
    for record in _records(2):
        state, _ = RunEngine.advance(state, record, context_factory)

    fresh = Observer(MOM)
    continued = replace(state, pipeline=replace(state.pipeline, strategy=_running((MOM, fresh))))
    with RunEngine.resume(continued):
        continued, _ = RunEngine.advance(continued, _record(2, Decimal("102")), context_factory)

    assert fresh.seen, "the fresh instance ran"
    assert fresh.last.history is not first.last.history
    assert fresh.last.history.as_of > first.last.history.as_of
