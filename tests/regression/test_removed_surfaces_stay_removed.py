"""The scheduled removals happened, and nothing quietly survived them.

Six surfaces carried a ``DeprecationWarning`` and a stated removal release
through v2.16. ADR-0034 removes them in v2.17 rather than v3.0, because v2.17 is
the last engineering release and a frozen public API should not contain a name
that is only there to warn about itself.

===================================== ========= ==============================
Surface                               Deprecated Replaced by
===================================== ========= ==============================
``alphalab.kernel``                   v2.6      nothing; zero consumers
``alphalab.integrations``             v2.6      ``alphalab.broker`` / ``brokers``
``alphalab.common.CommonEvent``       v2.6      ``alphalab.common.events.BaseEvent``
``alphalab.core.events``              v2.6      the canonical event vocabularies
``alphalab.persistence`` store (9)    v2.13     ``RunStateStore`` (ADR-0029)
``alphalab.runtime`` orphan (10)      v2.14     ``RunEngine`` / ``ExecutionPipeline``
``alphalab.production``               v2.14     nothing; zero consumers
===================================== ========= ==============================

This file replaces ``test_deprecation_notices.py``, which asserted that each
notice fired and that nothing was removed yet. It asserts the opposite half of
the same contract, and it is the more valuable half: a notice that lapses is an
annoyance, whereas a removal that leaves an alias behind defeats the removal.

**The alias check is the point.** ADR-0034's rule is that a removed name is
absent, not redirected: no module-level ``__getattr__`` serving it, no
re-export under a new spelling, and no shim module that imports something else
and renames it. An ``ImportError`` a caller must read and act on is the honest
answer; a silent redirect keeps the ambiguous architecture alive under a new
name, which is what the removal existed to end.
"""

import importlib
import json
import pkgutil
import subprocess
import sys

import pytest

import alphalab

#: Packages removed outright. Importing one must fail, not warn.
REMOVED_PACKAGES = (
    "alphalab.kernel",
    "alphalab.integrations",
    "alphalab.production",
    "alphalab.core.events",
)

#: Modules removed from packages that survive.
REMOVED_MODULES = (
    # The nine-module persistence store (ADR-0029 decision 6).
    "alphalab.persistence.protocol",
    "alphalab.persistence.storage",
    "alphalab.persistence.state",
    "alphalab.persistence.engine",
    "alphalab.persistence.adapter",
    "alphalab.persistence.snapshot",
    "alphalab.persistence.views",
    "alphalab.persistence.validation",
    "alphalab.persistence.events",
    # The ten-module orphan runtime lifecycle state machine (ADR-0030).
    "alphalab.runtime.engine",
    "alphalab.runtime.dispatcher",
    "alphalab.runtime.supervisor",
    "alphalab.runtime.events",
    "alphalab.runtime.validation",
    "alphalab.runtime.state",
    "alphalab.runtime.views",
    "alphalab.runtime.metrics",
    "alphalab.runtime.runtime",
    "alphalab.runtime.lifecycle",
)

#: ``package -> the names that must no longer be reachable through it``.
REMOVED_NAMES = {
    "alphalab.common": ("CommonEvent",),
    "alphalab.common.events": ("CommonEvent",),
    "alphalab.persistence": (
        "EventAppended",
        "EventsLoaded",
        "MemoryStorage",
        "MemoryStoreData",
        "PersistenceAdapter",
        "PersistenceEngine",
        "PersistenceProtocol",
        "PersistenceState",
        "PersistenceStatistics",
        "PersistenceSystemEvent",
        "Snapshot",
        "SnapshotLoaded",
        "SnapshotSaved",
        "StorageCleared",
        "StoredEvent",
        "event_count",
        "latest_snapshot",
        "snapshot_count",
        "storage_statistics",
        "validate_event_append",
        "validate_snapshot_load",
        "validate_snapshot_save",
    ),
    "alphalab.runtime": (
        "DispatchCompleted",
        "DispatchFailed",
        "EventDispatcher",
        "Heartbeat",
        "InvalidRuntimeTransitionError",
        "RuntimeEngine",
        "RuntimeEvent",
        "RuntimeFailed",
        "RuntimeMetrics",
        "RuntimePaused",
        "RuntimeResumed",
        "RuntimeStarted",
        "RuntimeState",
        "RuntimeStatus",
        "RuntimeStopped",
        "RuntimeSupervisor",
        "SupervisorState",
        "create_runtime",
        "dispatcher_statistics",
        "runtime_metrics",
        "runtime_status",
        "uptime",
        "validate_dispatch",
        "validate_heartbeat_config",
        "validate_transition",
    ),
    "alphalab.core": (
        "DomainEvent",
        "EventDispatcher",
        "EventHandler",
        "EventMiddleware",
        "EventNextHandler",
        "EventPipeline",
        "EventPriority",
        "EventRegistry",
        "LoggingMiddleware",
        "Metadata",
        "MetadataValue",
        "PriorityEventQueue",
        "ReplayEngine",
        "TimingMiddleware",
    ),
}

#: What had to survive beside each removal, because it shared the package.
SURVIVING_NEIGHBOURS = {
    # The persistence codec spine: every snapshot module in the repository
    # imports it, and it is the reason the store's notice was PEP 562 rather
    # than an import-time warning.
    "alphalab.persistence": (
        "serialize",
        "deserialize",
        "require",
        "require_schema_version",
        "as_mapping",
        "StateDecodeError",
        "SerializationError",
        "StorageError",
        "RunStateStore",
        "MemoryRunStateStore",
        "FileRunStateStore",
        "RunStateRef",
    ),
    # The canonical runtime surface, whose names the orphan machine collided
    # with. These are the reason the orphan modules were removed rather than
    # kept as harmless dead weight.
    "alphalab.runtime": (
        "AlphaLabRuntimeError",
        "ExecutionMode",
        "ExecutionPipeline",
        "ExecutionPipelineConfig",
        "ExecutionPipelineState",
        "ExecutionRouting",
        "LiveSession",
        "RunConfig",
        "RunEngine",
        "RunState",
        "RunStep",
        "RuntimeValidationError",
        "SkippedRecord",
        "TradingSession",
    ),
    "alphalab.common": ("BaseEvent", "AppendOnlyLog", "DEFAULT_SCHEMA_VERSION"),
    "alphalab.core": ("Fill", "Trade", "OrderRequest", "EventType", "Side"),
}


def _warnings_on_import(module: str) -> list[str]:
    """Every ``DeprecationWarning`` a *first* import of ``module`` emits.

    Run in a subprocess rather than by evicting entries from ``sys.modules``: a
    notice fires once per interpreter, so observing its absence needs a fresh
    one, and tearing ``alphalab`` out of the module cache in-process leaves
    every other test in the session holding stale classes.
    """

    source = (
        "import json, warnings\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        f"    __import__({module!r})\n"
        "print(json.dumps([\n"
        "    str(w.message) for w in caught\n"
        "    if issubclass(w.category, DeprecationWarning)\n"
        "]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=True
    )
    return list(json.loads(result.stdout))


# ---------------------------------------------------------------------------
# 1. The surfaces are gone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", REMOVED_PACKAGES)
def test_a_removed_package_cannot_be_imported(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_a_removed_module_cannot_be_imported(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


@pytest.mark.parametrize(
    ("package", "name"),
    [(pkg, name) for pkg, names in REMOVED_NAMES.items() for name in names],
    ids=lambda v: str(v),
)
def test_a_removed_name_is_absent_from_its_package(package: str, name: str) -> None:
    """Absent, and absent from ``__all__`` too -- not merely unexported."""

    module = importlib.import_module(package)

    with pytest.raises(AttributeError):
        getattr(module, name)

    assert name not in getattr(module, "__all__", ())


# ---------------------------------------------------------------------------
# 2. No alias survived the removal (ADR-0034, and Part D4 of the v2.17 contract)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("package", sorted(REMOVED_NAMES))
def test_no_package_serves_a_removed_name_through_a_getattr_hook(package: str) -> None:
    """A PEP 562 hook was the *notice* mechanism; a removal must not keep one.

    A ``__getattr__`` that resolved a removed name would be the compatibility
    alias ADR-0034 forbids, and it would be invisible to the ``__all__`` check
    above.
    """

    module = importlib.import_module(package)
    hook = module.__dict__.get("__getattr__")

    assert hook is None, (
        f"{package} still defines a module-level __getattr__. Every name it "
        "served is removed, so the hook can only be serving an alias."
    )


def test_no_surviving_module_re_exports_a_removed_name_under_any_spelling() -> None:
    """Sweep the whole package tree for the removed identities themselves.

    The ``__all__`` and ``getattr`` checks above catch a name kept under its own
    spelling. This catches the subtler shape: the same object re-exported under
    a *different* name, which would preserve the ambiguous architecture while
    passing every other assertion here.
    """

    forbidden = {name for names in REMOVED_NAMES.values() for name in names}
    # ``Metadata`` and ``MetadataValue`` are removed from ``alphalab.core`` only;
    # ``alphalab.common`` has always owned its own, and they are different types.
    forbidden -= {"Metadata", "MetadataValue", "EventDispatcher"}

    offenders: list[str] = []
    for info in pkgutil.walk_packages(alphalab.__path__, "alphalab."):
        module = importlib.import_module(info.name)
        for exported in getattr(module, "__all__", ()):
            value = getattr(module, exported, None)
            original = getattr(value, "__name__", None)
            if original in forbidden and original != exported:
                offenders.append(f"{info.name}.{exported} is {original}")

    assert not offenders, f"removed names re-exported under new spellings: {offenders}"


# ---------------------------------------------------------------------------
# 3. What had to survive, survived
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("package", "name"),
    [(pkg, name) for pkg, names in SURVIVING_NEIGHBOURS.items() for name in names],
    ids=lambda v: str(v),
)
def test_a_canonical_neighbour_of_a_removal_is_untouched(package: str, name: str) -> None:
    module = importlib.import_module(package)

    assert getattr(module, name, None) is not None
    assert name in module.__all__


def test_the_codec_spine_still_round_trips() -> None:
    """The half of ``alphalab.persistence`` that was always load-bearing."""

    from alphalab.persistence import deserialize, serialize

    assert deserialize(serialize({"a": 1})) == {"a": 1}


# ---------------------------------------------------------------------------
# 4. Nothing anywhere warns any more
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        "alphalab",
        "alphalab.common",
        "alphalab.core",
        "alphalab.persistence",
        "alphalab.runtime",
        "alphalab.allocation",
        "alphalab.oms",
        "alphalab.portfolio",
        "alphalab.runtime.execution_pipeline",
    ],
)
def test_importing_a_module_emits_no_deprecation_warning(module: str) -> None:
    offenders = _warnings_on_import(module)

    assert not offenders, f"importing {module} warned: {offenders}"


def test_importing_every_module_in_the_package_is_free_of_deprecation_warnings() -> None:
    """The whole tree, in one fresh interpreter, with the warning as an error.

    This is the repository-wide gate behind the release's ``pytest -q -W
    error::DeprecationWarning`` requirement: it proves no *module* warns, which
    the suite-level run cannot distinguish from no *test* touching one.
    """

    source = (
        "import importlib, pkgutil, warnings\n"
        "warnings.simplefilter('error', DeprecationWarning)\n"
        "import alphalab\n"
        "for info in pkgutil.walk_packages(alphalab.__path__, 'alphalab.'):\n"
        "    importlib.import_module(info.name)\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
