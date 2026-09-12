"""Deprecation notices are sized to blast radius, and stay alive.

v2.6 removes nothing. It says, at the smallest surface that reaches the people
who would care, that four things go in v3.0.

The mechanism differs per target because the cost of a warning differs per
target. A package with no production importer can warn at import: the warning
reaches exactly the callers who import it. ``alphalab.core.events`` deliberately
warns **nowhere**, because ``alphalab.core`` re-exports thirteen of its symbols
eagerly, so an import-time warning there would fire on the canonical core
package -- which the whole execution path depends on -- for every consumer, on
every run. ``CommonEvent`` warns on *use* through PEP 562, because nearly
everything imports ``alphalab.common`` for ``BaseEvent``.

A warning that fires where nobody can act on it is how people learn to filter
DeprecationWarning, and then the next real deprecation goes unread.
"""

import json
import subprocess
import sys
import warnings

import pytest

#: Target -> the release that removes it. Nothing is removed when it is listed.
REMOVAL_RELEASE = {
    "alphalab.integrations": "v3.0",
    "alphalab.kernel": "v3.0",
    "alphalab.common.CommonEvent": "v3.0",
    "alphalab.core.events": "v3.0",
    # v2.13, ADR-0029: the original nine-module store, replaced by RunStateStore.
    # Same mechanism as CommonEvent and for the same reason -- this package has
    # production importers of its codec spine, so the notice cannot be at import.
    "alphalab.persistence.store": "v3.0",
    # v2.14, ADR-0030: the orphan lifecycle state machine in alphalab.runtime,
    # and alphalab.production. Both take the PEP 562 mechanism, and the runtime
    # one must: that package *is* the execution path, so an import-time warning
    # would fire on every consumer of ExecutionPipeline.
    "alphalab.runtime.orphan": "v3.0",
    "alphalab.production": "v3.0",
}

#: The names ADR-0030 deprecates on the ``alphalab.runtime`` surface, and the
#: module still defining each. The canonical names beside them -- ``RunEngine``,
#: ``ExecutionPipeline``, ``RuntimeValidationError`` -- must stay silent.
DEPRECATED_RUNTIME_NAMES = (
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
)

#: The canonical runtime surface, which must never warn. These are the reason
#: the notice is PEP 562 rather than an import-time warning.
CANONICAL_RUNTIME_NAMES = (
    "AlphaLabRuntimeError",
    "ExecutionMode",
    "ExecutionPipeline",
    "ExecutionPipelineConfig",
    "ExecutionPipelineState",
    "ExecutionRouting",
    "RunConfig",
    "RunEngine",
    "RunState",
    "RunStep",
    "RuntimeValidationError",
    "SkippedRecord",
    "TradingSession",
)

#: The names ADR-0029 decision 6 deprecates, and the module still defining each.
DEPRECATED_STORE_NAMES = (
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
)

#: The codec spine, which is canonical and must stay silent.
CODEC_SPINE_NAMES = (
    "serialize",
    "deserialize",
    "require",
    "require_schema_version",
    "as_mapping",
    "StateDecodeError",
    "SerializationError",
)


def _warnings_on_import(module: str) -> list[str]:
    """Every ``DeprecationWarning`` a *first* import of ``module`` emits.

    Run in a subprocess rather than by evicting entries from ``sys.modules``.
    A deprecation notice fires once per interpreter, so observing it needs a
    fresh one -- and tearing ``alphalab`` out of the module cache in-process
    leaves every other test in the session holding stale classes.
    """

    source = (
        "import json, sys, warnings\n"
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


@pytest.mark.parametrize("module", ["alphalab.integrations", "alphalab.kernel"])
def test_a_deprecated_package_warns_on_import(module: str) -> None:
    messages = _warnings_on_import(module)

    assert messages, f"{module} no longer warns; the notice has lapsed"
    assert len(messages) == 1, f"{module} warns more than once"
    assert module in messages[0]
    assert REMOVAL_RELEASE[module] in messages[0], "a notice must name the removal release"


def test_common_event_warns_exactly_once_on_attribute_access() -> None:
    import alphalab.common

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        alphalab.common.CommonEvent  # noqa: B018

    matching = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(matching) == 1
    assert "v3.0" in str(matching[0].message), "a notice must name the removal release"
    assert "BaseEvent" in str(matching[0].message), "a notice must name the replacement"


def test_common_event_warns_when_imported_by_name() -> None:
    """``from ... import`` warns too, and may warn twice.

    ``_handle_fromlist`` consults the module attribute once to decide whether
    the name is a submodule and once to bind it, so a PEP 562 hook runs twice.
    That is CPython's import machinery, not something this module controls, and
    Python's default filter shows one warning per location regardless. The
    assertion is therefore on the content, not on a count we do not own.
    """

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from alphalab.common import CommonEvent

    assert CommonEvent is not None
    matching = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert matching, "importing the name by hand must still warn"
    assert all("v3.0" in str(w.message) for w in matching)


def test_common_event_is_still_the_type_it_always_was() -> None:
    """Deprecated, not broken."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from alphalab.common import CommonEvent
    from alphalab.common.events import CommonEvent as Direct

    assert CommonEvent is Direct


def test_an_unknown_attribute_still_raises_attribute_error() -> None:
    """The PEP 562 hook must not swallow ordinary typos."""

    import alphalab.common

    with pytest.raises(AttributeError, match="no attribute 'CommonEvnet'"):
        alphalab.common.CommonEvnet  # noqa: B018


# ---------------------------------------------------------------------------
# The surfaces that must stay silent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        "alphalab",
        "alphalab.common",
        "alphalab.core",
        "alphalab.core.events",
        "alphalab.persistence",
        "alphalab.allocation",
        "alphalab.oms",
        "alphalab.portfolio",
        "alphalab.runtime.execution_pipeline",
    ],
)
def test_importing_a_live_module_emits_no_deprecation_warning(module: str) -> None:
    """``alphalab.core`` is the one this exists for.

    It re-exports ``DomainEvent``, ``EventPipeline`` and eleven more from
    ``alphalab.core.events`` at its own line 5. A warning inside that subpackage
    would therefore fire on every ``import alphalab.core`` -- for every consumer
    on the execution path, none of which uses the deprecated pipeline.
    """

    offenders = _warnings_on_import(module)

    assert not offenders, f"importing {module} warned: {offenders}"


def test_a_clean_interpreter_importing_core_is_silent() -> None:
    """Belt and braces: no warning even with the module cache empty."""

    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::DeprecationWarning",
            "-c",
            "import alphalab.core; import alphalab.common; print('ok')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# ---------------------------------------------------------------------------
# v2.13: the original persistence store (ADR-0029 decision 6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", DEPRECATED_STORE_NAMES)
def test_a_deprecated_store_name_warns_on_attribute_access(name: str) -> None:
    import alphalab.persistence

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        getattr(alphalab.persistence, name)

    matching = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(matching) == 1, f"{name} warned {len(matching)} times"
    message = str(matching[0].message)
    assert name in message
    assert REMOVAL_RELEASE["alphalab.persistence.store"] in message, "name the removal release"
    assert "RunStateStore" in message, "a notice must name the replacement"


@pytest.mark.parametrize("name", CODEC_SPINE_NAMES)
def test_the_codec_spine_never_warns(name: str) -> None:
    """The reason the notice is PEP 562 and not at import.

    Every snapshot module in the repository imports one of these. A warning on
    this package's import would fire across the whole execution path to
    deprecate names that path never touches.
    """

    import alphalab.persistence

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        getattr(alphalab.persistence, name)

    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]


def test_importing_a_deprecated_store_module_directly_is_silent() -> None:
    """The notice guards the package surface, as ``alphalab.common.events`` is unguarded."""

    offenders = _warnings_on_import("alphalab.persistence.storage")

    assert not offenders, f"the submodule warned: {offenders}"


def test_a_deprecated_store_name_is_the_object_it_always_was() -> None:
    """Deprecated, not aliased and not shimmed."""

    import alphalab.persistence
    from alphalab.persistence.storage import MemoryStorage as Direct

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        served = alphalab.persistence.MemoryStorage

    assert served is Direct


def test_an_unknown_persistence_attribute_still_raises_attribute_error() -> None:
    """The PEP 562 hook must not swallow ordinary typos."""

    import alphalab.persistence

    with pytest.raises(AttributeError, match="no attribute 'MemoryStorge'"):
        alphalab.persistence.MemoryStorge  # noqa: B018


def test_the_new_store_boundary_is_not_deprecated() -> None:
    """``RunStateStore`` is a replacement, not a renamed ``PersistenceProtocol``."""

    import alphalab.persistence

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert alphalab.persistence.RunStateStore is not None
        assert alphalab.persistence.FileRunStateStore is not None
        assert alphalab.persistence.MemoryRunStateStore is not None
        assert alphalab.persistence.RunStateRef is not None

    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]


def test_nothing_is_actually_removed_in_this_release() -> None:
    """A deprecation notice is a notice, not a removal."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import alphalab.integrations
        import alphalab.kernel

    assert alphalab.kernel.StateStore is not None
    assert alphalab.integrations.__all__

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import alphalab.persistence

        assert alphalab.persistence.MemoryStorage is not None
        assert alphalab.persistence.PersistenceProtocol is not None
        assert alphalab.persistence.PersistenceEngine.initialize("still-here") is not None


# ---------------------------------------------------------------------------
# v2.14: the orphan runtime state machine and alphalab.production (ADR-0030)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", DEPRECATED_RUNTIME_NAMES)
def test_a_deprecated_runtime_name_warns_on_attribute_access(name: str) -> None:
    import alphalab.runtime

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        getattr(alphalab.runtime, name)

    matching = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(matching) == 1, f"{name} warned {len(matching)} times"
    message = str(matching[0].message)
    assert name in message
    assert REMOVAL_RELEASE["alphalab.runtime.orphan"] in message, "name the removal release"
    assert "RunEngine" in message, "a notice must name the replacement"


@pytest.mark.parametrize("name", CANONICAL_RUNTIME_NAMES)
def test_the_canonical_runtime_surface_never_warns(name: str) -> None:
    """The reason this notice is PEP 562 and not at import.

    ``alphalab.runtime`` *is* the execution path. A warning on its import would
    fire for every consumer of ``ExecutionPipeline`` and ``RunEngine`` to
    deprecate a state machine neither has ever touched.
    """

    import alphalab.runtime

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        getattr(alphalab.runtime, name)

    offenders = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not offenders, f"{name} is canonical and must not warn: {offenders}"


def test_a_deprecated_runtime_name_is_still_the_object_it_always_was() -> None:
    """Deprecated, not broken, and not an alias."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import alphalab.runtime

        served = alphalab.runtime.RuntimeEngine
    from alphalab.runtime.engine import RuntimeEngine as Direct

    assert served is Direct


def test_importing_a_deprecated_runtime_submodule_directly_is_silent() -> None:
    """The notice guards the package surface, not the submodule."""

    assert not _warnings_on_import("alphalab.runtime.engine")
    assert not _warnings_on_import("alphalab.runtime.state")


def test_an_unknown_runtime_attribute_still_raises_attribute_error() -> None:
    import alphalab.runtime

    with pytest.raises(AttributeError, match="no attribute 'RunEngien'"):
        alphalab.runtime.RunEngien  # noqa: B018


def test_importing_the_runtime_package_emits_no_deprecation_warning() -> None:
    """It is the execution path; importing it must stay silent."""

    assert not _warnings_on_import("alphalab.runtime")
    assert not _warnings_on_import("alphalab.runtime.run")
    assert not _warnings_on_import("alphalab.runtime.run_snapshot")


def test_a_production_name_warns_on_attribute_access() -> None:
    import alphalab.production

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        alphalab.production.Checkpoint  # noqa: B018

    matching = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(matching) == 1
    message = str(matching[0].message)
    assert REMOVAL_RELEASE["alphalab.production"] in message, "name the removal release"
    assert "RunStateStore" in message, "a notice must name the replacement"


def test_importing_the_production_package_emits_no_deprecation_warning() -> None:
    """Consistent with every other PEP 562 target here: the notice is on use."""

    assert not _warnings_on_import("alphalab.production")


def test_every_production_public_name_is_served_with_a_notice() -> None:
    """No name slips out of the package without one."""

    import alphalab.production

    for name in alphalab.production.__all__:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            getattr(alphalab.production, name)
        matching = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(matching) == 1, f"alphalab.production.{name} did not warn"
