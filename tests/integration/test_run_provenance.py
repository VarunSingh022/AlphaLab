"""A run is held to the schedule it was given, and provenance says how far (ADR-0053, M1).

A backtest's schedule is a driver input its record does not name (ADR-0051),
so until v4.3 a run continued from a checkpoint chain could be continued under
another schedule with nothing to refuse it, and a manifest could name less than
the run was given (ledger FUT-046). These tests hold the four answers ADR-0053
gives: a schedule bound by its digest's scheme and value; a continuation held
to the binding -- by its checkpoint chain's envelope, or by the cursor given
the binding kept beside it; provenance re-derived at every check; and a
vocabulary that never claims more than was checked.

The run is the two-currency backtest of ``test_scheduled_inputs.py``, whose
schedule funds and converts the book at the rate in force, so another schedule
makes another run.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.backtesting import (
    RUN_PROVENANCE_SCHEME,
    RUN_SCHEDULE_DIGEST_SCHEME,
    BacktestResult,
    Funding,
    ProvenanceError,
    RunProvenance,
    RunSchedule,
    ScheduleCursor,
    ScheduleValidationError,
    payload_provenance,
    provenance_from_primitives,
    run_provenance,
    verify_provenance,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.common.ids import current_id_position, id_scope
from alphalab.lifecycle import digest_run
from alphalab.market.record import MarketRecord
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.portfolio.fx import FxRate
from alphalab.runtime.checkpoint import (
    CHECKPOINT_SCHEMA,
    chain_schedule_verification,
    checkpoint,
    checkpoint_schedule,
    restore_checkpoints,
)
from alphalab.runtime.run import RunEngine
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.schedule_check import (
    ScheduleBinding,
    ScheduleVerification,
    verify_schedule_binding,
)
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration.harness import context_factory
from tests.integration.test_scheduled_inputs import (
    EUR_CASH,
    EUR_PLAN,
    EUR_SCHEDULE,
    EURUSD,
    SAP,
    SAP_MIDS,
    STRATEGY,
    Planner,
    _config,
    _eur_run,
    _quotes,
    _strategies,
)

#: The same funding at a rate the run never saw: a schedule that makes another run.
OTHER = RunSchedule.of(
    rates=(FxRate("EUR", "USD", Decimal("1.05"), 1.0, "ECB"), EURUSD[1]),
    fundings=(Funding(EUR_CASH, "EUR", 1.5),),
)


def _records() -> tuple[MarketRecord, ...]:
    return tuple(MarketDataset.of("EUR", _quotes(SAP.asset_id, SAP_MIDS)).records)


def _objects() -> RunObjects:
    """The live objects the run held, supplied back: a fresh strategy, which holds no state."""

    config = _config(SAP, two_currencies=True)
    pipeline = config.pipeline
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=pipeline.sizing_model,
            simulator=pipeline.simulator,
            strategies={STRATEGY: Planner(dict(EUR_PLAN))},
            instruments=pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )


def _checkpointed(stop: int, *, bound: bool = True) -> tuple[str, BacktestResult]:
    """The run driven by hand to ``stop`` records and checkpointed there; and run uninterrupted."""

    config = _config(SAP, two_currencies=True)
    assert config.seed is not None
    cursor = ScheduleCursor.start(EUR_SCHEDULE)
    # Built outside the scope, as the driver's caller builds it: it records lifecycle
    # events, and what they mint is not the run's.
    initial = _strategies(Planner(dict(EUR_PLAN)))
    with id_scope(config.seed):
        state = replace(RunEngine.initialize(config, initial), source_id="EUR")
        for record in _records()[:stop]:
            cursor, state = cursor.before(state, record.timestamp, context_factory)
            state = RunEngine.close_slice(
                state, context_factory, cursor.rates, before=record.timestamp
            )
            state, _ = RunEngine.advance(state, record, context_factory, rates=cursor.rates)
        payload, _ = checkpoint(state, schedule=cursor.binding if bound else None)
    return payload, _eur_run(EUR_SCHEDULE)


def _continued(payload: str, schedule: RunSchedule, objects: RunObjects) -> BacktestResult:
    """The chain restored with ``schedule``'s binding and driven to the end of the data."""

    state = restore_checkpoints([payload], objects, schedule_binding=schedule.binding)
    records = _records()
    cursor = ScheduleCursor.start(
        schedule, state.last_record_timestamp, expected=checkpoint_schedule([payload])
    )
    with RunEngine.resume(state):
        for record in records:
            if record.timestamp <= (state.last_record_timestamp or float("-inf")):
                continue
            cursor, state = cursor.before(state, record.timestamp, context_factory)
            state = RunEngine.close_slice(
                state, context_factory, cursor.rates, before=record.timestamp
            )
            state, _ = RunEngine.advance(state, record, context_factory, rates=cursor.rates)
        cursor, state = cursor.finish(state, context_factory, records[-1].timestamp)
        state = RunEngine.close_slice(state, context_factory, cursor.rates)
        return BacktestResult(run=RunEngine.finalize(state))


# --------------------------------------------------------------------------- #
# The binding
# --------------------------------------------------------------------------- #


def test_the_digest_scheme_is_named_and_never_rendered_into_the_digest() -> None:
    assert RUN_SCHEDULE_DIGEST_SCHEME == "alphalab.run_schedule.v1"
    assert RUN_SCHEDULE_DIGEST_SCHEME not in EUR_SCHEDULE.digest
    assert EUR_SCHEDULE.binding == ScheduleBinding(RUN_SCHEDULE_DIGEST_SCHEME, EUR_SCHEDULE.digest)


def test_an_empty_schedule_binds_as_none_as_a_manifest_records_it() -> None:
    assert RunSchedule().binding == ScheduleBinding(RUN_SCHEDULE_DIGEST_SCHEME, None)
    assert ScheduleCursor.start(RunSchedule()).binding.digest is None


@pytest.mark.parametrize("digest", ["", "A" * 64, "0" * 63, "g" * 64])
def test_a_binding_holds_only_a_sha256_digest(digest: str) -> None:
    with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
        ScheduleBinding(RUN_SCHEDULE_DIGEST_SCHEME, digest)


def test_the_vocabulary_never_claims_more_than_was_checked() -> None:
    bound, none = EUR_SCHEDULE.binding, RunSchedule().binding

    assert verify_schedule_binding(None, bound) is ScheduleVerification.UNRECORDED
    assert verify_schedule_binding(bound, bound) is ScheduleVerification.DECLARED
    assert verify_schedule_binding(none, none) is ScheduleVerification.NONE_GIVEN
    for recorded, supplied in ((bound, OTHER.binding), (bound, none), (none, bound)):
        with pytest.raises(StateDecodeError):
            verify_schedule_binding(recorded, supplied)
    with pytest.raises(StateDecodeError, match="cannot be compared"):
        verify_schedule_binding(bound, replace(bound, scheme="alphalab.run_schedule.v2"))


def test_a_cursor_given_a_binding_refuses_another_schedule_before_applying_anything() -> None:
    assert ScheduleCursor.start(EUR_SCHEDULE, expected=EUR_SCHEDULE.binding).position == 0
    for supplied in (OTHER, RunSchedule()):
        with pytest.raises(ScheduleValidationError):
            ScheduleCursor.start(supplied, 3.0, expected=EUR_SCHEDULE.binding)
    with pytest.raises(ScheduleValidationError):
        ScheduleCursor.start(EUR_SCHEDULE, expected=RunSchedule().binding)


# --------------------------------------------------------------------------- #
# A checkpoint chain binds in its envelope (schema 3)
# --------------------------------------------------------------------------- #


def test_a_checkpoint_given_no_binding_is_version_2_byte_for_byte() -> None:
    unbound, _ = _checkpointed(3, bound=False)
    bound, _ = _checkpointed(3)

    assert deserialize(unbound)["schema_version"] == 2 and "schedule" not in deserialize(unbound)
    written = deserialize(bound)
    assert written["schema_version"] == CHECKPOINT_SCHEMA == 3
    assert written["schedule"] == {
        "scheme": RUN_SCHEDULE_DIGEST_SCHEME,
        "digest": EUR_SCHEDULE.digest,
    }
    assert {k: v for k, v in written.items() if k not in {"schema_version", "schedule"}} == {
        k: v for k, v in deserialize(unbound).items() if k != "schema_version"
    }


@pytest.mark.parametrize("stop", [1, 3, 4])
def test_a_chain_continued_under_its_schedule_is_the_uninterrupted_run(stop: int) -> None:
    payload, uninterrupted = _checkpointed(stop)

    continued = _continued(payload, EUR_SCHEDULE, _objects())

    assert digest_run(continued).result_id == digest_run(uninterrupted).result_id
    assert (
        chain_schedule_verification([payload], EUR_SCHEDULE.binding)
        is ScheduleVerification.DECLARED
    )


def test_a_chain_is_refused_under_another_schedule_or_none_before_anything_is_restored() -> None:
    payload, _ = _checkpointed(3)
    objects = _objects()

    for supplied in (OTHER.binding, RunSchedule().binding):
        with pytest.raises(StateDecodeError, match="schedule"):
            restore_checkpoints([payload], objects, schedule_binding=supplied)
    with pytest.raises(StateDecodeError, match="no schedule binding was supplied"):
        restore_checkpoints([payload], objects)


def test_a_chain_written_without_a_binding_restores_as_before_and_says_so() -> None:
    payload, _ = _checkpointed(3, bound=False)

    assert checkpoint_schedule([payload]) is None
    assert chain_schedule_verification([payload], OTHER.binding) is ScheduleVerification.UNRECORDED
    restore_checkpoints([payload], _objects(), schedule_binding=OTHER.binding)


def test_a_chain_binding_two_schedules_is_refused() -> None:
    base, _ = _checkpointed(3)
    state = restore_checkpoints([base], _objects(), schedule_binding=EUR_SCHEDULE.binding)
    first = checkpoint(state, schedule=EUR_SCHEDULE.binding)[1]
    segment, _ = checkpoint(state, first, schedule=OTHER.binding)
    base_again, _ = checkpoint(state, schedule=EUR_SCHEDULE.binding)

    with pytest.raises(StateDecodeError, match="one run was given one schedule"):
        checkpoint_schedule([base_again, segment])


# --------------------------------------------------------------------------- #
# Provenance: issued by a plain function, re-derived at every check
# --------------------------------------------------------------------------- #


def test_a_results_provenance_is_bound_to_its_result_id_and_draws_nothing() -> None:
    result = _eur_run(EUR_SCHEDULE)
    with id_scope(5):
        before = current_id_position()
        provenance = run_provenance(result, schedule=EUR_SCHEDULE, produced_at=7.5)
        assert current_id_position() == before

    assert provenance.subject_digest == digest_run(result).result_id
    assert provenance.schedule == EUR_SCHEDULE.binding
    assert provenance.instruments_digest is not None
    assert provenance == run_provenance(
        result, schedule=EUR_SCHEDULE, result_id=digest_run(result).result_id
    ), "produced_at is recorded and never part of the identity"


def test_the_canonical_key_is_pinned() -> None:
    provenance = RunProvenance(
        "result", "a" * 64, ScheduleBinding(RUN_SCHEDULE_DIGEST_SCHEME, "b" * 64), "c" * 64
    )

    assert provenance.canonical_key == "\n".join(
        [
            RUN_PROVENANCE_SCHEME,
            "subject.kind=result",
            f"subject.digest={'a' * 64}",
            "schedule.scheme=alphalab.run_schedule.v1",
            f"schedule.digest={'b' * 64}",
            f"instruments={'c' * 64}",
        ]
    )
    assert (
        provenance.provenance_id
        == hashlib.sha256(provenance.canonical_key.encode("utf-8")).hexdigest()
    )


def test_a_result_with_provenance_and_its_schedule_is_verified() -> None:
    result = _eur_run(EUR_SCHEDULE)
    provenance = run_provenance(result, schedule=EUR_SCHEDULE)

    assert verify_provenance(result, provenance, EUR_SCHEDULE) is ScheduleVerification.VERIFIED
    assert verify_provenance(result, None, EUR_SCHEDULE) is ScheduleVerification.UNRECORDED


def test_provenance_refuses_another_schedule_another_run_and_an_edit() -> None:
    result, other = _eur_run(EUR_SCHEDULE), _eur_run(OTHER)
    provenance = run_provenance(result, schedule=EUR_SCHEDULE)

    with pytest.raises(ProvenanceError, match="not the schedule"):
        verify_provenance(result, provenance, OTHER)
    with pytest.raises(ProvenanceError, match="not the schedule"):
        verify_provenance(result, provenance, None)
    with pytest.raises(ProvenanceError, match="another subject"):
        verify_provenance(other, provenance, EUR_SCHEDULE)
    edited = replace(provenance, instruments_digest="0" * 64)
    assert edited.provenance_id != provenance.provenance_id
    with pytest.raises(ProvenanceError, match="other terms"):
        verify_provenance(result, edited, EUR_SCHEDULE)


def test_a_payloads_provenance_is_its_own_text_and_declared_only() -> None:
    payload, uninterrupted = _checkpointed(3, bound=False)
    provenance = payload_provenance(payload, schedule=EUR_SCHEDULE)

    assert provenance.subject_digest == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert verify_provenance(payload, provenance, EUR_SCHEDULE) is ScheduleVerification.DECLARED
    with pytest.raises(ProvenanceError, match="another subject"):
        verify_provenance(payload + " ", provenance, EUR_SCHEDULE)
    continued = _continued_with_provenance(payload, provenance, _objects())
    assert digest_run(continued).result_id == digest_run(uninterrupted).result_id
    with pytest.raises(ScheduleValidationError):
        ScheduleCursor.start(OTHER, 3.0, expected=provenance.schedule)


def _continued_with_provenance(
    payload: str, provenance: RunProvenance, objects: RunObjects
) -> BacktestResult:
    state = restore_checkpoints([payload], objects)
    records = _records()
    cursor = ScheduleCursor.start(
        EUR_SCHEDULE, state.last_record_timestamp, expected=provenance.schedule
    )
    with RunEngine.resume(state):
        for record in records:
            if record.timestamp <= (state.last_record_timestamp or float("-inf")):
                continue
            cursor, state = cursor.before(state, record.timestamp, context_factory)
            state = RunEngine.close_slice(
                state, context_factory, cursor.rates, before=record.timestamp
            )
            state, _ = RunEngine.advance(state, record, context_factory, rates=cursor.rates)
        cursor, state = cursor.finish(state, context_factory, records[-1].timestamp)
        state = RunEngine.close_slice(state, context_factory, cursor.rates)
        return BacktestResult(run=RunEngine.finalize(state))


def test_a_run_given_no_schedule_is_none_given_and_refuses_one() -> None:
    result = _eur_run(EUR_SCHEDULE)
    snapshot = serialize(capture_run(result.run))
    provenance = payload_provenance(snapshot, schedule=None)

    assert provenance.schedule.digest is None
    assert verify_provenance(snapshot, provenance, None) is ScheduleVerification.NONE_GIVEN
    with pytest.raises(ProvenanceError):
        verify_provenance(snapshot, provenance, EUR_SCHEDULE)


def test_provenance_round_trips_and_is_filed_as_evidence() -> None:
    provenance = run_provenance(_eur_run(EUR_SCHEDULE), schedule=EUR_SCHEDULE, produced_at=1.0)
    store = EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex())

    reference = store.put(provenance)
    assert store.put(provenance) == reference, "filing the same record twice changes nothing"
    assert provenance_from_primitives(deserialize(serialize(provenance))) == provenance
    assert reference.kind == "run_provenance" and reference.identity == provenance.provenance_id


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema_version", 2), ("subject_kind", "run"), ("subject_digest", "x")],
)
def test_a_stored_record_that_is_not_one_is_refused(field: str, value: object) -> None:
    stored = deserialize(serialize(run_provenance(_eur_run(EUR_SCHEDULE), schedule=EUR_SCHEDULE)))

    with pytest.raises(StateDecodeError):
        provenance_from_primitives({**stored, field: value})
