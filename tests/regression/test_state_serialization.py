"""Regression guard for B1: migrated states must serialize as sequences.

v2.1 replaced the ``tuple`` histories on the execution-path states with
:class:`~alphalab.common.append_log.AppendOnlyLog`. ``dataclasses.asdict``
recurses into tuples but deep-copies anything it does not recognise, so the log
reached the JSON encoder intact -- where a ``str()`` fallback turned it into
``"AppendOnlyLog([...])"``. Snapshots serialized without error and validated
cleanly, but their event histories were unreadable prose instead of data.

These tests pin the fix: histories serialize as JSON arrays, round-trip with
their contents and order intact, and unserializable values now raise instead of
being silently stringified.
"""

import json
from decimal import Decimal

import pytest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.engine import AllocationEngine
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.serialization import dataclass_to_dict
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.execution.engine import ExecutionEngine
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.state import ExecutionState
from alphalab.market.engine import MarketEngine
from alphalab.market.quote import Quote
from alphalab.oms.engine import OMSEngine
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.state import OMSState
from alphalab.persistence.adapter import PersistenceAdapter
from alphalab.persistence.exceptions import SerializationError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.persistence.state import PersistenceState
from alphalab.persistence.validation import validate_snapshot_save
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.risk.engine import RiskEngine
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.strategy.events import Intent

# ---------------------------------------------------------------------------
# One populated instance of every migrated state
# ---------------------------------------------------------------------------


def _portfolio_state() -> PortfolioState:
    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=Account("ACC-SER", "USD", "Serialization", 1.0)),
        Decimal("100000"),
        "USD",
        1.0,
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("1.00"), 2.0
    )
    return PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("-4"), Decimal("110.00"), Decimal("1.00"), 3.0
    )


def _risk_state():  # type: ignore[no-untyped-def]
    huge = Decimal("100000000")
    limits = RiskLimits(
        order_size=OrderSizeLimit(huge, huge),
        position=PositionLimit(huge, huge),
        exposure=ExposureLimit(huge, huge),
        leverage=LeverageLimit(Decimal("1000")),
        margin=MarginLimit(Decimal("1.00")),
        daily_loss=DailyLossLimit(huge),
        drawdown=DrawdownLimit(Decimal("1.00")),
    )
    from dataclasses import replace

    from alphalab.core.order_request import OrderRequest

    state = replace(RiskEngine.reset(limits), buying_power=Decimal("1000000"))
    request = OrderRequest("ORD-SER", "STRAT", "AAPL", Side.BUY, Decimal("10"), Decimal("100.00"))
    state, _ = RiskEngine.evaluate(state, request, 2.0)
    state, _ = RiskEngine.evaluate(state, request, 3.0)
    return state


def _market_state():  # type: ignore[no-untyped-def]
    state = MarketEngine.reset()
    for i in range(3):
        state = MarketEngine.publish_quote(
            state,
            Quote(
                "AAPL",
                float(i) + 1.0,
                Decimal("99"),
                Decimal("101"),
                Decimal("1"),
                Decimal("1"),
                "SIM",
                "USD",
            ),
        )
    return state


def _oms_state() -> OMSState:
    state = OMSState()
    order_id = OrderId.generate()
    order = Order(
        order_id,
        "STRAT",
        "AAPL",
        Side.BUY,
        OrderType.LIMIT,
        OrderStatus.NEW,
        Decimal("10"),
        Decimal("0"),
        Decimal("10"),
        Decimal("100"),
        None,
        Decimal("0"),
        1.0,
        1.0,
    )
    state = OMSEngine.submit(state, order, 1.0)
    state = OMSEngine.accept(state, order_id, 2.0)
    return OMSEngine.fill(state, order_id, Decimal("10"), Decimal("100"), 3.0)


def _execution_state() -> ExecutionState:
    instruction = OrderInstruction(
        "ORD-SER", "STRAT", "AAPL", Decimal("10"), Decimal("100"), Side.BUY, "SIM", "USD"
    )
    return ExecutionEngine.simulate(
        ExecutionState(),
        ExecutionSimulator(),
        instruction,
        Decimal("10"),
        Decimal("100"),
        1.0,
        FillStatus.FULL_FILL,
    )


def _allocation_state():  # type: ignore[no-untyped-def]
    from alphalab.allocation.constraints import AllocationConstraints
    from alphalab.allocation.sizing import FixedQuantitySizing

    state = AllocationEngine.initialize(
        CapitalBudget(
            Decimal("1000000"), Decimal("1000000"), Decimal("0"), {"S": Decimal("100000")}
        )
    )
    intents = (Intent(strategy_id="S", instrument="AAPL", target=Decimal("10"), timestamp=2.0),)
    state, _ = AllocationEngine.allocate(
        state,
        intents,
        {"AAPL": Decimal("100.00")},
        FixedQuantitySizing(),
        AllocationConstraints(),
        2.0,
    )
    return state


#: States whose whole-state payload is serializable, with their log fields.
MIGRATED_STATES = {
    "PortfolioState": (_portfolio_state, ("events",)),
    "RiskState": (_risk_state, ("events", "history")),
    "MarketState": (_market_state, ("events", "history")),
    "ExecutionState": (_execution_state, ("events", "history")),
    "AllocationState": (_allocation_state, ("events", "history")),
}

#: OMSState is migrated and its logs must serialize, but the whole state cannot
#: be JSON-encoded -- see test_oms_state_whole_state_limitation_is_pre_existing.
OMS_LOG_FIELDS = ("events", "history")


# ---------------------------------------------------------------------------
# 1 + 5. Every migrated state serializes, across all six representative states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(MIGRATED_STATES))
def test_every_migrated_state_serializes(name: str) -> None:
    factory, _ = MIGRATED_STATES[name]
    payload = serialize(factory())

    assert isinstance(payload, str)
    assert json.loads(payload)  # valid, non-empty JSON


# ---------------------------------------------------------------------------
# 2. Histories are JSON arrays, never strings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(MIGRATED_STATES))
def test_migrated_histories_serialize_as_json_arrays(name: str) -> None:
    factory, log_fields = MIGRATED_STATES[name]
    state = factory()
    decoded = json.loads(serialize(state))

    for field in log_fields:
        assert isinstance(getattr(state, field), AppendOnlyLog), f"{name}.{field} not migrated"
        value = decoded[field]
        assert isinstance(value, list), f"{name}.{field} serialized as {type(value).__name__}"
        assert not isinstance(value, str)
        assert "AppendOnlyLog(" not in json.dumps(value)
        assert len(value) == len(getattr(state, field))
        for entry in value:
            assert isinstance(entry, dict), f"{name}.{field} entries must be objects"


def test_nested_append_only_logs_serialize_too() -> None:
    """PortfolioState.ledger.transactions is a log nested inside a dataclass."""

    decoded = json.loads(serialize(_portfolio_state()))
    transactions = decoded["ledger"]["transactions"]

    assert isinstance(transactions, list)
    assert len(transactions) == 3  # deposit + buy + partial sell
    assert transactions[0]["asset_id"] == "CASH"


# ---------------------------------------------------------------------------
# 3. Round-trip preserves contents and order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(MIGRATED_STATES))
def test_snapshot_round_trip_preserves_event_contents_and_order(name: str) -> None:
    factory, log_fields = MIGRATED_STATES[name]
    state = factory()

    snapshot = PersistenceAdapter.to_snapshot(f"snap-{name}", name, 9.0, state)
    validate_snapshot_save(PersistenceState(engine_id="engine-ser"), snapshot)
    restored = deserialize(snapshot.payload)

    for field in log_fields:
        original = list(getattr(state, field))
        round_tripped = restored[field]
        assert isinstance(round_tripped, list)
        assert len(round_tripped) == len(original)
        # Order preserved, and each entry carries its own timestamp intact.
        assert [entry["timestamp"] for entry in round_tripped] == [e.timestamp for e in original]


def test_round_trip_preserves_portfolio_event_payloads_exactly() -> None:
    state = _portfolio_state()
    restored = deserialize(PersistenceAdapter.to_snapshot("s", "portfolio", 9.0, state).payload)

    events = restored["events"]
    assert [e["timestamp"] for e in events] == [1.0, 2.0, 3.0]
    assert events[0]["amount"] == "100000.00"  # CashDeposited
    assert events[1]["asset_id"] == "AAPL"  # PositionOpened
    assert events[2]["realized_pnl"] == "40.00"  # PositionReduced: (110-100)*4
    assert restored["realized_pnl"] == "40.00"
    assert restored["commission_paid"] == "2.00"


def test_serializing_is_deterministic() -> None:
    state = _portfolio_state()
    assert serialize(state) == serialize(state)


# ---------------------------------------------------------------------------
# 4. Non-migrated state serialization is unchanged
# ---------------------------------------------------------------------------


def test_non_migrated_states_and_plain_values_are_unaffected() -> None:
    """AnalyticsState and friends still hold tuples and serialize as before."""

    from alphalab.analytics.engine import AnalyticsEngine, PortfolioSnapshot

    snapshots = (
        PortfolioSnapshot(1.0, Decimal("100.00"), Decimal("100.00"), Decimal("0"), Decimal("0")),
        PortfolioSnapshot(2.0, Decimal("110.00"), Decimal("110.00"), Decimal("0"), Decimal("0")),
    )
    state = AnalyticsEngine.compile_report(AnalyticsEngine.initialize(), snapshots, (), 3.0)

    assert isinstance(state.reports, tuple)  # deliberately not migrated
    decoded = json.loads(serialize(state))
    assert isinstance(decoded["reports"], list)
    assert decoded["reports"][0]["ending_capital"] == "110.00"

    # Plain containers and scalars behave exactly as before.
    assert deserialize(serialize({"k": "v", "list": [1, 2, 3]})) == {"k": "v", "list": [1, 2, 3]}
    assert deserialize(serialize({"price": Decimal("150.50")})) == {"price": "150.50"}


def test_dataclass_to_dict_converts_logs_to_sequences() -> None:
    converted = dataclass_to_dict(_portfolio_state())

    assert isinstance(converted["events"], tuple)
    assert isinstance(converted["ledger"]["transactions"], tuple)
    assert all(isinstance(e, dict) for e in converted["events"])


# ---------------------------------------------------------------------------
# The encoder no longer hides failures
# ---------------------------------------------------------------------------


def test_unserializable_values_raise_instead_of_being_stringified() -> None:
    class Opaque:
        def __repr__(self) -> str:
            return "Opaque()"

    with pytest.raises(SerializationError, match="No deterministic JSON representation"):
        serialize({"bad": Opaque()})


def test_a_bare_append_only_log_serializes_as_an_array() -> None:
    assert deserialize(serialize(AppendOnlyLog([1, 2, 3]))) == [1, 2, 3]


def test_plain_enums_still_serialize_as_before() -> None:
    from alphalab.portfolio.types import TransactionType

    assert deserialize(serialize({"t": TransactionType.BUY})) == {"t": "TransactionType.BUY"}


# ---------------------------------------------------------------------------
# OMSState: its logs serialize; its order book has a separate, older limitation
# ---------------------------------------------------------------------------


def test_oms_state_logs_serialize_as_arrays() -> None:
    """OMSState is migrated, so its histories must serialize like the rest."""

    state = _oms_state()

    for field in OMS_LOG_FIELDS:
        log = getattr(state, field)
        assert isinstance(log, AppendOnlyLog)
        decoded = deserialize(serialize(log))
        assert isinstance(decoded, list)
        assert len(decoded) == len(log)
        assert [entry["timestamp"] for entry in decoded] == [e.timestamp for e in log]
        assert "AppendOnlyLog(" not in json.dumps(decoded)


def test_oms_state_whole_state_limitation_is_pre_existing_and_unrelated_to_logs() -> None:
    """OMSState has never been JSON-serializable, on v2.0.0 or v2.1.

    `OrderBook` indexes orders by `OrderId`, a dataclass, and neither
    `dataclasses.asdict` nor `json.dumps` can use a dataclass as a mapping key.
    That is a typed-identifier problem, not an append-only-log problem: the log
    fields above serialize correctly. Pinned here so the limitation is explicit
    rather than discovered again.
    """

    with pytest.raises(SerializationError, match="unhashable type"):
        serialize(_oms_state())
