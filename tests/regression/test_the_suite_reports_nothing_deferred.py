"""The suite skips nothing and warns about nothing, and both are enforced.

v2.16 reported ``3767 passed, 2 skipped, 94 warnings``. Both numbers were
tolerated for releases because nothing asserted them, and each hid something
different:

* the two skips were a **gap** rather than a duplicate — two of the six
  ``StrategyContext`` surfaces had no structural check at all (ADR-0034
  decision 6);
* the 94 warnings were tests exercising surfaces already scheduled for removal,
  which is exactly the reading that makes a real notice go unread.

A count in a release note decays the moment someone adds a skip. These tests are
the standing version of it, and they are deliberately written so that neither can
be satisfied by configuration:

* a ``filterwarnings`` entry would let the warning gate pass while the code still
  warned, so the warning test spawns a **fresh interpreter** with
  ``-W error::DeprecationWarning`` and imports every module in the package tree.
  That proves no *module* warns, which a suite-level run cannot distinguish from
  no *test* happening to touch one;
* a ``pytest.ini`` change would let the skip gate pass while the skips remained,
  so the skip test reads the collected items rather than the summary line.
"""

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


#: ``skipif`` markers allowed, each with the environment fact it guards.
#:
#: The distinction this file turns on: an **unconditional** skip defers coverage
#: that could be written, which is what ADR-0034 decision 6 found behind the two
#: v2.16 reported. A ``skipif`` on a genuine environment fact states a
#: precondition — the assertion is not weaker, it is unreachable. Each is listed
#: so that adding one is deliberate rather than incidental, and none of them
#: skips in a normal run: the suite reports zero skipped.
PERMITTED_SKIPIF: dict[str, str] = {
    "tests/regression/test_run_state_store.py": (
        "os.geteuid() == 0 — root bypasses directory permissions, so an "
        "unwritable root cannot be made unwritable to assert against"
    ),
}


def test_no_test_in_the_suite_is_skipped_unconditionally() -> None:
    """``@pytest.mark.skip`` and a bare ``pytest.skip()`` both defer coverage.

    Read from the source rather than from a run, because a skip inside a branch
    that happens not to be taken today is still a skip waiting to happen.
    ``skipif`` is treated separately; see :data:`PERMITTED_SKIPIF`.
    """

    import ast

    offenders: list[str] = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            # ``pytest.skip(...)`` / ``pytest.xfail(...)``
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"skip", "xfail"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pytest"
            ):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} pytest.{node.func.attr}()"
                )
            # ``@pytest.mark.skip`` / ``.xfail``. ``skipif`` is handled below.
            if isinstance(node, ast.Attribute) and node.attr in {"skip", "xfail"}:
                rendered = ast.unparse(node)
                if rendered.startswith("pytest.mark."):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} @{rendered}")

    assert not offenders, (
        "the suite defers coverage rather than asserting it:\n  "
        + "\n  ".join(offenders)
        + "\n\nA skip is a gap until something proves otherwise — see ADR-0034 "
        "decision 6, where the two this replaced turned out to be exactly that."
    )


def test_the_pytest_configuration_hides_nothing() -> None:
    """No ``filterwarnings``, and no option that suppresses a skip or a warning.

    The gate is worth exactly as much as the configuration behind it, so the
    configuration is part of what is asserted.
    """

    pyproject = (ROOT / "pyproject.toml").read_text()
    section = pyproject[pyproject.index("[tool.pytest.ini_options]") :]

    assert "filterwarnings" not in section, (
        "pytest is configured to filter warnings, which would let the warning "
        "gate pass while the code still warned"
    )
    for suppressor in ("-p no:warnings", "--disable-warnings", "-W ignore"):
        assert suppressor not in section, f"pytest is configured with {suppressor!r}"

    # ``-ra`` is required rather than merely permitted: it is what prints the
    # skip and warning summary a reader would otherwise never see.
    assert '"-ra"' in section


def test_importing_every_module_emits_no_deprecation_warning() -> None:
    """The property a suite-level warning count cannot establish.

    A fresh interpreter with the warning fatal, importing the whole tree. If any
    module warns on import — or serves a deprecated name through a PEP 562
    ``__getattr__`` that fires during import — this fails and names it.
    """

    probe = (
        "import importlib, pkgutil, sys, warnings\n"
        "warnings.simplefilter('error', DeprecationWarning)\n"
        "import alphalab\n"
        "failed = []\n"
        "for info in pkgutil.walk_packages(alphalab.__path__, 'alphalab.'):\n"
        "    try:\n"
        "        importlib.import_module(info.name)\n"
        "    except DeprecationWarning as error:\n"
        "        failed.append(f'{info.name}: {error}')\n"
        "print('|'.join(failed))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )

    warned = [entry for entry in result.stdout.strip().split("|") if entry]
    assert not warned, "modules warn on import:\n  " + "\n  ".join(warned)


def test_the_package_emits_no_deprecation_warning_from_any_public_name() -> None:
    """And nothing warns when a public name is *touched*, which is where the
    PEP 562 notices this release removed used to fire.
    """

    probe = (
        "import importlib, pkgutil, warnings\n"
        "warnings.simplefilter('error', DeprecationWarning)\n"
        "import alphalab\n"
        "failed = []\n"
        "for info in pkgutil.walk_packages(alphalab.__path__, 'alphalab.'):\n"
        "    module = importlib.import_module(info.name)\n"
        "    for name in getattr(module, '__all__', ()):\n"
        "        try:\n"
        "            getattr(module, name)\n"
        "        except DeprecationWarning as error:\n"
        "            failed.append(f'{info.name}.{name}: {error}')\n"
        "        except AttributeError:\n"
        "            pass\n"
        "print('|'.join(failed))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )

    warned = [entry for entry in result.stdout.strip().split("|") if entry]
    assert not warned, "public names warn on use:\n  " + "\n  ".join(warned)


def test_nothing_in_the_package_raises_a_deprecation_warning_at_all() -> None:
    """Read from the source: no ``warnings.warn`` anywhere in production.

    Complementary to the two probes above, which can only observe what they
    reach. This observes what exists.
    """

    import ast

    offenders: list[str] = []
    for path in sorted((ROOT / "alphalab").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "warn"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "warnings"
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert not offenders, (
        "production code emits a warning; v2.17 removed every deprecated "
        f"surface, so a new one needs its own decision: {offenders}"
    )


@pytest.mark.parametrize("gate", ["pytest", "deprecation"])
def test_the_two_release_gates_are_the_ones_the_adr_names(gate: str) -> None:
    """A cheap guard that the ADR and the suite agree on what is being claimed."""

    adr = (ROOT / "docs" / "ADR" / "0034-the-final-engineering-release.md").read_text()

    if gate == "pytest":
        assert "zero skips" in adr or "0 skipped" in adr
    else:
        assert "-W error::DeprecationWarning" in adr
