"""The instrument's currency decides what a run may trade.

Until v2.12 it decided nothing. ``InstrumentRecord.currency`` is one of the four
fields ``asset_id`` is derived from, so the registry has held an authoritative,
immutable answer for every registered instrument since v2.7 -- and the execution
path never asked. ``_instruction`` stamped ``ExecutionPipelineConfig.currency``
onto every instruction, the report copied it, and the position was booked from
it. Measured at v2.11.0, a EUR-registered instrument traded on a USD pipeline::

    InstrumentRecord("SAP", EQUITY, "XETR", "EUR")  # the registry's answer
    ExecutionReport.currency = "USD"  # from the config
    Position.currency = "USD"  # a position in no currency

No error, no warning, and nothing downstream could tell. v2.11 is what made this
indefensible rather than merely absent: it threaded the registry onto the
pipeline and read it on the fill path for sector, so the right answer was in
hand and ignored.

Two seams close it, and they answer different questions. Seam 1 asks whether
this run may *trade* an instrument, needs the registry, and drops the request.
Seam 2 asks whether a report is denominated in what the pipeline *settles*,
needs nothing but the configuration, and raises -- a venue fill has already
happened and cannot be declined. See ADR-0028.
"""

import inspect
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.broker.execution import BrokerExecution
from alphalab.common.ids import id_scope
from alphalab.core.enums import AssetType
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry
from alphalab.market.record import MarketRecord
from alphalab.oms.order import Order as OMSOrder
from alphalab.portfolio.account import Account
from alphalab.runtime.broker_routing import (
    RoutingConfig,
    apply_broker_execution,
    broker_order_id_for,
    execution_report_from_broker,
)
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineResult,
    ExecutionPipelineState,
    ExecutionRouting,
    SettlementRefusal,
    UnpricedReason,
)
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    quote,
    registry_of,
    running_strategy_state,
)

_SEED = 20280
_PRICE = Decimal("100")

_APPLE = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", sector="Technology")
_SAP = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR", sector="Technology")


def _config(
    strategy_id: str,
    *,
    currency: str = "USD",
    instruments: InstrumentRegistry | None = None,
    routing: ExecutionRouting = ExecutionRouting.SIMULATED,
) -> ExecutionPipelineConfig:
    """A pipeline settling in ``currency``, with base and settlement agreeing."""

    base = pipeline_config(strategy_id)
    return replace(
        base,
        account=Account("acct-v212", currency, "Currency Authority Account", 1.0),
        currency=currency,
        instruments=instruments,
        routing=routing,
    )


def _run(
    *,
    instrument: InstrumentRecord,
    settlement: str,
    instruments: InstrumentRegistry | None,
    quantity: str = "10",
    routing: ExecutionRouting = ExecutionRouting.SIMULATED,
) -> ExecutionPipelineResult:
    """One quote, one intent, through the real path."""

    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(strategy_id, instrument.asset_id, {2.0: Decimal(quantity)})
    config = _config(strategy_id, currency=settlement, instruments=instruments, routing=routing)
    with id_scope(_SEED):
        state = ExecutionPipeline.initialize(
            config, running_strategy_state(strategy_id, strategy), 1.0
        )
        return ExecutionPipeline.process_quote(
            state, quote(instrument.asset_id, 2.0, _PRICE), context_factory
        )


# --------------------------------------------------------------------------- #
# A. Authority: the registry decides, and it decides one thing
# --------------------------------------------------------------------------- #


def test_a_usd_instrument_trades_on_a_usd_pipeline() -> None:
    result = _run(instrument=_APPLE, settlement="USD", instruments=registry_of(_APPLE))

    assert len(result.fills) == 1
    assert result.settlement_refusals == ()
    assert [p.currency for p in result.state.portfolio.positions.values()] == ["USD"]


def test_a_eur_instrument_trades_on_a_eur_pipeline() -> None:
    """The distinction the release exists to keep: EUR is not "foreign"."""

    result = _run(instrument=_SAP, settlement="EUR", instruments=registry_of(_SAP))

    assert len(result.fills) == 1
    assert result.settlement_refusals == ()
    assert [p.currency for p in result.state.portfolio.positions.values()] == ["EUR"]
    assert list(result.state.portfolio.cash.balances) == ["EUR"]
    assert result.valuation is not None and result.valuation.currency == "EUR"


@pytest.mark.parametrize(
    ("instrument", "settlement"),
    [(_APPLE, "USD"), (_SAP, "EUR")],
)
def test_every_accepted_fill_books_the_instruments_own_currency(
    instrument: InstrumentRecord, settlement: str
) -> None:
    """The property the whole release delivers, stated as the registry states it.

    ``_instruction`` is unchanged and still reads ``config.currency``: under
    STRICT_MATCH that *is* the instrument's currency for anything that trades,
    because Seam 1 refuses everything else. The equality is enforced by the
    refusal rather than by a second registry read (ADR-0028 decision 5).
    """

    registry = registry_of(instrument)
    result = _run(instrument=instrument, settlement=settlement, instruments=registry)

    assert result.state.portfolio.positions
    for asset_id, position in result.state.portfolio.positions.items():
        record = registry.record_for(asset_id)
        assert record is not None
        assert position.currency == record.currency


def test_currency_is_read_through_record_for_and_mirrors_the_sector_reader() -> None:
    from alphalab.runtime.execution_pipeline import _currency_of, _sector_of

    assert list(inspect.signature(_currency_of).parameters) == list(
        inspect.signature(_sector_of).parameters
    )
    body = inspect.getsource(_currency_of).split('"""')[-1]
    assert "record_for(asset_id)" in body
    assert "for " not in body, "a keyed lookup, never a scan"


def test_an_unregistered_asset_is_not_a_settlement_fault() -> None:
    """``NOT_REGISTERED`` already owns that question; one fault, one name."""

    from alphalab.runtime.execution_pipeline import _settlement_refusal

    result = _run(instrument=_APPLE, settlement="USD", instruments=registry_of(_APPLE))
    stranger = str(uuid4())

    assert _settlement_refusal(result.state, stranger, 2.0) is None


# --------------------------------------------------------------------------- #
# B. The authority is opt-in: no registry, no authority, v2.11 behaviour
# --------------------------------------------------------------------------- #


def test_without_a_registry_a_foreign_instrument_still_books_in_settlement() -> None:
    """Not a gap. A run with no registry cannot know, and says so by doing what
    it always did: booking under the only declaration it has.
    """

    result = _run(instrument=_SAP, settlement="USD", instruments=None)

    assert len(result.fills) == 1
    assert result.settlement_refusals == ()
    assert [p.currency for p in result.state.portfolio.positions.values()] == ["USD"]


def test_with_and_without_a_registry_a_matching_run_is_identical() -> None:
    matching = _run(instrument=_APPLE, settlement="USD", instruments=registry_of(_APPLE))
    bare = _run(instrument=_APPLE, settlement="USD", instruments=None)

    assert [f.asset_id for f in matching.fills] == [f.asset_id for f in bare.fills]
    assert matching.state.portfolio.cash.balances == bare.state.portfolio.cash.balances
    assert matching.state.id_position.draws == bare.state.id_position.draws


# --------------------------------------------------------------------------- #
# C. Seam 1: a foreign instrument is dropped before the OMS
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("instrument", "settlement", "expected"),
    [(_SAP, "USD", "EUR"), (_APPLE, "EUR", "USD")],
)
def test_a_foreign_instrument_is_refused_in_both_directions(
    instrument: InstrumentRecord, settlement: str, expected: str
) -> None:
    """There is no privileged currency. "Foreign" is relative to the run."""

    result = _run(instrument=instrument, settlement=settlement, instruments=registry_of(instrument))

    assert len(result.settlement_refusals) == 1
    refusal = result.settlement_refusals[0]
    assert isinstance(refusal, SettlementRefusal)
    assert refusal.asset_id == instrument.asset_id
    assert refusal.instrument_currency == expected
    assert refusal.settlement_currency == settlement


def test_the_refused_request_never_reaches_the_oms_and_leaves_nothing_behind() -> None:
    result = _run(instrument=_SAP, settlement="USD", instruments=registry_of(_SAP))
    state = result.state

    assert result.order_requests, "allocation did emit a request"
    assert result.oms_orders == ()
    assert result.execution_reports == ()
    assert result.fills == ()
    assert result.trades == ()
    assert state.oms.orders.open_orders() == ()
    assert state.portfolio.positions == {}
    assert state.trade_records.to_tuple() == ()


def test_both_allocation_ledgers_are_retired() -> None:
    """The failure `_retire_dropped_request` exists to prevent: a contribution
    entry nothing could ever delete, growing for as long as the run continued.
    """

    result = _run(instrument=_SAP, settlement="USD", instruments=registry_of(_SAP))
    request = result.order_requests[0]

    assert request.order_id not in result.state.allocation.reservations
    assert request.order_id not in result.state.allocation.contributions


def test_the_refusal_names_the_instrument_both_currencies_and_the_fix() -> None:
    result = _run(instrument=_SAP, settlement="USD", instruments=registry_of(_SAP))
    detail = result.settlement_refusals[0].detail

    assert "SAP" in detail and "XETR" in detail
    assert "'EUR'" in detail and "'USD'" in detail
    assert "Account.base_currency" in detail, "the message must say how to fix it"


def test_the_refusal_does_not_claim_a_rate_is_missing() -> None:
    """A settlement boundary is not the absence of FX, and must not read as it.

    ``MixedCurrencyValuationError`` says "AlphaLab has no FX rate source". This
    is a different fault with a different fix -- run a pipeline that settles in
    the instrument's currency -- and borrowing that sentence would send a reader
    looking for a rate source that would not help (ADR-0028 decision 10).
    """

    result = _run(instrument=_SAP, settlement="USD", instruments=registry_of(_SAP))
    detail = result.settlement_refusals[0].detail

    assert "FX rate source" not in detail
    assert "no honest single number" not in detail
    assert "not a missing FX rate" in detail


def test_the_run_continues_after_a_refused_request() -> None:
    """A misconfigured instrument must not kill a session. ADR-0028 decision 4."""

    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(
        strategy_id, _SAP.asset_id, {2.0: Decimal("10"), 4.0: Decimal("10")}
    )
    config = _config(strategy_id, currency="USD", instruments=registry_of(_SAP, _APPLE))

    with id_scope(_SEED):
        state = ExecutionPipeline.initialize(
            config, running_strategy_state(strategy_id, strategy), 1.0
        )
        first = ExecutionPipeline.process_quote(
            state, quote(_SAP.asset_id, 2.0, _PRICE), context_factory
        )
        second = ExecutionPipeline.process_quote(
            first.state, quote(_SAP.asset_id, 4.0, _PRICE), context_factory
        )
        third = ExecutionPipeline.process_quote(
            second.state, quote(_APPLE.asset_id, 6.0, _PRICE), context_factory
        )

    assert len(first.settlement_refusals) == 1
    assert len(second.settlement_refusals) == 1
    assert third.valuation is not None, "the run is still valuing normally"


def test_an_unpriced_foreign_instrument_is_still_reported_as_unpriced() -> None:
    """Seam 1 runs *after* the price check, and the ordering is the decision.

    The run genuinely never priced this asset, and reinterpreting a
    classification that was already correct would change what an existing
    diagnostic means. ADR-0028 decision 3.
    """

    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(
        strategy_id, _APPLE.asset_id, {2.0: Decimal("10")}, {2.0: _SAP.asset_id}
    )
    config = _config(strategy_id, currency="USD", instruments=registry_of(_APPLE, _SAP))

    with id_scope(_SEED):
        state = ExecutionPipeline.initialize(
            config, running_strategy_state(strategy_id, strategy), 1.0
        )
        result = ExecutionPipeline.process_quote(
            state, quote(_APPLE.asset_id, 2.0, _PRICE), context_factory
        )

    assert result.settlement_refusals == ()
    assert result.state.unpriced_assets[_SAP.asset_id].reason is (
        UnpricedReason.REGISTERED_BUT_UNPRICED
    )


# --------------------------------------------------------------------------- #
# D. Seam 2: a report the pipeline does not settle is refused
# --------------------------------------------------------------------------- #


def _working_order(
    instruments: InstrumentRegistry | None,
) -> tuple[ExecutionPipelineState, OMSOrder]:
    """A live session with one working external order, ready for a venue fill."""

    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(strategy_id, _APPLE.asset_id, {2.0: Decimal("-10")})
    config = _config(
        strategy_id,
        currency="USD",
        instruments=instruments,
        routing=ExecutionRouting.EXTERNAL,
    )
    with id_scope(_SEED):
        state = ExecutionPipeline.initialize(
            config, running_strategy_state(strategy_id, strategy), 1.0
        )
        state = ExecutionPipeline.process_quote(
            state, quote(_APPLE.asset_id, 2.0, _PRICE), context_factory
        ).state
    return state, state.oms.orders.open_orders()[0]


def _venue_fill(order: OMSOrder) -> BrokerExecution:
    return BrokerExecution(
        execution_id=f"EX-{uuid4()}",
        broker_order_id=broker_order_id_for(order),
        symbol=order.asset_id,
        fill_quantity=Decimal("10"),
        fill_price=_PRICE,
        commission=Decimal("1"),
        timestamp=3.0,
    )


@pytest.mark.parametrize("with_registry", [True, False])
def test_a_venue_report_in_another_currency_is_refused(with_registry: bool) -> None:
    """Seam 2 needs no registry: it compares a report to the configuration."""

    state, order = _working_order(registry_of(_APPLE) if with_registry else None)
    report = execution_report_from_broker(
        _venue_fill(order), order, RoutingConfig(venue="VENUE", currency="JPY")
    )

    with pytest.raises(RuntimeValidationError) as refused:
        ExecutionPipeline.apply_execution_report(state, order, report)

    assert "'JPY'" in str(refused.value) and "'USD'" in str(refused.value)
    assert "RoutingConfig.currency" in str(refused.value)


def test_a_refused_report_leaves_the_callers_state_untouched() -> None:
    state, order = _working_order(registry_of(_APPLE))
    before = state
    report = execution_report_from_broker(
        _venue_fill(order), order, RoutingConfig(venue="VENUE", currency="JPY")
    )

    with pytest.raises(RuntimeValidationError):
        ExecutionPipeline.apply_execution_report(state, order, report)

    assert state is before
    assert state.portfolio.positions == {}
    assert list(state.portfolio.cash.balances) == ["USD"]
    assert state.execution.reports == before.execution.reports


def test_the_broker_convenience_path_is_refused_identically() -> None:
    """``apply_broker_execution`` delegates to ``apply_execution_report``; the
    seam must not be reachable around it.
    """

    state, order = _working_order(registry_of(_APPLE))
    execution = _venue_fill(order)
    mismatched = RoutingConfig(venue="VENUE", currency="JPY")

    with pytest.raises(RuntimeValidationError) as from_convenience:
        apply_broker_execution(state, order, execution, mismatched)
    with pytest.raises(RuntimeValidationError) as from_seam:
        ExecutionPipeline.apply_execution_report(
            state, order, execution_report_from_broker(execution, order, mismatched)
        )

    assert str(from_convenience.value) == str(from_seam.value)


def test_a_matching_venue_report_applies_normally() -> None:
    state, order = _working_order(registry_of(_APPLE))
    report = execution_report_from_broker(
        _venue_fill(order), order, RoutingConfig(venue="VENUE", currency="USD")
    )

    applied, fills, _ = ExecutionPipeline.apply_execution_report(state, order, report)

    assert len(fills) == 1
    assert [p.currency for p in applied.portfolio.positions.values()] == ["USD"]


def test_a_hand_built_report_cannot_bypass_the_seam() -> None:
    """The seam is on the report, not on ``RoutingConfig``, so nothing routes
    around it by constructing a report directly.
    """

    state, order = _working_order(registry_of(_APPLE))
    report = ExecutionReport(
        execution_id=f"EX-{uuid4()}",
        order_id=str(order.order_id.value),
        asset_id=order.asset_id,
        strategy_id=order.strategy_id,
        timestamp=3.0,
        fill_price=_PRICE,
        fill_quantity=Decimal("10"),
        commission=Decimal("1"),
        slippage=Decimal("0"),
        liquidity_flag="",
        venue="VENUE",
        currency="GBP",
        status=FillStatus.FULL_FILL,
    )

    with pytest.raises(RuntimeValidationError):
        ExecutionPipeline.apply_execution_report(state, order, report)


def test_the_simulated_path_cannot_produce_a_mismatched_report() -> None:
    """Structural, not incidental: ``_instruction`` builds every instruction from
    ``config.currency`` and the simulator copies it onto the report.
    """

    for settlement, instrument in (("USD", _APPLE), ("EUR", _SAP)):
        result = _run(
            instrument=instrument,
            settlement=settlement,
            instruments=registry_of(instrument),
        )
        assert [r.currency for r in result.execution_reports] == [settlement]

    from alphalab.runtime.execution_pipeline import _instruction

    assert "state.config.currency" in inspect.getsource(_instruction)


def test_routing_config_keeps_its_public_shape() -> None:
    """Constrained at Seam 2, not removed or redefined. ADR-0028 decision 8."""

    assert list(inspect.signature(RoutingConfig).parameters) == [
        "venue",
        "currency",
        "order_type",
    ]
    assert RoutingConfig().currency == "USD"
    assert list(inspect.signature(execution_report_from_broker).parameters) == [
        "execution",
        "oms_order",
        "config",
    ]


def test_route_order_is_unchanged_and_carries_no_settlement_knowledge() -> None:
    """The deferral recorded in ADR-0028 decision 8, pinned.

    ``route_order`` cannot compare the routing currency to the settlement
    currency because nothing in its signature carries one. Giving it that means
    a new parameter on a public broker-boundary function, which is a larger
    change than the earlier error location is worth.
    """

    from alphalab.runtime.broker_routing import route_order

    assert list(inspect.signature(route_order).parameters) == [
        "broker_state",
        "broker",
        "oms_order",
        "timestamp",
        "mapping",
        "config",
    ]
    assert "base_currency" not in inspect.getsource(route_order)


# --------------------------------------------------------------------------- #
# E. Determinism and identity are untouched
# --------------------------------------------------------------------------- #


def test_the_settlement_refusal_itself_mints_no_identifier() -> None:
    """Measured against the sibling drop, which is the only honest control.

    Allocation has already sized the request and opened both its ledgers by the
    time Seam 1 runs, so a refused run draws more than a run whose strategy
    emitted nothing -- that difference is allocation's, not the refusal's. The
    comparison that isolates this seam is against the *other* pre-OMS drop: an
    unpriced request reaches the same point, retires through the same
    ``_retire_dropped_request``, and never reaches the OMS either. Equal draws
    therefore mean the refusal costs nothing the existing path did not.
    """

    refused = _run(instrument=_SAP, settlement="USD", instruments=registry_of(_SAP))

    # The same intent, dropped for want of a price instead: a USD instrument the
    # run never quotes, on a pipeline that could otherwise settle it.
    unquoted = InstrumentRecord("MSFT", AssetType.EQUITY, "XNAS", "USD")
    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(
        strategy_id, _APPLE.asset_id, {2.0: Decimal("10")}, {2.0: unquoted.asset_id}
    )
    config = _config(strategy_id, currency="USD", instruments=registry_of(_APPLE, unquoted))
    with id_scope(_SEED):
        state = ExecutionPipeline.initialize(
            config, running_strategy_state(strategy_id, strategy), 1.0
        )
        dropped = ExecutionPipeline.process_quote(
            state, quote(_APPLE.asset_id, 2.0, _PRICE), context_factory
        )

    assert dropped.unpriced_requests, "the control really did drop its request"
    assert refused.state.id_position.draws == dropped.state.id_position.draws


def test_neither_seam_touches_instrument_identity() -> None:
    registry = registry_of(_SAP)
    before = (_SAP.asset_id, _SAP.canonical_key)

    _run(instrument=_SAP, settlement="USD", instruments=registry)
    record = registry.record_for(_SAP.asset_id)

    assert record is not None
    assert (record.asset_id, record.canonical_key) == before
    assert record.sector == "Technology", "classification is untouched"


def test_settlement_refusals_are_not_persisted() -> None:
    """Derived, per event, on the result -- which no snapshot carries.

    An aggregated durable record in the manner of ``UnpricedAsset`` would put a
    field on ``ExecutionPipelineState`` and move the pipeline schema. Deferred
    to the release that moves it anyway (ADR-0028 persistence semantics).
    """

    from alphalab.runtime import snapshot as pipeline_snapshot

    assert "settlement_refusals" not in inspect.getsource(pipeline_snapshot)
    assert "SettlementRefusal" not in inspect.getsource(pipeline_snapshot)
    assert not hasattr(
        _run(instrument=_SAP, settlement="USD", instruments=registry_of(_SAP)).state,
        "settlement_refusals",
    )


def test_the_result_field_is_appended_last() -> None:
    """So positional construction of an ``ExecutionPipelineResult`` keeps working."""

    from dataclasses import fields

    names = [f.name for f in fields(ExecutionPipelineResult)]

    assert names[-1] == "settlement_refusals"
    assert names.index("unpriced_requests") < names.index("valuation")


# --------------------------------------------------------------------------- #
# F. The engine underneath is not narrowed
# --------------------------------------------------------------------------- #


def test_portfolio_engine_still_books_any_currency_it_is_given() -> None:
    """ADR-0019 D2: the seams constrain the pipeline, not the public engine."""

    from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
    from alphalab.portfolio.valuation import PortfolioValuation

    state = PortfolioState(account=Account("acct", "EUR", "Foreign", 0.0))
    state = PortfolioEngine.apply_deposit(state, Decimal("100000"), "EUR", 0.0)
    state = PortfolioEngine.apply_fill(state, "a", Decimal("10"), _PRICE, Decimal("1"), 1.0, "EUR")

    assert state.positions["a"].currency == "EUR"
    assert PortfolioValuation.snapshot(state, 2.0, "EUR").currency == "EUR"


def test_a_wholly_foreign_pipeline_run_values_end_to_end() -> None:
    """The case the release must not break, driven through the real path."""

    result = _run(instrument=_SAP, settlement="EUR", instruments=registry_of(_SAP))

    assert result.valuation is not None
    assert result.valuation.currency == "EUR"
    assert result.state.analytics is not None
    assert result.state.portfolio_snapshots.to_tuple()


# --------------------------------------------------------------------------- #
# G. Cross-engine parity
# --------------------------------------------------------------------------- #


def test_every_environment_refuses_the_same_instrument_the_same_way() -> None:
    """Both seams sit on paths all four environments share, so parity is
    structural rather than a convention four call sites must keep.
    """

    from alphalab.backtesting.snapshot import BACKTEST_SNAPSHOT_SCHEMA
    from alphalab.runtime.session import ExecutionMode, SessionConfig, TradingSession

    assert BACKTEST_SNAPSHOT_SCHEMA == 1, "no schema moved"

    seen: dict[str, tuple[str, ...]] = {}
    for mode in (ExecutionMode.BACKTEST, ExecutionMode.REPLAY, ExecutionMode.PAPER):
        strategy_id = str(uuid4())
        strategy = ScriptedStrategy(strategy_id, _SAP.asset_id, {2.0: Decimal("10")})
        session = SessionConfig(
            pipeline=_config(strategy_id, currency="USD", instruments=registry_of(_SAP)),
            mode=mode,
            seed=_SEED,
            start_timestamp=1.0,
        )
        state = TradingSession.initialize(session, running_strategy_state(strategy_id, strategy))
        with id_scope(_SEED):
            _, result = TradingSession.advance(
                state,
                _record(_SAP.asset_id, 2.0),
                context_factory,
            )
        assert result is not None
        seen[mode.name] = tuple(r.instrument_currency for r in result.settlement_refusals)

    assert set(seen.values()) == {("EUR",)}, seen


def _record(asset_id: str, timestamp: float) -> MarketRecord:
    return MarketRecord(f"rec-{timestamp}", timestamp, quote(asset_id, timestamp, _PRICE))


def test_the_live_path_refuses_at_the_other_seam() -> None:
    """Live differs only in where an accepted order executes, so a foreign
    instrument is refused at Seam 1 there too -- and a venue report that
    disagrees is refused at Seam 2, which the other three never reach.
    """

    refused = _run(
        instrument=_SAP,
        settlement="USD",
        instruments=registry_of(_SAP),
        routing=ExecutionRouting.EXTERNAL,
    )

    assert len(refused.settlement_refusals) == 1
    assert refused.oms_orders == ()

    state, order = _working_order(registry_of(_APPLE))
    with pytest.raises(RuntimeValidationError):
        ExecutionPipeline.apply_execution_report(
            state,
            order,
            execution_report_from_broker(_venue_fill(order), order, RoutingConfig(currency="CHF")),
        )
