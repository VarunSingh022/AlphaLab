"""Smoke check for an *installed* AlphaLab distribution (ledger TST-003).

Run with an interpreter whose environment holds the built wheel or sdist and
nothing else, from a directory outside the source tree::

    python /path/to/tests/installed_smoke.py 3.11.0

It checks what the test suite cannot, because the suite runs against the source
tree: that the distribution carries every module the source has, that it reports
the version it was built as, that it ships its type marker, that importing any
of it warns about nothing, and that a backtest runs end to end from it. CI runs
it against the wheel and against the sdist, each in a fresh virtual environment.

Since v4.0 it also checks what an application depending on the release relies
on (``docs/INTEGRATION.md``): ``pip check`` finds nothing broken, the
distribution declares no runtime requirement, every name the public-API
manifest records is present with the binding it records, and the examples run
with every socket operation refused -- the engine needs no network.

Not collected by pytest (the name does not start with ``test_``): against the
source tree it would prove nothing.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import pkgutil
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

SOURCE_TREE = Path(__file__).resolve().parents[1]


def _source_modules() -> set[str]:
    """Every module the source tree defines, by dotted name."""

    package = SOURCE_TREE / "alphalab"
    names = set()
    for path in package.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(SOURCE_TREE).with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        names.add(".".join(parts))
    return names


def _check_public_api() -> int:
    """Every name ``docs/api/public_api.json`` records, present in the installed package.

    Each binding is recomputed with the manifest generator's own ``describe``, so
    a name that is present but re-signed fails as surely as one that is absent.
    """

    spec = importlib.util.spec_from_file_location(
        "installed_smoke_manifest", SOURCE_TREE / "docs" / "api" / "generate_public_api.py"
    )
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    manifest = json.loads(
        (SOURCE_TREE / "docs" / "api" / "public_api.json").read_text(encoding="utf-8")
    )
    names = 0
    for module_name, exports in manifest["packages"].items():
        module = importlib.import_module(module_name)
        for name, binding in exports.items():
            assert hasattr(module, name), f"{module_name}.{name} is missing from the distribution"
            found = generator.describe(getattr(module, name))
            assert found == binding, f"{module_name}.{name} is {found}, the manifest says {binding}"
            names += 1
    return names


def _refuse_the_network(event: str, args: tuple[Any, ...]) -> None:
    if event.startswith(("socket.", "http.client.connect", "urllib.Request", "ssl.")):
        raise PermissionError(f"the installed package reached for the network: {event}")


def main(expected_version: str) -> None:
    checked = subprocess.run(
        [sys.executable, "-m", "pip", "check"], capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, f"pip check: {checked.stdout}{checked.stderr}"

    import alphalab

    location = Path(alphalab.__file__).resolve()
    assert SOURCE_TREE not in location.parents, (
        f"alphalab was imported from the source tree ({location}), not from the "
        "installed distribution; run this from outside the checkout"
    )

    assert alphalab.__version__ == expected_version, (
        f"the package reports {alphalab.__version__!r}, expected {expected_version!r}"
    )
    installed = importlib.metadata.version("alphalab")
    assert installed == expected_version, (
        f"the installed metadata says {installed!r}, expected {expected_version!r}"
    )
    assert (location.parent / "py.typed").is_file(), "the distribution lacks py.typed"
    runtime = [
        requirement
        for requirement in importlib.metadata.requires("alphalab") or []
        if "extra ==" not in requirement
    ]
    assert not runtime, f"the distribution declares runtime requirements: {runtime}"

    imported = {"alphalab"}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for info in pkgutil.walk_packages(alphalab.__path__, "alphalab."):
            importlib.import_module(info.name)
            imported.add(info.name)

    missing = _source_modules() - imported
    assert not missing, f"the distribution lacks modules the source has: {sorted(missing)}"
    extra = imported - _source_modules()
    assert not extra, f"the distribution carries modules the source does not: {sorted(extra)}"

    public = _check_public_api()

    # End to end, from the installed package: the unified backtest example,
    # which drives a dataset through strategy, allocation, risk, OMS,
    # execution and portfolio, and checks a replay against the backtest; and
    # (v4.0) the strategy-building acceptance example, which ingests data,
    # starts a strategy through the public helpers, checks every fill against
    # arithmetic done by hand, refuses invalid input and continues a run from
    # its snapshot byte for byte.
    # (v4.0) with the network refused: an audit hook stays for the process.
    sys.addaudithook(_refuse_the_network)
    sys.path.insert(0, str(SOURCE_TREE / "examples"))
    for name in ("11_unified_backtest.py", "70_build_a_strategy.py"):
        # Loaded as a module, registered first: a dataclass with postponed
        # annotations looks its module up in sys.modules while it is built.
        spec = importlib.util.spec_from_file_location(
            f"installed_smoke_{name[:2]}", (SOURCE_TREE / "examples" / name)
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        run = module.main
        assert callable(run)
        run()

    print(
        f"installed alphalab {installed} at {location.parent}: pip check clean, no runtime "
        f"requirement, {len(imported)} modules, {public} public names as the manifest records, "
        "py.typed present, examples 11 and 70 ran with the network refused"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: installed_smoke.py EXPECTED_VERSION")
    main(sys.argv[1])
