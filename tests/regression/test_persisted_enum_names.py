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
    # v4.2.0 (ledger FUT-017): an instrument's margin terms, by their basis.
    "alphalab.instrument.margin.MarginBasis": ("VALUE_RATE", "PER_CONTRACT"),
    # v4.1.1 (ledger FUT-013), found by the v4.2.0 scanner, which reads
    # terms_from_primitives: an option's declared terms, by name.
    "alphalab.options.enums.ExerciseStyle": ("AMERICAN", "EUROPEAN", "BERMUDAN"),
    "alphalab.options.enums.OptionType": ("CALL", "PUT"),
    "alphalab.options.expiration.SettlementStyle": ("PHYSICAL", "CASH"),
    # v4.2.0 (ledger FUT-015): an instrument's settlement rule, by its basis.
    "alphalab.conventions.settlement.SettlementBasis": (
        "TRADE_DATE",
        "TRADING_DAYS",
        "CALENDAR_DAYS",
    ),
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
    "alphalab.runtime.execution_pipeline.ExecutionRouting": (
        "SIMULATED",
        "EXTERNAL",
        "ALGORITHMIC",
    ),
    "alphalab.runtime.execution_pipeline.UnpricedReason": (
        "NO_PRICE_OBSERVED",
        "NOT_REGISTERED",
        "REGISTERED_BUT_UNPRICED",
    ),
    # v4.1 (ledger FUT-001): a paused replay's cursor is written with its status.
    "alphalab.replay.state.ReplayStatus": ("READY", "RUNNING", "PAUSED", "STOPPED", "COMPLETED"),
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
    # v3.11 (ledger ACC-005), found by the v4.2.0 scanner, which reads
    # economics_from_primitives: how a position turns into cash.
    "alphalab.conventions.economics.SettlementModel": (
        "cash_equity",
        "futures_variation",
        "option_premium",
        "perpetual",
    ),
    # v4.1.1 (ledger FUT-009), found by the v4.2.0 scanner, which reads
    # book_from_primitives: a working algorithm's state and policies.
    "alphalab.execution.algorithms.AlgorithmStatus": (
        "working",
        "completed",
        "cancelled",
        "expired",
        "failed",
    ),
    "alphalab.execution.algorithms.IncompletePolicy": ("leave_unfilled", "complete_at_end"),
    "alphalab.execution.algorithms.MissingVolumePolicy": ("refuse", "time_weighted"),
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
    # v4.1.1 added "bond" (ledger FUT-010) and "index" (FUT-013), last, so no
    # written value moved.
    "alphalab.core.enums.AssetType": (
        "equity",
        "future",
        "option",
        "forex",
        "crypto",
        "cash",
        "bond",
        "index",
    ),
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
    # v4.1 (ledger FUT-001): the lifecycle log of a paused replay's cursor.
    "alphalab.backtesting.replay_snapshot._EVENTS": (
        "ReplayAdvanced",
        "ReplayPaused",
        "ReplayResumed",
        "ReplayStarted",
    ),
    "alphalab.runtime.snapshot._MARKET_EVENTS": (
        "BarClosed",
        "BookUpdated",
        "QuoteReceived",
        "SnapshotCreated",
        "TickReceived",
        "TradeReceived",
    ),
    "alphalab.runtime.snapshot._STRATEGY_EVENTS": (
        # v4.2.0: written only when a strategy's cancel leaves an order working.
        "CancelRefused",
        # v4.2.0 (FUT-014): written only when a strategy's exercise is refused.
        "ExerciseRefused",
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
        # v4.2.0 (ledger FUT-017): a margin call and the liquidation it placed.
        "MarginCall",
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


def is_decoder(name: str) -> bool:
    """Whether a function is one of the persistence layer's decoders, by its name.

    The decoding architecture names every decoder for what it reads: a
    snapshot's ``from_primitives``, or a part's ``<part>_from_primitives``
    (``terms_from_primitives``, ``margin_from_primitives``), and the
    ``restore`` -- ``restore_<part>``, ``<part>_restore`` -- that turns a
    decoded snapshot into live objects, where some payloads' names are read.
    Until v4.2.0 a module counted only when its text held ``def
    from_primitives``, so ``terms_from_primitives`` (FUT-013),
    ``economics_from_primitives`` (ACC-005), ``book_from_primitives`` (FUT-009)
    and ``margin_from_primitives`` (FUT-017) read enums no table pinned.
    """

    return (
        name in ("from_primitives", "restore")
        or name.endswith(("_from_primitives", "_restore"))
        or name.startswith("restore_")
    )


def decoded_names(tree: ast.Module) -> set[str]:
    """Every name a module's decoders read: their own bodies, what they reach, and its tables.

    From each decoder, the module's functions and methods it names are followed,
    as are the module-level tables (``_RISK_EVENTS = {...}``) it reads, so an
    enum read by a helper or through a table is found. A module with no decoder
    reads nothing; an enum the module uses elsewhere -- to validate, to route --
    is not a format name and is not reported.
    """

    functions: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.setdefault(node.name, []).append(node)
    tables: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    tables[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            tables[node.target.id] = node.value
    pending = [f for name, found in functions.items() if is_decoder(name) for f in found]
    seen: set[int] = set()
    names: set[str] = set()
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                names.add(child.id)
                pending.extend(functions.get(child.id, ()))
                if child.id in tables:
                    pending.append(tables[child.id])
            elif isinstance(child, ast.Attribute):
                pending.extend(functions.get(child.attr, ()))
    return names


def _decoder_enums() -> set[str]:
    """Every enum the package's decoders read, by where it is defined."""

    found: set[str] = set()
    for path in sorted((ROOT / "alphalab").rglob("*.py")):
        names = decoded_names(ast.parse(path.read_text(encoding="utf-8")))
        if not names:
            continue
        module = importlib.import_module(".".join(path.relative_to(ROOT).with_suffix("").parts))
        for name in names:
            value = getattr(module, name, None)
            if (
                isinstance(value, type)
                and issubclass(value, Enum)
                and value not in (Enum, StrEnum, IntEnum)
            ):
                found.add(f"{value.__module__}.{value.__qualname__}")
    return found


@pytest.mark.parametrize(
    "name",
    [
        "from_primitives",
        "margin_from_primitives",
        "terms_from_primitives",
        "_legs_from_primitives",  # a private part decoder
        "restore",
        "restore_checkpoints",
        "venue_restore",
    ],
)
def test_a_decoder_is_recognised_by_every_name_the_architecture_gives_one(name: str) -> None:
    assert is_decoder(name)


@pytest.mark.parametrize("name", ["to_primitives", "margin_to_primitives", "restored", "decode"])
def test_an_encoder_or_another_function_is_not_a_decoder(name: str) -> None:
    assert not is_decoder(name)


def test_an_enum_read_by_a_decoders_helper_or_table_is_found_and_one_used_elsewhere_is_not() -> (
    None
):
    tree = ast.parse(
        "TABLE = {'X': Through}\n"
        "def basis_from_primitives(value):\n"
        "    return _read(value), TABLE\n"
        "def _read(value):\n"
        "    return Helped[value]\n"
        "class Snapshot:\n"
        "    @classmethod\n"
        "    def from_primitives(cls, value):\n"
        "        return cls._decode(value)\n"
        "    @staticmethod\n"
        "    def _decode(value):\n"
        "        return Method(value)\n"
        "def validate(value):\n"
        "    return Elsewhere[value]\n"
    )
    names = decoded_names(tree)
    assert {"Helped", "Through", "Method"} <= names
    assert "Elsewhere" not in names
    assert decoded_names(ast.parse("def to_primitives(v):\n    return Unread[v]\n")) == set()


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
