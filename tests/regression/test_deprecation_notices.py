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

#: Target -> the release that removes it. Nothing is removed in v2.6.
REMOVAL_RELEASE = {
    "alphalab.integrations": "v3.0",
    "alphalab.kernel": "v3.0",
    "alphalab.common.CommonEvent": "v3.0",
    "alphalab.core.events": "v3.0",
}


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


def test_nothing_is_actually_removed_in_this_release() -> None:
    """A deprecation notice is a notice, not a removal."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import alphalab.integrations
        import alphalab.kernel

    assert alphalab.kernel.StateStore is not None
    assert alphalab.integrations.__all__
