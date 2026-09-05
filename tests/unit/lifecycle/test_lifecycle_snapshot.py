"""Typed capture / restore for LifecycleState.

v2.4 gave the lifecycle an audit trail that serialized deterministically and
could not be read back. These tests hold the other half: the same
``restore(capture(state)) == state`` contract ADR-0014 states for every AlphaLab
state, plus the one thing a lifecycle snapshot cannot carry on its own.

``ModelVersion.model`` is an arbitrary object, and ``__serializable__`` drops it
in favour of ``model_type`` and the artifact reference. ``restore`` therefore
takes the objects back from the caller and refuses -- loudly -- when one is
missing or is the wrong type. A registry silently holding ``None`` where a model
belongs would answer "which model is in production?" with something that looks
like an answer.
"""

import json
from dataclasses import dataclass, replace
from typing import Any

import pytest

from alphalab.common.ids import id_scope
from alphalab.experiment_tracking import complete_run, log_metrics, start_run
from alphalab.lifecycle import (
    LifecycleState,
    MetricThreshold,
    ValidationMethod,
    ValidationPolicy,
    active_strategy_version,
    build_evidence,
    deploy_strategy_version,
    promote_strategy_version,
    record_evidence,
    register_model_version,
    register_strategy,
    rollback_environment,
)
from alphalab.lifecycle.snapshot import (
    LIFECYCLE_SNAPSHOT_SCHEMA,
    capture,
    from_primitives,
    restore,
)
from alphalab.model_registry import ArtifactRef, ModelStage, promote
from alphalab.persistence.adapter import PersistenceAdapter
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.persistence.state import PersistenceState
from alphalab.persistence.storage import MemoryStorage
from alphalab.persistence.views import latest_snapshot
from alphalab.studio.strategy import StrategyDefinition

_SEED = 20250906
POLICY = ValidationPolicy("prod-v1", (MetricThreshold("sharpe_ratio", minimum=1.0),))


@dataclass(frozen=True, slots=True)
class _Model:
    """A stand-in for whatever a registry actually holds."""

    weights: tuple[float, ...]


MODEL = _Model((0.1, 0.2, 0.3))
MODELS: dict[str, object] = {"momentum@1": MODEL}


def _build() -> LifecycleState:
    """Run, model, two strategy versions, two deployments and a rollback."""

    state = LifecycleState()
    tracker, run_id = start_run(
        state.experiments, "sweep", {"fast": 5, "opt": "adam", "warm": True}, 1.0
    )
    tracker = log_metrics(tracker, run_id, {"sharpe": 1.4, "drawdown": 0.1})
    tracker = complete_run(tracker, run_id, 2.0)
    state = replace(state, experiments=tracker)

    state, model = register_model_version(
        state,
        "momentum",
        MODEL,
        3.0,
        run_id=run_id,
        metrics={"sharpe": 1.4},
        tags={"owner": "quant"},
        artifact=ArtifactRef("file://momentum-1.bin", "application/octet-stream", "d0", 2048),
    )
    state = replace(state, models=promote(state.models, "momentum", 1, ModelStage.STAGING, 4.0))

    for index, fast in enumerate((5.0, 8.0), start=1):
        definition = StrategyDefinition(
            f"ma-{index}", "MA crossover", str(index), "quant", "d", {"fast": fast, "slow": 20.0}
        )
        state, ref = register_strategy(
            state, "ma-crossover", definition, 5.0 + index, model=model, run_id=run_id
        )
        evidence = build_evidence(
            ValidationMethod.BACKTEST, str(ref), "ds-1", {"sharpe_ratio": 1.4}, 6.0, seed=7
        )
        state = record_evidence(state, evidence)
        state = promote_strategy_version(
            state, ref.name, ref.version, POLICY, evidence.evidence_id, 7.0 + index
        )
        state, _ = deploy_strategy_version(
            state, ref.name, ref.version, "paper", 9.0 + index, deployed_by="ci"
        )

    state, _ = rollback_environment(state, "paper", 20.0)
    return state


def _state() -> LifecycleState:
    with id_scope(_SEED):
        return _build()


def _payload() -> dict[str, Any]:
    decoded = json.loads(serialize(capture(_state())))
    assert isinstance(decoded, dict)
    return decoded


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


def test_restore_is_the_inverse_of_capture() -> None:
    state = _state()
    assert restore(capture(state), MODELS) == state


def test_restore_is_the_inverse_across_json() -> None:
    state = _state()
    assert restore(from_primitives(deserialize(serialize(capture(state)))), MODELS) == state


def test_every_registry_survives_the_round_trip() -> None:
    state = _state()
    restored = restore(from_primitives(deserialize(serialize(capture(state)))), MODELS)

    assert restored.experiments == state.experiments
    assert restored.models == state.models
    assert restored.strategies == state.strategies
    assert restored.deployments == state.deployments
    assert restored.evidence == state.evidence
    assert restored.releases == state.releases


def test_the_audit_trail_reads_back_as_typed_records() -> None:
    """The v2.4 tests asserted on decoded dicts. These are the real records."""

    restored = restore(from_primitives(deserialize(serialize(capture(_state())))), MODELS)
    moves = [(r.version, r.from_stage, r.to_stage) for r in restored.strategies.promotions]

    assert moves == [
        (1, ModelStage.NONE, ModelStage.STAGING),
        (1, ModelStage.STAGING, ModelStage.PRODUCTION),
        (2, ModelStage.NONE, ModelStage.STAGING),
        (2, ModelStage.STAGING, ModelStage.PRODUCTION),
        (1, ModelStage.PRODUCTION, ModelStage.ARCHIVED),
        (1, ModelStage.ARCHIVED, ModelStage.PRODUCTION),
        (2, ModelStage.PRODUCTION, ModelStage.ARCHIVED),
    ]
    assert all(record.reason for record in restored.strategies.promotions)


def test_the_deployment_ledger_still_answers_what_is_live() -> None:
    restored = restore(from_primitives(deserialize(serialize(capture(_state())))), MODELS)
    active = active_strategy_version(restored, "paper")

    assert active is not None
    assert active.ref.version == 1
    assert [r.is_rollback for r in restored.deployments.deployments] == [False, False, True]


def test_metric_histories_and_mixed_parameters_survive() -> None:
    state = _state()
    restored = restore(from_primitives(deserialize(serialize(capture(state)))), MODELS)
    run = next(iter(restored.experiments.runs.values()))

    assert run.metrics["sharpe"] == (1.4,)
    assert run.parameters == {"fast": 5, "opt": "adam", "warm": True}
    assert run.status.name == "COMPLETED"


def test_the_artifact_reference_survives() -> None:
    restored = restore(from_primitives(deserialize(serialize(capture(_state())))), MODELS)
    artifact = restored.models.versions["momentum"][1].artifact

    assert artifact is not None
    assert artifact.uri == "file://momentum-1.bin"
    assert artifact.checksum == "d0"
    assert artifact.size_bytes == 2048


def test_evidence_still_verifies_after_a_round_trip() -> None:
    """Evidence ids are content digests; a lossy round trip would break them."""

    from alphalab.lifecycle import verify_evidence_id

    restored = restore(from_primitives(deserialize(serialize(capture(_state())))), MODELS)
    assert restored.evidence
    assert all(verify_evidence_id(item) for item in restored.evidence.values())


def test_an_empty_lifecycle_round_trips() -> None:
    state = LifecycleState()
    assert restore(from_primitives(deserialize(serialize(capture(state)))), {}) == state


# --------------------------------------------------------------------------- #
# The supplied models
# --------------------------------------------------------------------------- #


def test_the_model_object_is_returned_by_the_caller_not_the_snapshot() -> None:
    restored = restore(from_primitives(deserialize(serialize(capture(_state())))), MODELS)
    assert restored.models.versions["momentum"][1].model is MODEL


def test_a_missing_model_is_refused_rather_than_left_none() -> None:
    payload = from_primitives(deserialize(serialize(capture(_state()))))
    with pytest.raises(StateDecodeError, match="No model supplied for 'momentum@1'"):
        restore(payload, {})


def test_a_model_of_the_wrong_type_is_refused() -> None:
    payload = from_primitives(deserialize(serialize(capture(_state()))))
    with pytest.raises(StateDecodeError, match=r"is a builtins\.object, but the snapshot"):
        restore(payload, {"momentum@1": object()})


def test_the_snapshot_records_the_type_it_dropped() -> None:
    record = capture(_state()).model_versions[0]
    assert record.model_type.endswith("_Model")
    assert record.ref == "momentum@1"


def test_the_serialized_payload_carries_no_model_object() -> None:
    version = _payload()["model_versions"][0]
    assert "model" not in version
    assert version["model_type"].endswith("_Model")


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_capture_and_serialization_are_deterministic() -> None:
    state = _state()
    assert serialize(capture(state)) == serialize(capture(state))


def test_two_seeded_builds_snapshot_identically() -> None:
    assert serialize(capture(_state())) == serialize(capture(_state()))


def test_a_restored_state_re_serializes_identically() -> None:
    payload = serialize(capture(_state()))
    restored = restore(from_primitives(deserialize(payload)), MODELS)
    assert serialize(capture(restored)) == payload


def test_a_restored_lifecycle_continues_processing_identically() -> None:
    """The point of restoring: the lifecycle carries on where it left off."""

    state = _state()
    restored = restore(from_primitives(deserialize(serialize(capture(state)))), MODELS)

    direct, _ = deploy_strategy_version(state, "ma-crossover", 1, "live-eu", 30.0)
    resumed, _ = deploy_strategy_version(restored, "ma-crossover", 1, "live-eu", 30.0)

    assert serialize(capture(resumed)) == serialize(capture(direct))
    live = active_strategy_version(resumed, "live-eu")
    assert live is not None and live.ref.version == 1


# --------------------------------------------------------------------------- #
# The persistence store is a real consumer
# --------------------------------------------------------------------------- #


def test_a_lifecycle_snapshot_round_trips_through_the_persistence_store() -> None:
    state = _state()
    storage = MemoryStorage()
    persistence = PersistenceState(engine_id="ENGINE-L")

    record = PersistenceAdapter.to_snapshot("SNAP-L", "lifecycle", 30.0, capture(state))
    persistence, _ = storage.save_snapshot(persistence, record, 30.0)

    stored = latest_snapshot(persistence, "lifecycle")
    assert stored is not None
    restored = restore(from_primitives(PersistenceAdapter.snapshot_payload(stored)), MODELS)

    assert restored == state


# --------------------------------------------------------------------------- #
# Malformed payloads fail explicitly
# --------------------------------------------------------------------------- #


def test_a_missing_field_names_the_field() -> None:
    payload = _payload()
    del payload["evidence"]
    with pytest.raises(StateDecodeError, match="missing 'evidence'"):
        from_primitives(payload)


def test_a_missing_nested_field_names_the_field() -> None:
    payload = _payload()
    del payload["strategy_versions"][0]["policy_id"]
    with pytest.raises(StateDecodeError, match="missing 'policy_id'"):
        from_primitives(payload)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("runs", {}, "runs is not an array"),
        ("production", [], "production is not an object"),
        ("releases", "none", "releases is not an object"),
        ("model_versions", {}, "model_versions is not an array"),
    ],
)
def test_a_wrong_type_names_the_field(field: str, value: Any, match: str) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises(StateDecodeError, match=match):
        from_primitives(payload)


def test_an_unknown_stage_member_is_refused() -> None:
    payload = _payload()
    payload["model_versions"][0]["stage"] = "ModelStage.SOMEWHERE"
    with pytest.raises(StateDecodeError, match="names no ModelStage member"):
        from_primitives(payload)


def test_a_bare_stage_name_is_refused() -> None:
    """The encoder writes 'ModelStage.STAGING'. Accepting 'STAGING' too would
    give the format two dialects."""

    payload = _payload()
    payload["model_versions"][0]["stage"] = "STAGING"
    with pytest.raises(StateDecodeError, match="is not a ModelStage"):
        from_primitives(payload)


def test_a_non_integer_version_is_refused() -> None:
    payload = _payload()
    payload["model_versions"][0]["version"] = "1"
    with pytest.raises(StateDecodeError, match="version is not an integer"):
        from_primitives(payload)


def test_a_non_boolean_rollback_flag_is_refused() -> None:
    payload = _payload()
    payload["deployments"][0]["is_rollback"] = 0
    with pytest.raises(StateDecodeError, match="is_rollback is not a boolean"):
        from_primitives(payload)


def test_a_malformed_parameter_value_is_refused() -> None:
    payload = _payload()
    payload["runs"][0]["parameters"]["fast"] = {"nested": 1}
    with pytest.raises(StateDecodeError, match="is not a parameter value"):
        from_primitives(payload)


def test_a_payload_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(StateDecodeError, match="lifecycle snapshot is not an object"):
        from_primitives([])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Schema version
# --------------------------------------------------------------------------- #


def test_the_snapshot_declares_its_schema_version() -> None:
    assert capture(_state()).schema_version == LIFECYCLE_SNAPSHOT_SCHEMA
    assert _payload()["schema_version"] == LIFECYCLE_SNAPSHOT_SCHEMA


def test_an_unknown_schema_version_is_refused() -> None:
    payload = _payload()
    payload["schema_version"] = LIFECYCLE_SNAPSHOT_SCHEMA + 7
    with pytest.raises(StateDecodeError, match="declares schema version 8"):
        from_primitives(payload)
