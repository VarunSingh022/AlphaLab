"""Classification reaches the run, and the run remembers what it used.

``AttributionMetrics.pnl_by_sector`` has been implemented, persisted and decoded
since v2.6 and empty on every pipeline run ever executed, because
``_trade_record`` hardcoded ``sector_id=None``. ``ExposureStatus.sector_exposure``
has been declared since before that and populated by nothing at all. The
registry that could answer both was already threaded through the whole execution
path and consulted only from ``_classify_unpriced`` -- a route a healthy run
never takes.

These tests pin v2.11's answer: the registry is read once per **fill**, on the
single path a simulated and a venue fill share, and the answer is frozen onto
the ``TradeRecord``. Two facts, two owners, two tenses -- the registry says what
an instrument *is* classified as, and the trade record says what it *was*
classified as when the fill happened. See ADR-0027.

Nothing here stubs a pipeline stage.
"""

import inspect
import time
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation.snapshot import ALLOCATION_SNAPSHOT_SCHEMA
from alphalab.analytics.attribution import calculate_attribution
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.backtesting.snapshot import BACKTEST_SNAPSHOT_SCHEMA
from alphalab.broker.execution import BrokerExecution
from alphalab.common.ids import current_id_position, id_scope
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.registry import (
    InstrumentRegistry,
    classify_instrument,
    register_instrument,
)
from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA
from alphalab.market.source import SequenceSource
from alphalab.oms.snapshot import OMS_SNAPSHOT_SCHEMA
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.portfolio.snapshot import PORTFOLIO_SNAPSHOT_SCHEMA
from alphalab.runtime.broker_routing import (
    RoutingConfig,
    apply_broker_execution,
    broker_order_id_for,
)
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.runtime.session import (
    ExecutionMode,
    SessionConfig,
    TradingSession,
)
from alphalab.runtime.session_snapshot import SESSION_SNAPSHOT_SCHEMA
from alphalab.runtime.snapshot import (
    PIPELINE_SNAPSHOT_SCHEMA,
    RuntimeObjects,
)
from alphalab.runtime.snapshot import capture as capture_pipeline
from alphalab.runtime.snapshot import from_primitives as pipeline_from_primitives
from alphalab.runtime.snapshot import restore as restore_pipeline
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    equity,
    pipeline_config,
    quote,
    registry_of,
    running_strategy_state,
)

_STRATEGY = "SECTOR-STRAT"

_APPLE = equity("AAPL", "Technology")
_MSFT = equity("MSFT", "Technology")
_JPM = equity("JPM", "Financials")
#: The same instrument as ``_APPLE`` -- the sector is outside the identity key,
#: so these two derive one ``asset_id``. That is ADR-0016 N5, and it is why a
#: test needing a *different* unclassified asset uses ``_TESLA``.
_PLAIN_APPLE = equity("AAPL")
_TESLA = equity("TSLA")

PRICE = Decimal("100")

#: Open at 2.0, close at 11.0 -- one opening fill and one realizing fill.
_ROUND_TRIP = {2.0: Decimal("10"), 11.0: Decimal("-10")}


def _run(
    plan: dict[float, Decimal],
    registry: InstrumentRegistry | None,
    asset_id: str,
    prices: dict[float, Decimal] | None = None,
) -> ExecutionPipelineState:
    """Drive the real pipeline over ``plan``, with or without a registry."""

    config = pipeline_config(_STRATEGY)
    if registry is not None:
        config = replace(config, instruments=registry)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(_STRATEGY, ScriptedStrategy(_STRATEGY, asset_id, plan)),
        1.0,
    )
    for timestamp in sorted(plan):
        price = (prices or {}).get(timestamp, PRICE)
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, timestamp, price), context_factory
        ).state
    return state


def _sectors(state: ExecutionPipelineState) -> list[str | None]:
    return [record.sector_id for record in state.trade_records]


# --------------------------------------------------------------------------- #
# A. The classification reaches the trade record
# --------------------------------------------------------------------------- #


def test_a_classified_instrument_reaches_the_trade_record() -> None:
    state = _run(_ROUND_TRIP, registry_of(_APPLE), _APPLE.asset_id)

    assert _sectors(state) == ["Technology", "Technology"]


def test_a_classification_applied_after_registration_reaches_the_run() -> None:
    """The registry write path, not just a record declared classified up front."""

    registry = classify_instrument(
        register_instrument(InstrumentRegistry(), _PLAIN_APPLE),
        _PLAIN_APPLE.asset_id,
        "Technology",
    )
    state = _run(_ROUND_TRIP, registry, _PLAIN_APPLE.asset_id)

    assert _sectors(state) == ["Technology", "Technology"]


def test_a_run_with_no_registry_records_no_sector() -> None:
    state = _run(_ROUND_TRIP, None, _APPLE.asset_id)

    assert _sectors(state) == [None, None]


def test_an_unregistered_asset_records_no_sector() -> None:
    """A registry that does not hold this asset answers nothing, and says so."""

    state = _run(_ROUND_TRIP, registry_of(_JPM), _APPLE.asset_id)

    assert _sectors(state) == [None, None]


def test_a_registered_but_unclassified_asset_records_no_sector() -> None:
    state = _run(_ROUND_TRIP, registry_of(_PLAIN_APPLE), _PLAIN_APPLE.asset_id)

    assert _sectors(state) == [None, None]


def test_an_unclassified_instrument_can_be_classified_and_then_reports() -> None:
    registry = registry_of(_PLAIN_APPLE)
    before = _run(_ROUND_TRIP, registry, _PLAIN_APPLE.asset_id)
    after = _run(
        _ROUND_TRIP,
        classify_instrument(registry, _PLAIN_APPLE.asset_id, "Technology"),
        _PLAIN_APPLE.asset_id,
    )

    assert _sectors(before) == [None, None]
    assert _sectors(after) == ["Technology", "Technology"]


# --------------------------------------------------------------------------- #
# B. Attribution, end to end
# --------------------------------------------------------------------------- #


def test_pnl_by_sector_buckets_a_real_run() -> None:
    state = _run(
        _ROUND_TRIP,
        registry_of(_APPLE),
        _APPLE.asset_id,
        prices={2.0: Decimal("100"), 11.0: Decimal("110")},
    )
    metrics = calculate_attribution(tuple(state.trade_records))

    assert set(metrics.pnl_by_sector) == {"Technology"}
    assert metrics.pnl_by_sector["Technology"] == Decimal("100.00")


def test_pnl_by_sector_sums_to_pnl_by_asset_over_classified_assets() -> None:
    state = _run(
        _ROUND_TRIP,
        registry_of(_APPLE),
        _APPLE.asset_id,
        prices={2.0: Decimal("100"), 11.0: Decimal("110")},
    )
    metrics = calculate_attribution(tuple(state.trade_records))

    assert sum(metrics.pnl_by_sector.values()) == sum(metrics.pnl_by_asset.values())


def test_two_assets_in_one_sector_land_in_one_bucket() -> None:
    registry = registry_of(_APPLE, _MSFT)
    prices = {2.0: Decimal("100"), 11.0: Decimal("110")}
    apple = _run(_ROUND_TRIP, registry, _APPLE.asset_id, prices)
    microsoft = _run(_ROUND_TRIP, registry, _MSFT.asset_id, prices)

    metrics = calculate_attribution(
        (*apple.trade_records, *microsoft.trade_records),
    )

    assert set(metrics.pnl_by_sector) == {"Technology"}
    assert len(metrics.pnl_by_asset) == 2
    assert metrics.pnl_by_sector["Technology"] == Decimal("200.00")


def test_two_sectors_stay_two_buckets() -> None:
    registry = registry_of(_APPLE, _JPM)
    prices = {2.0: Decimal("100"), 11.0: Decimal("110")}
    tech = _run(_ROUND_TRIP, registry, _APPLE.asset_id, prices)
    fin = _run(_ROUND_TRIP, registry, _JPM.asset_id, prices)

    metrics = calculate_attribution((*tech.trade_records, *fin.trade_records))

    assert set(metrics.pnl_by_sector) == {"Technology", "Financials"}


def test_no_unclassified_placeholder_appears_in_any_breakdown() -> None:
    """v2.6's absence rule, still enforced now that a real bucket exists."""

    registry = registry_of(_APPLE, _TESLA)
    classified = _run(_ROUND_TRIP, registry, _APPLE.asset_id)
    unclassified = _run(_ROUND_TRIP, registry, _TESLA.asset_id)

    both = calculate_attribution((*classified.trade_records, *unclassified.trade_records))

    assert "UNCLASSIFIED" not in both.pnl_by_sector
    assert set(both.pnl_by_sector) == {"Technology"}
    assert len(both.pnl_by_asset) == 2, "the unclassified asset is still counted by asset"
    assert _sectors(unclassified) == [None, None]


def test_an_unclassified_run_still_produces_an_empty_breakdown_not_a_bucket() -> None:
    state = _run(_ROUND_TRIP, None, _APPLE.asset_id)

    assert calculate_attribution(tuple(state.trade_records)).pnl_by_sector == {}


# --------------------------------------------------------------------------- #
# C. One path for a simulated fill and a venue fill
# --------------------------------------------------------------------------- #


def _live_session_with_a_working_order() -> Any:
    """A live session whose accepted order is left working for a venue."""

    backtest = backtest_config(_STRATEGY)
    config = SessionConfig(
        pipeline=replace(backtest.pipeline, instruments=registry_of(_APPLE)),
        mode=ExecutionMode.LIVE,
        fill_policy=backtest.fill_policy,
        seed=backtest.seed,
        start_timestamp=backtest.start_timestamp,
    )
    source = SequenceSource.from_records(
        "LIVE", dataset_of_quotes(_APPLE.asset_id, [Decimal("100")]).records
    )
    return TradingSession.run(
        config,
        source,
        running_strategy_state(
            _STRATEGY, ScriptedStrategy(_STRATEGY, _APPLE.asset_id, {2.0: Decimal("10")})
        ),
        context_factory,
    )


def test_the_live_session_leaves_the_order_working_and_records_no_trade_yet() -> None:
    session = _live_session_with_a_working_order()

    assert session.config.pipeline.routing is ExecutionRouting.EXTERNAL
    assert len(session.working_orders) == 1
    assert len(session.pipeline.trade_records) == 0


def test_a_venue_fill_records_the_same_sector_a_simulated_fill_does() -> None:
    """The single shared path is what makes this structural rather than lucky."""

    session = _live_session_with_a_working_order()
    order = session.working_orders[0]

    pipeline, fills, _ = apply_broker_execution(
        session.pipeline,
        order,
        BrokerExecution(
            execution_id="EX-1",
            broker_order_id=broker_order_id_for(order),
            symbol=_APPLE.asset_id,
            fill_quantity=order.quantity,
            fill_price=Decimal("100"),
            commission=Decimal("1.00"),
            timestamp=3.0,
            external_id="VENUE-1",
        ),
        RoutingConfig(venue="VENUE", currency="USD"),
    )

    assert len(fills) == 1
    assert [record.sector_id for record in pipeline.trade_records] == ["Technology"]


def test_the_sector_is_read_on_the_one_path_both_fills_take() -> None:
    """``_trade_record`` is a pure formatter; the read happens at its call site."""

    from alphalab.runtime import execution_pipeline as module

    assert "sector" in inspect.signature(module._trade_record).parameters
    source = inspect.getsource(module._apply_report_to_portfolio)

    assert "_sector_for(state, report.asset_id)" in source
    assert "_sector_for" not in inspect.getsource(module.ExecutionPipeline.process_market_event)


# --------------------------------------------------------------------------- #
# D. Provenance: reclassification never rewrites history
# --------------------------------------------------------------------------- #


def test_mid_run_reclassification_does_not_rewrite_earlier_trade_records() -> None:
    registry = registry_of(_APPLE)
    config = replace(pipeline_config(_STRATEGY), instruments=registry)
    plan = {2.0: Decimal("10"), 5.0: Decimal("5"), 11.0: Decimal("-15")}
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(_STRATEGY, ScriptedStrategy(_STRATEGY, _APPLE.asset_id, plan)),
        1.0,
    )

    state = ExecutionPipeline.process_quote(
        state, quote(_APPLE.asset_id, 2.0, PRICE), context_factory
    ).state
    first = tuple(state.trade_records)

    # The operator reclassifies between events. The registry is configuration,
    # so this is a new config value threaded onto the run.
    state = replace(
        state,
        config=replace(
            state.config,
            instruments=classify_instrument(registry, _APPLE.asset_id, "Hardware"),
        ),
    )
    for timestamp in (5.0, 11.0):
        state = ExecutionPipeline.process_quote(
            state, quote(_APPLE.asset_id, timestamp, PRICE), context_factory
        ).state

    assert _sectors(state) == ["Technology", "Hardware", "Hardware"]
    assert tuple(state.trade_records)[: len(first)] == first


def test_mid_run_reclassification_splits_the_breakdown_across_both_sectors() -> None:
    registry = registry_of(_APPLE)
    config = replace(pipeline_config(_STRATEGY), instruments=registry)
    plan = {2.0: Decimal("10"), 5.0: Decimal("-4"), 11.0: Decimal("-6")}
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(_STRATEGY, ScriptedStrategy(_STRATEGY, _APPLE.asset_id, plan)),
        1.0,
    )
    prices = {2.0: Decimal("100"), 5.0: Decimal("110"), 11.0: Decimal("120")}

    for timestamp in (2.0, 5.0):
        state = ExecutionPipeline.process_quote(
            state, quote(_APPLE.asset_id, timestamp, prices[timestamp]), context_factory
        ).state
    state = replace(
        state,
        config=replace(
            state.config,
            instruments=classify_instrument(registry, _APPLE.asset_id, "Hardware"),
        ),
    )
    state = ExecutionPipeline.process_quote(
        state, quote(_APPLE.asset_id, 11.0, prices[11.0]), context_factory
    ).state

    metrics = calculate_attribution(tuple(state.trade_records))

    assert set(metrics.pnl_by_sector) == {"Technology", "Hardware"}
    assert sum(metrics.pnl_by_sector.values()) == sum(metrics.pnl_by_asset.values())


def test_the_trade_record_holds_a_label_and_not_a_registry_reference() -> None:
    state = _run(_ROUND_TRIP, registry_of(_APPLE), _APPLE.asset_id)
    record = next(iter(state.trade_records))

    assert isinstance(record.sector_id, str)
    assert record.sector_id == "Technology"


def test_unclassifying_afterwards_leaves_the_completed_run_alone() -> None:
    registry = registry_of(_APPLE)
    state = _run(_ROUND_TRIP, registry, _APPLE.asset_id)
    classify_instrument(registry, _APPLE.asset_id, None)

    assert _sectors(state) == ["Technology", "Technology"]


def test_restoring_with_a_differently_classified_registry_leaves_history_intact() -> None:
    """The registry is configuration; the records carry their own truth."""

    state = _run(_ROUND_TRIP, registry_of(_APPLE), _APPLE.asset_id)
    snapshot = capture_pipeline(state)

    reclassified = registry_of(equity("AAPL", "Hardware"))
    restored = restore_pipeline(
        snapshot,
        RuntimeObjects(
            sizing_model=state.config.sizing_model,
            simulator=state.config.simulator,
            strategies={
                _STRATEGY: state.strategy.strategies[_STRATEGY].instance,
            },
            instruments=reclassified,
        ),
    )

    assert _sectors(restored) == ["Technology", "Technology"]
    assert restored.config.instruments is reclassified


# --------------------------------------------------------------------------- #
# E. The invariants v2.9 and v2.10 left behind
# --------------------------------------------------------------------------- #


def _seeded_draws(registry: InstrumentRegistry | None) -> int:
    with id_scope(20220):
        _run(_ROUND_TRIP, registry, _APPLE.asset_id)
        return current_id_position().draws


def test_resolving_a_sector_draws_no_identifier() -> None:
    """The whole feature is two keyed lookups; ADR-0022's stream must not move."""

    assert _seeded_draws(registry_of(_APPLE)) == _seeded_draws(None)


def test_the_identifier_stream_is_where_v2_10_left_it() -> None:
    """A pinned absolute, so a future change that does draw is caught here."""

    assert _seeded_draws(None) == 51


def test_a_classified_run_and_an_unclassified_one_differ_only_in_the_sector() -> None:
    classified = _run(_ROUND_TRIP, registry_of(_APPLE), _APPLE.asset_id)
    control = _run(_ROUND_TRIP, registry_of(_PLAIN_APPLE), _PLAIN_APPLE.asset_id)

    assert classified.portfolio.cash.balance("USD") == control.portfolio.cash.balance("USD")
    assert classified.portfolio.realized_pnl == control.portfolio.realized_pnl
    assert len(classified.fills) == len(control.fills)
    assert [r.realized_pnl for r in classified.trade_records] == [
        r.realized_pnl for r in control.trade_records
    ]
    assert _sectors(classified) != _sectors(control)


def test_a_run_with_no_registry_is_unchanged_in_every_field() -> None:
    """The v2.10 control: `instruments=None` must behave exactly as it did."""

    with id_scope(20220):
        control = _run(_ROUND_TRIP, None, _APPLE.asset_id)
    with id_scope(20220):
        again = _run(_ROUND_TRIP, None, _APPLE.asset_id)

    assert capture_pipeline(control) == capture_pipeline(again)
    assert _sectors(control) == [None, None]
    assert calculate_attribution(tuple(control.trade_records)).pnl_by_sector == {}


@pytest.mark.parametrize(
    ("constant", "expected"),
    [
        (PIPELINE_SNAPSHOT_SCHEMA, 2),
        (SESSION_SNAPSHOT_SCHEMA, 1),
        (BACKTEST_SNAPSHOT_SCHEMA, 1),
        (ALLOCATION_SNAPSHOT_SCHEMA, 1),
        (OMS_SNAPSHOT_SCHEMA, 1),
        (PORTFOLIO_SNAPSHOT_SCHEMA, 2),
        (LIFECYCLE_SNAPSHOT_SCHEMA, 1),
    ],
)
def test_no_schema_constant_moved(constant: int, expected: int) -> None:
    """v2.11 adds a value to two existing persisted fields and no field anywhere."""

    assert constant == expected


def test_a_classified_run_round_trips_in_memory_and_across_json() -> None:
    state = _run(_ROUND_TRIP, registry_of(_APPLE), _APPLE.asset_id)
    objects = RuntimeObjects(
        sizing_model=state.config.sizing_model,
        simulator=state.config.simulator,
        strategies={_STRATEGY: state.strategy.strategies[_STRATEGY].instance},
        instruments=state.config.instruments,
    )
    snapshot = capture_pipeline(state)

    assert restore_pipeline(snapshot, objects) == state

    across_json = pipeline_from_primitives(deserialize(serialize(snapshot)))

    assert restore_pipeline(across_json, objects) == state
    assert _sectors(restore_pipeline(across_json, objects)) == ["Technology", "Technology"]


def test_a_payload_carrying_a_null_sector_still_restores() -> None:
    """A v2.10 payload is exactly this: same shape, `sector_id` null throughout."""

    state = _run(_ROUND_TRIP, None, _APPLE.asset_id)
    payload = deserialize(serialize(capture_pipeline(state)))

    assert payload["schema_version"] == 2
    assert [record["sector_id"] for record in payload["trade_records"]] == [None, None]

    objects = RuntimeObjects(
        sizing_model=state.config.sizing_model,
        simulator=state.config.simulator,
        strategies={_STRATEGY: state.strategy.strategies[_STRATEGY].instance},
    )

    assert restore_pipeline(pipeline_from_primitives(payload), objects) == state


def test_the_trade_record_payload_gained_no_key() -> None:
    """No sector reason, source or timestamp: the shape is v2.10's exactly."""

    state = _run(_ROUND_TRIP, registry_of(_APPLE), _APPLE.asset_id)
    payload = deserialize(serialize(capture_pipeline(state)))

    assert set(payload["trade_records"][0]) == {
        "trade_id",
        "asset_id",
        "sector_id",
        "realized_pnl",
        "notional_value",
        "holding_period_seconds",
        "contributions",
    }


# --------------------------------------------------------------------------- #
# F. Cross-engine parity
# --------------------------------------------------------------------------- #


def test_backtest_replay_and_paper_agree_on_every_sector() -> None:
    registry = registry_of(_APPLE)
    mids = [Decimal("100"), Decimal("105"), Decimal("110")]
    plan = {2.0: Decimal("10"), 4.0: Decimal("-10")}

    def _config() -> Any:
        base = backtest_config(_STRATEGY)
        return replace(base, pipeline=replace(base.pipeline, instruments=registry))

    def _state() -> Any:
        return running_strategy_state(_STRATEGY, ScriptedStrategy(_STRATEGY, _APPLE.asset_id, plan))

    dataset = dataset_of_quotes(_APPLE.asset_id, mids)

    backtest = BacktestEngine.run(_config(), dataset, _state(), context_factory)
    replayed = ReplayBacktest.run(_config(), dataset, _state(), context_factory)

    session_base = _config()
    session = TradingSession.run(
        SessionConfig(
            pipeline=session_base.pipeline,
            mode=ExecutionMode.PAPER,
            fill_policy=session_base.fill_policy,
            seed=session_base.seed,
            start_timestamp=session_base.start_timestamp,
        ),
        SequenceSource.from_records("PAPER", dataset.records),
        _state(),
        context_factory,
    )

    expected = ["Technology", "Technology"]

    assert [r.sector_id for r in backtest.state.trade_records] == expected
    assert [r.sector_id for r in replayed.backtest.state.trade_records] == expected
    assert _sectors(session.pipeline) == expected


def test_the_backtest_report_carries_the_sector_breakdown() -> None:
    registry = registry_of(_APPLE)
    base = backtest_config(_STRATEGY)
    config = replace(base, pipeline=replace(base.pipeline, instruments=registry))
    result = BacktestEngine.run(
        config,
        dataset_of_quotes(_APPLE.asset_id, [Decimal("100"), Decimal("105"), Decimal("110")]),
        running_strategy_state(
            _STRATEGY,
            ScriptedStrategy(_STRATEGY, _APPLE.asset_id, {2.0: Decimal("10"), 4.0: Decimal("-10")}),
        ),
        context_factory,
    )
    report = result.state.analytics.reports[-1]

    assert set(report.attribution.pnl_by_sector) == {"Technology"}


# --------------------------------------------------------------------------- #
# G. Layering and cost
# --------------------------------------------------------------------------- #


def test_the_instrument_package_still_imports_no_higher_layer() -> None:
    """Classification added a write, not a dependency."""

    import pkgutil

    import alphalab.instrument as package

    forbidden = (
        "alphalab.market",
        "alphalab.runtime",
        "alphalab.analytics",
        "alphalab.strategy",
        "alphalab.oms",
        "alphalab.portfolio",
        "alphalab.risk",
        "alphalab.execution",
    )
    for module in pkgutil.iter_modules(package.__path__):
        source = inspect.getsource(__import__(f"alphalab.instrument.{module.name}", fromlist=["_"]))
        for name in forbidden:
            assert f"import {name}" not in source, f"{module.name} imports {name}"


def test_the_runtime_reads_the_registry_through_record_for_and_nothing_else() -> None:
    """ADR-0016 keeps resolution at the wire boundary; v2.11 takes none of it back."""

    from alphalab.runtime import execution_pipeline as module

    source = inspect.getsource(module)

    assert ".resolve(" not in source
    assert "register_instrument" not in source
    assert "classify_instrument" not in source
    # Four keyed reads, and no fifth. One for the unpriced path; one in
    # `_sector_of`, the single rule both the fill reader and the exposure reader
    # go through; one in `_currency_of`, its v2.12 sibling (ADR-0028); and one on
    # `_settlement_refusal`'s cold path, which a healthy run never takes and
    # which buys a message naming the instrument rather than only its id.
    assert source.count("record_for(") == 4


def test_sector_resolution_is_a_keyed_lookup_and_never_a_scan() -> None:
    """Cost must not depend on how many instruments the registry holds."""

    from alphalab.runtime.execution_pipeline import _sector_for

    def _elapsed(count: int) -> float:
        registry = registry_of(*(equity(f"SYM{index}", "Technology") for index in range(count)))
        state = _run({2.0: Decimal("1")}, registry, _APPLE.asset_id)
        state = replace(state, config=replace(state.config, instruments=registry))
        target = equity(f"SYM{count - 1}", "Technology").asset_id
        start = time.perf_counter()
        for _ in range(2_000):
            _sector_for(state, target)
        return time.perf_counter() - start

    small = _elapsed(10)
    large = _elapsed(400)

    # 40x the registry. A scan would cost about 40x; a keyed lookup is flat.
    # The bound is deliberately loose -- this catches a reintroduced scan, not
    # a constant factor.
    assert large < small * 5, f"lookup grew with registry size: {small=} {large=}"


def test_an_unclassified_asset_costs_one_lookup_and_returns_none() -> None:
    from alphalab.runtime.execution_pipeline import _sector_for

    state = _run({2.0: Decimal("1")}, registry_of(_PLAIN_APPLE), _PLAIN_APPLE.asset_id)

    assert _sector_for(state, _PLAIN_APPLE.asset_id) is None
    assert _sector_for(state, "not-an-asset") is None
    assert _sector_for(replace(state, config=replace(state.config, instruments=None)), "x") is None


# --------------------------------------------------------------------------- #
# H. Exposure by sector -- the second consumer that was never populated
# --------------------------------------------------------------------------- #


def _two_sector_book(registry: InstrumentRegistry | None) -> ExecutionPipelineState:
    """A run holding one long and one short, in two different sectors.

    ``asset_for`` redirects the intent at each timestamp, so one scripted
    strategy opens a position in each asset. The final event marks both without
    trading, so the exposure under test is the one a *marked* book produces.
    """

    config = pipeline_config(_STRATEGY)
    if registry is not None:
        config = replace(config, instruments=registry)

    plan = {2.0: Decimal("10"), 3.0: Decimal("-4"), 4.0: Decimal("0")}
    asset_for = {2.0: _APPLE.asset_id, 3.0: _JPM.asset_id}
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            _STRATEGY, ScriptedStrategy(_STRATEGY, _APPLE.asset_id, plan, asset_for)
        ),
        1.0,
    )
    for timestamp, asset_id in ((2.0, _APPLE.asset_id), (3.0, _JPM.asset_id)):
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, timestamp, PRICE), context_factory
        ).state
    # One more event: no intent, so the book is only re-marked.
    return ExecutionPipeline.process_quote(
        state, quote(_APPLE.asset_id, 5.0, PRICE), context_factory
    ).state


def test_sector_exposure_buckets_the_marked_positions() -> None:
    state = _two_sector_book(registry_of(_APPLE, _JPM))
    exposure = state.risk.exposure

    assert set(exposure.sector_exposure) == {"Technology", "Financials"}
    assert exposure.sector_exposure["Technology"] == Decimal("1000.00")
    assert exposure.sector_exposure["Financials"] == Decimal("-400.00")


def test_sector_exposure_is_signed_and_sums_to_net_exposure() -> None:
    """Signed, matching ``asset_exposure`` and ``net_exposure`` -- not gross."""

    exposure = _two_sector_book(registry_of(_APPLE, _JPM)).risk.exposure

    assert sum(exposure.sector_exposure.values()) == exposure.net_exposure
    assert sum(exposure.sector_exposure.values()) != exposure.gross_exposure


def test_two_assets_in_one_sector_share_one_exposure_bucket() -> None:
    exposure = _two_sector_book(registry_of(_APPLE, equity("JPM", "Technology"))).risk.exposure

    assert set(exposure.sector_exposure) == {"Technology"}
    assert exposure.sector_exposure["Technology"] == Decimal("600.00")


def test_an_unclassified_position_is_absent_from_sector_exposure_not_zeroed() -> None:
    exposure = _two_sector_book(registry_of(_APPLE, equity("JPM"))).risk.exposure

    assert set(exposure.sector_exposure) == {"Technology"}
    assert len(exposure.asset_exposure) == 2, "it is still counted by asset"


def test_an_unregistered_position_is_absent_from_sector_exposure() -> None:
    exposure = _two_sector_book(registry_of(_APPLE)).risk.exposure

    assert set(exposure.sector_exposure) == {"Technology"}
    assert len(exposure.asset_exposure) == 2


def test_sector_exposure_is_empty_when_no_registry_is_configured() -> None:
    exposure = _two_sector_book(None).risk.exposure

    assert exposure.sector_exposure == {}
    assert len(exposure.asset_exposure) == 2


def test_the_other_exposure_figures_are_untouched_by_the_sector_pass() -> None:
    """The single-pass rewrite must not move a number that already existed."""

    classified = _two_sector_book(registry_of(_APPLE, _JPM)).risk.exposure
    control = _two_sector_book(None).risk.exposure

    assert classified.gross_exposure == control.gross_exposure
    assert classified.net_exposure == control.net_exposure
    assert classified.long_exposure == control.long_exposure
    assert classified.short_exposure == control.short_exposure
    assert dict(classified.asset_exposure) == dict(control.asset_exposure)


def test_a_closed_position_leaves_its_sector_bucket() -> None:
    """Exposure describes the book now, not what the run ever held."""

    registry = registry_of(_APPLE)
    state = _run(_ROUND_TRIP, registry, _APPLE.asset_id)

    assert state.portfolio.positions == {}
    assert state.risk.exposure.sector_exposure == {}


def test_populating_sector_exposure_draws_no_additional_identifier() -> None:
    def _draws(registry: InstrumentRegistry | None) -> int:
        with id_scope(31337):
            _two_sector_book(registry)
            return current_id_position().draws

    assert _draws(registry_of(_APPLE, _JPM)) == _draws(None)


def test_sector_exposure_survives_a_round_trip_without_moving_the_schema() -> None:
    state = _two_sector_book(registry_of(_APPLE, _JPM))
    objects = RuntimeObjects(
        sizing_model=state.config.sizing_model,
        simulator=state.config.simulator,
        strategies={_STRATEGY: state.strategy.strategies[_STRATEGY].instance},
        instruments=state.config.instruments,
    )
    payload = deserialize(serialize(capture_pipeline(state)))
    restored = restore_pipeline(pipeline_from_primitives(payload), objects)

    assert payload["schema_version"] == PIPELINE_SNAPSHOT_SCHEMA == 2
    assert payload["risk"]["exposure"]["sector_exposure"] == {
        "Technology": "1000.00",
        "Financials": "-400.00",
    }
    assert dict(restored.risk.exposure.sector_exposure) == dict(state.risk.exposure.sector_exposure)
    assert restored == state


def test_the_exposure_payload_gained_no_key() -> None:
    state = _two_sector_book(registry_of(_APPLE, _JPM))
    payload = deserialize(serialize(capture_pipeline(state)))

    assert set(payload["risk"]["exposure"]) == {
        "gross_exposure",
        "net_exposure",
        "long_exposure",
        "short_exposure",
        "asset_exposure",
        "sector_exposure",
    }


def test_risk_limits_still_read_no_sector() -> None:
    """Exposure by sector is visibility. Enforcement was not extended."""

    from alphalab.risk import checks, limits

    for module in (checks, limits):
        assert "sector" not in inspect.getsource(module)


def test_the_simulator_and_registry_are_independent_configuration() -> None:
    """A registry is not an execution object; supplying one changes no fill."""

    classified = _run(_ROUND_TRIP, registry_of(_APPLE), _APPLE.asset_id)
    plain = _run(_ROUND_TRIP, None, _APPLE.asset_id)

    assert isinstance(classified.config.simulator, ExecutionSimulator)
    assert [f.quantity for f in classified.fills] == [f.quantity for f in plain.fills]
    assert [f.price for f in classified.fills] == [f.price for f in plain.fills]
