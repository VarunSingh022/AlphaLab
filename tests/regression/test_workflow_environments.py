"""Each workflow job installs what the code it runs imports (v4.1).

v4.0's benchmark job installed the package alone, and
``benchmarks/benchmark_strategy_runtime.py`` imported a helper from a test
module, which imports ``pytest``: the job failed with ``ModuleNotFoundError``
on the first hosted run, after every local gate had passed -- locally the
development environment had ``pytest``. The fix then was to install the
``dev`` extra; v4.1 also moved the helpers to a module that imports no test
dependency. These tests hold the rule both ways, by reading the code rather
than by running it in a bare environment:

* every module a benchmark imports -- following its imports through
  ``tests``, ``benchmarks`` and ``examples`` -- is the standard library,
  ``alphalab``, or a distribution ``benchmarks.yml`` installs;
* every example the installed smoke test runs from a bare environment, holding
  the built artifact and nothing else, imports nothing beyond the standard
  library and ``alphalab``.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

#: The module each development requirement provides, by distribution name. A
#: requirement added to the ``dev`` extra without a row here fails
#: ``test_every_dev_requirement_names_the_module_it_provides``.
PROVIDES = {
    "build": "build",
    "hatchling": "hatchling",
    "mypy": "mypy",
    "pre-commit": "pre_commit",
    "pytest": "pytest",
    "pytest-cov": "pytest_cov",
    "ruff": "ruff",
    "twine": "twine",
}

#: Top-level names that are this repository's own code.
LOCAL = {"alphalab", "benchmarks", "examples", "tests", "docs"}


def _dev_requirements() -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return [
        re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0]
        for requirement in project["optional-dependencies"]["dev"]
    ]


def _module_file(name: str) -> Path | None:
    """The file of a repository module named ``name``, if the repository defines one."""

    base = ROOT.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _external_closure(path: Path) -> set[str]:
    """Top-level names of every non-repository module ``path`` reaches, following repository ones.

    ``alphalab`` is not followed: it declares no runtime dependency, which
    ``installed_smoke.py`` checks on the built artifact.
    """

    seen: set[Path] = set()
    pending = [path]
    external: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for name in _imports(current):
            top = name.split(".")[0]
            if top == "alphalab":
                continue
            sibling = current.parent / f"{top}.py"
            if sibling.is_file():
                # A script's own directory is first on sys.path, so a sibling
                # module (benchmarks/_stable_timing.py) is the repository's.
                pending.append(sibling)
                continue
            if top in LOCAL:
                module = _module_file(name)
                if module is not None:
                    pending.append(module)
                continue
            if top in sys.stdlib_module_names or top == "__future__":
                continue
            external.add(top)
    return external


def _installed_by(workflow: str, job: str) -> set[str]:
    """The modules a job's ``pip install`` lines make available, beyond the package itself."""

    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    section = text.split(f"\n  {job}:\n", 1)[1]
    following = re.search(r"\n  [a-z_-]+:\n", section)
    section = section[: following.start()] if following else section
    installs = re.findall(r"pip install (.+)", section)
    assert installs, f"{workflow}'s {job} job installs nothing; this test reads its pip lines"
    modules: set[str] = set()
    for line in installs:
        if '".[dev]"' in line or "'.[dev]'" in line:
            modules.update(PROVIDES[requirement] for requirement in _dev_requirements())
    return modules


def test_every_dev_requirement_names_the_module_it_provides() -> None:
    assert sorted(_dev_requirements()) == sorted(PROVIDES)


def test_the_closure_follows_repository_imports_to_a_test_dependency() -> None:
    """A guard on the guard: the import v4.0's benchmark job failed on is found."""

    probe = ROOT / "tests" / "unit" / "strategy" / "test_strategy.py"
    assert "pytest" in _external_closure(probe)
    assert _installed_by("benchmarks.yml", "benchmarks") >= {"pytest"}


@pytest.mark.parametrize(
    "benchmark", sorted(path.name for path in (ROOT / "benchmarks").glob("benchmark_*.py"))
)
def test_the_benchmark_job_installs_what_every_benchmark_imports(benchmark: str) -> None:
    missing = _external_closure(ROOT / "benchmarks" / benchmark) - _installed_by(
        "benchmarks.yml", "benchmarks"
    )
    assert not missing, (
        f"benchmarks.yml does not install {sorted(missing)}, which {benchmark} imports"
    )


def test_no_benchmark_imports_a_test_module() -> None:
    """Shared helpers live beside the tests, in modules that are not tests (v4.1)."""

    offenders = {
        path.name: sorted(name for name in _imports(path) if re.search(r"\.test_\w+", name))
        for path in sorted((ROOT / "benchmarks").glob("*.py"))
    }
    assert {name: found for name, found in offenders.items() if found} == {}


def _smoke_examples() -> list[str]:
    smoke = (ROOT / "tests" / "installed_smoke.py").read_text(encoding="utf-8")
    names = re.findall(r'"(\d\d_\w+\.py)"', smoke)
    assert names, "installed_smoke.py names the examples it runs; this test reads them"
    return names


@pytest.mark.parametrize("example", _smoke_examples())
def test_every_example_the_installed_smoke_runs_needs_nothing_but_the_package(example: str) -> None:
    """The smoke environment holds the built artifact and nothing else."""

    assert _external_closure(ROOT / "examples" / example) == set()
