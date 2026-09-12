"""A run that produced no fills can say why.

Ten things can stop a request becoming a fill. Nine of them leave a reason
behind: allocation records ``AllocationRejected`` and ``BudgetExceeded``, risk
records ``RiskRejected`` with its violations, an externally routed order stays
open in the OMS, and a non-trading execution closes its order with the status
that ended it.

The tenth left nothing. A request for an asset the run never priced was dropped
before the OMS -- so no order, no status -- and emitted only an
``AllocationReservationReleased``, which is the identical event a risk
rejection, a ``NO_FILL`` and a partial-fill withdrawal each emit. The reason was
visible on the per-event ``ExecutionPipelineResult`` and gone by the time the
run finished. This is the ADR-0016 section 3 failure mode: a strategy naming an
instrument the run never priced produces zero fills and no explanation.

``ExecutionPipelineState.unpriced_assets`` records it, aggregated per asset so a
misconfigured live session counts rather than grows.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import AssetType
from alphalab.instrument import InstrumentRecord, InstrumentRegistry, register_instrument
from alphalab.market.source import SequenceSource
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineResult,
    UnpricedAsset,
    UnpricedReason,
)
from alphalab.runtime.run import ExecutionMode, RunConfig, RunStep
from alphalab.runtime.session import TradingSession
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    pipeline_config,
    quote,
    running_strategy_state,
)


def _running(strategy_id: str, asset_id: str, plan, asset_for=None) -> StrategyRuntimeState:  # type: ignore[no-untyped-def]
    return running_strategy_state(
        strategy_id, ScriptedStrategy(strategy_id, asset_id, plan, asset_for)
    )


def _drive(events: int, target: str | None = None, *, priced: str | None = None):  # type: ignore[no-untyped-def]
    """Run ``events`` quotes, with every intent naming ``target``."""

    strategy_id = str(uuid4())
    asset_id = priced or str(uuid4())
    named = target if target is not None else asset_id
    plan = {2.0 + index: Decimal("1") for index in range(events)}
    asset_for = {2.0 + index: named for index in range(events)}

    config = pipeline_config(strategy_id)
    state = ExecutionPipeline.initialize(
        config, _running(strategy_id, asset_id, plan, asset_for), 1.0
    )
    results: list[ExecutionPipelineResult] = []
    for index in range(events):
        result = ExecutionPipeline.process_quote(
            state, quote(asset_id, 2.0 + index, Decimal("100")), context_factory
        )
        results.append(result)
        state = result.state
    return state, asset_id, named, results


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #


def test_a_run_that_priced_everything_records_nothing() -> None:
    state, _, _, _ = _drive(4)

    assert len(state.fills) == 4
    assert dict(state.unpriced_assets) == {}


def test_one_dropped_request_records_one_asset() -> None:
    never = str(uuid4())
    state, _, _, _ = _drive(1, target=never)

    entries = tuple(state.unpriced_assets.values())
    assert len(entries) == 1
    entry = entries[0]
    assert entry.asset_id == never
    assert entry.occurrences == 1
    assert entry.first_timestamp == entry.last_timestamp == 2.0
    assert entry.reason is UnpricedReason.NO_PRICE_OBSERVED
    assert never in entry.detail


def test_repeated_drops_count_and_advance_the_last_timestamp() -> None:
    never = str(uuid4())
    state, _, _, _ = _drive(5, target=never)

    entry = state.unpriced_assets[never]
    assert entry.occurrences == 5
    assert entry.first_timestamp == 2.0
    assert entry.last_timestamp == 6.0


def test_the_first_timestamp_never_moves() -> None:
    never = str(uuid4())
    one, _, _, _ = _drive(1, target=never)
    many, _, _, _ = _drive(9, target=never)

    assert one.unpriced_assets[never].first_timestamp == 2.0
    assert many.unpriced_assets[never].first_timestamp == 2.0


def test_two_assets_are_recorded_in_first_drop_order() -> None:
    strategy_id, priced = str(uuid4()), str(uuid4())
    first, second = str(uuid4()), str(uuid4())
    plan = {2.0: Decimal("1"), 3.0: Decimal("1"), 4.0: Decimal("1")}
    asset_for = {2.0: first, 3.0: second, 4.0: first}

    config = pipeline_config(strategy_id)
    state = ExecutionPipeline.initialize(
        config, _running(strategy_id, priced, plan, asset_for), 1.0
    )
    for index, timestamp in enumerate((2.0, 3.0, 4.0)):
        state = ExecutionPipeline.process_quote(
            state, quote(priced, timestamp, Decimal("100") + Decimal(index)), context_factory
        ).state

    assert [entry.asset_id for entry in state.unpriced_assets.values()] == [first, second]
    assert state.unpriced_assets[first].occurrences == 2
    assert state.unpriced_assets[second].occurrences == 1


def test_a_priced_asset_keeps_its_record_after_it_becomes_tradeable() -> None:
    """The aggregate says what happened, not what is unpriced now.

    Pairs with ``test_a_later_quote_makes_the_previously_unpriced_asset_tradeable``:
    the asset does become tradeable, and the earlier drop stays on the record.
    """

    strategy_id = str(uuid4())
    late = str(uuid4())
    plan = {2.0: Decimal("1"), 3.0: Decimal("1")}
    asset_for = {2.0: late, 3.0: late}
    other = str(uuid4())

    config = pipeline_config(strategy_id)
    state = ExecutionPipeline.initialize(config, _running(strategy_id, other, plan, asset_for), 1.0)
    # First event prices only `other`, so the intent for `late` is dropped.
    state = ExecutionPipeline.process_quote(
        state, quote(other, 2.0, Decimal("100")), context_factory
    ).state
    assert state.unpriced_assets[late].occurrences == 1

    # Second event prices `late`, so the same intent now trades.
    result = ExecutionPipeline.process_quote(
        state, quote(late, 3.0, Decimal("100")), context_factory
    )

    assert result.fills, "the asset became tradeable"
    assert result.state.unpriced_assets[late].occurrences == 1, "the earlier drop is kept"


# --------------------------------------------------------------------------- #
# Boundedness
# --------------------------------------------------------------------------- #


def test_two_hundred_drops_of_one_asset_are_one_entry() -> None:
    never = str(uuid4())
    state, _, _, _ = _drive(200, target=never)

    assert len(state.unpriced_assets) == 1
    assert state.unpriced_assets[never].occurrences == 200


def test_the_aggregate_is_keyed_by_asset_not_by_event() -> None:
    """Size follows the distinct instruments named, never the event count."""

    never = str(uuid4())
    short, _, _, _ = _drive(3, target=never)
    long, _, _, _ = _drive(60, target=never)

    assert len(short.unpriced_assets) == len(long.unpriced_assets) == 1
    assert isinstance(long.unpriced_assets, PersistentMap)


# --------------------------------------------------------------------------- #
# Read-through
# --------------------------------------------------------------------------- #


def test_a_backtest_result_reports_what_its_run_declined() -> None:
    strategy_id, priced = str(uuid4()), str(uuid4())
    never = str(uuid4())
    plan = {2.0 + index: Decimal("1") for index in range(3)}
    asset_for = {2.0 + index: never for index in range(3)}
    mids = [Decimal("100"), Decimal("101"), Decimal("102")]
    dataset = dataset_of_quotes(priced, mids, "ds-unpriced")

    result = BacktestEngine.run(
        backtest_config(strategy_id),
        dataset,
        _running(strategy_id, priced, plan, asset_for),
        context_factory,
    )

    assert result.fills == ()
    assert [entry.asset_id for entry in result.unpriced_assets] == [never]
    assert result.unpriced_assets[0].occurrences == 3


def test_the_result_reads_through_rather_than_storing_a_second_copy() -> None:
    strategy_id, priced = str(uuid4()), str(uuid4())
    never = str(uuid4())
    plan = {2.0: Decimal("1")}
    asset_for = {2.0: never}
    dataset = dataset_of_quotes(priced, [Decimal("100")], "ds-read-through")

    result = BacktestEngine.run(
        backtest_config(strategy_id),
        dataset,
        _running(strategy_id, priced, plan, asset_for),
        context_factory,
    )

    assert result.unpriced_assets == tuple(result.state.unpriced_assets.values())
    assert "unpriced_assets" not in {f.name for f in result.__dataclass_fields__.values()}


def test_a_replay_agrees_with_the_run_it_wraps() -> None:
    strategy_id, priced = str(uuid4()), str(uuid4())
    never = str(uuid4())
    plan = {2.0: Decimal("1"), 3.0: Decimal("1")}
    asset_for = {2.0: never, 3.0: never}
    dataset = dataset_of_quotes(priced, [Decimal("100"), Decimal("101")], "ds-replay")

    replayed = ReplayBacktest.run(
        backtest_config(strategy_id),
        dataset,
        _running(strategy_id, priced, plan, asset_for),
        context_factory,
    )

    assert replayed.unpriced_assets == replayed.backtest.unpriced_assets
    assert replayed.unpriced_assets[0].occurrences == 2


def test_a_session_reports_what_it_declined() -> None:
    strategy_id, priced = str(uuid4()), str(uuid4())
    never = str(uuid4())
    plan = {2.0: Decimal("1"), 3.0: Decimal("1")}
    asset_for = {2.0: never, 3.0: never}
    dataset = dataset_of_quotes(priced, [Decimal("100"), Decimal("101")], "src-session")
    source = SequenceSource("src-session", dataset.records)

    state = TradingSession.run(
        RunConfig(pipeline=pipeline_config(strategy_id), mode=ExecutionMode.PAPER),
        source,
        _running(strategy_id, priced, plan, asset_for),
        context_factory,
    )

    assert [entry.asset_id for entry in state.unpriced_assets] == [never]
    assert state.unpriced_assets[0].occurrences == 2
    assert state.skipped.to_tuple() == (), "an unpriced asset is not a skipped record"


# --------------------------------------------------------------------------- #
# What did not change
# --------------------------------------------------------------------------- #


def test_the_per_event_result_still_carries_the_requests_it_always_did() -> None:
    never = str(uuid4())
    _, _, _, results = _drive(1, target=never)

    assert len(results[0].unpriced_requests) == 1
    assert results[0].unpriced_requests[0].asset_id == never


def test_the_backtest_step_gains_nothing() -> None:
    """Per-step data would duplicate the aggregate and grow the step log."""

    assert {field.name for field in RunStep.__dataclass_fields__.values()} == {
        "index",
        "event_id",
        "timestamp",
        "orders",
        "reports",
        "fills",
        "equity",
    }


def test_the_unpriced_asset_record_is_an_immutable_value() -> None:
    entry = UnpricedAsset("a", UnpricedReason.NO_PRICE_OBSERVED, "why", 1.0, 2.0, 3)

    assert entry == UnpricedAsset("a", UnpricedReason.NO_PRICE_OBSERVED, "why", 1.0, 2.0, 3)
    with pytest.raises(AttributeError):
        entry.occurrences = 4  # type: ignore[misc]
    assert not hasattr(entry, "__dict__"), "slots=True, like every value on this path"
    assert replace(entry, occurrences=4).occurrences == 4


def test_a_fresh_pipeline_starts_with_nothing_recorded() -> None:
    strategy_id, asset_id = str(uuid4()), str(uuid4())
    state = ExecutionPipeline.initialize(
        pipeline_config(strategy_id), _running(strategy_id, asset_id, {}), 1.0
    )

    assert dict(state.unpriced_assets) == {}


# --------------------------------------------------------------------------- #
# Not persisted
# --------------------------------------------------------------------------- #


def test_no_snapshot_carries_the_unpriced_aggregate() -> None:
    """``ExecutionPipelineState`` is not persisted, and this does not change that."""

    from alphalab.lifecycle.snapshot import LifecycleSnapshot
    from alphalab.oms.snapshot import OMSSnapshot
    from alphalab.portfolio.snapshot import PortfolioSnapshot

    for snapshot in (LifecycleSnapshot, OMSSnapshot, PortfolioSnapshot):
        names = {field.name for field in snapshot.__dataclass_fields__.values()}
        assert "unpriced_assets" not in names, f"{snapshot.__name__} must not carry it"


# --------------------------------------------------------------------------- #
# The optional registry reference, and the line it does not cross
# --------------------------------------------------------------------------- #


def test_no_registry_is_configured_by_default() -> None:
    assert pipeline_config(str(uuid4())).instruments is None


def test_without_a_registry_the_run_reports_only_what_it_saw() -> None:
    """No fabricated classification: it says it saw no price, and stops."""

    never = str(uuid4())
    state, _, _, _ = _drive(1, target=never)

    entry = state.unpriced_assets[never]
    assert entry.reason is UnpricedReason.NO_PRICE_OBSERVED
    assert "cannot say whether" in entry.detail


def _with_registry(registry: InstrumentRegistry, named: str):  # type: ignore[no-untyped-def]
    strategy_id, priced = str(uuid4()), str(uuid4())
    plan = {2.0: Decimal("1")}
    asset_for = {2.0: named}
    config = replace(pipeline_config(strategy_id), instruments=registry)
    state = ExecutionPipeline.initialize(
        config, _running(strategy_id, priced, plan, asset_for), 1.0
    )
    return ExecutionPipeline.process_quote(
        state, quote(priced, 2.0, Decimal("100")), context_factory
    ).state


def test_a_registry_separates_an_unknown_identifier_from_an_unpriced_instrument() -> None:
    """The two cases call for opposite fixes, so the run distinguishes them."""

    sap = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR")
    registry = register_instrument(InstrumentRegistry(), sap)
    unknown = str(uuid4())

    known_state = _with_registry(registry, sap.asset_id)
    unknown_state = _with_registry(registry, unknown)

    known = known_state.unpriced_assets[sap.asset_id]
    assert known.reason is UnpricedReason.REGISTERED_BUT_UNPRICED
    assert "SAP" in known.detail and "XETR" in known.detail and "EUR" in known.detail

    missing = unknown_state.unpriced_assets[unknown]
    assert missing.reason is UnpricedReason.NOT_REGISTERED
    assert "not a registered instrument" in missing.detail


def test_the_registry_never_changes_what_a_run_trades() -> None:
    """Identical fills with and without it: the reference is diagnostic only."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    plan = {2.0 + index: Decimal("1") for index in range(3)}
    registry = register_instrument(
        InstrumentRegistry(), InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD")
    )

    def run(instruments: InstrumentRegistry | None) -> tuple[tuple[str, ...], int]:
        config = replace(pipeline_config(strategy_id), instruments=instruments)
        state = ExecutionPipeline.initialize(config, _running(strategy_id, asset_id, plan), 1.0)
        for index in range(3):
            state = ExecutionPipeline.process_quote(
                state, quote(asset_id, 2.0 + index, Decimal("100")), context_factory
            ).state
        return tuple(fill.asset_id for fill in state.fills), len(state.fills)

    assert run(None) == run(registry)


def test_the_pipeline_only_ever_reads_the_registry() -> None:
    """ADR-0016 keeps resolution at the wire boundary; this must not take it back."""

    import inspect

    from alphalab.runtime import broker_routing, execution_pipeline, session

    source = "".join(
        inspect.getsource(module) for module in (execution_pipeline, session, broker_routing)
    )
    for forbidden in (
        ".resolve(",
        "derive_asset_id",
        "canonical_instrument_key",
        "register_instrument",
        "register_alias",
        ".by_provider",
    ):
        assert forbidden not in source, f"the pipeline must not call {forbidden}"

    assert "record_for" in source, "the one method it is allowed to call"


def test_the_registry_reference_reaches_no_snapshot() -> None:
    """``ExecutionPipelineConfig`` was never serializable and still is not."""

    from alphalab.lifecycle.snapshot import LifecycleSnapshot
    from alphalab.oms.snapshot import OMSSnapshot
    from alphalab.portfolio.snapshot import PortfolioSnapshot

    for snapshot in (LifecycleSnapshot, OMSSnapshot, PortfolioSnapshot):
        names = {field.name for field in snapshot.__dataclass_fields__.values()}
        assert "instruments" not in names
    assert not hasattr(ExecutionPipelineConfig, "__serializable__")
