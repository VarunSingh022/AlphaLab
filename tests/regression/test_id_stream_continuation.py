"""A stopped run continues its identifier stream instead of restarting it.

A seed says where a stream starts. Until v2.9 nothing said where it had
*reached*, and that omission was a correctness defect. A run that stopped and
continued had to re-enter :func:`~alphalab.common.ids.id_scope`, which builds a
fresh :class:`~alphalab.common.ids.DeterministicIdSource` positioned at zero, so
the continued run drew identifiers it had already used. Measured on v2.8.0 a
workload producing 41 identifiers produced up to **4 duplicates** when split and
resumed, against **zero** for the uninterrupted control -- a later ``fill_id``
landing on a UUID an earlier ``execution_id`` already held. Nothing raised.

:class:`~alphalab.common.ids.IdStreamPosition` is the missing fact and it is two
integers. The source counts what it mints, ``current_id_position`` reads that,
``ExecutionPipelineState.id_position`` stores it at each step boundary, and
``id_source_for`` rebuilds a source that has reached it. No ``new_id()`` call site
changes, the ``ContextVar`` stays exactly as it was, and instrument identity --
``uuid5`` over a canonical key, with no shared stream -- is untouched.

This step establishes the cursor. Persisting it is the snapshot work that
follows, so the tests here simulate the future restore contract directly with
``IdStreamPosition`` and ``id_source_for``. See ADR-0022.
"""

import uuid
from dataclasses import replace as dc_replace
from decimal import Decimal
from random import Random

import pytest

from alphalab.common.ids import (
    DeterministicIdSource,
    IdStreamPosition,
    current_id_position,
    id_scope,
    id_source,
    id_source_for,
    new_id,
    use_id_source,
)
from alphalab.core.enums import AssetType
from alphalab.instrument.identity import ALPHALAB_INSTRUMENT_NAMESPACE, derive_asset_id
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineState
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

SEED = 4242
STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())


def _legacy_stream(seed: int, count: int) -> list[str]:
    """The identifier sequence a pre-Step-5 source produced for ``seed``.

    Written out longhand rather than imported, so this is a fixed expectation
    that a change to the source cannot quietly move with it.
    """

    random = Random(seed)
    return [str(uuid.UUID(int=random.getrandbits(128), version=4)) for _ in range(count)]


# ---------------------------------------------------------------------------
# The source counts what it mints
# ---------------------------------------------------------------------------


def test_a_fresh_source_has_drawn_nothing() -> None:
    source = DeterministicIdSource(SEED)

    assert source.draws == 0
    assert source.position == IdStreamPosition(SEED, 0)


def test_one_identifier_is_one_draw() -> None:
    source = DeterministicIdSource(SEED)
    source()

    assert source.draws == 1


@pytest.mark.parametrize("count", [1, 2, 7, 100, 1_000])
def test_n_identifiers_are_n_draws(count: int) -> None:
    source = DeterministicIdSource(SEED)
    minted = [source() for _ in range(count)]

    assert source.draws == count
    assert len(set(minted)) == count


def test_the_identifier_sequence_is_unchanged_by_the_counter() -> None:
    """The counter must be free of observable effect on what is minted."""

    source = DeterministicIdSource(SEED)

    assert [source() for _ in range(20)] == _legacy_stream(SEED, 20)


def test_the_seed_is_still_reported() -> None:
    assert DeterministicIdSource(SEED).seed == SEED


# ---------------------------------------------------------------------------
# Reading the ambient position
# ---------------------------------------------------------------------------


def test_no_source_installed_reports_an_unseeded_position() -> None:
    assert current_id_position() == IdStreamPosition(None, 0)


def test_a_seeded_scope_reports_its_position() -> None:
    with id_scope(SEED):
        assert current_id_position() == IdStreamPosition(SEED, 0)
        new_id()
        new_id()
        assert current_id_position() == IdStreamPosition(SEED, 2)


def test_an_unseeded_scope_claims_no_deterministic_position() -> None:
    with id_scope(None):
        first = new_id()
        second = new_id()
        position = current_id_position()

    assert position.seed is None
    assert position.draws == 0
    assert first != second
    assert uuid.UUID(first).version == 4


def test_a_callers_own_callable_is_not_given_an_invented_position() -> None:
    """This module cannot know a foreign source's cursor and does not guess."""

    with use_id_source(lambda: str(uuid.uuid4())):
        new_id()

        assert current_id_position() == IdStreamPosition(None, 0)


def test_the_position_leaves_the_scope_behind() -> None:
    with id_scope(SEED):
        new_id()

    assert current_id_position() == IdStreamPosition(None, 0)


# ---------------------------------------------------------------------------
# Rebuilding a source at a position
# ---------------------------------------------------------------------------


def test_a_zero_position_reproduces_the_stream_from_the_start() -> None:
    resumed = id_source_for(IdStreamPosition(SEED, 0))

    assert resumed is not None
    assert [resumed() for _ in range(10)] == _legacy_stream(SEED, 10)


@pytest.mark.parametrize("prior", [1, 2, 17, 500])
def test_a_position_reproduces_the_stream_after_that_many_draws(prior: int) -> None:
    whole = _legacy_stream(SEED, prior + 10)
    resumed = id_source_for(IdStreamPosition(SEED, prior))

    assert resumed is not None
    assert [resumed() for _ in range(10)] == whole[prior:]


def test_a_rebuilt_source_reports_the_position_it_was_built_for() -> None:
    resumed = id_source_for(IdStreamPosition(SEED, 25))

    assert isinstance(resumed, DeterministicIdSource)
    assert resumed.position == IdStreamPosition(SEED, 25)


def test_an_unseeded_position_rebuilds_to_the_uuid4_default() -> None:
    """``None`` matches ``id_source``, so either can be handed to ``use_id_source``."""

    assert id_source_for(IdStreamPosition(None, 0)) is None
    assert id_source(None) is None


def test_a_large_position_remains_practical() -> None:
    """Advancing replays the generator's own sequence; ~1 us per draw."""

    resumed = id_source_for(IdStreamPosition(SEED, 100_000))

    assert isinstance(resumed, DeterministicIdSource)
    assert resumed.draws == 100_000
    assert resumed() == _legacy_stream(SEED, 100_001)[-1]


# ---------------------------------------------------------------------------
# The split-continuation contract, which is the defect
# ---------------------------------------------------------------------------


def test_a_split_continuation_equals_the_uninterrupted_one() -> None:
    """seed S, K then M, versus S for K+M."""

    prior, following = 17, 23
    uninterrupted = _legacy_stream(SEED, prior + following)

    produced = DeterministicIdSource(SEED)
    head = [produced() for _ in range(prior)]
    resumed = id_source_for(produced.position)
    assert resumed is not None
    continuation = [resumed() for _ in range(following)]

    assert head == uninterrupted[:prior]
    assert continuation == uninterrupted[prior:]
    assert len(set(head + continuation)) == prior + following


def test_every_split_point_reproduces_the_same_stream_with_no_duplicates() -> None:
    """The sweep the archaeology ran, now expected to find nothing."""

    total = 60
    uninterrupted = _legacy_stream(SEED, total)

    for boundary in range(1, total):
        produced = DeterministicIdSource(SEED)
        minted = [produced() for _ in range(boundary)]
        resumed = id_source_for(produced.position)
        assert resumed is not None
        minted += [resumed() for _ in range(total - boundary)]

        assert minted == uninterrupted, f"split at {boundary} diverged"
        assert len(set(minted)) == total, f"split at {boundary} produced a duplicate"


def test_restarting_from_the_seed_alone_is_what_used_to_collide() -> None:
    """The control for the fix: seeding afresh replays identifiers already used."""

    produced = DeterministicIdSource(SEED)
    minted = [produced() for _ in range(10)]

    restarted = DeterministicIdSource(SEED)
    replayed = [restarted() for _ in range(10)]

    assert replayed == minted, "a fresh source replays the stream from zero"

    resumed = id_source_for(produced.position)
    assert resumed is not None
    continued = [resumed() for _ in range(10)]

    assert not set(continued) & set(minted)


# ---------------------------------------------------------------------------
# The pipeline state owns the position
# ---------------------------------------------------------------------------


def _run(events: int) -> ExecutionPipelineState:
    state = ExecutionPipeline.initialize(
        pipeline_config(STRATEGY_ID),
        running_strategy_state(
            STRATEGY_ID,
            ScriptedStrategy(
                STRATEGY_ID, ASSET_ID, {2.0 + index: Decimal("5") for index in range(events)}
            ),
        ),
        1.0,
    )
    for index in range(events):
        state = ExecutionPipeline.process_quote(
            state,
            sized_quote(ASSET_ID, 2.0 + index, Decimal("100"), Decimal("100")),
            context_factory,
        ).state
    return state


def test_a_default_state_carries_an_unseeded_position() -> None:
    field = ExecutionPipelineState.__dataclass_fields__["id_position"]

    assert field.default_factory() == IdStreamPosition(None, 0)  # type: ignore[misc]


def test_initialize_records_the_draws_funding_took() -> None:
    with id_scope(SEED):
        state = ExecutionPipeline.initialize(
            pipeline_config(STRATEGY_ID),
            running_strategy_state(STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, {})),
            1.0,
        )

        assert state.id_position.seed == SEED
        assert state.id_position.draws > 0
        assert state.id_position == current_id_position()


def test_each_step_refreshes_the_position() -> None:
    with id_scope(SEED):
        state = ExecutionPipeline.initialize(
            pipeline_config(STRATEGY_ID),
            running_strategy_state(
                STRATEGY_ID,
                ScriptedStrategy(
                    STRATEGY_ID, ASSET_ID, {2.0 + index: Decimal("5") for index in range(3)}
                ),
            ),
            1.0,
        )
        seen = [state.id_position.draws]
        for index in range(3):
            state = ExecutionPipeline.process_quote(
                state,
                sized_quote(ASSET_ID, 2.0 + index, Decimal("100"), Decimal("100")),
                context_factory,
            ).state
            seen.append(state.id_position.draws)
            assert state.id_position == current_id_position()

    assert seen == sorted(seen)
    assert len(set(seen)) == len(seen), "every step drew at least one identifier"


def test_compile_analytics_refreshes_the_position() -> None:
    with id_scope(SEED):
        state = _run(2)
        before = state.id_position

        compiled = ExecutionPipeline.compile_analytics(state, 9.0)

        assert compiled.id_position.draws > before.draws
        assert compiled.id_position == current_id_position()


def test_the_position_is_readable_without_any_ambient_source() -> None:
    """The whole point: a state describes its own stream, so capture can be pure."""

    with id_scope(SEED):
        state = _run(2)

    assert current_id_position() == IdStreamPosition(None, 0)
    assert state.id_position.seed == SEED
    assert state.id_position.draws > 0


def test_replace_preserves_the_position() -> None:
    with id_scope(SEED):
        state = _run(1)

    assert dc_replace(state, market_prices={}).id_position == state.id_position


def test_the_position_participates_in_state_equality() -> None:
    with id_scope(SEED):
        state = _run(1)

    assert dc_replace(state, id_position=state.id_position) == state
    assert dc_replace(state, id_position=IdStreamPosition(SEED, 999_999)) != state


def test_an_unseeded_run_records_an_unseeded_position() -> None:
    state = _run(2)

    assert state.id_position == IdStreamPosition(None, 0)
    assert len(state.fills) == 2


def test_the_recorded_position_matches_the_identifiers_the_run_minted() -> None:
    """A cross-check the representation makes possible: draws bounds the ids."""

    with id_scope(SEED):
        state = _run(3)

    minted = (
        {str(order.order_id.value) for order in state.oms.orders.orders()}
        | {str(fill.fill_id) for fill in state.fills}
        | {str(trade.trade_id) for trade in state.trades}
        | {str(report.execution_id) for report in state.execution.history}
        | {str(txn.transaction_id) for txn in state.portfolio.ledger.transactions}
    )

    assert len(minted) <= state.id_position.draws
    assert len(minted) == len(
        [str(order.order_id.value) for order in state.oms.orders.orders()]
    ) + len(state.fills) + len(state.trades) + len(state.execution.history) + len(
        state.portfolio.ledger.transactions
    ), "the run's identifiers are all distinct"


# ---------------------------------------------------------------------------
# A full run, split and resumed, produces no duplicate identifier
# ---------------------------------------------------------------------------


def _identifiers(state: ExecutionPipelineState) -> list[str]:
    return [
        *(str(order.order_id.value) for order in state.oms.orders.orders()),
        *(str(fill.fill_id) for fill in state.fills),
        *(str(trade.trade_id) for trade in state.trades),
        *(str(report.execution_id) for report in state.execution.history),
        *(str(txn.transaction_id) for txn in state.portfolio.ledger.transactions),
    ]


def _drive(state: ExecutionPipelineState, indices: range) -> ExecutionPipelineState:
    for index in indices:
        state = ExecutionPipeline.process_quote(
            state,
            sized_quote(ASSET_ID, 2.0 + index, Decimal("100"), Decimal("100")),
            context_factory,
        ).state
    return state


@pytest.mark.parametrize("boundary", [1, 2, 4, 6, 7])
def test_a_run_split_at_any_boundary_reproduces_the_uninterrupted_identifiers(
    boundary: int,
) -> None:
    """The v2.8.0 defect, end to end: split, resume from the position, compare.

    The restore contract is simulated with ``IdStreamPosition`` and
    ``id_source_for`` because persisting it is later work. What is proved here is
    that the cursor is sufficient: nothing else has to be carried for the
    continuation to match.
    """

    events = 8
    plan = {2.0 + index: Decimal("5") for index in range(events)}

    def fresh() -> ExecutionPipelineState:
        return ExecutionPipeline.initialize(
            pipeline_config(STRATEGY_ID),
            running_strategy_state(STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, plan)),
            1.0,
        )

    with id_scope(SEED):
        control = _drive(fresh(), range(events))
    expected = _identifiers(control)

    with id_scope(SEED):
        partial = _drive(fresh(), range(boundary))
    captured = partial.id_position

    with use_id_source(id_source_for(captured)):
        resumed = _drive(partial, range(boundary, events))
    actual = _identifiers(resumed)

    assert actual == expected, f"split at {boundary} diverged from the control"
    assert len(set(actual)) == len(actual), f"split at {boundary} produced a duplicate"
    assert resumed.id_position == control.id_position


def test_the_restored_continuation_reuses_no_earlier_identifier() -> None:
    """Duplicates were the harm; this asserts their absence directly."""

    events, boundary = 8, 3
    plan = {2.0 + index: Decimal("5") for index in range(events)}

    with id_scope(SEED):
        partial = _drive(
            ExecutionPipeline.initialize(
                pipeline_config(STRATEGY_ID),
                running_strategy_state(STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, plan)),
                1.0,
            ),
            range(boundary),
        )
    before = set(_identifiers(partial))

    with use_id_source(id_source_for(partial.id_position)):
        resumed = _drive(partial, range(boundary, events))

    after = [identifier for identifier in _identifiers(resumed) if identifier not in before]

    assert after
    assert not set(after) & before
    assert len(set(_identifiers(resumed))) == len(_identifiers(resumed))


# ---------------------------------------------------------------------------
# Instrument identity is a different concern and stays one
# ---------------------------------------------------------------------------


def test_instrument_identity_does_not_draw_from_the_stream() -> None:
    with id_scope(SEED):
        before = current_id_position()
        derive_asset_id(AssetType.EQUITY, "XNAS", "AAPL", "USD")
        after = current_id_position()

    assert before == after == IdStreamPosition(SEED, 0)


def test_instrument_identity_is_the_same_inside_and_outside_a_seeded_scope() -> None:
    outside = derive_asset_id(AssetType.EQUITY, "XNAS", "AAPL", "USD")
    with id_scope(SEED):
        new_id()
        inside = derive_asset_id(AssetType.EQUITY, "XNAS", "AAPL", "USD")

    assert inside == outside
    assert uuid.UUID(outside).version == 5


def test_the_instrument_namespace_is_untouched() -> None:
    assert uuid.UUID("1935bdfa-e8c0-5611-ae10-607c3a67c19b") == ALPHALAB_INSTRUMENT_NAMESPACE


# ---------------------------------------------------------------------------
# One owner, and no plumbing
# ---------------------------------------------------------------------------


def test_new_id_takes_no_source_argument() -> None:
    """No call site changed, because there was nothing to pass."""

    import inspect

    assert list(inspect.signature(new_id).parameters) == []


def test_no_second_owner_of_the_stream_position() -> None:
    """The counter lives on the source; only the pipeline state stores it."""

    import dataclasses

    from alphalab.runtime.run import RunState

    for state in (RunState, RunState):
        names = {field.name for field in dataclasses.fields(state)}
        assert "id_position" not in names, f"{state.__name__} must not hold a second cursor"

    pipeline = {field.name for field in dataclasses.fields(ExecutionPipelineState)}
    assert "id_position" in pipeline
    assert not [name for name in pipeline if "draw" in name or "counter" in name]


def test_the_generator_state_is_not_the_persistence_contract() -> None:
    """Two integers, not a dump of generator words."""

    import dataclasses

    names = {field.name for field in dataclasses.fields(IdStreamPosition)}

    assert names == {"seed", "draws"}
    assert not hasattr(IdStreamPosition, "getstate")
