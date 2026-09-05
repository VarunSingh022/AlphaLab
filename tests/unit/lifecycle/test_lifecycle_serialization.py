"""The lifecycle state persists, round-trips, and is deterministic.

Whatever the lifecycle records has to survive being written down: a promotion
that cannot be read back six months later is not an audit trail. These tests go
through :mod:`alphalab.persistence.serializer`, the strict encoder that raises
rather than stringifying, so anything unserializable fails here rather than
being persisted as prose.
"""

import json
from dataclasses import replace

from alphalab.common.ids import id_scope
from alphalab.experiment_tracking import complete_run, log_metrics, start_run
from alphalab.lifecycle import (
    LifecycleState,
    MetricThreshold,
    ValidationMethod,
    ValidationPolicy,
    build_evidence,
    deploy_strategy_version,
    promote_strategy_version,
    record_evidence,
    register_model_version,
    register_strategy,
    rollback_environment,
)
from alphalab.model_registry import ArtifactRef, ModelStage, promote
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.studio.strategy import StrategyDefinition

POLICY = ValidationPolicy("prod-v1", (MetricThreshold("sharpe_ratio", minimum=1.0),))


def _definition(version: str, fast: float) -> StrategyDefinition:
    return StrategyDefinition(
        f"ma-{version}", "MA crossover", version, "quant", "d", {"fast": fast, "slow": 20.0}
    )


def _lifecycle() -> LifecycleState:
    """A state that has been all the way through: run, model, strategy,
    promotion, two deployments and a rollback."""

    state = LifecycleState()
    tracker, run_id = start_run(state.experiments, "ma-sweep", {"fast": 5, "slow": 20}, 1.0)
    tracker = log_metrics(tracker, run_id, {"sharpe": 1.4})
    tracker = complete_run(tracker, run_id, 2.0)
    state = replace(state, experiments=tracker)

    state, model = register_model_version(
        state,
        "momentum",
        object(),
        3.0,
        run_id=run_id,
        metrics={"sharpe": 1.4},
        artifact=ArtifactRef("file://momentum-1.bin", "application/octet-stream", "d0", 2048),
    )
    state = replace(state, models=promote(state.models, "momentum", 1, ModelStage.STAGING, 4.0))

    for index, fast in enumerate((5.0, 8.0), start=1):
        state, ref = register_strategy(
            state,
            "ma-crossover",
            _definition(str(index), fast),
            5.0 + index,
            model=model,
            run_id=run_id,
        )
        evidence = build_evidence(
            ValidationMethod.BACKTEST, str(ref), "ds-1", {"sharpe_ratio": 1.4}, 6.0, seed=7
        )
        state = record_evidence(state, evidence)
        state = promote_strategy_version(
            state, ref.name, ref.version, POLICY, evidence.evidence_id, 7.0 + index
        )
        state, _ = deploy_strategy_version(state, ref.name, ref.version, "paper", 9.0 + index)

    state, _ = rollback_environment(state, "paper", 20.0)
    return state


def test_the_whole_lifecycle_state_serializes() -> None:
    decoded = json.loads(serialize(_lifecycle()))
    assert sorted(decoded) == [
        "deployments",
        "evidence",
        "experiments",
        "models",
        "releases",
        "strategies",
    ]


def test_serializing_is_deterministic() -> None:
    state = _lifecycle()
    assert serialize(state) == serialize(state)


def test_two_seeded_runs_of_the_flow_serialize_identically() -> None:
    """Under a seed, the whole flow reproduces field for field.

    Every identity the lifecycle mints itself is already deterministic: a model
    or strategy version is numbered in registration order, an evidence id is a
    digest of what it records, and a release checksum is a digest of its
    manifest. The one identity that is not is the experiment ``run_id``, which
    comes from ``new_id()`` and is ``uuid4`` by default. ``id_scope`` is the
    mechanism v2.2 established for exactly this, and it is what makes a
    lifecycle comparable across executions rather than only within one.
    """
    with id_scope(20240):
        first = serialize(_lifecycle())
    with id_scope(20240):
        second = serialize(_lifecycle())

    assert first == second


def test_without_a_seed_only_the_run_id_differs() -> None:
    """The honest statement of the limit: quantities and derived ids reproduce,
    the run identifier does not."""
    unseeded = json.loads(serialize(_lifecycle()))
    other = json.loads(serialize(_lifecycle()))

    assert list(unseeded["experiments"]["runs"]) != list(other["experiments"]["runs"])
    assert (
        unseeded["models"]["versions"]["momentum"]["1"]["metrics"]
        == (other["models"]["versions"]["momentum"]["1"]["metrics"])
    )
    assert [record["to_stage"] for record in unseeded["strategies"]["promotions"]] == [
        record["to_stage"] for record in other["strategies"]["promotions"]
    ]


def test_the_histories_serialize_as_arrays_not_prose() -> None:
    decoded = json.loads(serialize(_lifecycle()))
    payload = json.dumps(decoded)

    for history in (
        decoded["strategies"]["promotions"],
        decoded["models"]["promotions"],
        decoded["deployments"]["deployments"],
    ):
        assert isinstance(history, list)
        assert all(isinstance(entry, dict) for entry in history)
    assert "AppendOnlyLog(" not in payload
    assert "PersistentMap(" not in payload


def test_the_model_object_serializes_as_its_type_and_artifact_not_as_prose() -> None:
    """A registry snapshot is metadata and references by construction."""
    version = json.loads(serialize(_lifecycle()))["models"]["versions"]["momentum"]["1"]

    assert "model" not in version
    assert version["model_type"] == "builtins.object"
    assert version["artifact"]["uri"] == "file://momentum-1.bin"
    assert version["artifact"]["checksum"] == "d0"


def test_the_audit_trail_reads_back_intact() -> None:
    restored = deserialize(serialize(_lifecycle()))
    moves = [
        (entry["version"], entry["from_stage"], entry["to_stage"])
        for entry in restored["strategies"]["promotions"]
    ]

    assert moves == [
        (1, "ModelStage.NONE", "ModelStage.STAGING"),
        (1, "ModelStage.STAGING", "ModelStage.PRODUCTION"),
        (2, "ModelStage.NONE", "ModelStage.STAGING"),
        (2, "ModelStage.STAGING", "ModelStage.PRODUCTION"),
        (1, "ModelStage.PRODUCTION", "ModelStage.ARCHIVED"),
        (1, "ModelStage.ARCHIVED", "ModelStage.PRODUCTION"),
        (2, "ModelStage.PRODUCTION", "ModelStage.ARCHIVED"),
    ]


def test_the_evidence_reads_back_with_its_measurements() -> None:
    state = _lifecycle()
    restored = deserialize(serialize(state))
    evidence_id = next(iter(state.evidence))

    assert restored["evidence"][evidence_id]["metrics"] == {"sharpe_ratio": 1.4}
    assert restored["evidence"][evidence_id]["seed"] == 7
    assert restored["evidence"][evidence_id]["method"] == "ValidationMethod.BACKTEST"


def test_the_deployment_ledger_reads_back_with_its_rollback_flagged() -> None:
    restored = deserialize(serialize(_lifecycle()))
    ledger = restored["deployments"]["deployments"]

    assert [entry["is_rollback"] for entry in ledger] == [False, False, True]
    assert ledger[-1]["replaced_version"] == 2


def test_an_empty_lifecycle_state_serializes_too() -> None:
    decoded = json.loads(serialize(LifecycleState()))
    assert decoded["evidence"] == {}
    assert decoded["strategies"]["versions"] == {}
