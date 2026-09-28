"""Smoke check for an *installed* AlphaLab distribution (ledger TST-003).

Run with an interpreter whose environment holds the built wheel or sdist and
nothing else, from a directory outside the source tree::

    python /path/to/tests/installed_smoke.py 3.10.0

It checks what the test suite cannot, because the suite runs against the source
tree: that the distribution carries every module the source has, that it reports
the version it was built as, that it ships its type marker, that importing any
of it warns about nothing, and that a backtest runs end to end from it. CI runs
it against the wheel and against the sdist, each in a fresh virtual environment.

Not collected by pytest (the name does not start with ``test_``): against the
source tree it would prove nothing.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
import sys
import warnings
from pathlib import Path

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


def main(expected_version: str) -> None:
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

    # End to end, from the installed package: the unified backtest example,
    # which drives a dataset through strategy, allocation, risk, OMS,
    # execution and portfolio, and checks a replay against the backtest.
    example = SOURCE_TREE / "examples" / "11_unified_backtest.py"
    sys.path.insert(0, str(example.parent))
    namespace: dict[str, object] = {"__name__": "installed_smoke_example"}
    exec(compile(example.read_text(encoding="utf-8"), str(example), "exec"), namespace)
    run = namespace["main"]
    assert callable(run)
    run()

    print(
        f"installed alphalab {installed} at {location.parent}: "
        f"{len(imported)} modules, py.typed present, example 11 ran"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: installed_smoke.py EXPECTED_VERSION")
    main(sys.argv[1])
