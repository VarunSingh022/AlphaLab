"""Complete, restorable snapshots of a :class:`~alphalab.backtesting.state.BacktestState`.

The backtest counterpart of :mod:`alphalab.runtime.session_snapshot`, and the same
two-envelope shape: :mod:`alphalab.runtime.snapshot` captures the pipeline core,
and this captures the run's own bookkeeping around it -- how far the dataset has
been consumed, and what each record produced.

It is a separate module from the session envelope rather than one shared "run"
snapshot because the dependency runs one way: ``alphalab.backtesting`` imports
``alphalab.runtime`` and never the reverse, so a single module holding both would
have to import backtesting from runtime and close a cycle. ADR-0023 sketched one
envelope; two is the same decision honouring the package boundary, and each state
is captured by the package that owns it, as portfolio, OMS and allocation are.

``BacktestConfig.pipeline`` and ``state.pipeline.config`` are the same object --
:func:`~alphalab.backtesting.engine.initialize` builds the pipeline from the
run's own configuration -- so the pipeline configuration is carried once, inside
the nested pipeline snapshot, and the run configuration is rebuilt around it.

Resuming
--------
:func:`restore` reconstructs state and does nothing else. Continuation is
:meth:`~alphalab.backtesting.engine.BacktestEngine.resume`, which opens the scope
the restored stream position implies::

    restored = restore(from_primitives(deserialize(payload)), objects)
    with BacktestEngine.resume(restored):
        for record in remaining:
            restored, _ = BacktestEngine.advance(restored, record, context_factory)

The boundary is *between* ``advance`` calls: a run that processed N records
resumes at record N+1 and replays nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from alphalab.backtesting.config import BacktestConfig
from alphalab.backtesting.state import BacktestState, BacktestStep
from alphalab.common.append_log import AppendOnlyLog
from alphalab.execution.policy import FillPolicy
from alphalab.oms.order import Order as OMSOrder

# The order, report and fill decoders belong to the modules that own those types.
# Imported rather than repeated: a second implementation of one decoding contract
# is how two readers come to disagree about the same payload.
from alphalab.oms.snapshot import _order
from alphalab.persistence.decode import (
    as_bool,
    as_decimal,
    as_float,
    as_int,
    as_mapping,
    as_sequence,
    as_str,
    require,
    require_schema_version,
)
from alphalab.runtime.snapshot import (
    PipelineSnapshot,
    RuntimeObjects,
    _fill,
    _report,
    _require_object,
)
from alphalab.runtime.snapshot import capture as capture_pipeline
from alphalab.runtime.snapshot import from_primitives as pipeline_from_primitives
from alphalab.runtime.snapshot import restore as restore_pipeline

__all__ = [
    "BACKTEST_SNAPSHOT_SCHEMA",
    "BacktestObjects",
    "BacktestSnapshot",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. See ADR-0023.
#:
#: A module-local literal rather than ``DEFAULT_SCHEMA_VERSION``, for the reason
#: v2.6 gave for the portfolio: that constant also versions ``CommonEvent`` and
#: ``BaseEvent``.
BACKTEST_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM: Final = "backtest"


@dataclass(frozen=True, slots=True)
class BacktestSnapshot:
    """Complete, JSON-serializable projection of a :class:`BacktestState`."""

    pipeline: PipelineSnapshot
    seed: int | None
    start_timestamp: float
    years_elapsed: float
    risk_free_rate: float
    compile_analytics: bool
    fill_policy_type: str
    processed: int
    current_timestamp: float
    steps: tuple[BacktestStep, ...]
    schema_version: int = BACKTEST_SNAPSHOT_SCHEMA


@dataclass(frozen=True, slots=True)
class BacktestObjects:
    """The live objects a backtest snapshot records but does not carry."""

    pipeline: RuntimeObjects
    fill_policy: FillPolicy


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(state: BacktestState) -> BacktestSnapshot:
    """Project ``state`` into its complete serializable snapshot."""

    return BacktestSnapshot(
        pipeline=capture_pipeline(state.pipeline),
        seed=state.config.seed,
        start_timestamp=state.config.start_timestamp,
        years_elapsed=state.config.years_elapsed,
        risk_free_rate=state.config.risk_free_rate,
        compile_analytics=state.config.compile_analytics,
        fill_policy_type=type(state.config.fill_policy).__name__,
        processed=state.processed,
        current_timestamp=state.current_timestamp,
        steps=state.steps.to_tuple(),
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(snapshot: BacktestSnapshot, objects: BacktestObjects) -> BacktestState:
    """Rebuild the state a snapshot was captured from.

    Raises:
        StateDecodeError: If a required live object is missing or of the wrong
            type.
        RuntimeValidationError: If the restored configuration fails a validation
            ``initialize`` enforces; the pipeline's own restore re-applies it.
    """

    pipeline = restore_pipeline(snapshot.pipeline, objects.pipeline)
    fill_policy = _require_object(objects.fill_policy, snapshot.fill_policy_type, "fill policy")

    return BacktestState(
        config=BacktestConfig(
            pipeline=pipeline.config,
            fill_policy=fill_policy,
            seed=snapshot.seed,
            start_timestamp=snapshot.start_timestamp,
            years_elapsed=snapshot.years_elapsed,
            risk_free_rate=snapshot.risk_free_rate,
            compile_analytics=snapshot.compile_analytics,
        ),
        pipeline=pipeline,
        processed=snapshot.processed,
        current_timestamp=snapshot.current_timestamp,
        steps=AppendOnlyLog(snapshot.steps),
    )


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _oms_order(value: Any, where: str) -> OMSOrder:
    return _order(as_mapping(value, where))


def _step(value: Any, where: str) -> BacktestStep:
    payload = as_mapping(value, where)

    def items(key: str, decode: Any) -> tuple[Any, ...]:
        return tuple(
            decode(item, f"{where}.{key}[{index}]")
            for index, item in enumerate(as_sequence(require(payload, key), f"{where}.{key}"))
        )

    return BacktestStep(
        index=as_int(require(payload, "index"), f"{where}.index"),
        event_id=as_str(require(payload, "event_id"), f"{where}.event_id"),
        timestamp=as_float(require(payload, "timestamp"), f"{where}.timestamp"),
        orders=items("orders", _oms_order),
        reports=items("reports", _report),
        fills=items("fills", _fill),
        equity=as_decimal(require(payload, "equity"), f"{where}.equity"),
    )


def from_primitives(payload: Mapping[str, Any]) -> BacktestSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`BacktestSnapshot`.

    Raises:
        StateDecodeError: If the payload is not an object, declares no schema
            version or one this build does not read, is missing a field, or holds
            a value of the wrong type. The message names the field.
    """

    payload = as_mapping(payload, "backtest snapshot")
    require_schema_version(payload, BACKTEST_SNAPSHOT_SCHEMA, _SUBSYSTEM)
    seed = require(payload, "seed")

    return BacktestSnapshot(
        pipeline=pipeline_from_primitives(as_mapping(require(payload, "pipeline"), "pipeline")),
        seed=None if seed is None else as_int(seed, "seed"),
        start_timestamp=as_float(require(payload, "start_timestamp"), "start_timestamp"),
        years_elapsed=as_float(require(payload, "years_elapsed"), "years_elapsed"),
        risk_free_rate=as_float(require(payload, "risk_free_rate"), "risk_free_rate"),
        compile_analytics=as_bool(require(payload, "compile_analytics"), "compile_analytics"),
        fill_policy_type=as_str(require(payload, "fill_policy_type"), "fill_policy_type"),
        processed=as_int(require(payload, "processed"), "processed"),
        current_timestamp=as_float(require(payload, "current_timestamp"), "current_timestamp"),
        steps=tuple(
            _step(item, f"steps[{index}]")
            for index, item in enumerate(as_sequence(require(payload, "steps"), "steps"))
        ),
        schema_version=BACKTEST_SNAPSHOT_SCHEMA,
    )
