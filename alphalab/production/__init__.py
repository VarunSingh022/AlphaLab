"""AlphaLab Production Runtime Engine Layer.

Deprecated in v2.14, removed in v3.0
------------------------------------
This package names itself after a runtime it does not run, and records a
durability it does not provide. Measured at v2.13 it had **zero** production
importers -- nothing outside this package and its own unit test touched any of
it -- and the two things it appears to offer do not exist:

* :class:`~alphalab.production.checkpoint.Checkpoint` holds six opaque
  caller-supplied strings that this package never reads, writes, decodes or
  validates. Creating one records that somebody claims to have a checkpoint; it
  does not produce one.
* :meth:`~alphalab.production.recovery.RecoveryEngine.recover` requires that a
  checkpoint has been recorded, names the most recent one in an event, sets
  ``is_running`` back to ``True`` and increments a counter. No portfolio, no
  order book, no position and no runtime state is rebuilt, because this package
  holds none of them.

Durable run state lives in
:class:`~alphalab.persistence.run_store.RunStateStore` as of v2.13, fed by
``capture`` and ``serialize`` from the module that owns the state and read back
through ``from_primitives`` and ``restore``; continuation is
:meth:`~alphalab.runtime.run.RunEngine.resume`. That path is proven byte-identical
across a process boundary. See ADR-0029 and ADR-0030.

``ProductionState`` is also a third runtime-shaped state beside
:class:`~alphalab.runtime.run.RunState` and
:class:`~alphalab.runtime.execution_pipeline.ExecutionPipelineState`, with its own
``runtime_id``, and ``RuntimeMetrics`` here collides with the one in the orphan
``alphalab.runtime`` modules deprecated alongside it. v3.0 is an
architecture-frozen release and cannot carry three runtime-shaped packages, one of
which is real.

**Nothing is removed here, and nothing is aliased.** Every module keeps its
behaviour, its signatures and its tests for the whole of v2.x. The warning fires
on **use of a name through this package**, not on importing it, which is the PEP
562 mechanism :mod:`alphalab.persistence` established; see ADR-0029 decision 6.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Deprecated, and served at runtime by ``__getattr__`` below so that touching
    # one of these names warns. Imported here for type checkers only: a caller
    # that still uses this package keeps its exact types, and a deprecation is
    # not a reason to make working code un-checkable.
    from alphalab.production.adapter import ProductionAdapter
    from alphalab.production.checkpoint import Checkpoint
    from alphalab.production.engine import ProductionEngine
    from alphalab.production.events import (
        AlertRaised,
        CheckpointCreated,
        CheckpointRestored,
        HealthUpdated,
        HeartbeatGenerated,
        HeartbeatTimeout,
        ModuleRestarted,
        ModuleStarted,
        ModuleStopped,
        ProductionEvent,
        RecoveryCompleted,
        RecoveryStarted,
        RuntimeStarted,
        RuntimeStopped,
    )
    from alphalab.production.exceptions import (
        CheckpointError,
        InvalidRuntimeStateError,
        ProductionError,
        ProductionValidationError,
        RecoveryError,
    )
    from alphalab.production.health import (
        SystemHealth,
        compute_health_score,
    )
    from alphalab.production.heartbeat import (
        HeartbeatRecord,
        HeartbeatStatus,
    )
    from alphalab.production.logging import (
        LogEntry,
        LogLevel,
    )
    from alphalab.production.metrics import RuntimeMetrics
    from alphalab.production.monitor import (
        Alert,
        create_alert,
    )
    from alphalab.production.process import (
        ManagedProcess,
        ProcessState,
    )
    from alphalab.production.protocol import SubsystemProtocol
    from alphalab.production.recovery import RecoveryEngine
    from alphalab.production.runtime import RuntimeOperations
    from alphalab.production.scheduler import RuntimeScheduler
    from alphalab.production.state import ProductionState
    from alphalab.production.supervisor import Supervisor
    from alphalab.production.validation import (
        validate_module_registration,
        validate_start,
        validate_stop,
    )
    from alphalab.production.views import (
        alert_summary,
        checkpoint_history,
        get_module,
        health_report,
        heartbeat_status,
        runtime_metrics,
        runtime_summary,
    )
__all__ = [
    "Alert",
    "AlertRaised",
    "Checkpoint",
    "CheckpointCreated",
    "CheckpointError",
    "CheckpointRestored",
    "HealthUpdated",
    "HeartbeatGenerated",
    "HeartbeatRecord",
    "HeartbeatStatus",
    "HeartbeatTimeout",
    "InvalidRuntimeStateError",
    "LogEntry",
    "LogLevel",
    "ManagedProcess",
    "ModuleRestarted",
    "ModuleStarted",
    "ModuleStopped",
    "ProcessState",
    "ProductionAdapter",
    "ProductionEngine",
    "ProductionError",
    "ProductionEvent",
    "ProductionState",
    "ProductionValidationError",
    "RecoveryCompleted",
    "RecoveryEngine",
    "RecoveryError",
    "RecoveryStarted",
    "RuntimeMetrics",
    "RuntimeOperations",
    "RuntimeScheduler",
    "RuntimeStarted",
    "RuntimeStopped",
    "SubsystemProtocol",
    "Supervisor",
    "SystemHealth",
    "alert_summary",
    "checkpoint_history",
    "compute_health_score",
    "create_alert",
    "get_module",
    "health_report",
    "heartbeat_status",
    "runtime_metrics",
    "runtime_summary",
    "validate_module_registration",
    "validate_start",
    "validate_stop",
]

#: Deprecated name -> the module that still defines it, unchanged.
#:
#: The submodules themselves do not warn. A direct
#: ``from alphalab.production.state import ProductionState`` is silent, exactly
#: as ``from alphalab.persistence.storage import MemoryStorage`` is: the notice
#: guards the package surface, which is the one a reader is steered toward.
_DEPRECATED_PRODUCTION: dict[str, str] = {
    "Alert": "monitor",
    "AlertRaised": "events",
    "Checkpoint": "checkpoint",
    "CheckpointCreated": "events",
    "CheckpointError": "exceptions",
    "CheckpointRestored": "events",
    "HealthUpdated": "events",
    "HeartbeatGenerated": "events",
    "HeartbeatRecord": "heartbeat",
    "HeartbeatStatus": "heartbeat",
    "HeartbeatTimeout": "events",
    "InvalidRuntimeStateError": "exceptions",
    "LogEntry": "logging",
    "LogLevel": "logging",
    "ManagedProcess": "process",
    "ModuleRestarted": "events",
    "ModuleStarted": "events",
    "ModuleStopped": "events",
    "ProcessState": "process",
    "ProductionAdapter": "adapter",
    "ProductionEngine": "engine",
    "ProductionError": "exceptions",
    "ProductionEvent": "events",
    "ProductionState": "state",
    "ProductionValidationError": "exceptions",
    "RecoveryCompleted": "events",
    "RecoveryEngine": "recovery",
    "RecoveryError": "exceptions",
    "RecoveryStarted": "events",
    "RuntimeMetrics": "metrics",
    "RuntimeOperations": "runtime",
    "RuntimeScheduler": "scheduler",
    "RuntimeStarted": "events",
    "RuntimeStopped": "events",
    "SubsystemProtocol": "protocol",
    "Supervisor": "supervisor",
    "SystemHealth": "health",
    "alert_summary": "views",
    "checkpoint_history": "views",
    "compute_health_score": "health",
    "create_alert": "monitor",
    "get_module": "views",
    "health_report": "views",
    "heartbeat_status": "views",
    "runtime_metrics": "views",
    "runtime_summary": "views",
    "validate_module_registration": "validation",
    "validate_start": "validation",
    "validate_stop": "validation",
}


def __getattr__(name: str) -> object:
    """Serve a production name with a deprecation notice, on use rather than on import.

    This package is deprecated in v2.14 and removed in v3.0. Use
    :class:`~alphalab.persistence.run_store.RunStateStore` for durable run state,
    the owning snapshot module's ``capture`` / ``restore`` pair to project and
    rebuild it, and :meth:`~alphalab.runtime.run.RunEngine.resume` to continue a
    run. See ADR-0029 and ADR-0030.

    The object served is the same object it always was. This is a notice, not an
    alias and not a shim.
    """

    module = _DEPRECATED_PRODUCTION.get(name)
    if module is not None:
        import importlib
        import warnings

        warnings.warn(
            f"alphalab.production.{name} is deprecated and will be removed in v3.0. "
            "Checkpoint holds opaque strings this package never decodes and "
            "RecoveryEngine.recover restores no state; use "
            "alphalab.persistence.RunStateStore with the owning snapshot module's "
            "capture/restore and alphalab.runtime.RunEngine.resume for durable run "
            f"state. Importing it directly from alphalab.production.{module} does "
            "not warn.",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(importlib.import_module(f"alphalab.production.{module}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
