"""
Comprehensive tests for the immutable kernel.

Verifies dispatching, snapshots, selectors,
versioning, state diffs, and immutability.
"""

from dataclasses import FrozenInstanceError, replace

import pytest

from alphalab.kernel import (
    MarketState,
    PortfolioState,
    PositionState,
    ReducerRegistry,
    SnapshotManager,
    StateDiff,
    StateStore,
    SystemState,
    VersionManager,
    get_cash,
    get_equity,
    get_market_price,
    get_portfolio_value,
    get_positions,
    get_realized_pnl,
    get_symbol_position,
    get_unrealized_pnl,
)


@pytest.fixture
def base_position() -> PositionState:
    return PositionState(symbol="AAPL", quantity=100.0, average_price=150.0, market_price=155.0)


@pytest.fixture
def base_system_state(base_position: PositionState) -> SystemState:
    market = MarketState(prices={"AAPL": 155.0, "GOOG": 2800.0}, calendar_status="OPEN")
    portfolio = PortfolioState(cash=50000.0, positions={"AAPL": base_position}, realized_pnl=500.0)
    return SystemState(version=1, timestamp=100.0, market=market, portfolio=portfolio)


# -------------------------------------------------------------------------
# Immutability Tests (Tests 1 to 5)
# -------------------------------------------------------------------------


def test_01_position_state_immutability(base_position: PositionState) -> None:
    with pytest.raises(FrozenInstanceError):
        base_position.quantity = 200.0  # type: ignore[misc]


def test_02_market_state_immutability() -> None:
    market = MarketState(prices={"AAPL": 150.0})
    with pytest.raises(FrozenInstanceError):
        market.calendar_status = "CLOSED"  # type: ignore[misc]


def test_03_portfolio_state_immutability() -> None:
    portfolio = PortfolioState(cash=1000.0)
    with pytest.raises(FrozenInstanceError):
        portfolio.cash = 2000.0  # type: ignore[misc]


def test_04_system_state_immutability(base_system_state: SystemState) -> None:
    with pytest.raises(FrozenInstanceError):
        base_system_state.version = 2  # type: ignore[misc]


def test_05_replace_creates_new_instance(base_system_state: SystemState) -> None:
    new_state = replace(base_system_state, version=2)
    assert base_system_state.version == 1
    assert new_state.version == 2
    assert base_system_state is not new_state


# -------------------------------------------------------------------------
# Selectors & Accounting Tests (Tests 6 to 11)
# -------------------------------------------------------------------------


def test_06_selectors_cash_and_positions(
    base_system_state: SystemState, base_position: PositionState
) -> None:
    assert get_cash(base_system_state) == 50000.0
    positions = get_positions(base_system_state)
    assert len(positions) == 1
    assert positions["AAPL"] == base_position


def test_07_selectors_equity_and_pnl(base_system_state: SystemState) -> None:
    # Equity = Cash (50,000) + AAPL Market Value (100 * 155 = 15,500) = 65,500
    assert get_equity(base_system_state) == 65500.0
    assert get_portfolio_value(base_system_state) == 65500.0
    # Unrealized PnL = (155 - 150) * 100 = 500
    assert get_unrealized_pnl(base_system_state) == 500.0
    assert get_realized_pnl(base_system_state) == 500.0


def test_08_selector_symbol_position(
    base_system_state: SystemState, base_position: PositionState
) -> None:
    assert get_symbol_position(base_system_state, "AAPL") == base_position
    assert get_symbol_position(base_system_state, "TSLA") is None


def test_09_selector_market_price(base_system_state: SystemState) -> None:
    assert get_market_price(base_system_state, "AAPL") == 155.0
    assert get_market_price(base_system_state, "MSFT") is None


def test_10_selector_immutability_guarantee(base_system_state: SystemState) -> None:
    initial_version = base_system_state.version
    _ = get_equity(base_system_state)
    assert base_system_state.version == initial_version


def test_11_position_state_derived_properties(base_position: PositionState) -> None:
    assert base_position.market_value == 15500.0
    assert base_position.unrealized_pnl == 500.0


# -------------------------------------------------------------------------
# Reducers & Registry Tests (Tests 12 to 15)
# -------------------------------------------------------------------------


def test_12_reducer_registration_and_execution() -> None:
    registry = ReducerRegistry()

    def dummy_cash_reducer(state: SystemState, event: dict[str, float]) -> SystemState:
        if "add_cash" in event:
            new_portfolio = replace(state.portfolio, cash=state.portfolio.cash + event["add_cash"])
            return replace(state, portfolio=new_portfolio)
        return state

    registry.register(dummy_cash_reducer)
    initial = SystemState()
    updated = registry.reduce(initial, {"add_cash": 500.0})

    assert initial.portfolio.cash == 0.0
    assert updated.portfolio.cash == 500.0


def test_13_reducer_composition() -> None:
    registry = ReducerRegistry()

    def version_reducer(state: SystemState, event: dict[str, float]) -> SystemState:
        return replace(state, version=state.version + 1)

    def cash_reducer(state: SystemState, event: dict[str, float]) -> SystemState:
        amount = event.get("cash", 0.0)
        new_portfolio = replace(state.portfolio, cash=state.portfolio.cash + amount)
        return replace(state, portfolio=new_portfolio)

    registry.register(version_reducer)
    registry.register(cash_reducer)

    state = SystemState()
    new_state = registry.reduce(state, {"cash": 1000.0})

    assert new_state.version == 1
    assert new_state.portfolio.cash == 1000.0


def test_14_reducer_unregister() -> None:
    registry = ReducerRegistry()

    def r1(state: SystemState, event: dict[str, float]) -> SystemState:
        return replace(state, version=state.version + 1)

    registry.register(r1)
    registry.unregister(r1)
    res = registry.reduce(SystemState(), {})
    assert res.version == 0


def test_15_reducer_idempotency_duplicate_register() -> None:
    registry = ReducerRegistry()

    def r1(state: SystemState, event: dict[str, float]) -> SystemState:
        return replace(state, version=state.version + 1)

    registry.register(r1)
    registry.register(r1)  # Should not duplicate execution
    res = registry.reduce(SystemState(), {})
    assert res.version == 1


# -------------------------------------------------------------------------
# State Store Tests (Tests 16 to 19)
# -------------------------------------------------------------------------


def test_16_store_dispatch_produces_new_state() -> None:
    registry = ReducerRegistry()

    def increment_version(state: SystemState, event: str) -> SystemState:
        if event == "INC":
            return replace(state, version=state.version + 1)
        return state

    registry.register(increment_version)
    store = StateStore(registry=registry)

    s0 = store.current_state()
    s1 = store.dispatch("INC")

    assert s0.version == 0
    assert s1.version == 1
    assert s0 is not s1


def test_17_store_history_tracking() -> None:
    registry = ReducerRegistry()
    registry.register(lambda s, e: replace(s, version=s.version + 1))
    store = StateStore(registry=registry)

    store.dispatch("EVENT_1")
    store.dispatch("EVENT_2")

    history = store.history()
    assert len(history) == 3
    assert [s.version for s in history] == [0, 1, 2]


def test_18_store_replace() -> None:
    store = StateStore()
    custom_state = SystemState(version=99)
    replaced = store.replace(custom_state)

    assert replaced.version == 99
    assert store.current_state().version == 99
    assert len(store.history()) == 2


def test_19_store_invalid_dispatch_type() -> None:
    registry = ReducerRegistry()
    registry.register(lambda s, e: "Not A State Object")
    store = StateStore(registry=registry)

    with pytest.raises(TypeError):
        store.dispatch("BROKEN_EVENT")


# -------------------------------------------------------------------------
# Snapshot & Restoration Tests (Tests 20 to 22)
# -------------------------------------------------------------------------


def test_20_snapshot_save_and_load(base_system_state: SystemState) -> None:
    manager = SnapshotManager()
    snap = manager.save(base_system_state, "chk_1")

    assert snap.checkpoint_id == "chk_1"
    assert snap.state == base_system_state
    loaded = manager.load("chk_1")
    assert loaded == snap


def test_21_store_snapshot_and_restore(base_system_state: SystemState) -> None:
    store = StateStore(initial_state=base_system_state)
    snap = store.snapshot("backup_1")

    # Mutate store pointer via replace
    store.replace(SystemState(version=999))
    assert store.current_state().version == 999

    # Restore from snapshot
    restored = store.restore(snap)
    assert restored.version == base_system_state.version
    assert store.current_state() == base_system_state


def test_22_snapshot_serialization_deterministic(base_system_state: SystemState) -> None:
    manager = SnapshotManager()
    manager.save(base_system_state, "chk_serial")

    payload = manager.serialize("chk_serial")
    assert isinstance(payload, bytes)

    new_manager = SnapshotManager()
    restored_snap = new_manager.deserialize_and_save(payload)
    assert restored_snap.state == base_system_state


# -------------------------------------------------------------------------
# Version Manager Tests (Tests 23 to 25)
# -------------------------------------------------------------------------


def test_23_version_manager_increment() -> None:
    vm = VersionManager()
    v1 = vm.increment(100.0)
    v2 = vm.increment(200.0)

    assert v1.number == 1
    assert v2.number == 2
    assert vm.current() == v2


def test_24_version_manager_rollback() -> None:
    vm = VersionManager()
    vm.increment(100.0)
    vm.increment(200.0)

    rolled_back = vm.rollback(steps=1)
    assert rolled_back.number == 1
    assert vm.current().number == 1


def test_25_version_manager_rollback_bounds() -> None:
    vm = VersionManager()
    with pytest.raises(ValueError):
        vm.rollback(steps=5)
    with pytest.raises(ValueError):
        vm.rollback(steps=-1)


# -------------------------------------------------------------------------
# State Diffing Engine Tests (Tests 26 to 28)
# -------------------------------------------------------------------------


def test_26_state_diff_changed_values() -> None:
    s1 = SystemState(version=1, timestamp=10.0)
    s2 = SystemState(version=2, timestamp=20.0)

    diff = StateDiff.compare(s1, s2)
    assert "version" in diff.changed
    assert diff.changed["version"] == (1, 2)
    assert diff.changed["timestamp"] == (10.0, 20.0)


def test_27_state_diff_added_and_removed_nested_keys() -> None:
    m1 = MarketState(prices={"AAPL": 150.0})
    m2 = MarketState(prices={"GOOG": 2800.0})

    diff = StateDiff.compare(m1, m2)
    assert "prices.AAPL" in diff.removed
    assert diff.removed["prices.AAPL"] == 150.0
    assert "prices.GOOG" in diff.added
    assert diff.added["prices.GOOG"] == 2800.0


def test_28_state_diff_deep_nesting(
    base_system_state: SystemState, base_position: PositionState
) -> None:
    new_pos = replace(base_position, market_price=160.0)
    new_portfolio = replace(base_system_state.portfolio, positions={"AAPL": new_pos})
    new_state = replace(base_system_state, portfolio=new_portfolio)

    diff = StateDiff.compare(base_system_state, new_state)
    assert "portfolio.positions.AAPL.market_price" in diff.changed
    assert diff.changed["portfolio.positions.AAPL.market_price"] == (155.0, 160.0)
