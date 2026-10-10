"""Nothing required is left for a later release (v3.13, ledger DOC-008).

v3.13 is the last release before the v4.0 freeze, and its claim is that every
entry of the pre-v4 ledger is implemented or kept with its reason, and that
nothing is assigned beyond it. When v3.13 re-read the current-state documents,
``docs/ARCHITECTURE.md``'s Implementation Status -- the section it calls
authoritative -- still called three rows "deliberately deferred": execution-path
delivery of external information (delivered in v3.12), construction's estimated,
EWMA and factor-model covariances (v3.11), and five execution capabilities
(v3.11 to v3.13). Nothing held the ledger's own claim either. These tests hold
both: the ledger, and the documents that describe the current state.
"""

from __future__ import annotations

import re
from pathlib import Path

import alphalab

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs" / "audit" / "PRE_V4_COMPLETION_LEDGER.yaml"

#: The documents that state what is built now, as opposed to what an ADR decided.
CURRENT_STATE = (
    "README.md",
    "docs/ARCHITECTURE.md",
    "docs/README.md",
    "ROADMAP.md",
    "nowandfuture.md",
)

#: How a closed entry's status begins: delivered, or kept with its reason.
CLOSED = (
    "implemented (",
    "kept as a boundary",
    "kept as a stated limitation",
    "kept as an external boundary",
)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.lstrip("v").split("."))


def _entries() -> list[tuple[str, str, str]]:
    """``(id, implementation status, release)`` of every ledger entry."""

    found = []
    for entry in LEDGER.read_text(encoding="utf-8").split('\n  - id: "')[1:]:
        status = re.search(r'\n    implementation_status: "([^"]*)"', entry)
        release = re.search(r'\n    release: "([^"]*)"', entry)
        assert status is not None and release is not None, entry[:40]
        found.append((entry.split('"', 1)[0], status.group(1), release.group(1)))
    return found


def test_the_ledger_is_read_whole() -> None:
    """A guard on the guard: every entry is parsed, and the count is the ledger's."""

    entries = _entries()
    text = LEDGER.read_text(encoding="utf-8")
    assert len(entries) == text.count('\n  - id: "') >= 215
    assert len({identifier for identifier, _, _ in entries}) == len(entries)


def test_every_ledger_entry_is_implemented_or_kept_with_its_reason() -> None:
    still_open = [(i, s) for i, s, _ in _entries() if not s.startswith(CLOSED)]
    assert not still_open, f"open ledger entries: {still_open}"


def test_no_ledger_entry_is_assigned_beyond_this_release() -> None:
    current = _version(alphalab.__version__)
    later = [(i, r) for i, _, r in _entries() if _version(r) > current]
    assert not later, f"assigned to a later release: {later}"


def test_no_current_state_document_calls_anything_deferred() -> None:
    offenders = []
    for document in CURRENT_STATE:
        text = (ROOT / document).read_text(encoding="utf-8")
        for match in re.finditer(r"not implemented, deliberately deferred", text, re.IGNORECASE):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{document}:{line}")
    assert not offenders, f"a deferral the ledger does not hold: {offenders}"


def test_every_item_roadmap_planned_before_v4_is_delivered() -> None:
    text = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    section = text.split("# Planned before v4", 1)[1].split("\n# ", 1)[0]
    rows = [line for line in section.splitlines() if re.match(r"^\| .* \| [A-Z]{3}-\d{3} \|", line)]
    assert rows, "the planned-before-v4 table moved; this guard reads it"
    undelivered = [row for row in rows if "delivered" not in row.rsplit("|", 2)[-2]]
    assert not undelivered, f"planned and not delivered: {undelivered}"
