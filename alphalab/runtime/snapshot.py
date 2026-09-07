"""Complete, restorable snapshots of the composite execution-path state.

Projects :class:`~alphalab.runtime.execution_pipeline.ExecutionPipelineState`
into a versioned, JSON-serializable form and back again.

The composite state
-------------------
``ExecutionPipelineState`` is the state every environment threads: a backtest, a
replay, a paper run and a live session all advance the same value through the
same step. Three of the states it composes already had snapshots of their own --
:mod:`alphalab.portfolio.snapshot`, :mod:`alphalab.oms.snapshot` and, since v2.9,
:mod:`alphalab.allocation.snapshot` -- and this envelope reuses each of them
rather than decoding their contents a second time. Market, risk, execution,
analytics and the strategy runtime have no standalone consumer, so they are
carried inline and versioned by this envelope; giving each a public module and a
constant of its own would create five future churn points for nobody (ADR-0023).

What the projection changes, and why
------------------------------------
Only what JSON cannot carry, or what is not data at all:

* **Live objects are referenced, not reconstructed.** ``sizing_model``,
  ``simulator``, the optional ``instruments`` registry and every
  ``StrategyProtocol`` instance are recorded as a *type name* and required back
  from the caller. :func:`restore` raises when one is missing and raises when a
  supplied object is of the wrong type; it never substitutes ``None``. This is
  ADR-0014's rule, and the shape of :func:`alphalab.lifecycle.snapshot.restore`'s
  ``models`` argument, applied to the execution path.
* **Heterogeneous event logs are tagged.** Market, strategy, risk, execution and
  analytics events each carry an explicit ``event_type``, without which a decoder
  cannot know which class a payload describes.
* ``strategy[*].subscriptions`` is a ``frozenset``, which the encoder has no
  representation for, so it projects as a sorted tuple and restores as a
  frozenset. Sorting is what keeps the payload deterministic.
* ``market_prices``, ``unpriced_assets`` and the order book project as plain
  mappings and arrays; :func:`restore` rebuilds the persistent forms. ADR-0014
  settled that a restored value compares equal without reproducing lineage.

The identifier position is **data**, taken from ``state.id_position`` and never
from the ambient source. A capture is therefore a pure function of the state it
is given, which is the whole reason Step 5 put the cursor on the state. Restoring
it puts the position back on the state and does nothing else: installing a source
and continuing execution is a separate concern with a separate owner
(:func:`~alphalab.common.ids.id_source_for`), and this module never calls it.

Nothing here mints an identifier, runs a strategy, processes an event, executes
an order or contacts a venue.

Construction-time validation is replayed
----------------------------------------
``_require_one_account_currency`` is called by
:meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.initialize` and by
nothing else. A restore that skipped it could rebuild a state that ``initialize``
would have refused, and ADR-0019's guarantee would hold for started runs but not
for restored ones -- an invariant that stops being checked on the second path
into the same state is exactly what makes a round trip untrustworthy. This module
calls the *same* function rather than reimplementing the rule, and calls it
before the state is built, as ``initialize`` does.

Schema version
--------------
Every payload carries ``schema_version``; a missing or unreadable version is
refused. There is no legacy path: pipeline snapshots are new in v2.9, so there is
nothing to be compatible with. Nested payloads keep their own versions and their
own decoders, so an OMS payload inside this envelope is validated by
``OMS_SNAPSHOT_SCHEMA`` and a portfolio payload by ``PORTFOLIO_SNAPSHOT_SCHEMA``.

Round trip
----------
::

    payload = serialize(capture(state))
    restored = restore(from_primitives(deserialize(payload)), objects)
    assert restored == state
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any, Final

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.snapshot import AllocationSnapshot
from alphalab.allocation.snapshot import capture as capture_allocation
from alphalab.allocation.snapshot import from_primitives as allocation_from_primitives
from alphalab.allocation.snapshot import restore as restore_allocation
from alphalab.analytics.attribution import AttributionMetrics, TradeRecord
from alphalab.analytics.drawdown import DrawdownMetrics
from alphalab.analytics.engine import PortfolioSnapshot as EquityPoint
from alphalab.analytics.events import AnalyticsEvent, ReportGenerated
from alphalab.analytics.exposure import ExposureMetrics
from alphalab.analytics.report import PerformanceReport, ReturnSummary, RiskSummary
from alphalab.analytics.state import AnalyticsState
from alphalab.analytics.summary import TradeMetrics
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.ids import IdStreamPosition
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import Side
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.ids import AssetId, FillId, TradeId
from alphalab.core.ids import OrderId as CoreOrderId
from alphalab.core.order_request import OrderRequest
from alphalab.core.trade import Trade as CoreTrade
from alphalab.execution.events import (
    ExecutionCompleted,
    ExecutionEvent,
    ExecutionExpired,
    ExecutionPartiallyFilled,
    ExecutionRejected,
    ExecutionSubmitted,
)
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.execution.state import ExecutionState
from alphalab.instrument.registry import InstrumentRegistry
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import (
    BarClosed,
    BookUpdated,
    MarketEvent,
    QuoteReceived,
    SnapshotCreated,
    TickReceived,
    TradeReceived,
)
from alphalab.market.level import OrderBookLevel
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.state import MarketState
from alphalab.market.tick import Tick
from alphalab.oms.snapshot import OMSSnapshot
from alphalab.oms.snapshot import capture as capture_oms
from alphalab.oms.snapshot import from_primitives as oms_from_primitives
from alphalab.oms.snapshot import restore as restore_oms
from alphalab.persistence.decode import (
    as_bool,
    as_decimal,
    as_decimal_mapping,
    as_float,
    as_int,
    as_mapping,
    as_named_enum,
    as_optional_str,
    as_sequence,
    as_str,
    as_value_enum,
    require,
    require_schema_version,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.account import Account
from alphalab.portfolio.snapshot import PortfolioSnapshot
from alphalab.portfolio.snapshot import capture as capture_portfolio
from alphalab.portfolio.snapshot import from_primitives as portfolio_from_primitives
from alphalab.portfolio.snapshot import restore as restore_portfolio
from alphalab.risk.decision import RiskDecision
from alphalab.risk.events import (
    BuyingPowerUpdated,
    DrawdownTriggered,
    ExposureUpdated,
    MarginUpdated,
    RiskApproved,
    RiskCheckStarted,
    RiskEvent,
    RiskRejected,
)
from alphalab.risk.exposure import ExposureStatus
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
from alphalab.risk.margin import MarginStatus
from alphalab.risk.models import RiskViolation
from alphalab.risk.state import RiskState
from alphalab.runtime.execution_pipeline import (
    ExecutionPipelineConfig,
    ExecutionPipelineState,
    ExecutionRouting,
    UnpricedAsset,
    UnpricedReason,
    _require_one_account_currency,
)
from alphalab.strategy.events import (
    FillEvent,
    LifecycleTransitioned,
    OrderEvent,
    StrategyRuntimeEvent,
    TimerEvent,
)
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.state import LifecycleState, StrategyState
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "PIPELINE_SNAPSHOT_SCHEMA",
    "AnalyticsEventRecord",
    "ExecutionEventRecord",
    "MarketEventRecord",
    "PipelineSnapshot",
    "RiskEventRecord",
    "RuntimeObjects",
    "StrategyEventRecord",
    "StrategyRecord",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. See ADR-0023.
#:
#: A module-local literal rather than ``DEFAULT_SCHEMA_VERSION``, for the reason
#: v2.6 gave for the portfolio and v2.8 for the lifecycle: that constant also
#: versions ``CommonEvent`` and ``BaseEvent``, so bumping it would version every
#: event in the system as a side effect of one subsystem's change.
PIPELINE_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM: Final = "pipeline"


# ---------------------------------------------------------------------------
# Tagged event records: five heterogeneous logs the envelope carries inline
# ---------------------------------------------------------------------------


def _types[EventT](*classes: type[EventT]) -> Mapping[str, type[EventT]]:
    return {cls.__name__: cls for cls in classes}


_MARKET_EVENTS: Mapping[str, type[MarketEvent]] = _types(
    QuoteReceived, TickReceived, TradeReceived, BarClosed, BookUpdated, SnapshotCreated
)
_STRATEGY_EVENTS: Mapping[str, type[StrategyRuntimeEvent]] = _types(
    LifecycleTransitioned, FillEvent, OrderEvent, TimerEvent
)
_RISK_EVENTS: Mapping[str, type[RiskEvent]] = _types(
    RiskCheckStarted,
    RiskApproved,
    RiskRejected,
    MarginUpdated,
    ExposureUpdated,
    BuyingPowerUpdated,
    DrawdownTriggered,
)
_EXECUTION_EVENTS: Mapping[str, type[ExecutionEvent]] = _types(
    ExecutionSubmitted,
    ExecutionCompleted,
    ExecutionPartiallyFilled,
    ExecutionRejected,
    ExecutionExpired,
)
_ANALYTICS_EVENTS: Mapping[str, type[AnalyticsEvent]] = _types(ReportGenerated)


@dataclass(frozen=True, slots=True)
class MarketEventRecord:
    """One market event plus the tag needed to read it back as its own type."""

    event_type: str
    event: MarketEvent


@dataclass(frozen=True, slots=True)
class StrategyEventRecord:
    """One strategy runtime event plus its type tag."""

    event_type: str
    event: StrategyRuntimeEvent


@dataclass(frozen=True, slots=True)
class RiskEventRecord:
    """One risk event plus its type tag."""

    event_type: str
    event: RiskEvent


@dataclass(frozen=True, slots=True)
class ExecutionEventRecord:
    """One execution event plus its type tag."""

    event_type: str
    event: ExecutionEvent


@dataclass(frozen=True, slots=True)
class AnalyticsEventRecord:
    """One analytics event plus its type tag."""

    event_type: str
    event: AnalyticsEvent


# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigRecord:
    """The pipeline configuration, with its live objects reduced to type names.

    ``sizing_model_type``, ``simulator_type`` and ``instruments_type`` record
    *what the object was*, never the object. :func:`restore` requires the caller
    to supply each one back and checks the type it is given, exactly as
    :func:`alphalab.lifecycle.snapshot.restore` does for a trained model.
    ``instruments_type`` is ``None`` when the run configured no registry, which
    is fully supported -- and a run that had one must be given one back.
    """

    account: Account
    starting_cash: Decimal
    budget: CapitalBudget
    allocation_constraints: AllocationConstraints
    risk_limits: RiskLimits
    venue: str
    currency: str
    routing: ExecutionRouting
    sizing_model_type: str
    simulator_type: str
    instruments_type: str | None


@dataclass(frozen=True, slots=True)
class StrategyRecord:
    """One registered strategy's durable metadata, without its instance.

    ``instance_type`` records what the strategy was; the object itself is
    supplied back on restore. ``subscriptions`` is a sorted tuple because a
    ``frozenset`` has no deterministic JSON form. ``config`` is carried as the
    value the encoder wrote: a configuration the encoder cannot write is refused
    at capture, by the encoder, as it is for every other state in the repository.
    """

    strategy_id: str
    status: LifecycleState
    config: Any
    subscriptions: tuple[str, ...]
    last_error: str | None
    instance_type: str


@dataclass(frozen=True, slots=True)
class MarketRecord:
    """The market engine's indexes and logs, carried inline."""

    latest_quotes: Mapping[str, Quote]
    latest_books: Mapping[str, OrderBookSnapshot]
    latest_ticks: Mapping[str, Tick]
    latest_bars: Mapping[str, Bar]
    history: tuple[MarketEventRecord, ...]
    events: tuple[MarketEventRecord, ...]


@dataclass(frozen=True, slots=True)
class RiskRecord:
    """The risk engine's limits, utilisation and logs, carried inline."""

    active_limits: RiskLimits
    margin: MarginStatus
    exposure: ExposureStatus
    cash: Decimal
    buying_power: Decimal
    peak_nav: Decimal
    current_nav: Decimal
    daily_loss: Decimal
    history: tuple[RiskDecision, ...]
    events: tuple[RiskEventRecord, ...]


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """The execution engine's applied-report ledger and logs, carried inline."""

    reports: Mapping[str, ExecutionReport]
    history: tuple[ExecutionReport, ...]
    events: tuple[ExecutionEventRecord, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsRecord:
    """Compiled reports and analytics events, carried inline."""

    reports: tuple[PerformanceReport, ...]
    events: tuple[AnalyticsEventRecord, ...]


@dataclass(frozen=True, slots=True)
class PipelineSnapshot:
    """Complete, JSON-serializable projection of an :class:`ExecutionPipelineState`."""

    config: ConfigRecord
    market: MarketRecord
    strategy: tuple[StrategyRecord, ...]
    strategy_events: tuple[StrategyEventRecord, ...]
    allocation: AllocationSnapshot
    risk: RiskRecord
    oms: OMSSnapshot
    execution: ExecutionRecord
    portfolio: PortfolioSnapshot
    analytics: AnalyticsRecord
    market_prices: Mapping[str, Decimal]
    fills: tuple[CoreFill, ...]
    trades: tuple[CoreTrade, ...]
    trade_records: tuple[TradeRecord, ...]
    portfolio_snapshots: tuple[EquityPoint, ...]
    unpriced_assets: Mapping[str, UnpricedAsset]
    id_position: IdStreamPosition
    schema_version: int = PIPELINE_SNAPSHOT_SCHEMA


@dataclass(frozen=True, slots=True)
class RuntimeObjects:
    """The live objects a snapshot records but does not carry.

    Supplied by the caller on :func:`restore`, which raises when one is missing
    and raises when one is of a type the snapshot did not record. Nothing is
    substituted and nothing is constructed from a recorded name: a type string is
    evidence, never an instruction to import.

    Attributes:
        sizing_model: The allocation sizing model the run used.
        simulator: The execution simulator the run used.
        strategies: Strategy instances by ``strategy_id``.
        instruments: The instrument registry, when the run configured one. The
            snapshot says whether it did; supplying one for a run that had none,
            or omitting one for a run that had one, is refused.
    """

    sizing_model: object
    simulator: object
    strategies: Mapping[str, StrategyProtocol]
    instruments: InstrumentRegistry | None = None


# ---------------------------------------------------------------------------
# Capture -- a pure projection of the state it is given
# ---------------------------------------------------------------------------


def _type_name(value: object) -> str:
    return type(value).__name__


def _tagged[EventT, RecordT](log: Sequence[EventT], record: type[RecordT]) -> tuple[RecordT, ...]:
    return tuple(record(type(event).__name__, event) for event in log)  # type: ignore[call-arg]


def _capture_config(config: ExecutionPipelineConfig) -> ConfigRecord:
    return ConfigRecord(
        account=config.account,
        starting_cash=config.starting_cash,
        budget=config.budget,
        allocation_constraints=config.allocation_constraints,
        risk_limits=config.risk_limits,
        venue=config.venue,
        currency=config.currency,
        routing=config.routing,
        sizing_model_type=_type_name(config.sizing_model),
        simulator_type=_type_name(config.simulator),
        instruments_type=(None if config.instruments is None else _type_name(config.instruments)),
    )


def _capture_strategies(state: StrategyRuntimeState) -> tuple[StrategyRecord, ...]:
    return tuple(
        StrategyRecord(
            strategy_id=strategy_id,
            status=strategy.status,
            config=strategy.config,
            subscriptions=tuple(sorted(strategy.subscriptions)),
            last_error=strategy.last_error,
            instance_type=_type_name(strategy.instance),
        )
        for strategy_id, strategy in state.strategies.items()
    )


def capture(state: ExecutionPipelineState) -> PipelineSnapshot:
    """Project ``state`` into its complete serializable snapshot.

    Pure: nothing is mutated, no identifier is minted, and ``id_position`` comes
    from the state rather than from the ambient identifier source -- so a state
    captured while a different source happens to be installed still records its
    own position.
    """

    return PipelineSnapshot(
        config=_capture_config(state.config),
        market=MarketRecord(
            latest_quotes=dict(state.market.latest_quotes),
            latest_books=dict(state.market.latest_books),
            latest_ticks=dict(state.market.latest_ticks),
            latest_bars=dict(state.market.latest_bars),
            history=_tagged(state.market.history, MarketEventRecord),
            events=_tagged(state.market.events, MarketEventRecord),
        ),
        strategy=_capture_strategies(state.strategy),
        strategy_events=_tagged(state.strategy.events, StrategyEventRecord),
        allocation=capture_allocation(state.allocation),
        risk=RiskRecord(
            active_limits=state.risk.active_limits,
            margin=state.risk.margin,
            exposure=state.risk.exposure,
            cash=state.risk.cash,
            buying_power=state.risk.buying_power,
            peak_nav=state.risk.peak_nav,
            current_nav=state.risk.current_nav,
            daily_loss=state.risk.daily_loss,
            history=state.risk.history.to_tuple(),
            events=_tagged(state.risk.events, RiskEventRecord),
        ),
        oms=capture_oms(state.oms),
        execution=ExecutionRecord(
            reports=dict(state.execution.reports),
            history=state.execution.history.to_tuple(),
            events=_tagged(state.execution.events, ExecutionEventRecord),
        ),
        portfolio=capture_portfolio(state.portfolio),
        analytics=AnalyticsRecord(
            reports=tuple(state.analytics.reports),
            events=_tagged(state.analytics.events, AnalyticsEventRecord),
        ),
        market_prices=dict(state.market_prices),
        fills=state.fills.to_tuple(),
        trades=state.trades.to_tuple(),
        trade_records=state.trade_records.to_tuple(),
        portfolio_snapshots=state.portfolio_snapshots.to_tuple(),
        unpriced_assets=dict(state.unpriced_assets),
        id_position=state.id_position,
    )


# ---------------------------------------------------------------------------
# Restore -- reconstruction, with live objects supplied back
# ---------------------------------------------------------------------------


def _require_object(supplied: object | None, recorded: str, what: str) -> Any:
    """Take a live object back from the caller, or refuse.

    Two checks and no third behaviour, following
    :func:`alphalab.lifecycle.snapshot.restore`'s ``_require_model``: the object
    must be supplied, and it must be what the snapshot recorded. ``None`` is
    never substituted, and the recorded name is never used to import anything.
    """

    if supplied is None:
        raise StateDecodeError(
            f"No {what} supplied. A pipeline snapshot records that it was a "
            f"{recorded} and never the object itself; pass it back through "
            "restore(snapshot, RuntimeObjects(...))."
        )
    actual = _type_name(supplied)
    if actual != recorded:
        raise StateDecodeError(
            f"The {what} supplied is a {actual}, but the snapshot recorded a {recorded}."
        )
    return supplied


def _restore_config(record: ConfigRecord, objects: RuntimeObjects) -> ExecutionPipelineConfig:
    if record.instruments_type is None:
        if objects.instruments is not None:
            raise StateDecodeError(
                "An instrument registry was supplied, but the snapshot records that "
                "the run configured none. Leaving it None is fully supported and "
                "supplying one would change what the run could say about an "
                "unpriced asset."
            )
        instruments = None
    else:
        instruments = _require_object(
            objects.instruments, record.instruments_type, "instrument registry"
        )

    return ExecutionPipelineConfig(
        account=record.account,
        starting_cash=record.starting_cash,
        budget=record.budget,
        allocation_constraints=record.allocation_constraints,
        risk_limits=record.risk_limits,
        sizing_model=_require_object(
            objects.sizing_model, record.sizing_model_type, "sizing model"
        ),
        simulator=_require_object(objects.simulator, record.simulator_type, "simulator"),
        venue=record.venue,
        currency=record.currency,
        routing=record.routing,
        instruments=instruments,
    )


def _restore_strategies(
    records: tuple[StrategyRecord, ...],
    events: tuple[StrategyEventRecord, ...],
    objects: RuntimeObjects,
) -> StrategyRuntimeState:
    strategies = {
        record.strategy_id: StrategyState(
            strategy_id=record.strategy_id,
            status=record.status,
            instance=_require_object(
                objects.strategies.get(record.strategy_id),
                record.instance_type,
                f"strategy instance for {record.strategy_id!r}",
            ),
            config=record.config,
            subscriptions=frozenset(record.subscriptions),
            last_error=record.last_error,
        )
        for record in records
    }
    return StrategyRuntimeState(
        strategies=strategies, events=tuple(record.event for record in events)
    )


def restore(snapshot: PipelineSnapshot, objects: RuntimeObjects) -> ExecutionPipelineState:
    """Rebuild the state a snapshot was captured from.

    Args:
        snapshot: The captured projection.
        objects: The live objects the snapshot recorded but did not carry. Every
            one must be present and of the recorded type.

    Returns:
        The reconstructed state, with ``id_position`` set to the captured
        position. No identifier source is installed and no identifier is minted:
        continuing a run is the caller's next step, not this function's.

    Raises:
        StateDecodeError: If a required live object is missing or of the wrong
            type.
        RuntimeValidationError: If the restored configuration fails a validation
            :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.initialize`
            enforces -- today, that its two currencies agree. The check runs
            before the state is built, so nothing is reconstructed against a
            refused configuration.
    """

    config = _restore_config(snapshot.config, objects)
    _require_one_account_currency(config)

    return ExecutionPipelineState(
        config=config,
        market=MarketState(
            latest_quotes=PersistentMap(snapshot.market.latest_quotes),
            latest_books=PersistentMap(snapshot.market.latest_books),
            latest_ticks=PersistentMap(snapshot.market.latest_ticks),
            latest_bars=PersistentMap(snapshot.market.latest_bars),
            history=AppendOnlyLog(record.event for record in snapshot.market.history),
            events=AppendOnlyLog(record.event for record in snapshot.market.events),
        ),
        strategy=_restore_strategies(snapshot.strategy, snapshot.strategy_events, objects),
        allocation=restore_allocation(snapshot.allocation),
        risk=RiskState(
            active_limits=snapshot.risk.active_limits,
            margin=snapshot.risk.margin,
            exposure=snapshot.risk.exposure,
            cash=snapshot.risk.cash,
            buying_power=snapshot.risk.buying_power,
            peak_nav=snapshot.risk.peak_nav,
            current_nav=snapshot.risk.current_nav,
            daily_loss=snapshot.risk.daily_loss,
            history=AppendOnlyLog(snapshot.risk.history),
            events=AppendOnlyLog(record.event for record in snapshot.risk.events),
        ),
        oms=restore_oms(snapshot.oms),
        execution=ExecutionState(
            reports=PersistentMap(snapshot.execution.reports),
            history=AppendOnlyLog(snapshot.execution.history),
            events=AppendOnlyLog(record.event for record in snapshot.execution.events),
        ),
        portfolio=restore_portfolio(snapshot.portfolio),
        analytics=AnalyticsState(
            reports=snapshot.analytics.reports,
            events=tuple(record.event for record in snapshot.analytics.events),
        ),
        market_prices=dict(snapshot.market_prices),
        fills=AppendOnlyLog(snapshot.fills),
        trades=AppendOnlyLog(snapshot.trades),
        trade_records=AppendOnlyLog(snapshot.trade_records),
        portfolio_snapshots=AppendOnlyLog(snapshot.portfolio_snapshots),
        unpriced_assets=PersistentMap(snapshot.unpriced_assets),
        id_position=snapshot.id_position,
    )


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _mapping_of[ValueT](value: Any, where: str, decode: Any) -> dict[str, ValueT]:
    """Decode a JSON object whose values all decode the same way."""

    payload = as_mapping(value, where)
    return {
        as_str(key, f"{where} key"): decode(item, f"{where}[{key}]")
        for key, item in payload.items()
    }


def _sequence_of[ItemT](value: Any, where: str, decode: Any) -> tuple[ItemT, ...]:
    return tuple(
        decode(item, f"{where}[{index}]") for index, item in enumerate(as_sequence(value, where))
    )


def _event[EventT](
    value: Any,
    where: str,
    known: Mapping[str, type[EventT]],
    subsystem: str,
    decoders: Mapping[str, Any],
) -> tuple[str, EventT]:
    """Decode one tagged event, refusing a tag this build does not know."""

    record = as_mapping(value, where)
    event_type = as_str(require(record, "event_type"), f"{where}.event_type")
    cls = known.get(event_type)
    if cls is None:
        names = ", ".join(sorted(known))
        raise StateDecodeError(
            f"{where}.event_type is not a {subsystem} event: {event_type!r}; "
            f"expected one of {names}"
        )

    payload = as_mapping(require(record, "event"), f"{where}.event")
    kwargs: dict[str, Any] = {}
    for field in fields(cls):  # type: ignore[arg-type]
        raw = require(payload, field.name)
        decoder = decoders.get(field.name)
        kwargs[field.name] = (
            decoder(raw, f"{where}.{field.name}")
            if decoder is not None
            else as_str(raw, f"{where}.{field.name}")
        )
    return event_type, cls(**kwargs)


# --- value types ------------------------------------------------------------


def _account(value: Any, where: str = "config.account") -> Account:
    payload = as_mapping(value, where)
    return Account(
        account_id=as_str(require(payload, "account_id"), f"{where}.account_id"),
        base_currency=as_str(require(payload, "base_currency"), f"{where}.base_currency"),
        name=as_str(require(payload, "name"), f"{where}.name"),
        created_at=as_float(require(payload, "created_at"), f"{where}.created_at"),
        status=as_str(require(payload, "status"), f"{where}.status"),
        metadata=dict(as_mapping(require(payload, "metadata"), f"{where}.metadata")),
    )


def _budget(value: Any, where: str = "config.budget") -> CapitalBudget:
    payload = as_mapping(value, where)
    return CapitalBudget(
        global_capital=as_decimal(require(payload, "global_capital"), f"{where}.global_capital"),
        maximum_exposure=as_decimal(
            require(payload, "maximum_exposure"), f"{where}.maximum_exposure"
        ),
        cash_buffer=as_decimal(require(payload, "cash_buffer"), f"{where}.cash_buffer"),
        strategy_budgets=as_decimal_mapping(
            require(payload, "strategy_budgets"), f"{where}.strategy_budgets"
        ),
    )


def _constraints(value: Any, where: str = "config.allocation_constraints") -> AllocationConstraints:
    payload = as_mapping(value, where)
    return AllocationConstraints(
        max_weight_per_asset=as_decimal(
            require(payload, "max_weight_per_asset"), f"{where}.max_weight_per_asset"
        ),
        min_weight_per_asset=as_decimal(
            require(payload, "min_weight_per_asset"), f"{where}.min_weight_per_asset"
        ),
        allow_shorting=as_bool(require(payload, "allow_shorting"), f"{where}.allow_shorting"),
        enforce_integer_quantities=as_bool(
            require(payload, "enforce_integer_quantities"), f"{where}.enforce_integer_quantities"
        ),
    )


def _risk_limits(value: Any, where: str) -> RiskLimits:
    payload = as_mapping(value, where)

    def limit(key: str, cls: Any, *names: str) -> Any:
        inner = as_mapping(require(payload, key), f"{where}.{key}")
        return cls(
            **{name: as_decimal(require(inner, name), f"{where}.{key}.{name}") for name in names}
        )

    return RiskLimits(
        order_size=limit("order_size", OrderSizeLimit, "max_quantity", "max_notional"),
        position=limit("position", PositionLimit, "max_quantity", "max_notional"),
        exposure=limit("exposure", ExposureLimit, "max_gross_exposure", "max_net_exposure"),
        leverage=limit("leverage", LeverageLimit, "max_leverage"),
        margin=limit("margin", MarginLimit, "max_margin_utilization"),
        daily_loss=limit("daily_loss", DailyLossLimit, "max_daily_loss"),
        drawdown=limit("drawdown", DrawdownLimit, "max_drawdown_pct"),
    )


def _quote(value: Any, where: str) -> Quote:
    payload = as_mapping(value, where)
    return Quote(
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        bid=as_decimal(require(payload, "bid"), f"{where}.bid"),
        ask=as_decimal(require(payload, "ask"), f"{where}.ask"),
        bid_size=as_decimal(require(payload, "bid_size"), f"{where}.bid_size"),
        ask_size=as_decimal(require(payload, "ask_size"), f"{where}.ask_size"),
        venue=as_str(require(payload, "venue"), f"{where}.venue"),
        currency=as_str(require(payload, "currency"), f"{where}.currency"),
    )


def _tick(value: Any, where: str) -> Tick:
    payload = as_mapping(value, where)
    return Tick(
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        price=as_decimal(require(payload, "price"), f"{where}.price"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
        trade_id=as_str(require(payload, "trade_id"), f"{where}.trade_id"),
        venue=as_str(require(payload, "venue"), f"{where}.venue"),
        currency=as_str(require(payload, "currency"), f"{where}.currency"),
    )


def _bar(value: Any, where: str) -> Bar:
    payload = as_mapping(value, where)
    return Bar(
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        open=as_decimal(require(payload, "open"), f"{where}.open"),
        high=as_decimal(require(payload, "high"), f"{where}.high"),
        low=as_decimal(require(payload, "low"), f"{where}.low"),
        close=as_decimal(require(payload, "close"), f"{where}.close"),
        volume=as_decimal(require(payload, "volume"), f"{where}.volume"),
        vwap=as_decimal(require(payload, "vwap"), f"{where}.vwap"),
        trade_count=as_int(require(payload, "trade_count"), f"{where}.trade_count"),
        timeframe=as_named_enum(TimeFrame, require(payload, "timeframe"), f"{where}.timeframe"),
    )


def _book(value: Any, where: str) -> OrderBookSnapshot:
    payload = as_mapping(value, where)

    def level(item: Any, at: str) -> OrderBookLevel:
        inner = as_mapping(item, at)
        return OrderBookLevel(
            price=as_decimal(require(inner, "price"), f"{at}.price"),
            size=as_decimal(require(inner, "size"), f"{at}.size"),
            orders=as_int(require(inner, "orders"), f"{at}.orders"),
        )

    return OrderBookSnapshot(
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        bids=_sequence_of(require(payload, "bids"), f"{where}.bids", level),
        asks=_sequence_of(require(payload, "asks"), f"{where}.asks", level),
        sequence=as_int(require(payload, "sequence"), f"{where}.sequence"),
    )


def _contribution(value: Any, where: str) -> StrategyContribution:
    payload = as_mapping(value, where)
    return StrategyContribution(
        strategy_id=as_str(require(payload, "strategy_id"), f"{where}.strategy_id"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
    )


def _order_request(value: Any, where: str) -> OrderRequest:
    payload = as_mapping(value, where)
    return OrderRequest(
        order_id=as_str(require(payload, "order_id"), f"{where}.order_id"),
        strategy_id=as_str(require(payload, "strategy_id"), f"{where}.strategy_id"),
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        side=as_value_enum(Side, require(payload, "side"), f"{where}.side"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
        price=as_decimal(require(payload, "price"), f"{where}.price"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        contributions=_sequence_of(
            require(payload, "contributions"), f"{where}.contributions", _contribution
        ),
    )


def _margin(value: Any, where: str) -> MarginStatus:
    payload = as_mapping(value, where)
    return MarginStatus(
        **{
            name: as_decimal(require(payload, name), f"{where}.{name}")
            for name in ("initial_margin", "maintenance_margin", "available_margin", "margin_used")
        }
    )


def _exposure(value: Any, where: str) -> ExposureStatus:
    payload = as_mapping(value, where)
    return ExposureStatus(
        gross_exposure=as_decimal(require(payload, "gross_exposure"), f"{where}.gross_exposure"),
        net_exposure=as_decimal(require(payload, "net_exposure"), f"{where}.net_exposure"),
        long_exposure=as_decimal(require(payload, "long_exposure"), f"{where}.long_exposure"),
        short_exposure=as_decimal(require(payload, "short_exposure"), f"{where}.short_exposure"),
        asset_exposure=as_decimal_mapping(
            require(payload, "asset_exposure"), f"{where}.asset_exposure"
        ),
        sector_exposure=as_decimal_mapping(
            require(payload, "sector_exposure"), f"{where}.sector_exposure"
        ),
    )


def _violation(value: Any, where: str) -> RiskViolation:
    payload = as_mapping(value, where)
    return RiskViolation(
        rule=as_str(require(payload, "rule"), f"{where}.rule"),
        description=as_str(require(payload, "description"), f"{where}.description"),
        severity=as_str(require(payload, "severity"), f"{where}.severity"),
        current_value=as_decimal(require(payload, "current_value"), f"{where}.current_value"),
        allowed_value=as_decimal(require(payload, "allowed_value"), f"{where}.allowed_value"),
    )


def _decision(value: Any, where: str) -> RiskDecision:
    payload = as_mapping(value, where)
    return RiskDecision(
        decision_id=as_str(require(payload, "decision_id"), f"{where}.decision_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        order_id=as_str(require(payload, "order_id"), f"{where}.order_id"),
        approved=as_bool(require(payload, "approved"), f"{where}.approved"),
        reason=as_str(require(payload, "reason"), f"{where}.reason"),
        violations=_sequence_of(require(payload, "violations"), f"{where}.violations", _violation),
        required_margin=as_decimal(require(payload, "required_margin"), f"{where}.required_margin"),
        remaining_buying_power=as_decimal(
            require(payload, "remaining_buying_power"), f"{where}.remaining_buying_power"
        ),
        exposure=_exposure(require(payload, "exposure"), f"{where}.exposure"),
    )


def _report(value: Any, where: str) -> ExecutionReport:
    payload = as_mapping(value, where)
    return ExecutionReport(
        execution_id=as_str(require(payload, "execution_id"), f"{where}.execution_id"),
        order_id=as_str(require(payload, "order_id"), f"{where}.order_id"),
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        strategy_id=as_str(require(payload, "strategy_id"), f"{where}.strategy_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        fill_price=as_decimal(require(payload, "fill_price"), f"{where}.fill_price"),
        fill_quantity=as_decimal(require(payload, "fill_quantity"), f"{where}.fill_quantity"),
        commission=as_decimal(require(payload, "commission"), f"{where}.commission"),
        slippage=as_decimal(require(payload, "slippage"), f"{where}.slippage"),
        liquidity_flag=as_str(require(payload, "liquidity_flag"), f"{where}.liquidity_flag"),
        venue=as_str(require(payload, "venue"), f"{where}.venue"),
        currency=as_str(require(payload, "currency"), f"{where}.currency"),
        status=as_named_enum(FillStatus, require(payload, "status"), f"{where}.status"),
    )


def _floats(value: Any, where: str) -> tuple[float, ...]:
    return tuple(
        as_float(item, f"{where}[{index}]") for index, item in enumerate(as_sequence(value, where))
    )


def _performance(value: Any, where: str) -> PerformanceReport:
    payload = as_mapping(value, where)

    def block(key: str, cls: Any, floats: Sequence[str] = (), decimals: Sequence[str] = ()) -> Any:
        inner = as_mapping(require(payload, key), f"{where}.{key}")
        kwargs: dict[str, Any] = {
            name: as_float(require(inner, name), f"{where}.{key}.{name}") for name in floats
        }
        kwargs |= {
            name: as_decimal(require(inner, name), f"{where}.{key}.{name}") for name in decimals
        }
        return cls, inner, kwargs

    cls, inner, kwargs = block(
        "returns", ReturnSummary, ("total_return", "cagr", "arithmetic_return", "geometric_return")
    )
    returns = cls(
        **kwargs,
        daily_returns=_floats(require(inner, "daily_returns"), f"{where}.returns.daily_returns"),
    )

    cls, inner, kwargs = block(
        "risk",
        RiskSummary,
        (
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "value_at_risk_95",
            "cvar_95",
            "annualized_volatility",
        ),
    )
    risk = cls(**kwargs)

    cls, inner, kwargs = block("drawdowns", DrawdownMetrics, ("max_drawdown", "ulcer_index"))
    drawdowns = cls(
        **kwargs,
        drawdowns=_floats(require(inner, "drawdowns"), f"{where}.drawdowns.drawdowns"),
    )

    cls, inner, kwargs = block(
        "exposure", ExposureMetrics, ("cash_pct", "leverage"), ("gross", "net", "long", "short")
    )
    exposure = cls(**kwargs)

    cls, inner, kwargs = block(
        "trades",
        TradeMetrics,
        ("win_rate", "loss_rate", "profit_factor", "avg_holding_period", "turnover"),
        ("avg_win", "avg_loss", "expectancy"),
    )
    trades = cls(**kwargs)

    attribution_at = f"{where}.attribution"
    attribution_payload = as_mapping(require(payload, "attribution"), attribution_at)
    attribution = AttributionMetrics(
        pnl_by_strategy=as_decimal_mapping(
            require(attribution_payload, "pnl_by_strategy"), f"{attribution_at}.pnl_by_strategy"
        ),
        pnl_by_asset=as_decimal_mapping(
            require(attribution_payload, "pnl_by_asset"), f"{attribution_at}.pnl_by_asset"
        ),
        pnl_by_sector=as_decimal_mapping(
            require(attribution_payload, "pnl_by_sector"), f"{attribution_at}.pnl_by_sector"
        ),
    )

    return PerformanceReport(
        report_id=as_str(require(payload, "report_id"), f"{where}.report_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        returns=returns,
        risk=risk,
        drawdowns=drawdowns,
        exposure=exposure,
        trades=trades,
        attribution=attribution,
        ending_capital=as_decimal(require(payload, "ending_capital"), f"{where}.ending_capital"),
    )


def _fill(value: Any, where: str) -> CoreFill:
    payload = as_mapping(value, where)
    return CoreFill(
        fill_id=FillId(as_str(require(payload, "fill_id"), f"{where}.fill_id")),
        order_id=CoreOrderId(as_str(require(payload, "order_id"), f"{where}.order_id")),
        asset_id=AssetId(as_str(require(payload, "asset_id"), f"{where}.asset_id")),
        side=as_value_enum(Side, require(payload, "side"), f"{where}.side"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
        price=as_decimal(require(payload, "price"), f"{where}.price"),
        filled_at=as_float(require(payload, "filled_at"), f"{where}.filled_at"),
        commission=as_decimal(require(payload, "commission"), f"{where}.commission"),
    )


def _trade(value: Any, where: str) -> CoreTrade:
    payload = as_mapping(value, where)
    order_id = as_optional_str(require(payload, "order_id"), f"{where}.order_id")
    return CoreTrade(
        trade_id=TradeId(as_str(require(payload, "trade_id"), f"{where}.trade_id")),
        asset_id=AssetId(as_str(require(payload, "asset_id"), f"{where}.asset_id")),
        side=as_value_enum(Side, require(payload, "side"), f"{where}.side"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
        average_price=as_decimal(require(payload, "average_price"), f"{where}.average_price"),
        fill_ids=tuple(
            FillId(as_str(item, f"{where}.fill_ids[{index}]"))
            for index, item in enumerate(
                as_sequence(require(payload, "fill_ids"), f"{where}.fill_ids")
            )
        ),
        executed_at=as_float(require(payload, "executed_at"), f"{where}.executed_at"),
        order_id=None if order_id is None else CoreOrderId(order_id),
    )


def _trade_record(value: Any, where: str) -> TradeRecord:
    payload = as_mapping(value, where)
    holding = require(payload, "holding_period_seconds")
    return TradeRecord(
        trade_id=as_str(require(payload, "trade_id"), f"{where}.trade_id"),
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        sector_id=as_optional_str(require(payload, "sector_id"), f"{where}.sector_id"),
        realized_pnl=as_decimal(require(payload, "realized_pnl"), f"{where}.realized_pnl"),
        notional_value=as_decimal(require(payload, "notional_value"), f"{where}.notional_value"),
        holding_period_seconds=(
            None if holding is None else as_float(holding, f"{where}.holding_period_seconds")
        ),
        contributions=_sequence_of(
            require(payload, "contributions"), f"{where}.contributions", _contribution
        ),
    )


def _equity_point(value: Any, where: str) -> EquityPoint:
    payload = as_mapping(value, where)
    return EquityPoint(
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        total_equity=as_decimal(require(payload, "total_equity"), f"{where}.total_equity"),
        cash=as_decimal(require(payload, "cash"), f"{where}.cash"),
        long_exposure=as_decimal(require(payload, "long_exposure"), f"{where}.long_exposure"),
        short_exposure=as_decimal(require(payload, "short_exposure"), f"{where}.short_exposure"),
    )


def _unpriced(value: Any, where: str) -> UnpricedAsset:
    payload = as_mapping(value, where)
    return UnpricedAsset(
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        reason=as_named_enum(UnpricedReason, require(payload, "reason"), f"{where}.reason"),
        detail=as_str(require(payload, "detail"), f"{where}.detail"),
        first_timestamp=as_float(require(payload, "first_timestamp"), f"{where}.first_timestamp"),
        last_timestamp=as_float(require(payload, "last_timestamp"), f"{where}.last_timestamp"),
        occurrences=as_int(require(payload, "occurrences"), f"{where}.occurrences"),
    )


def _id_position(value: Any, where: str = "id_position") -> IdStreamPosition:
    payload = as_mapping(value, where)
    seed = require(payload, "seed")
    return IdStreamPosition(
        seed=None if seed is None else as_int(seed, f"{where}.seed"),
        draws=as_int(require(payload, "draws"), f"{where}.draws"),
    )


# --- event field decoders, per subsystem ------------------------------------

_MARKET_FIELDS: Mapping[str, Any] = {
    "timestamp": as_float,
    "quote": _quote,
    "tick": _tick,
    "bar": _bar,
    "snapshot": _book,
}
_STRATEGY_FIELDS: Mapping[str, Any] = {
    "timestamp": as_float,
    "fill_quantity": as_decimal,
    "fill_price": as_decimal,
}
_RISK_FIELDS: Mapping[str, Any] = {
    "timestamp": as_float,
    "request": _order_request,
    "margin_used": as_decimal,
    "available_margin": as_decimal,
    "gross_exposure": as_decimal,
    "net_exposure": as_decimal,
    "buying_power": as_decimal,
    "current_drawdown": as_decimal,
    "max_drawdown": as_decimal,
}
_EXECUTION_FIELDS: Mapping[str, Any] = {
    "timestamp": as_float,
    "quantity": as_decimal,
    "price": as_decimal,
    "fill_price": as_decimal,
    "fill_quantity": as_decimal,
    "remaining_quantity": as_decimal,
}
_ANALYTICS_FIELDS: Mapping[str, Any] = {
    "timestamp": as_float,
    "num_snapshots": as_int,
    "num_trades": as_int,
}


def _config(value: Any) -> ConfigRecord:
    where = "config"
    payload = as_mapping(value, where)
    instruments = require(payload, "instruments_type")
    return ConfigRecord(
        account=_account(require(payload, "account")),
        starting_cash=as_decimal(require(payload, "starting_cash"), f"{where}.starting_cash"),
        budget=_budget(require(payload, "budget")),
        allocation_constraints=_constraints(require(payload, "allocation_constraints")),
        risk_limits=_risk_limits(require(payload, "risk_limits"), f"{where}.risk_limits"),
        venue=as_str(require(payload, "venue"), f"{where}.venue"),
        currency=as_str(require(payload, "currency"), f"{where}.currency"),
        routing=as_named_enum(ExecutionRouting, require(payload, "routing"), f"{where}.routing"),
        sizing_model_type=as_str(
            require(payload, "sizing_model_type"), f"{where}.sizing_model_type"
        ),
        simulator_type=as_str(require(payload, "simulator_type"), f"{where}.simulator_type"),
        instruments_type=(
            None if instruments is None else as_str(instruments, f"{where}.instruments_type")
        ),
    )


def _strategy_record(value: Any, where: str) -> StrategyRecord:
    payload = as_mapping(value, where)
    return StrategyRecord(
        strategy_id=as_str(require(payload, "strategy_id"), f"{where}.strategy_id"),
        status=as_named_enum(LifecycleState, require(payload, "status"), f"{where}.status"),
        config=require(payload, "config"),
        subscriptions=tuple(
            as_str(item, f"{where}.subscriptions[{index}]")
            for index, item in enumerate(
                as_sequence(require(payload, "subscriptions"), f"{where}.subscriptions")
            )
        ),
        last_error=as_optional_str(require(payload, "last_error"), f"{where}.last_error"),
        instance_type=as_str(require(payload, "instance_type"), f"{where}.instance_type"),
    )


def _market(value: Any) -> MarketRecord:
    where = "market"
    payload = as_mapping(value, where)

    def log(key: str) -> tuple[MarketEventRecord, ...]:
        return tuple(
            MarketEventRecord(
                *_event(
                    item,
                    f"{where}.{key}[{index}]",
                    _MARKET_EVENTS,
                    "market",
                    _MARKET_FIELDS,
                )
            )
            for index, item in enumerate(as_sequence(require(payload, key), f"{where}.{key}"))
        )

    return MarketRecord(
        latest_quotes=_mapping_of(
            require(payload, "latest_quotes"), f"{where}.latest_quotes", _quote
        ),
        latest_books=_mapping_of(require(payload, "latest_books"), f"{where}.latest_books", _book),
        latest_ticks=_mapping_of(require(payload, "latest_ticks"), f"{where}.latest_ticks", _tick),
        latest_bars=_mapping_of(require(payload, "latest_bars"), f"{where}.latest_bars", _bar),
        history=log("history"),
        events=log("events"),
    )


def _risk(value: Any) -> RiskRecord:
    where = "risk"
    payload = as_mapping(value, where)
    return RiskRecord(
        active_limits=_risk_limits(require(payload, "active_limits"), f"{where}.active_limits"),
        margin=_margin(require(payload, "margin"), f"{where}.margin"),
        exposure=_exposure(require(payload, "exposure"), f"{where}.exposure"),
        cash=as_decimal(require(payload, "cash"), f"{where}.cash"),
        buying_power=as_decimal(require(payload, "buying_power"), f"{where}.buying_power"),
        peak_nav=as_decimal(require(payload, "peak_nav"), f"{where}.peak_nav"),
        current_nav=as_decimal(require(payload, "current_nav"), f"{where}.current_nav"),
        daily_loss=as_decimal(require(payload, "daily_loss"), f"{where}.daily_loss"),
        history=_sequence_of(require(payload, "history"), f"{where}.history", _decision),
        events=tuple(
            RiskEventRecord(
                *_event(item, f"{where}.events[{index}]", _RISK_EVENTS, "risk", _RISK_FIELDS)
            )
            for index, item in enumerate(as_sequence(require(payload, "events"), f"{where}.events"))
        ),
    )


def _execution(value: Any) -> ExecutionRecord:
    where = "execution"
    payload = as_mapping(value, where)
    return ExecutionRecord(
        reports=_mapping_of(require(payload, "reports"), f"{where}.reports", _report),
        history=_sequence_of(require(payload, "history"), f"{where}.history", _report),
        events=tuple(
            ExecutionEventRecord(
                *_event(
                    item,
                    f"{where}.events[{index}]",
                    _EXECUTION_EVENTS,
                    "execution",
                    _EXECUTION_FIELDS,
                )
            )
            for index, item in enumerate(as_sequence(require(payload, "events"), f"{where}.events"))
        ),
    )


def _analytics(value: Any) -> AnalyticsRecord:
    where = "analytics"
    payload = as_mapping(value, where)
    return AnalyticsRecord(
        reports=_sequence_of(require(payload, "reports"), f"{where}.reports", _performance),
        events=tuple(
            AnalyticsEventRecord(
                *_event(
                    item,
                    f"{where}.events[{index}]",
                    _ANALYTICS_EVENTS,
                    "analytics",
                    _ANALYTICS_FIELDS,
                )
            )
            for index, item in enumerate(as_sequence(require(payload, "events"), f"{where}.events"))
        ),
    )


def from_primitives(payload: Mapping[str, Any]) -> PipelineSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`PipelineSnapshot`.

    Nested payloads are decoded by the module that owns them, so an OMS payload
    is validated against ``OMS_SNAPSHOT_SCHEMA`` and a portfolio payload against
    ``PORTFOLIO_SNAPSHOT_SCHEMA`` -- their errors reach the caller as their own,
    not flattened into an opaque pipeline failure.

    Raises:
        StateDecodeError: If the payload is not an object, declares no schema
            version or one this build does not read, is missing a field, or holds
            a value of the wrong type. The message names the field.
    """

    payload = as_mapping(payload, "pipeline snapshot")
    require_schema_version(payload, PIPELINE_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    return PipelineSnapshot(
        config=_config(require(payload, "config")),
        market=_market(require(payload, "market")),
        strategy=_sequence_of(require(payload, "strategy"), "strategy", _strategy_record),
        strategy_events=tuple(
            StrategyEventRecord(
                *_event(
                    item,
                    f"strategy_events[{index}]",
                    _STRATEGY_EVENTS,
                    "strategy",
                    _STRATEGY_FIELDS,
                )
            )
            for index, item in enumerate(
                as_sequence(require(payload, "strategy_events"), "strategy_events")
            )
        ),
        allocation=allocation_from_primitives(
            as_mapping(require(payload, "allocation"), "allocation")
        ),
        risk=_risk(require(payload, "risk")),
        oms=oms_from_primitives(as_mapping(require(payload, "oms"), "oms")),
        execution=_execution(require(payload, "execution")),
        portfolio=portfolio_from_primitives(as_mapping(require(payload, "portfolio"), "portfolio")),
        analytics=_analytics(require(payload, "analytics")),
        market_prices=as_decimal_mapping(require(payload, "market_prices"), "market_prices"),
        fills=_sequence_of(require(payload, "fills"), "fills", _fill),
        trades=_sequence_of(require(payload, "trades"), "trades", _trade),
        trade_records=_sequence_of(
            require(payload, "trade_records"), "trade_records", _trade_record
        ),
        portfolio_snapshots=_sequence_of(
            require(payload, "portfolio_snapshots"), "portfolio_snapshots", _equity_point
        ),
        unpriced_assets=_mapping_of(
            require(payload, "unpriced_assets"), "unpriced_assets", _unpriced
        ),
        id_position=_id_position(require(payload, "id_position")),
        schema_version=PIPELINE_SNAPSHOT_SCHEMA,
    )
