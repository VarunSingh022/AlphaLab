"""The schema versions the documentation states are the ones the code writes (ledger DOC-005).

``docs/ARCHITECTURE.md``, ``docs/STATE_MODEL.md`` and ``nowandfuture.md`` each
carry a table of the durable states and their schema constants. Nothing read
them against the code, and at v3.12.0 eight of the ten values in each were
stale: a reader was told the pipeline wrote version 3 or 4 when it wrote 6.
These tests read them.

Two directions are held. Every value a document states is the constant's value
now; and every schema constant the package defines is one these tests know,
so a new subsystem's constant cannot be added without the tables learning it.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Every schema constant the package defines, by the module that owns it.
CONSTANTS: dict[str, str] = {
    "OMS_SNAPSHOT_SCHEMA": "alphalab.oms.snapshot",
    "PORTFOLIO_SNAPSHOT_SCHEMA": "alphalab.portfolio.snapshot",
    "LIFECYCLE_SNAPSHOT_SCHEMA": "alphalab.lifecycle.snapshot",
    "ALLOCATION_SNAPSHOT_SCHEMA": "alphalab.allocation.snapshot",
    "PIPELINE_SNAPSHOT_SCHEMA": "alphalab.runtime.snapshot",
    "RUN_SNAPSHOT_SCHEMA": "alphalab.runtime.run_snapshot",
    "INSTRUMENT_SNAPSHOT_SCHEMA": "alphalab.instrument.snapshot",
    "BROKER_SNAPSHOT_SCHEMA": "alphalab.broker.snapshot",
    "LIVE_SNAPSHOT_SCHEMA": "alphalab.runtime.live_snapshot",
    "FX_FEED_SNAPSHOT_SCHEMA": "alphalab.portfolio.fx_feed",
    "RUN_STATE_ENVELOPE_SCHEMA": "alphalab.persistence.run_state",
    "CHECKPOINT_SCHEMA": "alphalab.runtime.checkpoint",
    "EVIDENCE_SCHEMA": "alphalab.model_registry.evidence",
}

#: The ten durable states' constants: every table must list each of them.
SNAPSHOT_CONSTANTS = tuple(name for name in CONSTANTS if name.endswith("_SNAPSHOT_SCHEMA"))

DOCUMENTS = ("docs/ARCHITECTURE.md", "docs/STATE_MODEL.md", "nowandfuture.md")

#: ``| ... | `NAME` | 7 |`` -- a table cell naming a constant, then its value.
_CELL = re.compile(r"\|\s*`(?P<name>[A-Z_]+_SCHEMA)`\s*\|\s*(?P<value>\d+)\s*\|")
#: ```module.NAME = 7``` -- a constant stated with its value, in a table or prose.
_ASSIGNED = re.compile(r"`(?:[a-z_.]+\.)?(?P<name>[A-Z_]+_SCHEMA) = (?P<value>\d+)`")
#: How a schema constant is defined in the package.
_DEFINED = re.compile(r"^(?P<name>[A-Z_]+_SCHEMA)(?:: Final)? = \d+$", re.MULTILINE)


def _value(name: str) -> int:
    value = getattr(importlib.import_module(CONSTANTS[name]), name)
    assert isinstance(value, int)
    return value


def _stated(document: str) -> list[tuple[str, int]]:
    text = (ROOT / document).read_text(encoding="utf-8")
    return [
        (match.group("name"), int(match.group("value")))
        for pattern in (_CELL, _ASSIGNED)
        for match in pattern.finditer(text)
    ]


@pytest.mark.parametrize("document", DOCUMENTS)
def test_every_schema_version_a_document_states_is_current(document: str) -> None:
    stated = _stated(document)
    assert stated, f"{document} states no schema version; the pattern no longer matches it"
    unknown = sorted({name for name, _ in stated} - set(CONSTANTS))
    assert not unknown, f"{document} names schema constants the package does not define: {unknown}"
    stale = [
        f"{name} = {value} (the code has {_value(name)})"
        for name, value in stated
        if value != _value(name)
    ]
    assert not stale, f"{document} states stale schema versions: {stale}"


@pytest.mark.parametrize("document", DOCUMENTS)
def test_every_table_lists_every_durable_state(document: str) -> None:
    listed = {name for name, _ in _stated(document)}
    assert set(SNAPSHOT_CONSTANTS) <= listed, sorted(set(SNAPSHOT_CONSTANTS) - listed)


def test_every_schema_constant_in_the_package_is_known_here() -> None:
    defined: dict[str, list[str]] = {}
    for path in sorted((ROOT / "alphalab").rglob("*.py")):
        for match in _DEFINED.finditer(path.read_text(encoding="utf-8")):
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            defined.setdefault(match.group("name"), []).append(module)

    assert defined == {name: [module] for name, module in CONSTANTS.items()}
