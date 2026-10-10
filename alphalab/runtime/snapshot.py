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

import math
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import time
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
from alphalab.analytics.report import PerformanceReport, Periodicity, ReturnSummary, RiskSummary
from alphalab.analytics.state import AnalyticsState
from alphalab.analytics.summary import TradeMetrics
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.currency_units import CurrencyUnits
from alphalab.common.evolve import evolve
from alphalab.common.exceptions import AlphaLabError, AlphaLabValidationError
from alphalab.common.ids import IdStreamPosition
from alphalab.common.persistent_map import PersistentMap
from alphalab.common.serialization import to_serializable
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import Side
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.ids import AssetId, FillId, TradeId
from alphalab.core.ids import OrderId as CoreOrderId
from alphalab.core.order_request import OrderRequest
from alphalab.core.trade import Trade as CoreTrade
from alphalab.data.feed import TradeAggressor
from alphalab.execution.events import (
    ExecutionCompleted,
    ExecutionEvent,
    ExecutionExpired,
    ExecutionPartiallyFilled,
    ExecutionRejected,
    ExecutionSubmitted,
)
from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import FillTiming
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
from alphalab.market.exceptions import MarketValidationError
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
    MARKET_TERMS_PAYLOAD,
    as_bool,
    as_decimal,
    as_decimal_mapping,
    as_float,
    as_int,
    as_mapping,
    as_named_enum,
    as_optional_decimal,
    as_optional_str,
    as_order_terms,
    as_sequence,
    as_str,
    as_value_enum,
    require,
)
from alphalab.persistence.exceptions import SerializationError, StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.persistence.upgrade import SchemaHistory, SchemaStep, SchemaUpgradeWarning
from alphalab.portfolio.account import Account
from alphalab.portfolio.snapshot import PortfolioSnapshot, declared_units_of_v3_book
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
    ClassificationLimit,
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
from alphalab.risk.models import RiskSeverity, RiskViolation
from alphalab.risk.state import RiskState
from alphalab.runtime.calendars import VenueCalendars, venue_calendars_from_primitives
from alphalab.runtime.execution_pipeline import (
    ExecutionPipelineConfig,
    ExecutionPipelineState,
    ExecutionRouting,
    UnpricedAsset,
    UnpricedReason,
    _grouped_book,
    _require_classifiable,
    _require_one_account_currency,
)
from alphalab.runtime.retention import RetentionPolicy
from alphalab.strategy.events import (
    FillEvent,
    LifecycleTransitioned,
    OrderEvent,
    SliceClosed,
    StrategyRuntimeEvent,
    TimerEvent,
)
from alphalab.strategy.protocol import StrategyProtocol, StrategyStateProtocol
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.state import StrategyState, StrategyStatus
from alphalab.strategy.subscription import SUBSCRIBE_ALL

__all__ = [
    "NOT_ASKED",
    "PIPELINE_SCHEMA_HISTORY",
    "PIPELINE_SNAPSHOT_SCHEMA",
    "READABLE_PIPELINE_SCHEMAS",
    "RETAINED_LOGS",
    "AnalyticsEventRecord",
    "ExecutionEventRecord",
    "MarketEventRecord",
    "PipelineSnapshot",
    "RiskEventRecord",
    "RuntimeObjects",
    "StrategyEventRecord",
    "StrategyRecord",
    "StrategyStateRecord",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module writes. See ADR-0023, and ADR-0025 decision 8 for
#: the move to 2.
#:
#: A module-local literal rather than ``DEFAULT_SCHEMA_VERSION``, for the reason
#: v2.6 gave for the portfolio and v2.8 for the lifecycle: that constant also
#: versions ``BaseEvent``, so bumping it would version every
#: event in the system as a side effect of one subsystem's change.
#:
#: Version 2 adds one field to each strategy record: what that strategy said
#: when it was asked for its state. Nothing else about the envelope changed, and
#: the run envelope nesting this payload did not move with it -- it nests this
#: payload and this decoder validates its own version, which is the churn
#: confinement ADR-0023 decision 1 separated the envelopes to buy.
#:
#: v2.14 spent that confinement exactly as intended, and this constant stayed at
#: 2. ``SESSION_SNAPSHOT_SCHEMA`` and ``BACKTEST_SNAPSHOT_SCHEMA`` were retired
#: with the two run states they versioned, replaced by the single
#: :data:`~alphalab.runtime.run_snapshot.RUN_SNAPSHOT_SCHEMA`: the run layer
#: moved and this core did not. See ADR-0030.
#:
#: Version 3 carries two configuration fields settlement-level multi-currency
#: added: ``ExecutionPipelineConfig.also_settles`` and ``CapitalBudget.currency``
#: (ADR-0035). Both are on the *configuration*, which is why the envelope moved
#: and no state record did.
#:
#: Version 4 (v3.10) records the minor units the account books money at
#: (``config.account.currency_units``, see :mod:`alphalab.common.currency_units`).
#:
#: Version 5 (v3.11) writes a bar's interval as its code (``"30m"``) rather than
#: as a member of the closed enumeration it replaced (``"TimeFrame.M1"``), and
#: allows a bar's ``vwap`` and ``trade_count`` to be ``null`` -- not reported
#: (ledger DAT-005). Each strategy record says whether the strategy is owed its
#: ``on_start`` (``started``, ledger EXE-005), and its subscriptions are the
#: routing rather than a note (ledger EXE-007).
#:
#: Version 6 (v3.12) carries the trading calendar declared for each listing
#: venue (``config.calendars``, ledger EXE-010): a calendar is data, so it is
#: recorded rather than supplied back. It also records whether the budget
#: enforces per-strategy ceilings (``config.budget.enforce_strategy_budgets``,
#: ledger OFE-003) and each classification-bucket limit
#: (``risk_limits.classification``, ledger OFE-001) -- and the run's retention
#: policy (``config.retention``) with, for each log it trimmed, how many entries
#: were dropped before those recorded (``dropped``, ledger PRF-004), and each
#: trade print's aggressor side, when its source reported one (``aggressor`` on
#: every tick, ledger FEA-004).
#:
#: Version 7 (v3.13) writes each strategy's status under the enum's v3.13 name,
#: ``StrategyStatus.RUNNING`` where version 6 wrote ``LifecycleState.RUNNING``
#: (ledger API-001): a plain enum member is persisted with its class name, so
#: the name is part of the format (ledger PER-004). Every earlier version is
#: read through :data:`PIPELINE_SCHEMA_HISTORY`.
PIPELINE_SNAPSHOT_SCHEMA: Final = 7

_SUBSYSTEM: Final = "pipeline"

#: Every pipeline log a :class:`~alphalab.runtime.retention.RetentionPolicy` may
#: trim, by the dotted path from the pipeline state that reaches it -- the names
#: the snapshot's ``dropped`` counts travel under -- with how to read it and how
#: to put it back. Spelled out rather than walked by name: this module turns no
#: string into an attribute (v3.12, ledger PRF-004).
_RETAINED: Final[
    tuple[
        tuple[
            str,
            Callable[[ExecutionPipelineState], AppendOnlyLog[Any]],
            Callable[[ExecutionPipelineState, AppendOnlyLog[Any]], ExecutionPipelineState],
        ],
        ...,
    ]
] = (
    (
        "market.history",
        lambda s: s.market.history,
        lambda s, log: evolve(s, market=evolve(s.market, history=log)),
    ),
    (
        "market.events",
        lambda s: s.market.events,
        lambda s, log: evolve(s, market=evolve(s.market, events=log)),
    ),
    (
        "allocation.history",
        lambda s: s.allocation.history,
        lambda s, log: evolve(s, allocation=evolve(s.allocation, history=log)),
    ),
    (
        "allocation.events",
        lambda s: s.allocation.events,
        lambda s, log: evolve(s, allocation=evolve(s.allocation, events=log)),
    ),
    (
        "risk.history",
        lambda s: s.risk.history,
        lambda s, log: evolve(s, risk=evolve(s.risk, history=log)),
    ),
    (
        "risk.events",
        lambda s: s.risk.events,
        lambda s, log: evolve(s, risk=evolve(s.risk, events=log)),
    ),
    (
        "oms.history",
        lambda s: s.oms.history,
        lambda s, log: evolve(s, oms=evolve(s.oms, history=log)),
    ),
    ("oms.events", lambda s: s.oms.events, lambda s, log: evolve(s, oms=evolve(s.oms, events=log))),
    (
        "execution.history",
        lambda s: s.execution.history,
        lambda s, log: evolve(s, execution=evolve(s.execution, history=log)),
    ),
    (
        "execution.events",
        lambda s: s.execution.events,
        lambda s, log: evolve(s, execution=evolve(s.execution, events=log)),
    ),
    (
        "portfolio.events",
        lambda s: s.portfolio.events,
        lambda s, log: evolve(s, portfolio=evolve(s.portfolio, events=log)),
    ),
    (
        "portfolio.ledger.transactions",
        lambda s: s.portfolio.ledger.transactions,
        lambda s, log: evolve(
            s, portfolio=evolve(s.portfolio, ledger=evolve(s.portfolio.ledger, transactions=log))
        ),
    ),
    ("fills", lambda s: s.fills, lambda s, log: evolve(s, fills=log)),
    ("trades", lambda s: s.trades, lambda s, log: evolve(s, trades=log)),
    ("trade_records", lambda s: s.trade_records, lambda s, log: evolve(s, trade_records=log)),
    (
        "portfolio_snapshots",
        lambda s: s.portfolio_snapshots,
        lambda s, log: evolve(s, portfolio_snapshots=log),
    ),
)

#: The names of the pipeline's retained logs, in a fixed order.
RETAINED_LOGS: Final = tuple(path for path, _, _ in _RETAINED)


def _dropped_counts(state: ExecutionPipelineState) -> dict[str, int]:
    """Each retained log's non-zero dropped count, by its name."""

    return {path: count for path, read, _ in _RETAINED if (count := read(state).dropped)}


def _with_dropped(
    state: ExecutionPipelineState, dropped: Mapping[str, int]
) -> ExecutionPipelineState:
    """``state`` with each named log saying how many entries it dropped before its first."""

    for path, read, write in _RETAINED:
        count = dropped.get(path, 0)
        if count:
            state = write(state, AppendOnlyLog.restored(read(state), count))
    return state


def _v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    """Add what settlement-level multi-currency recorded, as a v2 payload meant it.

    A v2.16 pipeline settled exactly one currency by construction, so its
    ``also_settles`` is the empty set -- what the payload says, not a value
    invented for it -- and its budget's currency is ``""``, which is how v2.17
    spells "determined, not stated". See ADR-0035 and ADR-0025 decision 9.
    """

    config = dict(payload["config"])
    config.setdefault("also_settles", [])
    config["budget"] = {"currency": "", **config["budget"]}
    return {**payload, "config": config}


def _v3_report(report: dict[str, Any]) -> dict[str, Any]:
    """Restate one version-3 performance report in version 4's shape.

    Nothing is recomputed: the figures are the ones the v3.9 writer reported.
    What changes is that the report now *says* how they were computed. v3.9
    annualized every run with 252 periods without asking, so the basis is
    recorded as :attr:`~alphalab.analytics.report.Periodicity.ASSUMED` at 252.
    The span its CAGR used and the risk-free rate its ratios used were the run's
    and were not written into the report, so they are ``None`` -- not recorded
    -- as are the trade counts v3.9 did not keep. A profit factor v3.9 wrote as
    an infinity is ``None``, which is how v3.10 records "undefined".
    """

    returns = dict(report["returns"])
    returns["period_returns"] = returns.pop("daily_returns")
    returns.update(periods_per_year=252.0, periodicity="ASSUMED", years_elapsed=None)
    risk = {**report["risk"], "risk_free_rate": None}
    trades = {**report["trades"], "fills": None, "closed_trades": None}
    for key, value in trades.items():
        if isinstance(value, float) and not math.isfinite(value):
            trades[key] = None
    return {**report, "returns": returns, "risk": risk, "trades": trades}


def _v3_to_v4(payload: dict[str, Any]) -> dict[str, Any]:
    """Record the account's minor units, and restate the analytics basis.

    The configuration's account is the portfolio's account; both must declare
    the same units, so they are computed from the nested version-3 portfolio
    payload by the portfolio's own rule (which also refuses a book holding
    fractional amounts of a currency ISO 4217 gives no decimals). Compiled
    performance reports are restated by :func:`_v3_report`.
    """

    portfolio = payload["portfolio"]
    declared = (
        declared_units_of_v3_book(portfolio)
        if portfolio.get("schema_version") == 3
        else dict(portfolio["account"].get("currency_units", {}))
    )
    config = dict(payload["config"])
    config["account"] = {**config["account"], "currency_units": declared}
    # Every simulated order before v3.10 filled at the event that decided it.
    config["fill_timing"] = FillTiming.SAME_EVENT.value
    analytics = dict(payload["analytics"])
    analytics["reports"] = [_v3_report(report) for report in analytics["reports"]]
    config["risk_limits"] = _v3_risk_limits(config["risk_limits"], "config.risk_limits")
    risk = dict(payload["risk"])
    risk["active_limits"] = _v3_risk_limits(risk["active_limits"], "risk.active_limits")
    risk["history"] = [{**decision, "breaches": []} for decision in risk["history"]]
    # v3.9 maintained no trading day, so none had begun.
    risk.update(trading_day=None, day_start_nav=None)
    return {**payload, "config": config, "analytics": analytics, "risk": risk}


def _v3_risk_limits(limits: dict[str, Any], where: str) -> dict[str, Any]:
    """Carry a version-3 limit set into version 4, which declares a daily loss's day.

    A v3.9 ``DailyLossLimit`` recorded an amount and no trading day, and the
    v3.9 gate never enforced it -- nothing maintained the loss it read (ledger
    KD-002). The faithful reading of what that limit *did* is no limit, so the
    upgraded configuration has none, and the resumed run behaves as the v3.9 run
    did. The amount is not carried, because v3.10 cannot state it without a day,
    and no day is invented: :class:`SchemaUpgradeWarning` says so, naming it.
    """

    upgraded = dict(limits)
    daily = upgraded.get("daily_loss")
    if daily is not None:
        warnings.warn(
            SchemaUpgradeWarning(
                f"{where}.daily_loss of {daily.get('max_daily_loss')} was not carried into "
                "schema version 4: v3.9 recorded no trading day for it and never enforced it, "
                "so the upgraded run has no daily loss limit, as the v3.9 run had none in "
                "effect. Restate it as DailyLossLimit(amount, zone=...) to enforce it."
            ),
            stacklevel=2,
        )
    upgraded["daily_loss"] = None
    return upgraded


#: The interval code of each member of the enumeration ``TimeFrame`` was until
#: v3.11, as the version-4 encoder wrote them.
_V4_TIMEFRAME_CODES: Final = {
    "TimeFrame.M1": "1m",
    "TimeFrame.M5": "5m",
    "TimeFrame.M15": "15m",
    "TimeFrame.H1": "1h",
    "TimeFrame.H4": "4h",
    "TimeFrame.D1": "1d",
    "TimeFrame.W1": "1w",
    "TimeFrame.MN1": "1M",
}


def _v4_intervals(value: Any) -> Any:
    """Rewrite every bar's ``TimeFrame.<member>`` as the member's interval code.

    Bars sit in the market record's latest bars and inside its history and event
    logs; every mapping holding a ``timeframe`` in the version-4 spelling is one,
    and nothing else in a version-4 payload is spelled that way. A bar's
    ``vwap`` and ``trade_count`` are carried as written: a version-4 zero may
    have meant "not reported", but it may equally have been a reported zero,
    and the payload does not say which -- so it is not guessed.
    """

    if isinstance(value, dict):
        rewritten = {key: _v4_intervals(item) for key, item in value.items()}
        code = rewritten.get("timeframe")
        if isinstance(code, str) and code in _V4_TIMEFRAME_CODES:
            rewritten["timeframe"] = _V4_TIMEFRAME_CODES[code]
        return rewritten
    if isinstance(value, list):
        return [_v4_intervals(item) for item in value]
    return value


def _v4_strategy(record: dict[str, Any], where: str) -> dict[str, Any]:
    """Carry one version-4 strategy record into version 5.

    Two facts, each read as what the version-4 run *did*:

    * **It owes no** ``on_start``. v3.10 never delivered the hook, and a
      continued run delivering it mid-run would call setup on a strategy that
      has been trading; ``started`` is ``True``.
    * **It received every event.** v3.10 recorded subscriptions and routed on
      none of them, so the faithful reading of a declaration it never enforced is
      ``"*"`` -- the precedent the version-4 upgrade set for a daily loss limit
      v3.9 never enforced. A declaration other than ``"*"`` is not carried, and
      :class:`SchemaUpgradeWarning` names it.
    """

    declared = record.get("subscriptions")
    if declared != [SUBSCRIBE_ALL]:
        warnings.warn(
            SchemaUpgradeWarning(
                f"{where}.subscriptions {declared!r} were recorded and never enforced before "
                "schema version 5, so the upgraded strategy keeps receiving every event, as "
                "it did: its subscriptions are ['*']. Resubscribe to have them enforced."
            ),
            stacklevel=2,
        )
    return {**record, "subscriptions": [SUBSCRIBE_ALL], "started": True}


def _v4_risk_event(record: Any) -> Any:
    """A version-4 risk event, its request given the terms every request then had."""

    if not isinstance(record, dict):
        return record
    event = record.get("event")
    if isinstance(event, dict) and isinstance(event.get("request"), dict):
        request = {**event["request"], "terms": dict(MARKET_TERMS_PAYLOAD)}
        return {**record, "event": {**event, "request": request}}
    return record


def _v4_to_v5(payload: dict[str, Any]) -> dict[str, Any]:
    """Write each bar's interval as its code, restate each strategy's routing, and give
    each order request its terms.

    See :func:`_v4_intervals` and :func:`_v4_strategy`. Every request a
    version-4 run made was a market order good for the day -- the pipeline could
    make no other (ledger EXE-003) -- so a request inside a risk event is given
    exactly those terms. The allocation and OMS payloads nested here carry their
    own versions and are upgraded by their own histories.
    """

    strategies = [
        _v4_strategy(dict(record), f"strategy[{index}]")
        for index, record in enumerate(payload["strategy"])
    ]
    risk = dict(payload["risk"])
    risk["events"] = [_v4_risk_event(record) for record in risk.get("events", ())]
    return {
        **payload,
        "market": _v4_intervals(payload["market"]),
        "strategy": strategies,
        "risk": risk,
    }


def _v5_to_v6(payload: dict[str, Any]) -> dict[str, Any]:
    """Record that a version-5 run declared no venue calendar, ceiling, bucket limit or retention,
    and that none of its prints carried an aggressor side (:func:`_v5_ticks`).

    A v3.11 pipeline could hold no calendar (ledger EXE-010): it refused a
    simulated day order that did not state its close, and that is exactly what
    an empty declaration does. The order a v3.11 run did place carries the
    close its caller stated, so nothing it holds is reinterpreted. Nor did any
    v3.11 budget enforce a strategy's amount as a ceiling (OFE-003). The nested
    allocation payload carries its own version and is upgraded by its own
    history.
    """

    config = dict(payload["config"])
    config["calendars"] = {"by_exchange": {}, "default": None}
    config["budget"] = {**config["budget"], "enforce_strategy_budgets": False}
    # Nor did any limit a classification bucket (OFE-001), and none trimmed a
    # history (PRF-004).
    config["risk_limits"] = {**config["risk_limits"], "classification": []}
    config["retention"] = dict.fromkeys(("market_history", "steps", "audit_events", "results"))
    risk = dict(payload["risk"])
    risk["active_limits"] = {**risk["active_limits"], "classification": []}
    return {
        **payload,
        "config": config,
        "risk": risk,
        "dropped": {},
        "market": _v5_ticks(payload["market"]),
    }


#: A version-5 tick's fields, exactly: what identifies one inside the market record.
_V5_TICK_FIELDS: Final = frozenset(
    ("asset_id", "timestamp", "price", "quantity", "trade_id", "venue", "currency")
)


def _v5_ticks(value: Any) -> Any:
    """Give every version-5 tick the aggressor side it could not record: none.

    Ticks sit in the market record's latest ticks and inside its history and
    event logs, and a mapping holding exactly a tick's seven fields is one. No
    v3.11 print carried a direction (ledger FEA-004), so ``None`` -- "not
    reported" -- is what each one already meant.
    """

    if isinstance(value, dict):
        rewritten = {key: _v5_ticks(item) for key, item in value.items()}
        if set(rewritten) == _V5_TICK_FIELDS:
            rewritten["aggressor"] = None
        return rewritten
    if isinstance(value, list):
        return [_v5_ticks(item) for item in value]
    return value


#: How a version-6 strategy status begins: the enum's name before v3.13.
_V6_STATUS_PREFIX: Final = "LifecycleState."


def _v6_to_v7(payload: dict[str, Any]) -> dict[str, Any]:
    """Write each strategy's status under the enum's v3.13 name.

    A plain enum member is persisted as ``ClassName.MEMBER``, so renaming the
    strategy runtime's ``LifecycleState`` to
    :class:`~alphalab.strategy.state.StrategyStatus` (ledger API-001) changed
    how a status is written and nothing about what it means:
    ``LifecycleState.RUNNING`` was always ``StrategyStatus.RUNNING``. A status
    written any other way is left as it is, for the decoder to refuse by name.
    """

    strategies: list[Any] = []
    for record in payload["strategy"]:
        status = record.get("status") if isinstance(record, dict) else None
        if isinstance(status, str) and status.startswith(_V6_STATUS_PREFIX):
            member = status.removeprefix(_V6_STATUS_PREFIX)
            record = {**record, "status": f"{StrategyStatus.__name__}.{member}"}
        strategies.append(record)
    return {**payload, "strategy": strategies}


#: How every pipeline payload a release has written is read by this one.
#:
#: A version-1 payload is still missing nothing: it records every field its
#: writer knew about, and "this run captured no strategy state" is an accurate
#: reading of it rather than an invented value -- the decoder reads a strategy
#: record without ``state`` as :data:`NOT_ASKED` exactly when the payload
#: *started* at version 1 (see :meth:`SchemaHistory.read
#: <alphalab.persistence.upgrade.SchemaHistory.read>`). Contrast the portfolio,
#: which refuses its own versions 1 and 2: a default is allowed only when it is
#: what the payload already meant.
PIPELINE_SCHEMA_HISTORY: Final = SchemaHistory(
    _SUBSYSTEM,
    PIPELINE_SNAPSHOT_SCHEMA,
    (
        SchemaStep(
            1,
            "version 2 recorded what each strategy said when asked for its state",
            upgrade=lambda payload: payload,
        ),
        SchemaStep(
            2,
            "version 3 recorded also_settles and the budget's currency",
            upgrade=_v2_to_v3,
        ),
        SchemaStep(
            3,
            "version 4 records the account's minor units and each report's analytics basis",
            upgrade=_v3_to_v4,
        ),
        SchemaStep(
            4,
            "version 5 writes a bar's interval as its code, allows an unreported vwap, "
            "records whether each strategy is owed on_start and routes on its subscriptions, "
            "and gives each order request its terms",
            upgrade=_v4_to_v5,
        ),
        SchemaStep(
            5,
            "version 6 carries the trading calendar declared for each listing venue, "
            "whether the budget enforces per-strategy ceilings, classification limits, the "
            "retention policy with what each trimmed log dropped, and each print's aggressor",
            upgrade=_v5_to_v6,
        ),
        SchemaStep(
            6,
            "version 7 writes each strategy's status under the enum's v3.13 name, StrategyStatus",
            upgrade=_v6_to_v7,
        ),
    ),
)

#: The versions :func:`from_primitives` reads -- every one a release wrote.
READABLE_PIPELINE_SCHEMAS: Final = PIPELINE_SCHEMA_HISTORY.readable

#: The version whose strategy records first carried ``state`` (v2.10).
_STATE_INTRODUCED_AT: Final = 2


class _NotAsked:
    """The type of :data:`NOT_ASKED`. Deliberately not serializable."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "NOT_ASKED"


#: What a strategy record carries when the payload's writer never asked.
#:
#: Three states must stay distinguishable, and two of them are not ``None``:
#:
#: ==================================  ==========================  ============
#: ``StrategyRecord.state``            JSON                        Means
#: ==================================  ==========================  ============
#: :data:`NOT_ASKED`                   key absent (version 1)      not asked
#: ``None``                            ``null``                    declared none
#: :class:`StrategyStateRecord`        ``{"payload":…,"version":…}``  declared
#: ==================================  ==========================  ============
#:
#: :func:`capture` never produces this: a version-2 capture always asks. It
#: arises only from decoding a version-1 payload, and it has no version-2
#: representation -- which is why it has no serializable projection and the
#: encoder refuses it rather than inventing one.
NOT_ASKED: Final = _NotAsked()


# ---------------------------------------------------------------------------
# Tagged event records: five heterogeneous logs the envelope carries inline
# ---------------------------------------------------------------------------


def _types[EventT](*classes: type[EventT]) -> Mapping[str, type[EventT]]:
    return {cls.__name__: cls for cls in classes}


_MARKET_EVENTS: Mapping[str, type[MarketEvent]] = _types(
    QuoteReceived, TickReceived, TradeReceived, BarClosed, BookUpdated, SnapshotCreated
)
_STRATEGY_EVENTS: Mapping[str, type[StrategyRuntimeEvent]] = _types(
    LifecycleTransitioned, FillEvent, OrderEvent, TimerEvent, SliceClosed
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
    #: Sorted, not a ``frozenset``: this record is the serializable projection,
    #: and a set has no deterministic JSON order. ``restore`` rebuilds the set.
    also_settles: tuple[str, ...]
    routing: ExecutionRouting
    sizing_model_type: str
    simulator_type: str
    instruments_type: str | None
    fill_timing: FillTiming
    calendars: VenueCalendars
    retention: RetentionPolicy


@dataclass(frozen=True, slots=True)
class StrategyStateRecord:
    """What one declaring strategy said when it was asked for its state.

    ``payload`` is the value the strategy's own ``capture_state`` returned, and
    is not decoded by this module: reconstructing it is the strategy's job, and
    the reason the codec is two-sided (ADR-0025 decision 3). ``version`` is the
    strategy author's own integer, carried and handed back, never interpreted.

    A ``payload`` of ``None`` here is still a *declared* state -- the record's
    existence is what says the strategy declared. "Declared nothing" is the
    absence of this record, not a record holding nothing.
    """

    payload: Any
    version: int


@dataclass(frozen=True, slots=True)
class StrategyRecord:
    """One registered strategy's durable metadata, without its instance.

    ``instance_type`` records what the strategy was; the object itself is
    supplied back on restore. ``subscriptions`` is a sorted tuple because a
    ``frozenset`` has no deterministic JSON form. ``config`` is carried as the
    value the encoder wrote: a configuration the encoder cannot write is refused
    at capture, by the encoder, as it is for every other state in the repository.
    Its semantics are unchanged by schema 2 (ADR-0025 decision 4).

    ``state`` is the schema-2 addition and is three-valued -- see
    :data:`NOT_ASKED`. It defaults to :data:`NOT_ASKED` so that a record built
    without it reads as "nobody asked", which is the only safe default: it makes
    a declaring strategy's restore refuse rather than silently start empty.
    """

    strategy_id: str
    status: StrategyStatus
    config: Any
    subscriptions: tuple[str, ...]
    last_error: str | None
    instance_type: str
    state: StrategyStateRecord | _NotAsked | None = NOT_ASKED
    #: Whether the strategy is owed no ``on_start`` (schema 5). See
    #: :attr:`~alphalab.strategy.state.StrategyState.started`.
    started: bool = False


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
    trading_day: str | None = None
    day_start_nav: Decimal | None = None


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
    #: For each log a retention policy trimmed, how many entries it dropped
    #: before the ones recorded here, by the path :data:`RETAINED_LOGS` names
    #: it by. Only non-zero counts are written (v3.12, ledger PRF-004).
    dropped: Mapping[str, int] = field(default_factory=dict)
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
        also_settles=tuple(sorted(config.also_settles)),
        routing=config.routing,
        sizing_model_type=_type_name(config.sizing_model),
        simulator_type=_type_name(config.simulator),
        instruments_type=(None if config.instruments is None else _type_name(config.instruments)),
        fill_timing=config.fill_timing,
        calendars=config.calendars,
        retention=config.retention,
    )


#: The members a strategy defines to declare that it owns durable state, taken
#: from the protocol itself so the two cannot drift. Defining all of them is
#: :class:`~alphalab.strategy.protocol.StrategyStateProtocol`; defining some is
#: refused (ADR-0025 decision 3).
_STATE_MEMBERS: Final = tuple(
    sorted(StrategyStateProtocol.__protocol_attrs__)  # type: ignore[attr-defined]
)


def _defines(instance: object, name: str) -> bool:
    """Whether ``name`` is defined on ``instance``'s class or one of its bases.

    Deliberately a lookup through ``__mro__`` rather than ``getattr``: declaring
    durable state is a property of a strategy *class*, not something an instance
    acquires at runtime, and a class with a ``__getattr__`` catch-all would
    otherwise answer yes to everything and be asked for state it does not have.
    Stricter than ``isinstance`` against the protocol, and in the safe
    direction.
    """

    for klass in type(instance).__mro__:
        if name in klass.__dict__:
            # First hit wins, as attribute lookup does, and it must be callable:
            # a subclass setting the name to ``None`` is removing the member, not
            # defining it.
            return callable(klass.__dict__[name])
    return False


def _declares_state(instance: object, strategy_id: str) -> bool:
    """Whether ``instance`` declares durable state, refusing a partial codec.

    Structural, and it agrees with ``isinstance(instance, StrategyStateProtocol)``
    for every class-defined strategy. The partial case is the reason this is a
    function rather than an ``isinstance`` call: ``isinstance`` answers ``False``
    for a strategy that defines ``capture_state`` and no ``restore_state``,
    silently treating a half-written codec as no codec -- and the run would then
    continue from a payload whose state was never captured.

    Raises:
        SerializationError: If some but not all members are defined.
    """

    present = tuple(name for name in _STATE_MEMBERS if _defines(instance, name))
    if len(present) == len(_STATE_MEMBERS):
        return True
    if not present:
        return False

    missing = sorted(set(_STATE_MEMBERS) - set(present))
    raise SerializationError(
        f"Strategy {strategy_id!r} ({_type_name(instance)}) defines {sorted(present)} "
        f"but not {missing}. A strategy that owns durable state supplies both "
        "directions of its codec and its version; declaring one without the "
        "others writes a payload nothing can read back. Define the missing "
        "members, or none of them. See ADR-0025 decision 3."
    )


def _capture_state(instance: object, strategy_id: str) -> StrategyStateRecord | None:
    """Ask one strategy for its state, or record that it declared none.

    A pure read of the instance. Nothing here mints an identifier, mutates the
    strategy or emits an event -- and a strategy whose own ``capture_state``
    does is outside what this module can police, which is the residual ADR-0025
    decision 13 states.

    **The state is put through the existing encoder here, not later.** Two
    things follow from that, and both are the point:

    * A state the encoder cannot write is refused *at capture*, so an
      unencodable value can never reach a :class:`PipelineSnapshot` that looks
      valid in memory and fails at some unrelated ``serialize`` call later.
      ADR-0025 decision 3 says the encoder raises at capture; this is what makes
      that literally true rather than eventually true.
    * The record carries the **JSON-decoded primitives**, which is what
      ADR-0025 decision 3 promises ``restore_state`` receives. Without this
      normalization the in-memory path would hand a strategy its ``Decimal`` and
      ``tuple`` back while the JSON path handed it ``str`` and ``list``, so a
      codec written against one shape would break on the other -- a divergence
      between two paths into the same contract.

    No encoder branch is added and nothing is reflected over: this is the same
    :func:`~alphalab.persistence.serializer.serialize` every other state uses,
    and a strategy type with no JSON form declares its projection through
    ``__serializable__``, which that encoder already honours.

    Raises:
        SerializationError: If the codec is partial, if the version is not an
            integer, if the strategy's own ``capture_state`` raises, or if the
            state it returns is one the encoder refuses to write.
    """

    if not _declares_state(instance, strategy_id):
        return None

    try:
        version = instance.strategy_state_version()  # type: ignore[attr-defined]
        raw = instance.capture_state()  # type: ignore[attr-defined]
    except Exception as exc:
        raise SerializationError(
            f"Strategy {strategy_id!r} ({_type_name(instance)}) raised while "
            f"describing its state: {exc!r}. A strategy that cannot describe "
            "its state must not produce a snapshot claiming it did."
        ) from exc

    if not isinstance(version, int) or isinstance(version, bool):
        raise SerializationError(
            f"Strategy {strategy_id!r} ({_type_name(instance)}) returned "
            f"{version!r} from strategy_state_version(), which is not an integer."
        )

    try:
        # ``to_serializable`` first, exactly as the real encoding path reaches
        # this value: ``serialize`` applies that conversion through the
        # enclosing dataclass, and it is the step that honours
        # ``__serializable__``. Validating the bare value without it would
        # refuse a custom type that encodes perfectly well in place.
        payload = deserialize(serialize(to_serializable(raw)))
    except SerializationError as exc:
        raise SerializationError(
            f"Strategy {strategy_id!r} ({_type_name(instance)}) returned state the "
            f"encoder cannot write: {exc}. Return a value the encoder accepts -- a "
            "Decimal, a dataclass, an Enum, a UUID, a JSON native, or a mapping or "
            "sequence of those -- or give the type a '__serializable__' projection, "
            "which is the extension point that already exists. No encoder branch is "
            "added for strategy state."
        ) from exc

    return StrategyStateRecord(payload=payload, version=version)


def _capture_strategies(state: StrategyRuntimeState) -> tuple[StrategyRecord, ...]:
    return tuple(
        StrategyRecord(
            strategy_id=strategy_id,
            status=strategy.status,
            config=strategy.config,
            subscriptions=tuple(sorted(strategy.subscriptions)),
            last_error=strategy.last_error,
            instance_type=_type_name(strategy.instance),
            state=_capture_state(strategy.instance, strategy_id),
            started=strategy.started,
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
            trading_day=state.risk.trading_day,
            day_start_nav=state.risk.day_start_nav,
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
        dropped=_dropped_counts(state),
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
        also_settles=frozenset(record.also_settles),
        routing=record.routing,
        instruments=instruments,
        fill_timing=record.fill_timing,
        calendars=record.calendars,
        retention=record.retention,
    )


def _restore_state(record: StrategyRecord, instance: object) -> None:
    """Reconcile what the payload records against what the instance declares.

    Four mismatches, each refusing the **whole** restore rather than this one
    strategy (ADR-0025 decision 11). Restoring the others and skipping this one
    would produce a state that is internally consistent, compares equal on every
    Class-1 value, and is wrong -- the failure mode the release exists to
    remove.

    Nothing is ever substituted: no fresh state, no empty mapping, no default.

    Raises:
        StateDecodeError: On any of the four mismatches, or when the strategy's
            own ``restore_state`` raises. The message names the strategy, and
            the original exception is chained.
    """

    strategy_id = record.strategy_id
    declares = _declares_state(instance, strategy_id)

    if isinstance(record.state, _NotAsked):
        if declares:
            raise StateDecodeError(
                f"Strategy {strategy_id!r} ({_type_name(instance)}) declares durable "
                f"state, but this payload is schema version 1 and was written before "
                "strategy state could be captured. It cannot say whether the strategy "
                "had state, and starting it empty would invent the one fact that "
                "decides whether the continuation is correct. Continue this run with a "
                "strategy that declares no state, or resume from a schema "
                f"{PIPELINE_SNAPSHOT_SCHEMA} payload."
            )
        return

    if record.state is None:
        if declares:
            raise StateDecodeError(
                f"Strategy {strategy_id!r} ({_type_name(instance)}) declares durable "
                "state, but the payload records that it declared none. One of the two "
                "is wrong and this module cannot tell which, so it refuses rather than "
                "restoring a strategy whose memory the payload never held."
            )
        return

    if not declares:
        raise StateDecodeError(
            f"Strategy {strategy_id!r} ({_type_name(instance)}) does not declare durable "
            "state, but the payload carries state for it. The supplied instance cannot "
            "consume it, and discarding it would silently lose exactly what was "
            "captured. Supply an instance that declares state, or a payload that "
            "carries none."
        )

    try:
        instance.restore_state(  # type: ignore[attr-defined]
            record.state.payload, record.state.version
        )
    except Exception as exc:
        raise StateDecodeError(
            f"Strategy {strategy_id!r} ({_type_name(instance)}) raised while restoring "
            f"its state at version {record.state.version}: {exc!r}. The whole restore is "
            "refused; nothing partial is returned."
        ) from exc


def _restore_strategies(
    records: tuple[StrategyRecord, ...],
    events: tuple[StrategyEventRecord, ...],
    objects: RuntimeObjects,
) -> StrategyRuntimeState:
    strategies = {}
    for record in records:
        instance = _require_object(
            objects.strategies.get(record.strategy_id),
            record.instance_type,
            f"strategy instance for {record.strategy_id!r}",
        )
        # Reconciled before any state is built, so a refusal returns nothing
        # partial -- the boundary rule every other decoder here keeps.
        _restore_state(record, instance)
        strategies[record.strategy_id] = StrategyState(
            strategy_id=record.strategy_id,
            status=record.status,
            instance=instance,
            config=record.config,
            subscriptions=frozenset(record.subscriptions),
            last_error=record.last_error,
            started=record.started,
        )
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
            enforces -- that its two currencies agree, and that classification
            limits have a registry to read. The checks run before the state is
            built, so nothing is reconstructed against a refused configuration.
    """

    config = _restore_config(snapshot.config, objects)
    _require_one_account_currency(config)
    _require_classifiable(config)

    state = ExecutionPipelineState(
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
            trading_day=snapshot.risk.trading_day,
            day_start_nav=snapshot.risk.day_start_nav,
        ),
        oms=restore_oms(snapshot.oms),
        execution=ExecutionState(
            reports=PersistentMap(snapshot.execution.reports),
            history=AppendOnlyLog(snapshot.execution.history),
            events=AppendOnlyLog(record.event for record in snapshot.execution.events),
        ),
        portfolio=_grouped_book(restore_portfolio(snapshot.portfolio), config),
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
    return _with_dropped(state, snapshot.dropped)


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
    for member in fields(cls):  # type: ignore[arg-type]
        raw = require(payload, member.name)
        decoder = decoders.get(member.name)
        kwargs[member.name] = (
            decoder(raw, f"{where}.{member.name}")
            if decoder is not None
            else as_str(raw, f"{where}.{member.name}")
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
        currency_units=_currency_units(
            require(payload, "currency_units"), f"{where}.currency_units"
        ),
    )


def _currency_units(value: Any, where: str) -> CurrencyUnits:
    payload = as_mapping(value, where)
    try:
        return CurrencyUnits(
            {
                as_str(currency, f"{where} key"): as_int(units, f"{where}[{currency}]")
                for currency, units in payload.items()
            }
        )
    except AlphaLabValidationError as exc:
        raise StateDecodeError(f"{where} is not a valid set of currency units: {exc}") from exc


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
        # "" in a payload upgraded from version 1 or 2, where it means
        # "unstated" -- what a single-currency pipeline's budget was.
        currency=as_str(require(payload, "currency"), f"{where}.currency"),
        # False in a payload upgraded from version 5 or earlier: nothing before
        # v3.12 enforced a strategy's budget (OFE-003).
        enforce_strategy_budgets=as_bool(
            require(payload, "enforce_strategy_budgets"), f"{where}.enforce_strategy_budgets"
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
        daily_loss=_daily_loss_limit(require(payload, "daily_loss"), f"{where}.daily_loss"),
        drawdown=limit("drawdown", DrawdownLimit, "max_drawdown_pct"),
        # Empty in a payload upgraded from version 5 or earlier: nothing before
        # v3.12 limited a classification bucket (OFE-001).
        classification=tuple(
            _classification_limit(item, f"{where}.classification[{index}]")
            for index, item in enumerate(
                as_sequence(require(payload, "classification"), f"{where}.classification")
            )
        ),
    )


def _classification_limit(value: Any, where: str) -> ClassificationLimit:
    payload = as_mapping(value, where)
    try:
        return ClassificationLimit(
            dimension=as_str(require(payload, "dimension"), f"{where}.dimension"),
            max_gross=as_optional_decimal(require(payload, "max_gross"), f"{where}.max_gross"),
            max_share=as_optional_decimal(require(payload, "max_share"), f"{where}.max_share"),
            label=as_optional_str(require(payload, "label"), f"{where}.label"),
            refuse_unclassified=as_bool(
                require(payload, "refuse_unclassified"), f"{where}.refuse_unclassified"
            ),
        )
    except AlphaLabError as exc:
        raise StateDecodeError(f"{where} is not a valid classification limit: {exc}") from exc


def _daily_loss_limit(value: Any, where: str) -> DailyLossLimit | None:
    if value is None:
        return None
    payload = as_mapping(value, where)
    raw_start = as_str(require(payload, "day_start"), f"{where}.day_start")
    try:
        return DailyLossLimit(
            max_daily_loss=as_decimal(
                require(payload, "max_daily_loss"), f"{where}.max_daily_loss"
            ),
            zone=as_str(require(payload, "zone"), f"{where}.zone"),
            day_start=time.fromisoformat(raw_start),
        )
    except (ValueError, AlphaLabError) as exc:
        raise StateDecodeError(f"{where} is not a valid daily loss limit: {exc}") from exc


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
    aggressor = require(payload, "aggressor")
    return Tick(
        asset_id=as_str(require(payload, "asset_id"), f"{where}.asset_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        price=as_decimal(require(payload, "price"), f"{where}.price"),
        quantity=as_decimal(require(payload, "quantity"), f"{where}.quantity"),
        trade_id=as_str(require(payload, "trade_id"), f"{where}.trade_id"),
        venue=as_str(require(payload, "venue"), f"{where}.venue"),
        currency=as_str(require(payload, "currency"), f"{where}.currency"),
        aggressor=None
        if aggressor is None
        else as_value_enum(TradeAggressor, aggressor, f"{where}.aggressor"),
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
        vwap=as_optional_decimal(require(payload, "vwap"), f"{where}.vwap"),
        trade_count=_optional_int(require(payload, "trade_count"), f"{where}.trade_count"),
        timeframe=_timeframe(require(payload, "timeframe"), f"{where}.timeframe"),
    )


def _timeframe(value: Any, where: str) -> TimeFrame:
    """Decode an interval from its code (schema 5); earlier codes were upgraded to it."""

    try:
        return TimeFrame.parse(as_str(value, where))
    except MarketValidationError as error:
        raise StateDecodeError(f"{where}: {error}") from None


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
        terms=as_order_terms(require(payload, "terms"), f"{where}.terms"),
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
        severity=as_value_enum(RiskSeverity, require(payload, "severity"), f"{where}.severity"),
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
        breaches=_sequence_of(require(payload, "breaches"), f"{where}.breaches", _violation),
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


def _optional_float(value: Any, field: str) -> float | None:
    return None if value is None else as_float(value, field)


def _optional_decimal(value: Any, field: str) -> Decimal | None:
    return None if value is None else as_decimal(value, field)


def _optional_int(value: Any, field: str) -> int | None:
    return None if value is None else as_int(value, field)


def _performance(value: Any, where: str) -> PerformanceReport:
    payload = as_mapping(value, where)

    def inner(key: str) -> Mapping[str, Any]:
        return as_mapping(require(payload, key), f"{where}.{key}")

    def floats(block: Mapping[str, Any], key: str, *names: str) -> dict[str, float | None]:
        # Every statistic may be ``null``: undefined is recorded as undefined.
        return {
            name: _optional_float(require(block, name), f"{where}.{key}.{name}") for name in names
        }

    block = inner("returns")
    returns = ReturnSummary(
        **floats(block, "returns", "total_return", "cagr", "arithmetic_return", "geometric_return"),
        period_returns=_floats(require(block, "period_returns"), f"{where}.returns.period_returns"),
        periods_per_year=_optional_float(
            require(block, "periods_per_year"), f"{where}.returns.periods_per_year"
        ),
        periodicity=as_value_enum(
            Periodicity, require(block, "periodicity"), f"{where}.returns.periodicity"
        ),
        years_elapsed=_optional_float(
            require(block, "years_elapsed"), f"{where}.returns.years_elapsed"
        ),
    )

    block = inner("risk")
    risk = RiskSummary(
        **floats(
            block,
            "risk",
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "value_at_risk_95",
            "cvar_95",
            "annualized_volatility",
        ),
        risk_free_rate=_optional_float(
            require(block, "risk_free_rate"), f"{where}.risk.risk_free_rate"
        ),
    )

    block = inner("drawdowns")
    drawdowns = DrawdownMetrics(
        max_drawdown=as_float(require(block, "max_drawdown"), f"{where}.drawdowns.max_drawdown"),
        ulcer_index=as_float(require(block, "ulcer_index"), f"{where}.drawdowns.ulcer_index"),
        drawdowns=_floats(require(block, "drawdowns"), f"{where}.drawdowns.drawdowns"),
    )

    block = inner("exposure")
    exposure = ExposureMetrics(
        cash_pct=as_float(require(block, "cash_pct"), f"{where}.exposure.cash_pct"),
        leverage=as_float(require(block, "leverage"), f"{where}.exposure.leverage"),
        **{
            name: as_decimal(require(block, name), f"{where}.exposure.{name}")
            for name in ("gross", "net", "long", "short")
        },
    )

    block = inner("trades")
    optional_floats = floats(
        block, "trades", "win_rate", "loss_rate", "profit_factor", "avg_holding_period", "turnover"
    )
    trades = TradeMetrics(
        win_rate=optional_floats["win_rate"],
        loss_rate=optional_floats["loss_rate"],
        avg_win=_optional_decimal(require(block, "avg_win"), f"{where}.trades.avg_win"),
        avg_loss=_optional_decimal(require(block, "avg_loss"), f"{where}.trades.avg_loss"),
        profit_factor=optional_floats["profit_factor"],
        expectancy=_optional_decimal(require(block, "expectancy"), f"{where}.trades.expectancy"),
        avg_holding_period=optional_floats["avg_holding_period"],
        turnover=optional_floats["turnover"],
        fills=_optional_int(require(block, "fills"), f"{where}.trades.fills"),
        closed_trades=_optional_int(
            require(block, "closed_trades"), f"{where}.trades.closed_trades"
        ),
    )

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


def _assets(value: Any, where: str) -> tuple[str, ...]:
    return tuple(
        as_str(item, f"{where}[{index}]") for index, item in enumerate(as_sequence(value, where))
    )


_STRATEGY_FIELDS: Mapping[str, Any] = {
    "timestamp": as_float,
    "fill_quantity": as_decimal,
    "fill_price": as_decimal,
    "attributed_quantity": as_optional_decimal,
    "quantity": as_optional_decimal,
    "filled_quantity": as_optional_decimal,
    "assets": _assets,
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
        # Empty in a payload upgraded from version 1 or 2: such a pipeline
        # settled exactly one currency. See PIPELINE_SCHEMA_HISTORY.
        also_settles=tuple(
            as_str(entry, f"{where}.also_settles[{index}]")
            for index, entry in enumerate(
                as_sequence(require(payload, "also_settles"), f"{where}.also_settles")
            )
        ),
        routing=as_named_enum(ExecutionRouting, require(payload, "routing"), f"{where}.routing"),
        sizing_model_type=as_str(
            require(payload, "sizing_model_type"), f"{where}.sizing_model_type"
        ),
        simulator_type=as_str(require(payload, "simulator_type"), f"{where}.simulator_type"),
        instruments_type=(
            None if instruments is None else as_str(instruments, f"{where}.instruments_type")
        ),
        fill_timing=_fill_timing(require(payload, "fill_timing"), f"{where}.fill_timing"),
        # Empty in a payload upgraded from version 5 or earlier: such a pipeline
        # held no calendar. See PIPELINE_SCHEMA_HISTORY.
        calendars=venue_calendars_from_primitives(
            require(payload, "calendars"), f"{where}.calendars"
        ),
        # Keeps everything in a payload upgraded from version 5 or earlier: no
        # earlier run trimmed a history. See PIPELINE_SCHEMA_HISTORY.
        retention=_retention(require(payload, "retention"), f"{where}.retention"),
    )


def _retention(value: Any, where: str) -> RetentionPolicy:
    payload = as_mapping(value, where)
    bounds: dict[str, int | None] = {}
    for name in ("market_history", "steps", "audit_events", "results"):
        bound = require(payload, name)
        bounds[name] = None if bound is None else as_int(bound, f"{where}.{name}")
    try:
        return RetentionPolicy(**bounds)
    except AlphaLabError as error:
        raise StateDecodeError(f"{where}: {error}") from error


def _dropped(value: Any) -> dict[str, int]:
    """The non-zero dropped counts of the retained logs, each a log this envelope names."""

    payload = as_mapping(value, "dropped")
    counts: dict[str, int] = {}
    for path, count in payload.items():
        if path not in RETAINED_LOGS:
            raise StateDecodeError(
                f"dropped names {path!r}, which is not a log a retention policy trims; it "
                f"names one of {list(RETAINED_LOGS)}."
            )
        number = as_int(count, f"dropped.{path}")
        if number < 1:
            raise StateDecodeError(
                f"dropped.{path} is {number}; only a positive count of dropped entries is written."
            )
        counts[path] = number
    return counts


def _fill_timing(value: Any, where: str) -> FillTiming:
    text = as_str(value, where)
    try:
        return FillTiming(text)
    except ValueError as exc:
        raise StateDecodeError(
            f"{where} must be one of {[timing.value for timing in FillTiming]}, got {text!r}."
        ) from exc


def _strategy_state_record(value: Any, where: str) -> StrategyStateRecord:
    """Decode one declared state: the strategy's payload, and its own version.

    ``payload`` is required to be present and is taken exactly as written --
    this module does not decode it, because only the strategy knows what it
    means. ``version`` is validated as an integer, because this module does
    carry it and a non-integer version is a malformed payload rather than a
    strategy's business.
    """

    payload = as_mapping(value, where)
    return StrategyStateRecord(
        payload=require(payload, "payload"),
        version=as_int(require(payload, "version"), f"{where}.version"),
    )


def _strategy_record(value: Any, where: str, version: int) -> StrategyRecord:
    """Decode one strategy record, at the envelope version that carried it.

    ``version`` decides whether ``state`` is expected at all: a schema-1 payload
    has no such field and must not carry one, and a schema-2 payload must always
    have it -- present and ``null`` for a strategy that declared none. That is
    ``require``'s existing rule, that a missing field is never filled in with a
    default, applied to the one field whose absence means something.
    """

    payload = as_mapping(value, where)
    # Compared with the version that introduced ``state``, not with the current
    # one. Until v3.10 this read ``version < PIPELINE_SNAPSHOT_SCHEMA``, which was
    # right while the constant was 2 and wrong from v2.17, when it became 3: a
    # genuine version-2 payload -- which always carries ``state`` -- was then
    # refused as malformed, and one missing it read as "never asked".
    if version < _STATE_INTRODUCED_AT:
        if "state" in payload:
            raise StateDecodeError(
                f"{where} declares schema version {version} and carries 'state', "
                f"which was introduced at version {_STATE_INTRODUCED_AT}. A "
                "payload that says it predates a field and then carries it is "
                "malformed; it is not read as the later version."
            )
        state: StrategyStateRecord | _NotAsked | None = NOT_ASKED
    else:
        raw = require(payload, "state")
        state = None if raw is None else _strategy_state_record(raw, f"{where}.state")

    return StrategyRecord(
        strategy_id=as_str(require(payload, "strategy_id"), f"{where}.strategy_id"),
        status=as_named_enum(StrategyStatus, require(payload, "status"), f"{where}.status"),
        config=require(payload, "config"),
        subscriptions=tuple(
            as_str(item, f"{where}.subscriptions[{index}]")
            for index, item in enumerate(
                as_sequence(require(payload, "subscriptions"), f"{where}.subscriptions")
            )
        ),
        last_error=as_optional_str(require(payload, "last_error"), f"{where}.last_error"),
        instance_type=as_str(require(payload, "instance_type"), f"{where}.instance_type"),
        state=state,
        started=as_bool(require(payload, "started"), f"{where}.started"),
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
        trading_day=as_optional_str(require(payload, "trading_day"), f"{where}.trading_day"),
        day_start_nav=_optional_decimal(
            require(payload, "day_start_nav"), f"{where}.day_start_nav"
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
    payload, version = PIPELINE_SCHEMA_HISTORY.read(payload)

    return PipelineSnapshot(
        config=_config(require(payload, "config")),
        market=_market(require(payload, "market")),
        strategy=tuple(
            _strategy_record(item, f"strategy[{index}]", version)
            for index, item in enumerate(as_sequence(require(payload, "strategy"), "strategy"))
        ),
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
        dropped=_dropped(require(payload, "dropped")),
        schema_version=PIPELINE_SNAPSHOT_SCHEMA,
    )
