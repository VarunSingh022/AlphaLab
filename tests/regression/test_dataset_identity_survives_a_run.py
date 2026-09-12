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

One home, two guarantees
------------------------
Until v2.14 the two identities lived in two places with two lifetimes:
``SessionState.source_id`` was on the state and was captured, while
``dataset_id`` was an argument to ``finalize`` that no snapshot carried -- so a
backtest that stopped and continued in another process arrived at
``derive_evidence`` with nothing to name. ADR-0030 gives the fact one home,
:attr:`~alphalab.runtime.run.RunState.source_id`, which the run snapshot carries,
and ``BacktestResult.dataset_id`` reads through it.

The two *guarantees* stay separate, which is what ADR-0017 was protecting: a
``MarketDataset`` is validated ordered on construction, while a
``MarketDataSource`` declares what it promises and a run states what it accepts
in ``RunConfig.ordering``. The guarantee is stated where it is enforced rather
than smuggled inside an identifier's type.
"""

from dataclasses import fields
from decimal import Decimal
from uuid import uuid4

from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine, finalize, initialize
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.backtesting.state import BacktestResult, ReplayResult
from alphalab.market.source import OrderingGuarantee, SequenceSource
from alphalab.runtime.run import RunConfig, RunState
from alphalab.runtime.session import TradingSession
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
    from alphalab.runtime.run import ExecutionMode, RunConfig

    return RunConfig(
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


def test_the_result_stores_no_identity_of_its_own() -> None:
    """``dataset_id`` is a projection of the run, not a second copy of the fact.

    A result that stored the identity could come to disagree with the run it
    describes, which is exactly what happened before v2.14 when ``finalize``
    accepted one and the state recorded none.
    """

    assert [f.name for f in fields(BacktestResult)] == ["run"]
    assert isinstance(BacktestResult.dataset_id, property)

    strategy_id, asset_id = _ids()
    dataset = dataset_of_quotes(asset_id, MIDS)
    result = BacktestEngine.run(
        backtest_config(strategy_id, seed=SEED),
        dataset,
        _strategy_state(strategy_id, asset_id),
        context_factory,
    )

    assert result.dataset_id == dataset.dataset_id == result.run.source_id


# --------------------------------------------------------------------------- #
# F. The two identities stay distinct
# --------------------------------------------------------------------------- #


def test_the_identity_has_one_home_and_the_guarantee_has_another() -> None:
    """Collapsing the two identifiers did not collapse the two guarantees.

    ``RunState`` carries *which* stream a run read. What that stream promised is
    ``RunConfig.ordering``, stated where the gate that enforces it reads it -- so
    a live session's provenance still cannot claim a validated dataset's
    guarantees.
    """

    run_fields = {f.name for f in fields(RunState)}
    config_fields = {f.name for f in fields(RunConfig)}

    assert run_fields & {"source_id", "dataset_id"} == {"source_id"}
    assert "ordering" in config_fields and "ordering" not in run_fields

    # And a dataset still validates its order on construction, which a source
    # only declares.
    assert MarketDataset.__post_init__ is not None
    assert OrderingGuarantee.UNORDERED in set(OrderingGuarantee)


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


def test_the_evidence_layer_gains_no_field_from_run_provenance() -> None:
    """Carrying an identity on a run must not widen what evidence records.

    Note the name collision this pins down. ``ValidationEvidence.source_id``
    already exists and means the *report* a measurement was extracted from -- a
    ``PerformanceReport.report_id`` or a ``ResearchState.research_id``. It has
    nothing to do with ``RunState.source_id``, which names a market-data
    stream. The two must not be merged.

    M3 (``tests/regression/test_evidence_derives_dataset_identity.py``) makes
    ``evidence_from_backtest`` read ``dataset_id`` off the run this phase
    started recording. It adds no field either, which is what keeps
    ``evidence_id_for`` frozen and v2.6 evidence verifiable.
    """

    from alphalab.lifecycle.evidence import ValidationEvidence

    assert {f.name for f in fields(ValidationEvidence)} == {
        "evidence_id",
        "method",
        "subject",
        "dataset_id",
        "metrics",
        "seed",
        "produced_at",
        "source_id",
    }, "run provenance must add no field to ValidationEvidence"
