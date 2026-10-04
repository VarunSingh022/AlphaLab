"""The names a snapshot writes are part of its format, and are pinned here (ledger PER-004).

Three kinds of name reach a payload, and none of them is free to change:

* **A plain ``Enum`` member** is written as ``ClassName.MEMBER``
  (``"StrategyStatus.RUNNING"``), and :func:`~alphalab.persistence.decode.as_named_enum`
  reads only that form. Renaming the class, or a member, makes every payload
  already written unreadable -- v3.13's ``LifecycleState`` -> ``StrategyStatus``
  (ledger API-001) is exactly that change, made with pipeline schema 6 -> 7 to
  rewrite it.
* **A ``StrEnum`` member** is written as its value (``"market"``), so its values
  are the format and its names are not.
* **An event** in a persisted log is tagged with its class name
  (``"OrderSubmitted"``), and each decoder resolves the tag through a fixed
  table.

The tables below are every such name as v3.13 writes it. A test failing here is
not a test to update by itself: renaming something it lists needs a schema step
that rewrites what earlier releases wrote (``SchemaHistory`` in
:mod:`alphalab.persistence.upgrade`), and the golden payloads under
``tests/fixtures/snapshots/`` are the evidence that step works. Adding a member
or an event is additive -- extend the table.
"""

from __future__ import annotations

import ast
import importlib
from enum import Enum, IntEnum, StrEnum
from pathlib import Path

import pytest

from alphalab.broker.snapshot import BrokerSnapshotDecodeError

ROOT = Path(__file__).resolve().parents[2]

#: Every plain enum a decoder reads, by where it is defined: its member names.
NAMED: dict[str, tuple[str, ...]] = {
    "alphalab.broker.order.BrokerOrderStatus": ("PENDING_SUBMIT", "SUBMITTED", "PENDING_CANCEL"),
    "alphalab.broker.reconciliation.ExecutionOutcome": (
        "APPLIED",
        "DUPLICATE",
        "UNKNOWN_ORDER",
        "TERMINAL_ORDER",
        "OVERFILL",
        "INVALID",
    ),
    "alphalab.broker.state.ConnectionStatus": (
        "DISCONNECTED",
        "CONNECTING",
        "CONNECTED",
        "RECONNECTING",
        "FAILED",
    ),
    "alphalab.execution.fill.FillStatus": (
        "FULL_FILL",
        "PARTIAL_FILL",
        "NO_FILL",
        "REJECTED",
        "EXPIRED",
    ),
    "alphalab.experiment_tracking.tracker.RunStatus": ("RUNNING", "COMPLETED", "FAILED"),
    "alphalab.lifecycle.evidence.ValidationMethod": ("BACKTEST", "RESEARCH", "EXTERNAL", "STUDY"),
    "alphalab.market.source.OrderingGuarantee": ("CHRONOLOGICAL", "UNORDERED"),
    "alphalab.model_registry.registry.ModelStage": ("NONE", "STAGING", "PRODUCTION", "ARCHIVED"),
    "alphalab.portfolio.fx_feed.FxFeedOutcome": ("APPLIED", "DUPLICATE", "SUPERSEDED"),
    "alphalab.portfolio.types.TransactionType": (
        "BUY",
        "SELL",
        "DIVIDEND",
        "DEPOSIT",
        "WITHDRAWAL",
        "FEE",
        "INTEREST",
        "TRANSFER",
        "VARIATION_MARGIN",
        "FUNDING",
    ),
    "alphalab.runtime.execution_pipeline.ExecutionRouting": ("SIMULATED", "EXTERNAL"),
    "alphalab.runtime.execution_pipeline.UnpricedReason": (
        "NO_PRICE_OBSERVED",
        "NOT_REGISTERED",
        "REGISTERED_BUT_UNPRICED",
    ),
    "alphalab.runtime.run.ExecutionMode": ("BACKTEST", "REPLAY", "PAPER", "LIVE"),
    "alphalab.strategy.state.StrategyStatus": (
        "CREATED",
        "CONFIGURED",
        "INITIALIZED",
        "SUBSCRIBED",
        "RUNNING",
        "PAUSED",
        "STOPPING",
        "STOPPED",
        "FAILED",
        "DISPOSED",
    ),
}

#: Every enum written by value that a decoder reads: its values.
VALUES: dict[str, tuple[str, ...]] = {
    "alphalab.analytics.report.Periodicity": ("DECLARED", "OBSERVED", "UNDEFINED", "ASSUMED"),
    "alphalab.common.order_terms.OrderType": ("market", "limit", "stop", "stop_limit"),
    "alphalab.common.order_terms.TimeInForce": (
        "day",
        "good_til_cancelled",
        "immediate_or_cancel",
        "fill_or_kill",
        "good_til_date",
        "at_the_opening",
        "at_the_close",
    ),
    "alphalab.core.enums.AssetType": ("equity", "future", "option", "forex", "crypto", "cash"),
    "alphalab.core.enums.OrderStatus": (
        "new",
        "pending",
        "accepted",
        "partially_filled",
        "filled",
        "cancel_pending",
        "cancelled",
        "rejected",
        "expired",
    ),
    "alphalab.core.enums.Side": ("buy", "sell"),
    "alphalab.data.feed.TradeAggressor": ("BUYER", "SELLER"),
    "alphalab.execution.policy.FillTiming": ("same_event", "next_event"),
    "alphalab.risk.models.RiskSeverity": ("HIGH", "CRITICAL"),
}

#: Every persisted event log's tag table: the class names its decoder resolves.
EVENT_TAGS: dict[str, tuple[str, ...]] = {
    "alphalab.runtime.snapshot._MARKET_EVENTS": (
        "BarClosed",
        "BookUpdated",
        "QuoteReceived",
        "SnapshotCreated",
        "TickReceived",
        "TradeReceived",
    ),
    "alphalab.runtime.snapshot._STRATEGY_EVENTS": (
        "FillEvent",
        "LifecycleTransitioned",
        "OrderEvent",
        "SliceClosed",
        "TimerEvent",
    ),
    "alphalab.runtime.snapshot._RISK_EVENTS": (
        "BuyingPowerUpdated",
        "DrawdownTriggered",
        "ExposureUpdated",
        "MarginUpdated",
        "RiskApproved",
        "RiskCheckStarted",
        "RiskRejected",
    ),
    "alphalab.runtime.snapshot._EXECUTION_EVENTS": (
        "ExecutionCompleted",
        "ExecutionExpired",
        "ExecutionPartiallyFilled",
        "ExecutionRejected",
        "ExecutionSubmitted",
    ),
    "alphalab.runtime.snapshot._ANALYTICS_EVENTS": ("ReportGenerated",),
    "alphalab.oms.snapshot._EVENT_TYPES": (
        "OrderAccepted",
        "OrderCancelled",
        "OrderExpired",
        "OrderFilled",
        "OrderPartiallyFilled",
        "OrderRejected",
        "OrderReplaced",
        "OrderSubmitted",
    ),
    "alphalab.portfolio.snapshot._EVENT_TYPES": (
        "CashConverted",
        "CashDeposited",
        "CashFlowBooked",
        "CashWithdrawn",
        "MarketValueUpdated",
        "PortfolioValuationUpdated",
        "PositionClosed",
        "PositionIncreased",
        "PositionOpened",
        "PositionReduced",
        "PositionSplit",
        "VariationSettled",
    ),
    "alphalab.allocation.snapshot._EVENT_TYPES": (
        "AllocationCompleted",
        "AllocationExecutionApplied",
        "AllocationRejected",
        "AllocationReservationReleased",
        "AllocationStarted",
        "BudgetExceeded",
        "NettingCompleted",
    ),
    "alphalab.broker.snapshot._EVENT_TYPES": (
        "BrokerConnected",
        "BrokerDisconnected",
        "ExecutionReceived",
        "Heartbeat",
        "OrderAccepted",
        "OrderCancelled",
        "OrderRejected",
        "OrderSubmitted",
    ),
}

_RENAME = (
    "is part of the persisted format. Renaming it needs a schema step that rewrites "
    "what earlier releases wrote; then update this table."
)


def _resolve(qualified: str) -> object:
    module, _, name = qualified.rpartition(".")
    return getattr(importlib.import_module(module), name)


def _decoder_enums() -> set[str]:
    """Every enum a module that decodes a snapshot refers to, by where it is defined."""

    found: set[str] = set()
    for path in sorted((ROOT / "alphalab").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "def from_primitives" not in text:
            continue
        module = importlib.import_module(".".join(path.relative_to(ROOT).with_suffix("").parts))
        tree = ast.parse(text)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for name in names:
            value = getattr(module, name, None)
            if (
                isinstance(value, type)
                and issubclass(value, Enum)
                and value not in (Enum, StrEnum, IntEnum)
            ):
                found.add(f"{value.__module__}.{value.__qualname__}")
    return found


def test_every_enum_a_decoder_reads_is_pinned() -> None:
    assert _decoder_enums() == set(NAMED) | set(VALUES)


@pytest.mark.parametrize("qualified", sorted(NAMED))
def test_a_plain_enum_keeps_its_class_and_member_names(qualified: str) -> None:
    cls = _resolve(qualified)
    assert isinstance(cls, type) and issubclass(cls, Enum)
    assert not issubclass(cls, str | int), f"{qualified} is written by value, not by name"
    assert cls.__name__ == qualified.rpartition(".")[2], f"{qualified}'s class name {_RENAME}"
    assert tuple(cls.__members__) == NAMED[qualified], f"{qualified}'s member names {_RENAME}"
    # What the encoder writes is ``str(member)``: an override of ``__str__``
    # would change the format as surely as a rename.
    assert [str(member) for member in cls] == [
        f"{cls.__name__}.{name}" for name in NAMED[qualified]
    ]


@pytest.mark.parametrize("qualified", sorted(VALUES))
def test_an_enum_written_by_value_keeps_its_values(qualified: str) -> None:
    cls = _resolve(qualified)
    assert isinstance(cls, type) and issubclass(cls, Enum)
    assert tuple(member.value for member in cls) == VALUES[qualified], (
        f"{qualified}'s values {_RENAME}"
    )


@pytest.mark.parametrize("qualified", sorted(EVENT_TAGS))
def test_every_event_tag_is_its_class_name_and_pinned(qualified: str) -> None:
    table = _resolve(qualified)
    assert isinstance(table, dict)
    assert tuple(sorted(table)) == EVENT_TAGS[qualified], f"an event tag in {qualified} {_RENAME}"
    assert all(cls.__name__ == tag for tag, cls in table.items())


def test_the_broker_decoder_reads_a_qualified_name_only_under_its_own_class() -> None:
    """Until v3.13 the broker codec discarded the qualifier unread (PER-004)."""

    from alphalab.broker.snapshot import _enum
    from alphalab.broker.state import ConnectionStatus

    assert _enum(ConnectionStatus, "ConnectionStatus.CONNECTED", "f") is ConnectionStatus.CONNECTED
    assert _enum(ConnectionStatus, "CONNECTED", "f") is ConnectionStatus.CONNECTED
    with pytest.raises(BrokerSnapshotDecodeError, match="qualified name must read"):
        _enum(ConnectionStatus, "OrderStatus.CONNECTED", "f")
