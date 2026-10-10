"""Every test the completion ledger cites exists, down to the named test (v3.13).

``docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`` records, for each finding, the
test that holds its fix (``test_status``). Nothing checked those references,
and they rot quietly: the v3.12 release gates found DET-002 still naming a test
deleted with the optimizer, and renaming a test file in v3.13 broke two more
the moment it happened. This reads every ``tests/...py`` reference anywhere in
the ledger -- a file, optionally ``::test`` or ``::Class::test`` -- and fails
on one that names nothing.

The ledger is read as text: its layout is fixed by the generator that writes
it (one ``- id:`` per finding, scalar fields on one line), and the suite does
not depend on a YAML parser for one check.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs" / "audit" / "PRE_V4_COMPLETION_LEDGER.yaml"

_REFERENCE = re.compile(r"(tests/[\w/]+\.py)(?:::([\w\[\]\-.]+))?(?:::(\w+))?")


def _entries() -> dict[str, str]:
    text = LEDGER.read_text(encoding="utf-8")
    entries = text.split('\n  - id: "')[1:]
    return {entry.split('"', 1)[0]: entry for entry in entries}


def test_the_ledger_is_read_whole() -> None:
    entries = _entries()
    assert len(entries) == LEDGER.read_text(encoding="utf-8").count('\n  - id: "')
    assert len(entries) >= 194
    assert all(re.fullmatch(r"[A-Z]+-\d{3}", finding) for finding in entries)


def test_every_cited_test_exists() -> None:
    problems: list[str] = []
    checked = 0
    for finding, entry in _entries().items():
        for path, first, second in _REFERENCE.findall(entry):
            checked += 1
            source_path = ROOT / path
            if not source_path.is_file():
                problems.append(f"{finding}: {path} does not exist")
                continue
            source = source_path.read_text(encoding="utf-8")
            for name in (first, second):
                if not name:
                    continue
                base = name.split("[", 1)[0]
                if not re.search(
                    rf"^\s*(?:async\s+)?(?:def|class) {re.escape(base)}\b", source, re.M
                ):
                    problems.append(f"{finding}: {path} defines no {base}")
    assert checked >= 200, f"only {checked} references found; the ledger's layout has changed"
    assert not problems, "\n".join(problems)
