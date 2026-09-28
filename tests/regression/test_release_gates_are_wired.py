"""The gates a release is checked by are the gates CI and the hooks run.

Ledger TST-003 and TST-004. Until v3.10 CI ran the suite without ``-W error`` --
so a ResourceWarning from an unclosed socket scrolled past -- never ran an
example or a benchmark, and never installed what it built; and the pre-commit
hooks pinned ruff 0.12 and mypy 1.16 while ``pyproject.toml`` asked for ruff
0.16 and mypy 2, with no pytest in the mypy hook's environment, so a commit
could pass its hooks and fail CI. These tests read the configuration, because
the configuration is the gate.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
BENCHMARKS = ROOT / ".github" / "workflows" / "benchmarks.yml"
PRE_COMMIT = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
DEV = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "optional-dependencies"
]["dev"]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.lstrip("v").split("."))


def _satisfies(version: str, requirement: str) -> bool:
    """Whether ``version`` meets a ``name>=a,<b`` requirement from pyproject."""

    specifiers = re.sub(r"^[A-Za-z0-9_.-]+", "", requirement).split(",")
    for specifier in specifiers:
        match = re.fullmatch(r"(>=|<|==)([0-9.]+)", specifier.strip())
        assert match, f"unhandled specifier {specifier!r} in {requirement!r}"
        operator, bound = match.groups()
        if operator == ">=" and not _version(version) >= _version(bound):
            return False
        if operator == "<" and not _version(version) < _version(bound):
            return False
        if operator == "==" and _version(version) != _version(bound):
            return False
    return True


def _requirement(name: str) -> str:
    (found,) = [str(entry) for entry in DEV if re.match(rf"{name}\b", entry)]
    return found


def _hook_revision(repository: str) -> str:
    match = re.search(rf"repo: https://github.com/{repository}\n\s+rev: (\S+)", PRE_COMMIT)
    assert match, f"no pre-commit hook for {repository}"
    return match.group(1)


def test_ci_treats_warnings_as_errors() -> None:
    assert "python -m pytest -W error" in CI


def test_ci_runs_every_example() -> None:
    assert "examples/[0-9]*.py" in CI
    assert 'python -W error "${example}"' in CI


def test_ci_installs_what_it_built_into_clean_environments() -> None:
    for distribution in ("dist/*.whl", "dist/*.tar.gz"):
        assert f"pip install {distribution}" in CI
    assert CI.count("tests/installed_smoke.py") == 2
    assert (ROOT / "tests" / "installed_smoke.py").is_file()


def test_every_benchmark_runs_on_a_schedule() -> None:
    workflow = BENCHMARKS.read_text(encoding="utf-8")
    assert "schedule:" in workflow
    assert "benchmarks/benchmark_*.py" in workflow
    unmatched = [
        path.name
        for path in (ROOT / "benchmarks").glob("*.py")
        if not path.name.startswith("benchmark_")
    ]
    assert unmatched == [], f"benchmarks the scheduled run would not find: {unmatched}"


def test_the_hooks_run_the_tool_versions_ci_installs() -> None:
    ruff = _hook_revision("astral-sh/ruff-pre-commit")
    mypy = _hook_revision("pre-commit/mirrors-mypy")

    assert _satisfies(ruff, _requirement("ruff")), (ruff, _requirement("ruff"))
    assert _satisfies(mypy, _requirement("mypy")), (mypy, _requirement("mypy"))


def test_the_mypy_hook_checks_what_ci_checks_with_what_the_tests_import() -> None:
    hook = PRE_COMMIT[PRE_COMMIT.index("mirrors-mypy") :]

    assert "python -m mypy ." in CI
    assert 'args: ["."]' in hook
    assert re.search(r'additional_dependencies:\n\s+- "pytest', hook)
