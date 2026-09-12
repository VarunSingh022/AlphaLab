"""Complete, restorable snapshots of a :class:`~alphalab.runtime.run.RunState`.

The outer of two envelopes
--------------------------
:mod:`alphalab.runtime.snapshot` captures ``ExecutionPipelineState``, which is
the stable core every environment threads and which ADR-0030 freezes. This
module captures the layer around it: how far a run has read, what it declined to
act on, what each record produced, and the stream it read.

**One run envelope, where v2.13 had two.** ``SESSION_SNAPSHOT_SCHEMA`` and
``BACKTEST_SNAPSHOT_SCHEMA`` existed because ``SessionState`` and
``BacktestState`` existed, and ADR-0023 decision 1 split them so that the
reshape it anticipated could move them without versioning the pipeline core.
That reshape is v2.14, and this is what it moves to. The core does not move with
it: :data:`~alphalab.runtime.snapshot.PIPELINE_SNAPSHOT_SCHEMA` stays at 2,
which is exactly the churn confinement the split was bought for.

``RunConfig.pipeline`` and ``state.pipeline.config`` are the *same* object:
:meth:`~alphalab.runtime.run.RunEngine.initialize` builds the pipeline from the
run's own configuration. The envelope therefore carries the pipeline
configuration exactly once, inside the nested pipeline snapshot, and
:func:`restore` rebuilds the run configuration around it. Two copies could
disagree; one cannot.

What is supplied back
---------------------
Everything :class:`~alphalab.runtime.snapshot.RuntimeObjects` covers, plus the
run's :class:`~alphalab.execution.policy.FillPolicy` -- a policy object, not
data. Missing or wrongly typed objects are refused, never substituted.

No cross-version read
---------------------
:data:`RUN_SNAPSHOT_SCHEMA` is new in v2.14 and reads nothing older. A v2.13
``SessionSnapshot`` payload lacks ``years_elapsed``, ``risk_free_rate`` and
``compile_analytics``, and a v2.13 ``BacktestSnapshot`` payload lacks ``mode`` --
which is genuinely unknowable, because a backtest run and a replay run wrote
identical payloads. No honest value exists for either, so both are refused rather
than filled in with a dataclass default, which is ``require``'s stated rule. That
is the portfolio precedent rather than the OMS one, and it is a decision rather
than an omission. Read a v2.13 payload with the v2.13 build that wrote it.

Resuming
--------
:func:`restore` reconstructs state and does nothing else: it installs no
identifier source and processes no record. Continuation is
:meth:`~alphalab.runtime.run.RunEngine.resume`, which opens the scope the
restored stream position implies, and the caller then advances the records the
run has not seen::

    restored = restore(from_primitives(deserialize(payload)), objects)
    with RunEngine.resume(restored):
        for record in remaining:
            restored, _ = RunEngine.advance(restored, record, context_factory)

The boundary is *between* ``advance`` calls. A run that processed N records
resumes at record N+1; nothing is replayed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from alphalab.common.append_log import AppendOnlyLog
from alphalab.execution.policy import FillPolicy
from alphalab.market.record import MarketRecord
from alphalab.market.source import OrderingGuarantee
from alphalab.oms.order import Order as OMSOrder

# The order decoder belongs to the module that owns the type. Imported rather
# than repeated: a second implementation of one decoding contract is how two
# readers come to disagree about the same payload.
from alphalab.oms.snapshot import _order
from alphalab.persistence.decode import (
    as_bool,
    as_decimal,
    as_float,
    as_int,
    as_mapping,
    as_named_enum,
    as_optional_str,
    as_sequence,
    as_str,
    require,
    require_schema_version,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.run import ExecutionMode, RunConfig, RunState, RunStep, SkippedRecord

# The market-record, fill and report decoders belong to the pipeline snapshot,
# which owns the state they came from. Imported for the same reason.
from alphalab.runtime.snapshot import (
    PipelineSnapshot,
    RuntimeObjects,
    _bar,
    _fill,
    _quote,
    _report,
    _require_object,
    _tick,
)
from alphalab.runtime.snapshot import capture as capture_pipeline
from alphalab.runtime.snapshot import from_primitives as pipeline_from_primitives
from alphalab.runtime.snapshot import restore as restore_pipeline

__all__ = [
    "RUN_SNAPSHOT_SCHEMA",
    "RunObjects",
    "RunSnapshot",
    "SkippedRecordRecord",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. See ADR-0030.
#:
#: A module-local literal rather than ``DEFAULT_SCHEMA_VERSION``, for the reason
#: v2.6 gave for the portfolio, v2.9 for the pipeline and v2.13 for the run-state
#: envelope: that constant also versions ``CommonEvent`` and ``BaseEvent``, so
#: bumping it would version every event in the system as a side effect of one
#: subsystem's change.
#:
#: It is the *only* run-envelope constant. It replaces ``SESSION_SNAPSHOT_SCHEMA``
#: and ``BACKTEST_SNAPSHOT_SCHEMA``, which are removed rather than aliased, and it
#: is new, so it has nothing to be compatible with.
RUN_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM: Final = "run"

#: A market record's payload is a union, so it carries the tag that says which.
_PAYLOAD_TYPES: Mapping[str, Any] = {"Quote": _quote, "Bar": _bar, "Tick": _tick}


@dataclass(frozen=True, slots=True)
class SkippedRecordRecord:
    """One record the run declined to act on, with its payload tagged.

    ``MarketRecord.payload`` is a ``Quote | Bar | Tick``; without the tag a
    decoder cannot know which of the three a payload describes.
    """

    payload_type: str
    record: MarketRecord
    reason: str


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Complete, JSON-serializable projection of a :class:`RunState`."""

    pipeline: PipelineSnapshot
    mode: ExecutionMode
    seed: int | None
    start_timestamp: float
    ordering: OrderingGuarantee
    max_market_data_age_seconds: float | None
    years_elapsed: float
    risk_free_rate: float
    compile_analytics: bool
    fill_policy_type: str
    processed: int
    current_timestamp: float
    last_record_timestamp: float | None
    source_id: str | None
    steps: tuple[RunStep, ...]
    skipped: tuple[SkippedRecordRecord, ...]
    schema_version: int = RUN_SNAPSHOT_SCHEMA


@dataclass(frozen=True, slots=True)
class RunObjects:
    """The live objects a run snapshot records but does not carry.

    Attributes:
        pipeline: What :func:`alphalab.runtime.snapshot.restore` requires --
            sizing model, simulator, strategy instances and the optional
            instrument registry.
        fill_policy: How a simulated venue answers each order. A policy object,
            so it is recorded by type and supplied back like the rest.
    """

    pipeline: RuntimeObjects
    fill_policy: FillPolicy


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(state: RunState) -> RunSnapshot:
    """Project ``state`` into its complete serializable snapshot.

    The pipeline configuration is carried once, by the nested pipeline snapshot,
    because the run's copy is the same object.
    """

    return RunSnapshot(
        pipeline=capture_pipeline(state.pipeline),
        mode=state.config.mode,
        seed=state.config.seed,
        start_timestamp=state.config.start_timestamp,
        ordering=state.config.ordering,
        max_market_data_age_seconds=state.config.max_market_data_age_seconds,
        years_elapsed=state.config.years_elapsed,
        risk_free_rate=state.config.risk_free_rate,
        compile_analytics=state.config.compile_analytics,
        fill_policy_type=type(state.config.fill_policy).__name__,
        processed=state.processed,
        current_timestamp=state.current_timestamp,
        last_record_timestamp=state.last_record_timestamp,
        source_id=state.source_id,
        steps=state.steps.to_tuple(),
        skipped=tuple(
            SkippedRecordRecord(type(entry.record.payload).__name__, entry.record, entry.reason)
            for entry in state.skipped
        ),
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(snapshot: RunSnapshot, objects: RunObjects) -> RunState:
    """Rebuild the state a snapshot was captured from.

    The pipeline is rebuilt first, and its configuration becomes the run
    configuration's, so the two cannot disagree. Construction-time validation is
    re-applied by the pipeline's own restore.

    Returns:
        The reconstructed run. No identifier source is installed and no record is
        processed: continuing is :meth:`~alphalab.runtime.run.RunEngine.resume`
        plus :meth:`~alphalab.runtime.run.RunEngine.advance`.

    Raises:
        StateDecodeError: If a required live object is missing or of the wrong
            type.
        RuntimeValidationError: If the restored configuration fails a validation
            ``initialize`` enforces.
    """

    pipeline = restore_pipeline(snapshot.pipeline, objects.pipeline)
    fill_policy = _require_object(objects.fill_policy, snapshot.fill_policy_type, "fill policy")

    config = RunConfig(
        pipeline=pipeline.config,
        mode=snapshot.mode,
        fill_policy=fill_policy,
        seed=snapshot.seed,
        start_timestamp=snapshot.start_timestamp,
        ordering=snapshot.ordering,
        max_market_data_age_seconds=snapshot.max_market_data_age_seconds,
        years_elapsed=snapshot.years_elapsed,
        risk_free_rate=snapshot.risk_free_rate,
        compile_analytics=snapshot.compile_analytics,
    )
    return RunState(
        config=config,
        pipeline=pipeline,
        processed=snapshot.processed,
        current_timestamp=snapshot.current_timestamp,
        last_record_timestamp=snapshot.last_record_timestamp,
        source_id=snapshot.source_id,
        steps=AppendOnlyLog(snapshot.steps),
        skipped=AppendOnlyLog(
            SkippedRecord(entry.record, entry.reason) for entry in snapshot.skipped
        ),
    )


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _oms_order(value: Any, where: str) -> OMSOrder:
    return _order(as_mapping(value, where))


def _step(value: Any, where: str) -> RunStep:
    payload = as_mapping(value, where)

    def items(key: str, decode: Any) -> tuple[Any, ...]:
        return tuple(
            decode(item, f"{where}.{key}[{index}]")
            for index, item in enumerate(as_sequence(require(payload, key), f"{where}.{key}"))
        )

    return RunStep(
        index=as_int(require(payload, "index"), f"{where}.index"),
        event_id=as_str(require(payload, "event_id"), f"{where}.event_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        orders=items("orders", _oms_order),
        reports=items("reports", _report),
        fills=items("fills", _fill),
        equity=as_decimal(require(payload, "equity"), f"{where}.equity"),
    )


def _skipped(value: Any, where: str) -> SkippedRecordRecord:
    payload = as_mapping(value, where)
    payload_type = as_str(require(payload, "payload_type"), f"{where}.payload_type")
    decode = _PAYLOAD_TYPES.get(payload_type)
    if decode is None:
        known = ", ".join(sorted(_PAYLOAD_TYPES))
        raise StateDecodeError(
            f"{where}.payload_type is not a market payload: {payload_type!r}; "
            f"expected one of {known}"
        )

    record = as_mapping(require(payload, "record"), f"{where}.record")
    return SkippedRecordRecord(
        payload_type=payload_type,
        record=MarketRecord(
            event_id=as_str(require(record, "event_id"), f"{where}.record.event_id"),
            timestamp=as_float(require(record, "timestamp"), f"{where}.record.timestamp"),
            payload=decode(require(record, "payload"), f"{where}.record.payload"),
        ),
        reason=as_str(require(payload, "reason"), f"{where}.reason"),
    )


def _optional_float(value: Any, where: str) -> float | None:
    return None if value is None else as_float(value, where)


def _optional_int(value: Any, where: str) -> int | None:
    return None if value is None else as_int(value, where)


def from_primitives(payload: Mapping[str, Any]) -> RunSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`RunSnapshot`.

    The nested pipeline payload is decoded by the module that owns it, so its
    version and its field errors reach the caller as its own rather than being
    normalized here.

    Raises:
        StateDecodeError: If the payload is not an object, declares no schema
            version or one this build does not read, is missing a field, or holds
            a value of the wrong type. The message names the field. A v2.13
            session or backtest payload is one this build does not read; see the
            module docstring for why it is refused rather than filled in.
    """

    payload = as_mapping(payload, "run snapshot")
    require_schema_version(payload, RUN_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    def sequence(key: str, decode: Any) -> tuple[Any, ...]:
        return tuple(
            decode(item, f"{key}[{index}]")
            for index, item in enumerate(as_sequence(require(payload, key), key))
        )

    return RunSnapshot(
        pipeline=pipeline_from_primitives(as_mapping(require(payload, "pipeline"), "pipeline")),
        mode=as_named_enum(ExecutionMode, require(payload, "mode"), "mode"),
        seed=_optional_int(require(payload, "seed"), "seed"),
        start_timestamp=as_float(require(payload, "start_timestamp"), "start_timestamp"),
        ordering=as_named_enum(OrderingGuarantee, require(payload, "ordering"), "ordering"),
        max_market_data_age_seconds=_optional_float(
            require(payload, "max_market_data_age_seconds"), "max_market_data_age_seconds"
        ),
        years_elapsed=as_float(require(payload, "years_elapsed"), "years_elapsed"),
        risk_free_rate=as_float(require(payload, "risk_free_rate"), "risk_free_rate"),
        compile_analytics=as_bool(require(payload, "compile_analytics"), "compile_analytics"),
        fill_policy_type=as_str(require(payload, "fill_policy_type"), "fill_policy_type"),
        processed=as_int(require(payload, "processed"), "processed"),
        current_timestamp=as_float(require(payload, "current_timestamp"), "current_timestamp"),
        last_record_timestamp=_optional_float(
            require(payload, "last_record_timestamp"), "last_record_timestamp"
        ),
        source_id=as_optional_str(require(payload, "source_id"), "source_id"),
        steps=sequence("steps", _step),
        skipped=sequence("skipped", _skipped),
        schema_version=RUN_SNAPSHOT_SCHEMA,
    )
