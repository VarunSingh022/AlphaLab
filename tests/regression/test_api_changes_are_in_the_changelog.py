"""Every public name removed or changed since the last release is named in this release's CHANGELOG.

Until v3.13 a migration table was as complete as its author's memory: the v3.12
release found a table row for a function new in that release and none for three
restated signatures, and only a hand-run API diff caught it (ledger DOC-006).
The public API is data now (``docs/api/public_api.json``, ledger API-002), and
each release's manifest is kept under ``docs/api/history/``; so the diff is
mechanical. A name the previous release exported and this one does not, and a
name whose binding changed -- its signature, its fields, an enum's members --
must each appear in this release's section of ``CHANGELOG.md``, in backticks.

A module that moved but is exported under the same name with the same
signature is not a change a caller sees, and is not required. Additions are not
required either: they break nobody.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import alphalab

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "docs" / "api" / "history"
CURRENT = ROOT / "docs" / "api" / "public_api.json"
CHANGELOG = ROOT / "CHANGELOG.md"

#: ``kind module.path.Name<rest>``: the kind, the defining path, and what follows.
_DESCRIPTION = re.compile(r"^(?P<kind>\w+) (?P<path>[\w.]+?)(?P<rest>[(\[].*)?$", re.S)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _previous() -> dict[str, Any]:
    """The newest recorded release older than this one."""

    current = _version(alphalab.__version__)
    older = sorted(
        (path for path in HISTORY.glob("*.json") if _version(path.stem) < current),
        key=lambda path: _version(path.stem),
    )
    assert older, f"docs/api/history holds no release older than {alphalab.__version__}"
    loaded = json.loads(older[-1].read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _seen_by_a_caller(description: str) -> str:
    """A binding as a caller sees it: its kind, its own name and its signature."""

    match = _DESCRIPTION.match(description)
    if match is None:
        return description
    return f"{match['kind']} {match['path'].rsplit('.', 1)[-1]}{match['rest'] or ''}"


def _section(release: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    heading = f"\n# [{release}]"
    assert heading in text, f"CHANGELOG.md has no section for {release}"
    start = text.index(heading)
    end = text.find("\n# [", start + len(heading))
    return text[start : end if end != -1 else len(text)]


def _changes() -> list[str]:
    before = _previous()["packages"]
    after = json.loads(CURRENT.read_text(encoding="utf-8"))["packages"]
    changed: list[str] = []
    for package in sorted(set(before) | set(after)):
        old, new = before.get(package, {}), after.get(package, {})
        changed += [f"{package}.{name}" for name in sorted(set(old) - set(new))]
        changed += [
            f"{package}.{name}"
            for name in sorted(set(old) & set(new))
            if _seen_by_a_caller(old[name]) != _seen_by_a_caller(new[name])
        ]
    return changed


def test_the_comparison_finds_what_it_should() -> None:
    """A guard on the guard: the diff against the previous release is not empty."""

    assert _changes(), "no change since the previous release -- or the comparison is broken"


def test_every_removed_or_changed_name_is_in_the_changelog() -> None:
    section = _section(alphalab.__version__)
    quoted = " ".join(re.findall(r"`([^`]+)`", section))
    missing = [
        qualified
        for qualified in _changes()
        if not re.search(rf"(?<![\w]){re.escape(qualified.rsplit('.', 1)[-1])}(?![\w])", quoted)
    ]
    assert not missing, (
        f"removed or changed since the previous release and not named in CHANGELOG.md "
        f"[{alphalab.__version__}]: {missing}"
    )
