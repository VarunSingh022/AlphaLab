"""v2.10: a strategy sees the marked book, and its own share of its own orders.

Before this milestone `StrategyContext` had nine fields and every construction
site in the repository passed `object()` for six of them. A strategy could
observe the event handed to its hook and nothing else -- not its position, not
its cash, not its orders, not the price it was about to be marked at -- and
`execution_pipeline.py` documented the gap against itself.

These tests pin ADR-0026: what the pipeline populates, when it assembles it, and
every boundary it must not cross.

The one that would be easy to get wrong
---------------------------------------
`OrderBook.orders_for_strategy` exists, is public, and answers `()` for every
order the execution path produces -- ADR-0015 decision 4 leaves a netted order
unattributed on purpose, because `MOMENTUM` wanting +60 and `MEANREV` wanting
+40 make one `BUY 100` and one `strategy_id` cannot describe it. A context built
on that index would be permanently, silently empty, and would pass a test
written with a hand-constructed order. `test_the_view_is_not_built_from_the_oms_strategy_index`
is the regression that stops it coming back.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.ids import current_id_position, id_scope
from alphalab.market.quote import Quote
from alphalab.market.record import MarketRecord
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.context_views import (
    MarketView,
    OrderShare,
    OrderView,
    PortfolioView,
    RiskView,
    order_shares_by_strategy,
)
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.runtime.snapshot import capture as capture_pipeline
from alphalab.runtime.snapshot import from_primitives as pipeline_from_primitives
from alphalab.runtime.snapshot import restore as restore_pipeline
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import LifecycleState
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

SEED = 20251007
ASSET = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
MOM, REV = "MOMENTUM", "MEANREV"


# ---------------------------------------------------------------------------
# Strategies that record what they were shown
# ---------------------------------------------------------------------------


class Observer(BaseStrategy):
    """Records every context it is handed, and optionally trades.

    The contexts are kept so a test can assert on what the strategy *saw* at
    dispatch, which is the only place the marking order is observable.
    """

    def __init__(
        self,
        strategy_id: str,
        quantity: Decimal | None = None,
        trade_on: frozenset[float] | None = None,
    ) -> None:
        self._strategy_id = strategy_id
        self._quantity = quantity
        self._trade_on = trade_on
        self.seen: list[StrategyContext] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.seen.append(context)
        if self._quantity is None:
            return ()
        if self._trade_on is not None and event.quote.timestamp not in self._trade_on:
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


# ---------------------------------------------------------------------------
# Workload
# ---------------------------------------------------------------------------


def _record(index: int, mid: Decimal, asset: str = ASSET) -> MarketRecord:
    return MarketRecord(
        event_id=f"REC-{index}",
        timestamp=2.0 + index,
        payload=sized_quote(asset, 2.0 + index, mid, Decimal("1000")),
    )


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


def _config(routing: ExecutionRouting = ExecutionRouting.SIMULATED) -> Any:
    return replace(pipeline_config(MOM), routing=routing)


def _drive(
    *strategies: tuple[str, BaseStrategy],
    records: list[MarketRecord] | None = None,
    routing: ExecutionRouting = ExecutionRouting.SIMULATED,
    factory: Any = context_factory,
) -> ExecutionPipelineState:
    config = _config(routing)
    with id_scope(SEED):
        state = ExecutionPipeline.initialize(config, _running(*strategies), 1.0)
        for record in records if records is not None else [_record(0, Decimal("100"))]:
            state = ExecutionPipeline.process_record(state, record, factory).state
    return state


def _live(*strategies: tuple[str, BaseStrategy], **kwargs: Any) -> ExecutionPipelineState:
    """EXTERNAL routing leaves accepted orders working, so contributions survive."""

    return _drive(*strategies, routing=ExecutionRouting.EXTERNAL, **kwargs)


#: Trade at the first event only. The context observed at the second therefore
#: holds a settled position and has no order in flight for its own event, which
#: is what lets it be compared against the final state field for field. A
#: strategy that traded on every event would be compared against a state that
#: had moved on -- the context is built *before* its event's order executes,
#: which is correct and is asserted in its own right below.
FIRST = frozenset({2.0})
TWO = [_record(0, Decimal("100")), _record(1, Decimal("130"))]


# ---------------------------------------------------------------------------
# Narrowing the context's fields to the views the pipeline actually installs
# ---------------------------------------------------------------------------
#
# ``StrategyContext``'s field types are the marker protocols it has carried
# since v0.10.0, and they stay method-less: giving them the views' signatures
# would make ``alphalab.strategy`` -- today a strict leaf importing only
# ``alphalab.common`` -- depend on portfolio, oms, risk and market, which is an
# architectural change ADR-0026 does not authorise. These helpers narrow to the
# concrete view, and each one doubles as an assertion that the overlay
# installed it.


def _pf(context: StrategyContext) -> PortfolioView:
    view = context.portfolio
    assert isinstance(view, PortfolioView), f"the overlay did not install a PortfolioView: {view!r}"
    return view


def _od(context: StrategyContext) -> OrderView:
    view = context.orders
    assert isinstance(view, OrderView), f"the overlay did not install an OrderView: {view!r}"
    return view


def _rk(context: StrategyContext) -> RiskView:
    view = context.risk_view
    assert isinstance(view, RiskView), f"the overlay did not install a RiskView: {view!r}"
    return view


def _mk(context: StrategyContext) -> MarketView:
    view = context.market
    assert isinstance(view, MarketView), f"the overlay did not install a MarketView: {view!r}"
    return view


# ---------------------------------------------------------------------------
# 1 -- the marked portfolio
# ---------------------------------------------------------------------------


def test_the_strategy_sees_the_portfolio_marked_at_this_event() -> None:
    """Marked, not stale: the price in the book is this event's, not the last."""

    watcher = Observer(MOM)
    trader = Observer(REV, Decimal("10"), FIRST)
    state = _drive((MOM, watcher), (REV, trader), records=TWO)

    seen = _pf(watcher.last).position(ASSET)
    assert seen is not None, "the strategy held a position and could not see it"
    assert seen.market_price == Decimal("130"), "marked at this event, not the previous one"
    assert seen.market_price == state.portfolio.positions[ASSET].market_price


def test_the_context_is_not_built_from_the_pre_mark_state() -> None:
    """The mistake the ordering guards against, asserted directly.

    Reading ``state.portfolio`` instead of the marked local would show the
    previous event's price. The two differ here by construction, so a
    regression cannot hide.
    """

    watcher = Observer(MOM)
    trader = Observer(REV, Decimal("10"), FIRST)
    records = [_record(0, Decimal("100")), _record(1, Decimal("130")), _record(2, Decimal("175"))]
    _drive((MOM, watcher), (REV, trader), records=records)

    marked = [_pf(context).position(ASSET) for context in watcher.seen[1:]]
    assert all(position is not None for position in marked)
    prices = [position.market_price for position in marked if position is not None]
    assert prices == [Decimal("130"), Decimal("175")], "each dispatch saw its own event's mark"


def test_cash_and_account_truth_reach_the_strategy() -> None:
    watcher = Observer(MOM)
    state = _drive((MOM, watcher), (REV, Observer(REV, Decimal("10"), FIRST)), records=TWO)
    view = _pf(watcher.last)

    assert view.account == state.portfolio.account
    assert view.cash("USD") == state.portfolio.cash.balance("USD")
    assert view.realized_pnl == state.portfolio.realized_pnl
    assert view.commission_paid == state.portfolio.commission_paid
    assert view.quantity(OTHER) == Decimal("0"), "flat is zero, not None"
    assert view.position(OTHER) is None, "and no position is None, not a placeholder"


# ---------------------------------------------------------------------------
# 2-5 -- order attribution
# ---------------------------------------------------------------------------


def test_a_strategy_sees_its_own_live_order() -> None:
    trader = Observer(MOM, Decimal("25"), FIRST)
    state = _live((MOM, trader), records=TWO)

    view = _od(trader.last)
    assert len(state.oms.orders.open_orders()) == 1
    assert len(view) == 1
    assert view.shares[0].quantity == Decimal("25")
    assert view.shares[0].asset_id == ASSET
    assert view.net_quantity(ASSET) == Decimal("25")


def test_two_strategies_netted_into_one_order_each_see_only_their_share() -> None:
    """MOMENTUM +60 and MEANREV +40 make one BUY 100. Neither owns it."""

    mom = Observer(MOM, Decimal("60"), FIRST)
    rev = Observer(REV, Decimal("40"), FIRST)
    state = _live((MOM, mom), (REV, rev), records=TWO)

    orders = state.oms.orders.open_orders()
    assert len(orders) == 1 and orders[0].quantity == Decimal("100")

    assert [s.quantity for s in _od(mom.last)] == [Decimal("60")]
    assert [s.quantity for s in _od(rev.last)] == [Decimal("40")]
    assert not _od(mom.last).shares[0].is_sole_contributor
    assert not _od(rev.last).shares[0].is_sole_contributor
    # Same order, two views, two different attributable quantities.
    assert _od(mom.last).shares[0].order == _od(rev.last).shares[0].order


def test_another_strategys_orders_are_invisible() -> None:
    """A strategy that placed nothing sees nothing, while another has a live order."""

    trader, watcher = Observer(MOM, Decimal("25"), FIRST), Observer(REV)
    state = _live((MOM, trader), (REV, watcher), records=TWO)

    assert len(state.oms.orders.open_orders()) == 1
    assert len(_od(trader.last)) == 1
    assert len(_od(watcher.last)) == 0, "REV placed nothing and must see nothing"


def test_a_terminal_order_leaves_the_view() -> None:
    """Contribution retirement (ADR-0021) is what bounds the view to live orders."""

    trader = Observer(MOM, Decimal("25"), FIRST)
    # SIMULATED fills immediately, so the order is terminal by the next event.
    state = _drive((MOM, trader), records=TWO)

    assert len(state.oms.orders.orders()) >= 1, "orders were placed"
    assert state.oms.orders.open_orders() == (), "and all of them filled"
    assert dict(state.allocation.contributions) == {}, "so their contributions retired"
    assert len(_od(trader.last)) == 0, "and the view no longer shows them"


def test_the_view_is_not_built_from_the_oms_strategy_index() -> None:
    """The regression that would otherwise return silently.

    ``orders_for_strategy`` answers ``()`` for a pipeline order because
    ADR-0015 decision 4 leaves a netted order unattributed. A context built on
    it would be permanently empty and every other test here would still pass.
    """

    trader = Observer(MOM, Decimal("25"), FIRST)
    state = _live((MOM, trader), records=TWO)

    assert state.oms.orders.orders_for_strategy(MOM) == (), "the index cannot answer"
    assert len(_od(trader.last)) == 1, "the contributions join can, and did"


def test_the_index_skips_a_contribution_whose_order_is_no_longer_open() -> None:
    """A view cannot outlive the order it describes."""

    trader = Observer(MOM, Decimal("25"), FIRST)
    state = _live((MOM, trader), records=TWO)

    stripped = replace(state.oms, orders=state.oms.orders.__class__())
    assert order_shares_by_strategy(stripped, state.allocation) == {}


# ---------------------------------------------------------------------------
# 6-7 -- factory compatibility and the overlay
# ---------------------------------------------------------------------------


class _Clock:
    def now(self) -> float:
        return 41.0


class _Logger:
    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


_CLOCK, _LOGGER = _Clock(), _Logger()
_CONFIG = {"threshold": Decimal("1.5"), "note": "caller-owned"}


def _placeholder_factory(strategy_id: str) -> StrategyContext:
    """A caller factory of exactly the shape every existing site uses."""

    return StrategyContext(
        portfolio=object(),
        market=object(),
        clock=_CLOCK,
        logger=_LOGGER,
        risk_view=object(),
        config=_CONFIG,
        orders=object(),
        history=object(),
        universe=object(),
    )


def test_the_caller_factory_still_works_and_its_own_fields_survive() -> None:
    watcher = Observer(MOM)
    _drive((MOM, watcher), (REV, Observer(REV, Decimal("10"), FIRST)), factory=_placeholder_factory)
    context = watcher.last

    assert context.clock is _CLOCK, "the caller's object, not a copy"
    assert context.logger is _LOGGER
    assert context.config is _CONFIG
    assert context.config["threshold"] == Decimal("1.5")


def test_pipeline_owned_fields_overwrite_whatever_the_caller_supplied() -> None:
    """No caller-installable second source of truth."""

    watcher = Observer(MOM)
    _drive((MOM, watcher), (REV, Observer(REV, Decimal("10"), FIRST)), factory=_placeholder_factory)
    context = watcher.last

    assert isinstance(_pf(context), PortfolioView)
    assert isinstance(_od(context), OrderView)
    assert isinstance(_rk(context), RiskView)
    assert isinstance(_mk(context), MarketView)
    for field_name in ("portfolio", "orders", "risk_view", "market"):
        assert type(getattr(context, field_name)) is not object


def test_a_caller_supplied_portfolio_cannot_win() -> None:
    class Fake:
        def position(self, asset_id: str) -> None:
            raise AssertionError("a fabricated portfolio reached the strategy")

    def fabricating(strategy_id: str) -> StrategyContext:
        return replace(_placeholder_factory(strategy_id), portfolio=Fake())

    watcher = Observer(MOM)
    _drive((MOM, watcher), (REV, Observer(REV, Decimal("10"), FIRST)), factory=fabricating)

    assert isinstance(_pf(watcher.last), PortfolioView)


def test_deferred_fields_keep_the_callers_placeholder_and_are_honestly_empty() -> None:
    watcher = Observer(MOM)
    _drive((MOM, watcher), factory=_placeholder_factory)

    assert type(watcher.last.history) is object, "history is deferred, and says so"
    assert type(watcher.last.universe) is object, "universe is deferred, and says so"


def test_the_reinforcement_learning_consumer_still_reaches_its_agent() -> None:
    """Its `_PendingDecision` rides on `config`, which the overlay never touches."""

    from alphalab.reinforcement_learning.environment import _make_context_factory, _PendingDecision

    pending = _PendingDecision(
        strategy_id=MOM, asset_id=ASSET, signed_quantity=Decimal("7"), timestamp=2.0
    )
    watcher = Observer(MOM)
    _drive((MOM, watcher), factory=_make_context_factory(pending))

    assert watcher.last.config is pending
    assert watcher.last.config.signed_quantity == Decimal("7")
    assert isinstance(_pf(watcher.last), PortfolioView), "and it still gets the overlay"


# ---------------------------------------------------------------------------
# 8 -- the RUNNING check
# ---------------------------------------------------------------------------


class _CountingFactory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, strategy_id: str) -> StrategyContext:
        self.calls.append(strategy_id)
        return _placeholder_factory(strategy_id)


def test_no_context_is_built_for_a_strategy_that_is_not_running() -> None:
    running = _running((MOM, Observer(MOM)))
    paused_entry, _ = RuntimeSupervisor.pause(running.strategies[MOM], 2.0)
    state = replace(
        _running((MOM, Observer(MOM)), (REV, Observer(REV))),
        strategies={
            MOM: paused_entry,
            **{REV: _running((REV, Observer(REV))).strategies[REV]},
        },
    )
    counting = _CountingFactory()

    StrategyEngine.process_event(state, object(), counting, 2.0)

    assert state.strategies[MOM].status is LifecycleState.PAUSED
    assert counting.calls == [REV], "only the running strategy had a context built"


@pytest.mark.parametrize(
    "transition",
    [
        lambda entry: RuntimeSupervisor.pause(entry, 2.0)[0],
        lambda entry: RuntimeSupervisor.stop(entry, 2.0)[0],
        lambda entry: RuntimeSupervisor.fail(entry, "boom", 2.0)[0],
    ],
)
def test_every_non_running_status_skips_construction(transition: Any) -> None:
    state = _running((MOM, Observer(MOM)))
    state = replace(state, strategies={MOM: transition(state.strategies[MOM])})
    counting = _CountingFactory()

    StrategyEngine.process_event(state, object(), counting, 2.0)

    assert counting.calls == []


def test_a_running_strategy_gets_exactly_one_context_and_one_dispatch() -> None:
    observer = Observer(MOM)
    state = _running((MOM, observer))
    counting = _CountingFactory()
    quote = _record(0, Decimal("100"))

    from alphalab.market.engine import MarketEngine
    from alphalab.market.state import MarketState

    payload = quote.payload
    assert isinstance(payload, Quote)
    market = MarketEngine.publish_quote(MarketState(), payload)
    StrategyEngine.process_event(state, market.events[-1], counting, 2.0)

    assert counting.calls == [MOM]
    assert len(observer.seen) == 1, "one dispatch, not two"


def test_skipping_construction_does_not_change_the_resulting_state() -> None:
    """The skip is exactly what Dispatcher did: state unchanged, nothing emitted."""

    state = _running((MOM, Observer(MOM)))
    state = replace(state, strategies={MOM: RuntimeSupervisor.pause(state.strategies[MOM], 2.0)[0]})

    after, intents = StrategyEngine.process_event(state, object(), _placeholder_factory, 2.0)

    assert after.strategies == state.strategies
    assert intents == ()
    assert after.events == state.events


# ---------------------------------------------------------------------------
# 9 -- failure semantics
# ---------------------------------------------------------------------------


def test_a_factory_that_returns_the_wrong_type_raises_naming_the_strategy() -> None:
    def broken(strategy_id: str) -> Any:
        return {"not": "a context"}

    with pytest.raises(RuntimeValidationError, match=MOM) as excinfo:
        _drive((MOM, Observer(MOM)), factory=broken)

    assert "not a StrategyContext" in str(excinfo.value)
    assert "Nothing is substituted" in str(excinfo.value)


def test_a_factory_returning_none_is_refused_rather_than_substituted() -> None:
    def broken(strategy_id: str) -> Any:
        return None

    with pytest.raises(RuntimeValidationError, match="NoneType"):
        _drive((MOM, Observer(MOM)), factory=broken)


def test_a_populated_field_is_never_none_or_a_bare_object() -> None:
    watcher = Observer(MOM)
    _drive((MOM, watcher), (REV, Observer(REV, Decimal("10"), FIRST)), factory=_placeholder_factory)

    for field_name in ("portfolio", "orders", "risk_view", "market"):
        value = getattr(watcher.last, field_name)
        assert value is not None
        assert type(value) is not object


# ---------------------------------------------------------------------------
# 10-11 -- immutability, by attempt
# ---------------------------------------------------------------------------


def _context() -> StrategyContext:
    watcher = Observer(MOM)
    _drive(
        (MOM, watcher),
        (REV, Observer(REV, Decimal("10"), FIRST)),
        records=TWO,
        factory=_placeholder_factory,
    )
    return watcher.last


@pytest.mark.parametrize("field_name", ["portfolio", "orders", "risk_view", "market", "config"])
def test_a_context_field_cannot_be_rebound(field_name: str) -> None:
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        setattr(_context(), field_name, object())


@pytest.mark.parametrize(
    "accessor",
    [
        lambda c: c.portfolio.positions,
        lambda c: c.portfolio.balances,
        lambda c: c.portfolio.reserved,
        lambda c: c.market.prices,
    ],
)
def test_a_returned_mapping_cannot_be_mutated(accessor: Any) -> None:
    mapping: Mapping[str, Any] = accessor(_context())

    with pytest.raises(TypeError):
        mapping["INJECTED"] = Decimal("1")  # type: ignore[index]
    with pytest.raises((TypeError, AttributeError)):
        mapping.clear()  # type: ignore[attr-defined]


def test_mutating_a_returned_mapping_cannot_reach_pipeline_state() -> None:
    """Not merely refused -- refused without a route back to the real dict."""

    context = _context()
    positions = _pf(context).positions

    with pytest.raises(TypeError):
        positions[ASSET] = "corrupt"  # type: ignore[index]
    assert _pf(context).position(ASSET) is not None
    assert "INJECTED" not in _pf(context).positions


@pytest.mark.parametrize(
    "accessor",
    [lambda c: c.orders.shares, lambda c: c.orders.for_asset(ASSET), lambda c: c.market.assets],
)
def test_a_returned_sequence_is_a_tuple_and_cannot_be_appended_to(accessor: Any) -> None:
    sequence = accessor(_context())

    assert isinstance(sequence, tuple)
    with pytest.raises(AttributeError):
        sequence.append(object())  # type: ignore[attr-defined]


def test_nested_values_are_frozen_dataclasses() -> None:
    from dataclasses import FrozenInstanceError

    context = _context()
    position = _pf(context).position(ASSET)
    assert position is not None

    with pytest.raises(FrozenInstanceError):
        position.quantity = Decimal("999")  # type: ignore[misc]


@pytest.mark.parametrize(
    "forbidden",
    ["submit", "cancel", "replace", "modify", "place", "send", "amend", "reserve", "release"],
)
def test_the_order_view_exposes_no_submission_or_mutation_path(forbidden: str) -> None:
    assert not hasattr(OrderView, forbidden)
    assert not hasattr(OrderShare, forbidden)


def test_no_engine_is_reachable_from_a_context() -> None:
    """Views expose data; engines stay outside."""

    context = _context()
    for view in (_pf(context), _od(context), _rk(context), _mk(context)):
        names = [n for n in dir(view) if not n.startswith("_")]
        assert not [n for n in names if "engine" in n.lower()]


def test_the_order_share_does_not_claim_sole_ownership_of_a_netted_order() -> None:
    mom = Observer(MOM, Decimal("60"), FIRST)
    rev = Observer(REV, Decimal("40"), FIRST)
    _live((MOM, mom), (REV, rev), records=TWO)

    share = _od(mom.last).shares[0]
    assert share.quantity == Decimal("60")
    assert share.order.quantity == Decimal("100"), "the order's total is not the share"
    assert not share.is_sole_contributor


# ---------------------------------------------------------------------------
# 12 -- restored-context equality
# ---------------------------------------------------------------------------


def test_a_context_built_from_restored_state_equals_the_control() -> None:
    """The context is derived, so it round-trips for free (ADR-0026 decision 8)."""

    config = _config()
    strategy = Observer(MOM, Decimal("10"))
    records = [_record(index, Decimal(100 + index)) for index in range(4)]
    with id_scope(SEED):
        control = ExecutionPipeline.initialize(config, _running((MOM, strategy)), 1.0)
        for record in records:
            control = ExecutionPipeline.process_record(control, record, context_factory).state

    objects = RuntimeObjects(
        sizing_model=config.sizing_model,
        simulator=config.simulator,
        strategies={MOM: strategy},
        instruments=config.instruments,
    )
    restored = restore_pipeline(
        pipeline_from_primitives(deserialize(serialize(capture_pipeline(control)))), objects
    )

    assert PortfolioView(restored.portfolio) == PortfolioView(control.portfolio)
    assert RiskView(restored.risk) == RiskView(control.risk)
    assert MarketView(restored.market, restored.market_prices) == MarketView(
        control.market, control.market_prices
    )
    assert order_shares_by_strategy(restored.oms, restored.allocation) == (
        order_shares_by_strategy(control.oms, control.allocation)
    )


def test_the_context_is_not_added_to_any_snapshot() -> None:
    from dataclasses import fields

    from alphalab.runtime.snapshot import PIPELINE_SNAPSHOT_SCHEMA, PipelineSnapshot

    names = {field.name for field in fields(PipelineSnapshot)}
    assert not [n for n in names if "context" in n]
    assert PIPELINE_SNAPSHOT_SCHEMA == 2, "populating a context moves no schema"


# ---------------------------------------------------------------------------
# 13-14 -- identifiers and dispatch count
# ---------------------------------------------------------------------------


def test_building_a_context_mints_no_identifier() -> None:
    state = _live((MOM, Observer(MOM, Decimal("25"))))

    with id_scope(SEED):
        before = current_id_position()
        shares = order_shares_by_strategy(state.oms, state.allocation)
        _ = PortfolioView(state.portfolio).positions
        _ = RiskView(state.risk).buying_power
        _ = MarketView(state.market, state.market_prices).prices
        _ = OrderView(shares.get(MOM, ()))
        after = current_id_position()

    assert before == after


def test_populating_the_context_does_not_change_the_identifier_stream() -> None:
    """The run mints exactly what it minted before contexts were populated."""

    trader = Observer(MOM, Decimal("10"))
    records = [_record(index, Decimal(100 + index)) for index in range(4)]
    state = _drive((MOM, trader), records=records)

    assert state.id_position.seed == SEED
    ids = [str(order.order_id.value) for order in state.oms.orders.orders()]
    assert len(ids) == len(set(ids))


def test_each_running_strategy_is_dispatched_exactly_once_per_event() -> None:
    mom, rev = Observer(MOM), Observer(REV)
    records = [_record(index, Decimal(100 + index)) for index in range(5)]
    _drive((MOM, mom), (REV, rev), records=records)

    assert len(mom.seen) == 5
    assert len(rev.seen) == 5


def test_building_a_context_does_not_mutate_pipeline_state() -> None:
    state = _live((MOM, Observer(MOM, Decimal("25"))))
    before = capture_pipeline(state)

    _ = order_shares_by_strategy(state.oms, state.allocation)
    _ = PortfolioView(state.portfolio).positions
    _ = MarketView(state.market, state.market_prices).prices

    assert capture_pipeline(state) == before


# ---------------------------------------------------------------------------
# 15-16 -- the SHOULD views
# ---------------------------------------------------------------------------


def test_the_risk_view_is_the_resynced_state_the_engine_evaluates_against() -> None:
    watcher = Observer(MOM)
    state = _drive((MOM, watcher), (REV, Observer(REV, Decimal("10"), FIRST)), records=TWO)
    view = _rk(watcher.last)

    assert view.buying_power == state.risk.buying_power
    assert view.current_nav == state.risk.current_nav
    assert view.peak_nav == state.risk.peak_nav
    assert view.daily_loss == state.risk.daily_loss
    assert view.margin == state.risk.margin
    assert view.exposure == state.risk.exposure
    assert view.active_limits == state.risk.active_limits


def test_the_market_view_shows_this_events_prices_and_indexes() -> None:
    watcher = Observer(MOM)
    state = _drive((MOM, watcher), records=TWO)
    view = _mk(watcher.last)

    assert view.price(ASSET) == Decimal("130")
    assert view.price(OTHER) is None, "unpriced is None, which is a fact"
    assert view.quote(ASSET) == state.market.latest_quotes[ASSET]
    assert view.assets == (ASSET,)


def test_the_market_view_exposes_no_history() -> None:
    """History is deferred; a lookback needs a look-ahead guard it does not have."""

    view = _mk(_context())
    assert not hasattr(view, "history")
    assert not hasattr(view, "bars")
    assert not hasattr(view, "lookback")


# ---------------------------------------------------------------------------
# 17-18 -- nothing that worked before stopped working
# ---------------------------------------------------------------------------


def test_a_strategy_that_ignores_its_context_is_unaffected() -> None:
    """The v2.9 stateless case, byte for byte."""

    from alphalab.backtesting.engine import BacktestEngine
    from tests.integration.harness import ScriptedStrategy, backtest_config, dataset_of_quotes

    dataset = dataset_of_quotes(ASSET, [Decimal("100"), Decimal("101"), Decimal("102")])
    plan = {2.0: Decimal("5"), 4.0: Decimal("-5")}

    def run() -> Any:
        return BacktestEngine.run(
            backtest_config(MOM, seed=SEED),
            dataset,
            running_strategy_state(MOM, ScriptedStrategy(MOM, ASSET, plan)),
            context_factory,
        )

    left, right = run(), run()
    assert left.fills == right.fills
    assert left.state.portfolio.positions == right.state.portfolio.positions
    assert left.state.id_position == right.state.id_position
