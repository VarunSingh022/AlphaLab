"""The sdist carries every top-level file and directory a test reads (v4.0).

The source distribution ships ``tests/``, and until v4.0 it did not ship what
those tests read: ``CHANGELOG.md``, ``ROADMAP.md``, ``nowandfuture.md``, the CI
workflows, ``.gitattributes`` and ``.pre-commit-config.yaml``. Unpacked and
tested, the 4.0.0 candidate's sdist failed fifteen tests and could not collect a
sixteenth -- nothing wrong with the engine, everything wrong with the archive.
This reads every string literal in the test suite, takes the first component of
each that names an entry of the repository root, and requires the sdist's
``include`` list to carry it.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: What a working copy -- or an unpacked sdist -- holds that is neither source nor
#: read by a test: caches, environments, build output, and ``PKG-INFO``, which the
#: build writes into the sdist itself (a test that builds an archive names it).
_LOCAL = {
    "PKG-INFO",
    ".DS_Store",
    ".coverage",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "htmlcov",
    "venv",
}


def _included() -> set[str]:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entries = configuration["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    return {entry.strip("/").split("/", 1)[0] for entry in entries}


def _read_by_tests() -> set[str]:
    present = {
        entry.name
        for entry in ROOT.iterdir()
        if entry.name not in _LOCAL and not entry.name.endswith(".egg-info")
    }
    found: set[str] = set()
    for path in (ROOT / "tests").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                first = node.value.split("/", 1)[0]
                if first in present:
                    found.add(first)
    return found


def test_the_reading_finds_what_the_tests_are_known_to_read() -> None:
    assert {"CHANGELOG.md", "ROADMAP.md", "nowandfuture.md", "pyproject.toml"} <= _read_by_tests()


def test_every_top_level_entry_a_test_reads_is_in_the_sdist() -> None:
    missing = sorted(_read_by_tests() - _included())
    assert not missing, (
        f"tests read {missing} from the repository root, and pyproject.toml's sdist include "
        "list does not carry them: the sdist's own tests would fail"
    )
