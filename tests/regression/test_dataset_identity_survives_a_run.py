"""A finished run names the data it consumed.

``BacktestEngine.run`` received a ``MarketDataset`` carrying a validated,
non-empty ``dataset_id``, iterated its records, and threw the identity away:
``BacktestResult`` was ``(config, state, steps, records_processed, seed)``.
``TradingSession.run`` did the same with ``MarketDataSource.source_id``, reading
it only to compose an error message for an unordered source.

``lifecycle.evidence`` documents the consequence about itself -- "neither a
``BacktestResult`` nor a ``ResearchState`` carries the dataset it consumed, and
inventing one would be a guess" -- which is why ``ValidationEvidence.dataset_id``
is a caller's claim rather than a measurement. This is ADR-0017's M2: the run
carries the identity. Deriving evidence from it is M3 and is not implemented
here.

Two identities, not one
-----------------------
``dataset_id`` names a finite, ordered dataset that ``validate_dataset`` has
checked. ``source_id`` names a stream that may declare ``UNORDERED`` and may be
live. They are kept as separate fields on separate types on purpose: collapsing
them would let a live session's provenance claim the guarantees a validated
dataset carries.
"""

from dataclasses import fields
from decimal import Decimal
from uuid import uuid4

from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine, finalize, initialize
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.backtesting.state import BacktestResult, ReplayResult
from alphalab.market.source import SequenceSource
from alphalab.runtime.session import SessionState, TradingSession
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

MIDS = [Decimal("100.005"), Decimal("120.007"), Decimal("119.003")]
PLAN = {2.0: Decimal("10")}
SEED = 20260906


def _ids() -> tuple[str, str]:
    return str(uuid4()), str(uuid4())


def _strategy_state(strategy_id: str, asset_id: str) -> StrategyRuntimeState:
    return running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, PLAN))


# --------------------------------------------------------------------------- #
# A. A backtest names its dataset
# --------------------------------------------------------------------------- #


def test_a_backtest_result_names_the_dataset_it_consumed() -> None:
    strategy_id, asset_id = _ids()
    dataset = dataset_of_quotes(asset_id, MIDS)

    result = BacktestEngine.run(
        backtest_config(strategy_id),
        dataset,
        _strategy_state(strategy_id, asset_id),
        context_factory,
    )

    assert result.dataset_id == dataset.dataset_id
    assert result.dataset_id is not None


def test_two_runs_over_different_datasets_name_different_data() -> None:
    """The point of carrying it: the identity distinguishes one run's data from another's."""

    strategy_id, asset_id = _ids()
    first = dataset_of_quotes(asset_id, MIDS)
    second = MarketDataset.of(
        "OTHER-DATASET",
        [sized_quote(asset_id, 2.0 + index, mid, Decimal("100")) for index, mid in enumerate(MIDS)],
    )

    state = _strategy_state(strategy_id, asset_id)
    left = BacktestEngine.run(backtest_config(strategy_id), first, state, context_factory)
    right = BacktestEngine.run(
        backtest_config(strategy_id),
        second,
        _strategy_state(strategy_id, asset_id),
        context_factory,
    )

    assert left.dataset_id != right.dataset_id
    assert right.dataset_id == "OTHER-DATASET"


# --------------------------------------------------------------------------- #
# B. A replay names the same dataset
# --------------------------------------------------------------------------- #


def test_a_replay_result_names_the_dataset_it_replayed() -> None:
    strategy_id, asset_id = _ids()
    dataset = dataset_of_quotes(asset_id, MIDS)

    replay = ReplayBacktest.run(
        backtest_config(strategy_id, seed=SEED),
        dataset,
        _strategy_state(strategy_id, asset_id),
        context_factory,
    )

    assert replay.dataset_id == dataset.dataset_id
    assert replay.backtest.dataset_id == dataset.dataset_id


def test_the_replay_result_reads_its_identity_from_the_run_it_wraps() -> None:
    """One storage location, not two: a replay cannot disagree with its own backtest."""

    assert "dataset_id" not in {f.name for f in fields(ReplayResult)}
    assert isinstance(ReplayResult.dataset_id, property)


# --------------------------------------------------------------------------- #
# C. Parity is unaffected
# --------------------------------------------------------------------------- #


def test_backtest_and_replay_agree_on_the_dataset_they_consumed() -> None:
    strategy_id, asset_id = _ids()
    dataset = dataset_of_quotes(asset_id, MIDS)
    config = backtest_config(strategy_id, seed=SEED)

    backtest = BacktestEngine.run(
        config, dataset, _strategy_state(strategy_id, asset_id), context_factory
    )
    replay = ReplayBacktest.run(
        config, dataset, _strategy_state(strategy_id, asset_id), context_factory
    )

    assert backtest.dataset_id == replay.backtest.dataset_id
    # The run itself is unchanged by carrying an identity.
    assert backtest.fills == replay.backtest.fills
    assert backtest.equity_curve == replay.backtest.equity_curve
    assert backtest.records_processed == replay.backtest.records_processed


# --------------------------------------------------------------------------- #
# D. A session names its source
# --------------------------------------------------------------------------- #


def test_a_session_records_the_source_it_read() -> None:
    strategy_id, asset_id = _ids()
    dataset = dataset_of_quotes(asset_id, MIDS)
    source = SequenceSource("LIVE-FEED-1", dataset.records)

    state = TradingSession.run(
        _session_config(strategy_id),
        source,
        _strategy_state(strategy_id, asset_id),
        context_factory,
    )

    assert state.source_id == source.source_id == "LIVE-FEED-1"


def _session_config(strategy_id: str):  # type: ignore[no-untyped-def]
    from alphalab.runtime.session import ExecutionMode, SessionConfig

    return SessionConfig(
        pipeline=pipeline_config(strategy_id),
        mode=ExecutionMode.BACKTEST,
        start_timestamp=1.0,
    )


# --------------------------------------------------------------------------- #
# E. A hand-driven run invents nothing
# --------------------------------------------------------------------------- #


def test_a_hand_driven_run_records_no_dataset_rather_than_inventing_one() -> None:
    """``initialize``/``advance``/``finalize`` without a dataset has nothing to name.

    ``None`` is the honest answer, and it is what M3 will refuse to build
    evidence from. A default like ``""`` would read as a recorded identity.
    """

    strategy_id, asset_id = _ids()
    state = initialize(backtest_config(strategy_id), _strategy_state(strategy_id, asset_id))

    assert finalize(state).dataset_id is None


def test_a_session_driven_by_hand_records_no_source() -> None:
    strategy_id, asset_id = _ids()

    state = TradingSession.initialize(
        _session_config(strategy_id), _strategy_state(strategy_id, asset_id)
    )

    assert state.source_id is None


def test_a_result_constructed_directly_still_needs_no_dataset_id() -> None:
    """Existing direct constructors stay valid: the field is optional, not required."""

    names = [f.name for f in fields(BacktestResult)]
    dataset_field = next(f for f in fields(BacktestResult) if f.name == "dataset_id")

    assert names[-1] == "dataset_id", "appended, so positional construction is unchanged"
    assert dataset_field.default is None


# --------------------------------------------------------------------------- #
# F. The two identities stay distinct
# --------------------------------------------------------------------------- #


def test_dataset_identity_and_source_identity_are_separate_concepts() -> None:
    """A dataset is validated and finite; a source may be live and unordered."""

    result_fields = {f.name for f in fields(BacktestResult)}
    session_fields = {f.name for f in fields(SessionState)}

    assert "dataset_id" in result_fields and "source_id" not in result_fields
    assert "source_id" in session_fields and "dataset_id" not in session_fields


# --------------------------------------------------------------------------- #
# G. Neither field reaches persistence
# --------------------------------------------------------------------------- #


def test_neither_identity_enters_any_persisted_snapshot() -> None:
    """M2 is a runtime change. ADR-0017 moves no schema, and this holds it to that."""

    from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA, LifecycleSnapshot
    from alphalab.oms.snapshot import OMSSnapshot
    from alphalab.portfolio.snapshot import PORTFOLIO_SNAPSHOT_SCHEMA, PortfolioSnapshot

    for snapshot in (LifecycleSnapshot, PortfolioSnapshot, OMSSnapshot):
        names = {f.name for f in fields(snapshot)}
        assert "dataset_id" not in names, f"{snapshot.__name__} gained a dataset identity"
        assert "source_id" not in names, f"{snapshot.__name__} gained a source identity"

    assert LIFECYCLE_SNAPSHOT_SCHEMA == 1, "M2 must not move the lifecycle schema"
    assert PORTFOLIO_SNAPSHOT_SCHEMA == 2, "unchanged since v2.6"


def test_evidence_is_untouched_by_m2() -> None:
    """M3 derives evidence from the run. This phase does not, and must not look like it does.

    Note the name collision this pins down. ``ValidationEvidence.source_id``
    already exists and means the *report* a measurement was extracted from -- a
    ``PerformanceReport.report_id`` or a ``ResearchState.research_id``. It has
    nothing to do with ``SessionState.source_id``, which names a market-data
    stream. M2 adds the latter and must not disturb the former.
    """

    import inspect

    from alphalab.lifecycle.evidence import ValidationEvidence, evidence_from_backtest

    assert {f.name for f in fields(ValidationEvidence)} == {
        "evidence_id",
        "method",
        "subject",
        "dataset_id",
        "metrics",
        "seed",
        "produced_at",
        "source_id",
    }, "M2 must add no field to ValidationEvidence"
    assert "dataset_id" in inspect.signature(evidence_from_backtest).parameters, (
        "M3 removes this parameter; M2 must leave it alone"
    )
